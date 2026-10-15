"""
The tool-calling agent loop.

Autonomous execution loop with:
- Deterministic gating via Validator on writes
- Automatic post-execution Verifier
- Step-by-step audit logging into the SQLite database
- Revisions limiter and maximum step limits
- Structured Decision output
"""

import json
import re
from typing import Optional

from app.agent.llm import BaseLLMProvider, LLMMessage, get_provider
from app.agent.prompts import SYSTEM_PROMPT, format_situation_prompt
from app.agent.schemas import Decision, Situation
from app.agent.tools import TOOL_DEFINITIONS, execute_tool
from app.audit.trace import TraceRecorder


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first valid JSON object from text."""
    if not text:
        return None
    # Try finding markdown ```json ... ``` blocks
    json_block = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_block:
        try:
            return json.loads(json_block.group(1))
        except Exception:
            pass

    # Try finding raw curly braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except Exception:
            pass

    return None


def run_agent(
    situation: Situation,
    scenario_id: Optional[str] = None,
    provider: Optional[BaseLLMProvider] = None,
    max_steps: int = 15,
    max_revisions: int = 3,
) -> Decision:
    """Run the purchasing agent loop for a situation."""
    if provider is None:
        provider = get_provider()

    recorder = TraceRecorder(scenario_id=scenario_id)

    # Initial conversation messages
    messages: list[LLMMessage] = [
        LLMMessage(role="system", content=SYSTEM_PROMPT),
        LLMMessage(role="user", content=format_situation_prompt(situation)),
    ]

    revisions_count = 0
    created_po_id: Optional[int] = None
    created_po_qty: int = 0
    created_supplier_id: Optional[str] = None
    last_validation_status = "not_run"
    last_verification_status = "not_run"
    escalated = False
    escalation_reason = ""
    approval_requested = False
    approval_reason = ""
    constraints_checked: set[str] = set()
    factors: list[str] = []

    final_decision: Optional[Decision] = None

    for step_idx in range(1, max_steps + 1):
        # 1. Call LLM
        response = provider.chat(messages, TOOL_DEFINITIONS)

        recorder.record_step(
            step_type="llm",
            name="llm_response",
            input_data={"step": step_idx, "message_count": len(messages)},
            output_data={
                "content": response.content,
                "tool_calls": [
                    {"name": tc.tool_name, "arguments": tc.arguments}
                    for tc in (response.tool_calls or [])
                ],
                "stop_reason": response.stop_reason,
            },
        )

        # 2. Check for tool calls
        if response.tool_calls:
            assistant_msg = LLMMessage(
                role="assistant",
                content=response.content,
                tool_calls=response.tool_calls,
            )
            messages.append(assistant_msg)

            for tc in response.tool_calls:
                recorder.record_step(
                    step_type="tool_call",
                    name=tc.tool_name,
                    input_data=tc.arguments,
                )

                # Execute tool
                result = execute_tool(tc.tool_name, tc.arguments)

                recorder.record_step(
                    step_type="tool_result",
                    name=tc.tool_name,
                    output_data=result,
                )

                # Process specific tool outcomes
                if tc.tool_name == "validate_proposed_po":
                    for c in ["budget", "storage", "moq", "case_pack", "shelf_life", "lead_time_stockout"]:
                        constraints_checked.add(c)
                    if result.get("valid") is True:
                        last_validation_status = "passed"
                    else:
                        last_validation_status = "failed"
                        revisions_count += 1
                        factors.append(f"Validation failed: {result.get('violations', [])}")

                elif tc.tool_name == "create_po":
                    for c in ["budget", "storage", "moq", "case_pack", "shelf_life"]:
                        constraints_checked.add(c)
                    if result.get("created"):
                        last_validation_status = "passed"
                        po_info = result.get("po", {})
                        created_po_id = po_info.get("id")
                        created_po_qty = po_info.get("qty", tc.arguments.get("qty", 0))
                        created_supplier_id = po_info.get("supplier_id", tc.arguments.get("supplier_id"))
                        ver = result.get("verification", {})
                        ver_status = ver.get("status", "VERIFIED")
                        last_verification_status = ver_status.lower()
                        if ver_status == "MISMATCH":
                            factors.append(f"Verification mismatch detected: {ver.get('mismatches')}")
                    else:
                        last_validation_status = "failed"
                        revisions_count += 1
                        factors.append(f"PO creation blocked: {result.get('violations', [])}")

                elif tc.tool_name == "modify_po":
                    if result.get("modified"):
                        ver = result.get("verification", {})
                        if ver:
                            last_verification_status = ver.get("status", "VERIFIED").lower()

                elif tc.tool_name == "escalate":
                    escalated = True
                    escalation_reason = tc.arguments.get("reason", "Escalated by agent")
                    factors.append(f"Escalation: {escalation_reason}")

                elif tc.tool_name == "request_human_approval":
                    approval_requested = True
                    approval_reason = tc.arguments.get("reason", "High value order")
                    factors.append(f"Human approval requested: {approval_reason}")

                elif tc.tool_name == "get_open_pos":
                    pos = result.get("open_pos", [])
                    if pos:
                        constraints_checked.add("open_pos")
                        factors.append(f"Found {len(pos)} open PO(s)")

                elif tc.tool_name == "calculate_net_requirement":
                    net_need = result.get("net_need")
                    factors.append(f"Calculated net need: {net_need}")

                # Append tool result to conversation
                messages.append(
                    LLMMessage(
                        role="tool",
                        content=json.dumps(result, default=str),
                        tool_call_id=tc.call_id,
                        tool_name=tc.tool_name,
                    )
                )

            # Check revision limit
            if revisions_count > max_revisions:
                escalated = True
                escalation_reason = f"Exceeded max revisions limit ({max_revisions})"
                final_decision = Decision(
                    outcome="ESCALATE",
                    final_qty=0,
                    rationale=escalation_reason,
                    factors=factors,
                    constraints_checked=list(constraints_checked),
                    validation_status=last_validation_status,
                    verification_status=last_verification_status,
                    confidence=0.5,
                )
                break

        else:
            # LLM provided a textual response with no tool calls -> parse final decision
            parsed = _extract_json_object(response.content)
            if parsed and "outcome" in parsed:
                final_decision = Decision(
                    outcome=parsed.get("outcome", "INVESTIGATE").upper(),
                    final_qty=parsed.get("final_qty", created_po_qty),
                    supplier_id=parsed.get("supplier_id") or created_supplier_id or situation.supplier_id,
                    po_id=parsed.get("po_id") or created_po_id,
                    rationale=parsed.get("rationale", response.content),
                    factors=parsed.get("factors") or factors,
                    constraints_checked=parsed.get("constraints_checked") or list(constraints_checked),
                    validation_status=last_validation_status if last_validation_status != "not_run" else parsed.get("validation_status", "not_run"),
                    verification_status=last_verification_status if last_verification_status != "not_run" else parsed.get("verification_status", "not_run"),
                    confidence=float(parsed.get("confidence", 0.9)),
                )
            else:
                # Infer structured decision from conversation history
                outcome = "INVESTIGATE"
                qty = 0
                rationale = response.content or "Completed purchasing analysis"

                if escalated:
                    outcome = "ESCALATE"
                    rationale = escalation_reason or rationale
                elif approval_requested:
                    outcome = "MODIFY"
                    rationale = f"Approval requested: {approval_reason}"
                    qty = situation.recommended_qty
                elif created_po_id is not None:
                    qty = created_po_qty
                    if qty == situation.recommended_qty:
                        outcome = "ACCEPT"
                    else:
                        outcome = "MODIFY"
                elif any("open PO" in f or "duplicate" in f for f in factors):
                    outcome = "REJECT"
                    rationale = "Existing open PO already covers required demand"

                final_decision = Decision(
                    outcome=outcome,
                    final_qty=qty,
                    supplier_id=created_supplier_id or situation.supplier_id,
                    po_id=created_po_id,
                    rationale=rationale,
                    factors=factors,
                    constraints_checked=list(constraints_checked),
                    validation_status=last_validation_status,
                    verification_status=last_verification_status,
                    confidence=0.85 if outcome in ["ACCEPT", "MODIFY", "REJECT"] else 0.6,
                )
            break

    # If step loop exhausted without decision
    if final_decision is None:
        final_decision = Decision(
            outcome="ESCALATE" if not created_po_id else "MODIFY",
            final_qty=created_po_qty,
            po_id=created_po_id,
            supplier_id=created_supplier_id or situation.supplier_id,
            rationale=f"Agent completed loop (steps: {max_steps})",
            factors=factors,
            constraints_checked=list(constraints_checked),
            validation_status=last_validation_status,
            verification_status=last_verification_status,
            confidence=0.5,
        )

    # Persist final decision in audit DB
    recorder.finish(
        outcome=final_decision.outcome,
        final_po_id=final_decision.po_id,
        status="completed" if final_decision.outcome != "ESCALATE" else "escalated",
    )

    return final_decision

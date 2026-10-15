"""
LLM provider abstraction with mock, Anthropic, and OpenAI backends.

The mock provider follows pre-recorded tool-call sequences per scenario,
so the demo and tests run with no API key.

Usage:
    provider = get_provider()
    response = provider.chat(messages, tools)
"""

import json
from dataclasses import dataclass
from typing import Optional

from app.config import settings
from app.agent.schemas import ToolCallRequest


@dataclass
class LLMMessage:
    """A message in the conversation."""
    role: str           # "system", "user", "assistant", "tool"
    content: str = ""
    tool_calls: list[ToolCallRequest] | None = None
    tool_call_id: str = ""
    tool_name: str = ""


@dataclass
class LLMResponse:
    """Response from the LLM provider."""
    content: str = ""
    tool_calls: list[ToolCallRequest] | None = None
    stop_reason: str = ""   # "end_turn", "tool_use", "max_tokens"
    usage: dict | None = None


class BaseLLMProvider:
    """Abstract base for LLM providers."""

    def chat(self, messages: list[LLMMessage], tools: list[dict]) -> LLMResponse:
        raise NotImplementedError


# ── Mock Provider ──────────────────────────────────────────────────────

class MockLLMProvider(BaseLLMProvider):
    """
    Deterministic mock provider that follows scripted tool-call sequences.
    
    Each scenario has a pre-recorded sequence of (tool_calls, text) steps.
    The mock "LLM" replays these in order, reacting to tool results
    just enough to be realistic.
    """

    def __init__(self):
        self._step = 0
        self._scenario_script: list[dict] | None = None

    def set_script(self, script: list[dict]):
        """
        Set the tool-call script for this scenario.
        
        Each entry is a dict:
          {"tool_calls": [{"name": "...", "arguments": {...}}], "text": "..."}
        or:
          {"text": "...", "decision": {...}}  (final response)
        """
        self._step = 0
        self._scenario_script = script

    def chat(self, messages: list[LLMMessage], tools: list[dict]) -> LLMResponse:
        # If no script, use auto-detection from the situation
        if self._scenario_script is None:
            return self._auto_respond(messages)

        if self._step >= len(self._scenario_script):
            # Script exhausted — return final text
            return LLMResponse(content="Script exhausted. Ending.", stop_reason="end_turn")

        entry = self._scenario_script[self._step]
        self._step += 1

        if "tool_calls" in entry:
            calls = [
                ToolCallRequest(
                    tool_name=tc["name"],
                    arguments=tc.get("arguments", {}),
                    call_id=f"mock_{self._step}_{i}",
                )
                for i, tc in enumerate(entry["tool_calls"])
            ]
            return LLMResponse(
                content=entry.get("text", ""),
                tool_calls=calls,
                stop_reason="tool_use",
            )
        else:
            return LLMResponse(
                content=entry.get("text", ""),
                stop_reason="end_turn",
            )

    def _auto_respond(self, messages: list[LLMMessage]) -> LLMResponse:
        """
        Auto-detect scenario from the situation message and generate
        a reasonable mock response. Used when no explicit script is set.
        """
        # Look at the last user message for situation context
        last_user = ""
        for m in reversed(messages):
            if m.role == "user":
                last_user = m.content
                break

        # Check if we just got tool results — if so, try to proceed
        last_msg = messages[-1] if messages else None
        if last_msg and last_msg.role == "tool":
            # We're mid-script, generate next step based on context
            return self._generate_next_step(messages)

        # First call — start investigation
        return LLMResponse(
            content="Let me investigate this purchasing situation.",
            tool_calls=[
                ToolCallRequest(
                    tool_name="get_inventory",
                    arguments=self._extract_sku_node(last_user),
                    call_id="mock_auto_1",
                ),
            ],
            stop_reason="tool_use",
        )

    def _generate_next_step(self, messages: list[LLMMessage]) -> LLMResponse:
        """Generate the next mock step based on conversation state."""
        # Count how many tool calls we've made
        tool_call_count = sum(1 for m in messages if m.tool_calls)

        if tool_call_count == 0:
            # Get forecast
            sku_node = self._extract_sku_node_from_messages(messages)
            return LLMResponse(
                content="Getting demand forecast.",
                tool_calls=[ToolCallRequest(
                    tool_name="get_demand_forecast",
                    arguments=sku_node,
                    call_id="mock_auto_2",
                )],
                stop_reason="tool_use",
            )
        elif tool_call_count <= 4:
            # Return final decision
            return LLMResponse(
                content=json.dumps({
                    "outcome": "INVESTIGATE",
                    "rationale": "Mock agent completed investigation",
                    "final_qty": 0,
                    "confidence": 0.5,
                }),
                stop_reason="end_turn",
            )
        else:
            return LLMResponse(
                content=json.dumps({
                    "outcome": "ESCALATE",
                    "rationale": "Mock agent reached max steps",
                    "final_qty": 0,
                    "confidence": 0.3,
                }),
                stop_reason="end_turn",
            )

    def _extract_sku_node(self, text: str) -> dict:
        """Extract sku_id and node_id from text, defaulting to common values."""
        sku = "SKU-RICE-5KG"
        node = "NODE-KRM"
        for known_sku in ["SKU-MILK-1L", "SKU-RICE-5KG", "SKU-OIL-1L", "SKU-SHAMPOO", "SKU-BATTERY", "SKU-DIAPER"]:
            if known_sku in text:
                sku = known_sku
                break
        for known_node in ["NODE-KRM", "NODE-IND"]:
            if known_node in text:
                node = known_node
                break
        return {"sku_id": sku, "node_id": node}

    def _extract_sku_node_from_messages(self, messages: list[LLMMessage]) -> dict:
        for m in messages:
            if m.role == "user":
                return self._extract_sku_node(m.content)
        return {"sku_id": "SKU-RICE-5KG", "node_id": "NODE-KRM"}


# ── Anthropic Provider ─────────────────────────────────────────────────

class AnthropicLLMProvider(BaseLLMProvider):
    """Provider using the Anthropic SDK for Claude models."""

    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        self.model = settings.llm_model

    def chat(self, messages: list[LLMMessage], tools: list[dict]) -> LLMResponse:
        # Separate system message
        system = ""
        api_messages = []
        for m in messages:
            if m.role == "system":
                system = m.content
            elif m.role == "assistant":
                content_blocks = []
                if m.content:
                    content_blocks.append({"type": "text", "text": m.content})
                if m.tool_calls:
                    for tc in m.tool_calls:
                        content_blocks.append({
                            "type": "tool_use",
                            "id": tc.call_id,
                            "name": tc.tool_name,
                            "input": tc.arguments,
                        })
                api_messages.append({"role": "assistant", "content": content_blocks or m.content})
            elif m.role == "tool":
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m.tool_call_id,
                        "content": m.content,
                    }],
                })
            else:
                api_messages.append({"role": m.role, "content": m.content})

        # Convert tool definitions to Anthropic format
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]

        response = self.client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=api_messages,
            tools=anthropic_tools,
        )

        # Parse response
        content = ""
        tool_calls = []
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(ToolCallRequest(
                    tool_name=block.name,
                    arguments=block.input,
                    call_id=block.id,
                ))

        return LLMResponse(
            content=content,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=response.stop_reason,
            usage={"input": response.usage.input_tokens, "output": response.usage.output_tokens},
        )


# ── OpenAI Provider ────────────────────────────────────────────────────

class OpenAILLMProvider(BaseLLMProvider):
    """Provider using the OpenAI SDK."""

    def __init__(self):
        import openai
        self.client = openai.OpenAI(api_key=settings.openai_api_key)
        self.model = settings.llm_model

    def chat(self, messages: list[LLMMessage], tools: list[dict]) -> LLMResponse:
        api_messages = []
        for m in messages:
            if m.role == "tool":
                api_messages.append({
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "content": m.content,
                })
            elif m.role == "assistant" and m.tool_calls:
                api_messages.append({
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        {
                            "id": tc.call_id,
                            "type": "function",
                            "function": {"name": tc.tool_name, "arguments": json.dumps(tc.arguments)},
                        }
                        for tc in m.tool_calls
                    ],
                })
            else:
                api_messages.append({"role": m.role, "content": m.content})

        # Convert tools to OpenAI function-calling format
        openai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=api_messages,
            tools=openai_tools,
            max_tokens=4096,
        )

        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls = None

        if choice.message.tool_calls:
            tool_calls = [
                ToolCallRequest(
                    tool_name=tc.function.name,
                    arguments=json.loads(tc.function.arguments),
                    call_id=tc.id,
                )
                for tc in choice.message.tool_calls
            ]

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            stop_reason=choice.finish_reason,
            usage={"input": response.usage.prompt_tokens, "output": response.usage.completion_tokens}
            if response.usage else None,
        )


# ── Provider factory ───────────────────────────────────────────────────

def get_provider() -> BaseLLMProvider:
    """Get the configured LLM provider based on LLM_PROVIDER env var."""
    if settings.llm_provider == "mock":
        return MockLLMProvider()
    elif settings.llm_provider == "anthropic":
        return AnthropicLLMProvider()
    elif settings.llm_provider == "openai":
        return OpenAILLMProvider()
    else:
        raise ValueError(f"Unknown LLM_PROVIDER: {settings.llm_provider}")

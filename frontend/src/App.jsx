import React, { useState, useEffect } from 'react';
import {
  Play,
  RotateCcw,
  CheckCircle,
  AlertTriangle,
  XCircle,
  HelpCircle,
  ShieldCheck,
  ShieldAlert,
  ArrowRight,
  Layers,
  Activity,
  UserCheck,
  Database,
  Cpu,
} from 'lucide-react';

const API_BASE = 'http://localhost:8000';

const FALLBACK_SCENARIOS = [
  {
    id: 'scenario_1',
    title: 'Scenario 1: Happy Path ACCEPT',
    description: 'Recommended order for Milk (120 units) verified against forecast and lead time. Creates PO and verifies state.',
    situation: {
      scenario_type: 'recommendation',
      sku_id: 'SKU-MILK-1L',
      node_id: 'NODE-KRM',
      recommended_qty: 120,
      supplier_id: 'SUP-FRESH',
    },
  },
  {
    id: 'scenario_2',
    title: 'Scenario 2: Open PO Covers Demand -> REJECT',
    description: 'Existing open PO for 300 units arriving in 2 days already covers demand. Agent rejects recommendation to avoid over-ordering.',
    situation: {
      scenario_type: 'recommendation',
      sku_id: 'SKU-RICE-5KG',
      node_id: 'NODE-KRM',
      recommended_qty: 200,
      supplier_id: 'SUP-MEGA',
    },
  },
  {
    id: 'scenario_3',
    title: 'Scenario 3: Storage Constraint -> MODIFY',
    description: 'Recommended 800 units exceeds storage and max coverage limits. Agent detects violation and adapts down to 100 safe units.',
    situation: {
      scenario_type: 'constraint',
      sku_id: 'SKU-RICE-5KG',
      node_id: 'NODE-IND',
      recommended_qty: 800,
      supplier_id: 'SUP-MEGA',
    },
  },
  {
    id: 'scenario_4',
    title: 'Scenario 4: Budget Nearly Exhausted -> ESCALATE',
    description: 'Personal-care budget has ₹2,000 remaining, but supplier MOQ requires ₹2,760. Validator blocks order; agent escalates.',
    situation: {
      scenario_type: 'constraint',
      sku_id: 'SKU-SHAMPOO',
      node_id: 'NODE-KRM',
      recommended_qty: 48,
      supplier_id: 'SUP-MEGA',
    },
  },
  {
    id: 'scenario_5',
    title: 'Scenario 5: Supplier Shortfall -> Split to Alternate',
    description: 'Primary supplier delivers only 100 of 200 units. Agent finds alternate supplier SUP-QUICK and issues split PO for remainder.',
    situation: {
      scenario_type: 'shortfall',
      sku_id: 'SKU-RICE-5KG',
      node_id: 'NODE-KRM',
      recommended_qty: 200,
      supplier_id: 'SUP-MEGA',
      event_details: { shortfall_qty: 100, original_qty: 200 },
    },
  },
  {
    id: 'scenario_8',
    title: 'Scenario 8: Fault Recovery (Case-Pack Mismatch)',
    description: 'ERP rounds order from 50 to 60 units due to supplier case-pack misbehaviour. Verifier detects MISMATCH; agent adapts safely.',
    situation: {
      scenario_type: 'fault_recovery',
      sku_id: 'SKU-MILK-1L',
      node_id: 'NODE-KRM',
      recommended_qty: 50,
      supplier_id: 'SUP-FRESH',
      event_details: { fault: 'ROUND_TO_CASEPACK' },
    },
  },
  {
    id: 'scenario_10',
    title: 'Scenario 10: Value > ₹50,000 -> Request Human Approval',
    description: 'High value diaper order (₹112,500) exceeds auto-approval threshold. Agent requests human approval instead of auto-executing.',
    situation: {
      scenario_type: 'approval_required',
      sku_id: 'SKU-DIAPER',
      node_id: 'NODE-KRM',
      recommended_qty: 150,
      supplier_id: 'SUP-MEGA',
    },
  },
];

export default function App() {
  const [scenarios, setScenarios] = useState(FALLBACK_SCENARIOS);
  const [selectedScenarioId, setSelectedScenarioId] = useState('scenario_1');
  const [situation, setSituation] = useState(FALLBACK_SCENARIOS[0].situation);

  const [loading, setLoading] = useState(false);
  const [decision, setDecision] = useState(null);
  const [decisionId, setDecisionId] = useState(null);
  const [traceSteps, setTraceSteps] = useState([]);
  const [recentRuns, setRecentRuns] = useState([]);

  const [activeFaults, setActiveFaults] = useState({});
  const [approvalRequest, setApprovalRequest] = useState(null);

  // Fetch preset scenarios and recent decisions on load
  useEffect(() => {
    fetch(`${API_BASE}/agent/scenarios`)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data) && data.length > 0) {
          setScenarios(data);
        }
      })
      .catch(() => {});

    loadRecentDecisions();
  }, []);

  const loadRecentDecisions = () => {
    fetch(`${API_BASE}/agent/decisions?limit=8`)
      .then((r) => r.json())
      .then((data) => {
        if (Array.isArray(data)) setRecentRuns(data);
      })
      .catch(() => {});
  };

  const handleSelectScenario = (id) => {
    setSelectedScenarioId(id);
    const sc = scenarios.find((s) => s.id === id);
    if (sc) {
      setSituation({ ...sc.situation });
      setDecision(null);
      setTraceSteps([]);
      setApprovalRequest(null);
    }
  };

  const handleRunAgent = async () => {
    setLoading(true);
    setDecision(null);
    setTraceSteps([]);
    setApprovalRequest(null);

    try {
      const res = await fetch(`${API_BASE}/agent/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          situation,
          scenario_id: selectedScenarioId,
          provider: 'mock',
        }),
      });

      const data = await res.json();
      setDecision(data.decision);
      setDecisionId(data.decision_id);

      // Check if human approval was requested
      if (
        data.decision.outcome === 'MODIFY' &&
        data.decision.rationale &&
        data.decision.rationale.toLowerCase().includes('approval')
      ) {
        setApprovalRequest({
          sku_id: situation.sku_id,
          node_id: situation.node_id,
          supplier_id: situation.supplier_id || 'SUP-MEGA',
          qty: situation.recommended_qty,
          unit_price: 750.0,
          decision_id: data.decision_id,
        });
      }

      // Load trace steps
      if (data.decision_id) {
        const traceRes = await fetch(`${API_BASE}/agent/decisions/${data.decision_id}`);
        const traceData = await traceRes.json();
        setTraceSteps(traceData.steps || []);
      }

      loadRecentDecisions();
    } catch (err) {
      console.error('Run failed:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleToggleFault = async (faultName) => {
    const isCurrentlyActive = !!activeFaults[faultName];
    const newState = !isCurrentlyActive;
    try {
      await fetch(`${API_BASE}/erp/faults/${faultName}?enabled=${newState}`, { method: 'POST' });
      setActiveFaults((prev) => ({ ...prev, [faultName]: newState }));
    } catch (err) {
      console.error('Fault toggle error:', err);
    }
  };

  const handleApprovalAction = async (action) => {
    if (!approvalRequest) return;
    try {
      const res = await fetch(`${API_BASE}/agent/approve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          ...approvalRequest,
          action,
          notes: `Human action: ${action}`,
        }),
      });
      const data = await res.json();
      setApprovalRequest(null);

      // Reload trace to see human approval step
      if (decisionId) {
        const traceRes = await fetch(`${API_BASE}/agent/decisions/${decisionId}`);
        const traceData = await traceRes.json();
        setTraceSteps(traceData.steps || []);
      }
      loadRecentDecisions();
    } catch (err) {
      console.error('Approval failed:', err);
    }
  };

  const handleLoadPastDecision = async (id) => {
    try {
      const res = await fetch(`${API_BASE}/agent/decisions/${id}`);
      const data = await res.json();
      setDecisionId(id);
      setDecision({
        outcome: data.outcome || 'UNKNOWN',
        final_qty: 0,
        rationale: `Historical Run #${data.id} (${data.scenario_id || 'manual'})`,
        validation_status: 'passed',
        verification_status: 'verified',
        confidence: 0.9,
      });
      setTraceSteps(data.steps || []);
    } catch (err) {
      console.error('Failed to load decision:', err);
    }
  };

  const activeScenario = scenarios.find((s) => s.id === selectedScenarioId);

  return (
    <div className="app-container">
      {/* ── Top Navigation Bar ─────────────────────────────────────────── */}
      <header className="navbar">
        <div className="nav-brand">
          <span className="brand-badge">Rappi India</span>
          <div>
            <span className="brand-title">AI Purchasing Agent</span>
            <span className="brand-subtitle">Autonomous Quick-Commerce Operations</span>
          </div>
        </div>

        <div className="nav-actions">
          <div className="status-pill">
            <span className="dot"></span>
            Backend: Connected
          </div>
          <div className="status-pill">
            <Cpu size={14} />
            Provider: Mock (Deterministic)
          </div>
        </div>
      </header>

      {/* ── Main Content Grid ──────────────────────────────────────────── */}
      <main className="main-content">
        {/* ── Left Column: Controls & Scenarios ────────────────────────── */}
        <section className="controls-column">
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">
                <Layers size={18} color="#3b82f6" />
                Scenario Presets
              </h2>
            </div>

            <div className="form-group">
              <label className="form-label">Select Evaluation Scenario</label>
              <select
                className="select"
                value={selectedScenarioId}
                onChange={(e) => handleSelectScenario(e.target.value)}
              >
                {scenarios.map((sc) => (
                  <option key={sc.id} value={sc.id}>
                    {sc.title}
                  </option>
                ))}
              </select>
            </div>

            {activeScenario && (
              <div
                style={{
                  background: 'rgba(59, 130, 246, 0.08)',
                  border: '1px solid rgba(59, 130, 246, 0.25)',
                  borderRadius: 'var(--radius-md)',
                  padding: '0.85rem',
                  fontSize: '0.8rem',
                  color: '#cbd5e1',
                  marginBottom: '1rem',
                }}
              >
                <div style={{ fontWeight: 600, color: '#93c5fd', marginBottom: '0.3rem' }}>
                  {activeScenario.title}
                </div>
                <div>{activeScenario.description}</div>
              </div>
            )}

            <div className="card-header" style={{ marginTop: '1.25rem' }}>
              <h3 className="card-title" style={{ fontSize: '0.85rem' }}>
                <Activity size={16} color="#10b981" />
                Situation Parameters
              </h3>
            </div>

            <div className="form-group">
              <label className="form-label">SKU ID</label>
              <input
                className="input"
                value={situation.sku_id || ''}
                onChange={(e) => setSituation({ ...situation, sku_id: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Fulfillment Node</label>
              <input
                className="input"
                value={situation.node_id || ''}
                onChange={(e) => setSituation({ ...situation, node_id: e.target.value })}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Recommended Quantity</label>
              <input
                className="input"
                type="number"
                value={situation.recommended_qty || 0}
                onChange={(e) =>
                  setSituation({ ...situation, recommended_qty: parseInt(e.target.value) || 0 })
                }
              />
            </div>

            <div className="form-group">
              <label className="form-label">Target / Primary Supplier</label>
              <input
                className="input"
                value={situation.supplier_id || ''}
                onChange={(e) => setSituation({ ...situation, supplier_id: e.target.value })}
              />
            </div>

            <button
              className="btn btn-primary"
              style={{ width: '100%', marginTop: '0.5rem' }}
              onClick={handleRunAgent}
              disabled={loading}
            >
              <Play size={16} />
              {loading ? 'Agent Investigating & Validating...' : 'Run Autonomous Agent'}
            </button>
          </div>

          {/* ── Fault Injection Toggles ────────────────────────────────── */}
          <div className="card" style={{ marginTop: '1.25rem' }}>
            <div className="card-header">
              <h2 className="card-title">
                <AlertTriangle size={18} color="#f59e0b" />
                Mock ERP Fault Injection
              </h2>
            </div>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-dim)', marginBottom: '0.6rem' }}>
              Toggle misbehaviours to test post-execution verification and auto-recovery:
            </p>

            <div className="fault-grid">
              {[
                { key: 'ROUND_TO_CASEPACK', label: 'ROUND_TO_CASEPACK (qty round-up)' },
                { key: 'PARTIAL_ACCEPT', label: 'PARTIAL_ACCEPT (50% fulfillment)' },
                { key: 'PRICE_CHANGE', label: 'PRICE_CHANGE (+10% invoice deviation)' },
                { key: 'ETA_SLIP', label: 'ETA_SLIP (+2 days delivery delay)' },
                { key: 'REJECT_BUDGET', label: 'REJECT_BUDGET (concurrent lock)' },
              ].map(({ key, label }) => {
                const isActive = !!activeFaults[key];
                return (
                  <div
                    key={key}
                    className={`fault-toggle ${isActive ? 'active' : ''}`}
                    onClick={() => handleToggleFault(key)}
                  >
                    <span className="fault-name">{label}</span>
                    <span style={{ fontWeight: 700, color: isActive ? '#f59e0b' : '#64748b' }}>
                      {isActive ? 'ACTIVE' : 'OFF'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>

          {/* ── Past Runs History ───────────────────────────────────────── */}
          <div className="card" style={{ marginTop: '1.25rem' }}>
            <div className="card-header">
              <h2 className="card-title">
                <Database size={18} color="#8b5cf6" />
                Audit Log History
              </h2>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
              {recentRuns.map((r) => (
                <div
                  key={r.id}
                  onClick={() => handleLoadPastDecision(r.id)}
                  style={{
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center',
                    padding: '0.5rem 0.75rem',
                    borderRadius: 'var(--radius-sm)',
                    background: 'var(--bg-inset)',
                    cursor: 'pointer',
                    fontSize: '0.78rem',
                    border: '1px solid var(--border-subtle)',
                  }}
                >
                  <span style={{ fontFamily: 'var(--font-mono)' }}>Run #{r.id} ({r.scenario_id || 'manual'})</span>
                  <span
                    style={{
                      fontWeight: 700,
                      color:
                        r.outcome === 'ACCEPT'
                          ? '#10b981'
                          : r.outcome === 'MODIFY'
                          ? '#f59e0b'
                          : '#f43f5e',
                    }}
                  >
                    {r.outcome || r.status}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* ── Right Column: Decision Card & Trace Timeline ─────────────── */}
        <section className="results-column">
          {/* Human Approval Card */}
          {approvalRequest && (
            <div className="approval-card">
              <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
                <UserCheck size={20} color="#f59e0b" />
                <h3 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#fde68a' }}>
                  Human Approval Required
                </h3>
              </div>
              <p style={{ fontSize: '0.85rem', color: '#cbd5e1' }}>
                Order value (₹112,500) exceeds the configured auto-approval threshold of ₹50,000 for{' '}
                <strong>{approvalRequest.sku_id}</strong> (150 units).
              </p>
              <div className="approval-actions">
                <button
                  className="btn btn-approve"
                  onClick={() => handleApprovalAction('approve')}
                >
                  <CheckCircle size={16} /> Approve & Execute PO
                </button>
                <button
                  className="btn btn-reject"
                  onClick={() => handleApprovalAction('reject')}
                >
                  <XCircle size={16} /> Reject Proposal
                </button>
              </div>
            </div>
          )}

          {/* Decision Summary Card */}
          {decision && (
            <div className={`decision-hero outcome-${decision.outcome.toLowerCase()}`}>
              <div className="hero-header">
                <div>
                  <span className="stat-label">Autonomous Decision</span>
                  <div style={{ marginTop: '0.2rem' }}>
                    <span className={`outcome-badge badge-${decision.outcome.toLowerCase()}`}>
                      {decision.outcome}
                    </span>
                  </div>
                </div>

                <div style={{ textAlign: 'right' }}>
                  <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end' }}>
                    <span className="status-pill">
                      <ShieldCheck size={13} color="#10b981" />
                      Validator: {decision.validation_status}
                    </span>
                    <span className="status-pill">
                      <Activity size={13} color="#3b82f6" />
                      Verifier: {decision.verification_status}
                    </span>
                  </div>
                  <div style={{ fontSize: '0.72rem', color: 'var(--text-dim)', marginTop: '0.3rem' }}>
                    Confidence: {(decision.confidence * 100).toFixed(0)}%
                  </div>
                </div>
              </div>

              <div className="hero-stats">
                <div className="stat-box">
                  <span className="stat-label">Final Order Qty</span>
                  <span className="stat-val">{decision.final_qty} units</span>
                </div>
                <div className="stat-box">
                  <span className="stat-label">Supplier Assigned</span>
                  <span className="stat-val" style={{ fontSize: '1rem' }}>
                    {decision.supplier_id || 'None'}
                  </span>
                </div>
                <div className="stat-box">
                  <span className="stat-label">PO Reference ID</span>
                  <span className="stat-val" style={{ fontSize: '1rem' }}>
                    {decision.po_id ? `PO #${decision.po_id}` : 'None (No Write)'}
                  </span>
                </div>
              </div>

              <div className="hero-rationale">
                <strong>Executive Rationale:</strong> {decision.rationale}
              </div>

              {decision.factors && decision.factors.length > 0 && (
                <div style={{ marginTop: '0.85rem' }}>
                  <span className="stat-label">Contributing Supply Chain Factors:</span>
                  <div className="pills-row">
                    {decision.factors.map((f, i) => (
                      <span key={i} className="pill">
                        {f}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Trace Timeline */}
          <div className="card">
            <div className="card-header">
              <h2 className="card-title">
                <Activity size={18} color="#3b82f6" />
                Execution Trace Timeline
              </h2>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-dim)' }}>
                {traceSteps.length} Steps Recorded
              </span>
            </div>

            {loading && (
              <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)' }}>
                <Activity className="animate-spin" size={32} style={{ margin: '0 auto 1rem' }} />
                <div>Agent evaluating inventory, demand curves & policy rules...</div>
              </div>
            )}

            {!loading && traceSteps.length === 0 && (
              <div style={{ textAlign: 'center', padding: '3rem', color: 'var(--text-dim)' }}>
                Select a scenario and click <strong>"Run Autonomous Agent"</strong> to observe the
                real-time reasoning, tool calls, and post-execution verification.
              </div>
            )}

            <div className="timeline">
              {traceSteps.map((step) => {
                const isViolation =
                  step.output &&
                  (step.output.is_valid === false ||
                    (step.output.violations && step.output.violations.length > 0));

                const isMismatch =
                  step.output &&
                  step.output.verification &&
                  step.output.verification.status === 'MISMATCH';

                return (
                  <div key={step.step_no} className="timeline-step">
                    <div className={`step-marker type-${step.type}`}>{step.step_no}</div>

                    <div className="step-body">
                      <div className="step-header">
                        <span className="step-title">
                          {step.name || step.type}
                        </span>
                        <span className="step-badge" style={{ background: 'var(--bg-inset)' }}>
                          {step.type}
                        </span>
                      </div>

                      {step.note && (
                        <div style={{ fontSize: '0.78rem', color: '#93c5fd', marginBottom: '0.4rem' }}>
                          {step.note}
                        </div>
                      )}

                      {step.input && (
                        <div style={{ marginBottom: '0.4rem' }}>
                          <span className="stat-label">Input Parameters:</span>
                          <pre className="code-snippet">{JSON.stringify(step.input, null, 2)}</pre>
                        </div>
                      )}

                      {step.output && (
                        <div>
                          <span className="stat-label">Tool Result:</span>
                          <pre className="code-snippet">{JSON.stringify(step.output, null, 2)}</pre>
                        </div>
                      )}

                      {isViolation && (
                        <div className="violation-alert">
                          <strong>Pure-Code Validator Block:</strong> Proposed PO violated deterministic policy rules. Execution prevented.
                        </div>
                      )}

                      {isMismatch && (
                        <div className="mismatch-alert">
                          <strong>Verifier Deviation Alert:</strong> ERP actual state deviated from agent intent (Fault detected).
                        </div>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}

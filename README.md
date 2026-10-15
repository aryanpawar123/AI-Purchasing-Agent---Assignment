# Autonomous AI Purchasing Agent for Quick-Commerce

[![Test Suite](https://img.shields.io/badge/pytest-129%20passed-10b981.svg)](#running-tests)
[![Evals](https://img.shields.io/badge/evals-100%25%20pass%20rate%20(33%2F33)-3b82f6.svg)](evals/report.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](backend)
[![React](https://img.shields.io/badge/react-18%2B-61dafb.svg)](frontend)
[![Zero API Key](https://img.shields.io/badge/mock%20mode-zero%20API%20key%20needed-purple.svg)](#deterministic-mock-mode)

An autonomous purchasing agent built for a quick-commerce enterprise (Rappi India take-home assignment). The system **makes, executes, and validates** purchasing decisions based on real system state across dark stores, supplier catalogs, departmental budgets, and warehouse capacity.

> **Core Operating Principle: The LLM proposes, deterministic code validates.**  
> The LLM never performs final constraint checks and possesses zero arithmetic authority. A pure-code Validator gates every write, and a post-execution Verifier automatically compares actual ERP state against intent.

---

## Key Capabilities

- **Deterministic Arithmetic:** Safety stock, demand during lead time, net need, and coverage days computed by pure, unit-tested functions (`calculations.py`).
- **Pre-Execution Gating:** 8 strict policy rules (Budget, Storage, MOQ, Case-Pack, Supplier Max Qty, Stock-out Risk, Perishable Shelf-Life, Approval Threshold) evaluated before any PO can be committed.
- **Post-Execution Verification:** Autorun verifier checks actual ERP records after every write to detect and recover from supplier faults (partial accept, price change, case-pack rounding, ETA slips).
- **Human-in-the-Loop Governance:** Orders exceeding ₹50,000 trigger approval requests with one-click interactive review cards in the UI.
- **Auditability:** Every tool call, LLM thought, validator violation, and verification diff is recorded chronologically in an SQLite audit trail.
- **Deterministic Mock Mode:** Ships with zero-cost, zero-API-key support for 100% reproducible testing and demo runs.

---

## Architecture Overview

```
                      ┌────────────────────────────────────────┐
                      │    React + Vite Operations Dashboard   │
                      └───────────────────▲────────────────────┘
                                          │ HTTP / REST
                      ┌───────────────────▼────────────────────┐
                      │    FastAPI Application (/agent, /erp)  │
                      └───────────────────┬────────────────────┘
                                          │
                   ┌──────────────────────┴──────────────────────┐
                   │                                             │
      ┌────────────▼─────────────┐                 ┌─────────────▼────────────┐
      │   Autonomous Agent Core  │                 │    Deterministic Gates   │
      │   - loop.py (tool loop)  │                 │    - calculations.py     │
      │   - prompts.py (system)  │                 │    - validator.py (gate) │
      │   - tools.py (dispatch)  │                 │    - verifier.py (diff)  │
      │   - llm.py (providers)   │                 └─────────────▲────────────┘
      └────────────┬─────────────┘                               │
                   │                                             │
                   └──────────────► Pure Code Validation ────────┘
```

For detailed architecture diagrams, sequence flows, and design trade-offs, see [docs/architecture.md](docs/architecture.md).

---

## Quickstart

### Option A: Running with Docker Compose (Recommended)

Brings up both backend and frontend services in one command:

```bash
# Clone and enter repo
git clone <repo-url>
cd "AI Purchasing Agent — Assignment"

# Launch backend (port 8000) and frontend (port 5173)
docker-compose up --build
```

- **Frontend Dashboard:** [http://localhost:5173](http://localhost:5173)
- **API Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** [http://localhost:8000/health](http://localhost:8000/health)

---

### Option B: Local Setup (Without Docker)

#### 1. Backend Setup (Python 3.11+)

```bash
cd backend

# Create and activate virtual environment (optional but recommended)
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Seed the SQLite database with test fixtures
python -m app.seed

# Start FastAPI development server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Frontend Setup (Node 18+)

```bash
cd frontend

# Install dependencies
npm install

# Start Vite dev server
npm run dev
```

Visit [http://localhost:5173](http://localhost:5173).

---

## Deterministic Mock Mode (No API Keys Needed)

By default, the system operates in **Mock Mode** (`LLM_PROVIDER=mock`). It includes realistic, pre-recorded tool-calling sequences for all 11 evaluation scenarios. You can run all tests, evals, and UI demos **without needing any API key**.

To switch to a live Anthropic or OpenAI model:
1. Copy `.env.example` to `.env`
2. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=your_key`
3. Restart the server.

---

## Running Tests

The test suite covers DB models, mock ERP endpoints, pure calculation functions, the 8 validator rules, verifier mismatch diffs, tool dispatch, and agent feedback loops:

```bash
cd backend
python -m pytest tests/ -v
```

**Result:** `129 passed in 4.84s (0 warnings)`.

---

## Running the Evaluation Harness

The evaluation harness executes the agent against all 11 scenario fixtures with a programmatic rubric (decision correctness, information gathered, constraints respected, action appropriateness, validation gating, and fault recovery):

```bash
# Run 3 iterations per scenario with deterministic mock provider
python evals/run_evals.py --provider mock -n 3

# Or test a single scenario
python evals/run_evals.py --scenario scenario_1
```

The script produces a detailed evaluation report at [`evals/report.md`](evals/report.md):

| Metric | Result |
|---|---|
| **Total Scenarios** | 11 |
| **Total Executions** | 33 runs (N=3 per fixture) |
| **Pass Rate** | **100.0%** (33 / 33 passed) |
| **Run-to-Run Variance** | 0 (Deterministic) |

---

## Interactive UI & Demo Walkthrough

The React dashboard provides an intuitive interface for live demonstrations:

1. **Scenario 1: Happy Path ACCEPT**
   - Select `Scenario 1: Happy Path ACCEPT` from the dropdown.
   - Click **Run Autonomous Agent**.
   - Observe the agent fetch inventory (90 available), compute net requirement (120 units), pass validation, create PO #2, and verify exact ERP match.
2. **Scenario 3: Constraint-Driven MODIFY**
   - Select `Scenario 3: Storage Constraint -> MODIFY`.
   - Recommended quantity is 800 units.
   - Observe the Validator flag a `BLOCK` violation on warehouse capacity. The agent adapts down to 100 safe units, validates, and commits.
3. **Scenario 5: Supplier Shortfall & Split Order**
   - Select `Scenario 5: Supplier Shortfall -> Split to Alternate`.
   - Primary supplier fulfills only 100 of 200 units.
   - The agent queries `get_alternate_suppliers`, finds `SUP-QUICK`, validates terms, and creates a split PO for the remaining 100 units.
4. **Scenario 8: Fault Injection & Post-Execution Recovery**
   - In the **Mock ERP Fault Injection** box, toggle `ROUND_TO_CASEPACK (qty round-up)`.
   - Select `Scenario 8: Fault Recovery`.
   - The agent requests 50 units. The mock ERP silently rounds up to 60 units (case-pack multiple).
   - The post-execution Verifier detects `MISMATCH`. The agent alerts the operator, re-validates 60 units against storage/budget, and absorbs the difference safely.
5. **Scenario 10: Human Approval Workflow**
   - Select `Scenario 10: High Value Order -> Request Approval`.
   - The order value for 150 diapers is ₹112,500 (> ₹50,000 threshold).
   - The agent yields a **Human Approval Required** card.
   - Click **Approve & Execute PO** to see the human-in-the-loop action executed through the pure-code validator gate.

---

## Repository Structure

```
.
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, lifespan, CORS, routes
│   │   ├── config.py            # Pydantic Settings (.env configuration)
│   │   ├── db.py                # SQLite database session and engine
│   │   ├── models.py            # SQLAlchemy models (SKUs, inventory, POs, traces)
│   │   ├── seed.py              # Realistic seed data generator
│   │   ├── mock_erp/
│   │   │   ├── api.py           # Simulated ERP/OMS endpoints
│   │   │   └── fault_injection.py # Fault simulation engine
│   │   ├── agent/
│   │   │   ├── loop.py          # Autonomous tool-calling loop
│   │   │   ├── prompts.py       # System prompt & situation formatter
│   │   │   ├── tools.py         # 14 agent tools with JSON schemas
│   │   │   ├── llm.py           # Provider abstraction (Mock, Anthropic, OpenAI)
│   │   │   ├── mock_scripts.py  # Scripted sequences for deterministic runs
│   │   │   └── api.py           # Agent REST API router (/agent/*)
│   │   ├── policy/
│   │   │   ├── calculations.py  # Pure mathematical formulas
│   │   │   ├── validator.py     # 8 deterministic validation rules
│   │   │   └── verifier.py      # Post-execution state comparator
│   │   └── audit/
│   │       └── trace.py         # Step-by-step trace recorder
│   ├── tests/                   # Pytest test suite (129 unit & integration tests)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── App.jsx              # Interactive operations dashboard
│   │   └── index.css            # Dark theme, glassmorphism design system
│   ├── Dockerfile
│   └── package.json
├── evals/
│   ├── scenarios/*.json         # 11 structured scenario fixtures
│   ├── run_evals.py             # Multi-run evaluation harness with scoring rubric
│   └── report.md                # Generated evaluation report
├── docs/
│   └── architecture.md          # Architecture & Sequence diagrams, policy specs
├── docker-compose.yml           # Multi-container orchestration
├── .env.example
└── README.md
```

---

## License

MIT License. Designed and engineered for the Rappi India AI Purchasing Agent Take-Home Assignment.

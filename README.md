# Autonomous AI Purchasing Agent for Quick-Commerce

[![Test Suite](https://img.shields.io/badge/pytest-129%20passed-10b981.svg)](#running-tests)
[![Evals](https://img.shields.io/badge/evals-100%25%20pass%20rate%20(33%2F33)-3b82f6.svg)](evals/report.md)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](backend)
[![React](https://img.shields.io/badge/react-18%2B-61dafb.svg)](frontend)
[![Zero API Key](https://img.shields.io/badge/mock%20mode-zero%20API%20key%20needed-purple.svg)](#zero-api-key-mode)

An autonomous purchasing agent built for a quick-commerce enterprise (Rappi India take-home assignment). The system **makes, executes, and validates** purchasing decisions based on real system state across dark stores, supplier catalogs, departmental budgets, and warehouse capacity.

> **Core Operating Principle: The LLM proposes, deterministic code validates.**  
> The LLM never performs final constraint checks and possesses zero arithmetic authority. A pure-code **Validator** gates every write, and a post-execution **Verifier** re-reads system state after every write to detect state drift and trigger recovery.
>
> 💡 *For an architectural deep-dive and interview walkthrough, see [`CONCEPTUAL_GUIDE.md`](CONCEPTUAL_GUIDE.md) and [`docs/architecture.md`](docs/architecture.md).*

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

### The 4-Stage Operational Pipeline

1. **Tool Investigation:** The agent queries mock ERP endpoints via tools (`get_inventory`, `get_demand_forecast`, `get_open_pos`, `get_supplier_terms`, `get_alternate_suppliers`, `get_budget`, `get_storage_capacity`).
2. **Deterministic Arithmetic:** Net purchasing need, safety stock, and coverage days are computed exclusively by pure, unit-tested Python functions (`calculations.py`).
3. **Pre-Execution Validator:** Pure-code gate enforces 8 strict business rules (Budget, Storage, MOQ, Case-Pack, Supplier Max Qty, Stock-out Risk, Perishable Shelf-Life, Approval Threshold) before any write is committed.
4. **Post-Execution Verifier:** Automatically re-reads the PO from ERP, compares actual state against intent, and triggers recovery on mismatch.

---

## Quickstart

### Option A: Docker Compose (Recommended)

```bash
git clone <repo-url>
cd "AI Purchasing Agent — Assignment"

docker-compose up --build
```

- **Operations Dashboard:** [http://localhost:5173](http://localhost:5173)
- **API Documentation:** [http://localhost:8000/docs](http://localhost:8000/docs)
- **Health Check:** [http://localhost:8000/health](http://localhost:8000/health)

---

### Option B: Local Setup (Without Docker)

#### 1. Backend (Python 3.11+)
```bash
cd backend
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

#### 2. Frontend (Node 18+)
```bash
cd frontend
npm install
npm run dev
```

Visit [http://localhost:5173](http://localhost:5173).

---

## Evaluation Scenarios & Programmatic Rubric

The primary scenario is **Scenario 4 (Purchasing Constraint)**, built as a superset of recommendation review, supplier shortfall, and constraint adaptation:

| Scenario | Objective | Expected Outcome |
|---|---|---|
| **Scenario 1** | System recommendation verified with no constraints | `ACCEPT` |
| **Scenario 2** | Open PO arriving in 2 days already covers demand | `REJECT` |
| **Scenario 3** | Storage capacity exceeded; quantity capped safely | `MODIFY` |
| **Scenario 4** | Budget exhausted below supplier MOQ | `ESCALATE` |
| **Scenario 5** | Supplier shortfall (100/200); split PO to alternate | `MODIFY` |
| **Scenario 6** | Supplier shortfall covered by on-hand buffer | `REJECT` |
| **Scenario 7** | Supplier shortfall with no alternate available | `ESCALATE` |
| **Scenario 8** | Fault injection: case-pack round-up mismatch | `MODIFY` (Recovered) |
| **Scenario 9** | Missing demand forecast; agent refuses to guess | `INVESTIGATE` |
| **Scenario 10** | High-value order (> ₹50,000 threshold) | `MODIFY` (Human Approval) |
| **Scenario 11** | (Stretch) Sales velocity 2.5× forecast spike | `MODIFY` (Anomaly Detected) |

---

## Deterministic Mock Mode (Zero API Keys Needed)

By default, the system operates in **Mock Mode** (`LLM_PROVIDER=mock`). It includes realistic, pre-recorded tool-calling sequences for all 11 evaluation scenarios. You can run all tests, evaluations, and UI demonstrations **without needing any API key**.

To switch to a live Anthropic or OpenAI model:
1. Copy `.env.example` to `.env`
2. Set `LLM_PROVIDER=anthropic` and `ANTHROPIC_API_KEY=your_key`
3. Restart the server.

---

## Running Tests & Evaluations

```bash
cd backend

# Run the 129 Pytest test suite (DB models, calculations, validator, verifier, tools, feedback loop)
python -m pytest tests/ -v

# Run the programmatic evaluation harness (11 scenarios × 3 iterations)
python ../evals/run_evals.py --provider mock -n 3
```

**Results:**
- **Unit & Integration Suite:** `129 passed in 3.29s (100%)`
- **Evaluation Benchmark:** `33 / 33 passed (100.0% pass rate)` (See [`evals/report.md`](evals/report.md))

---

## License

MIT License. Designed and engineered for the Rappi India AI Purchasing Agent Take-Home Assignment.

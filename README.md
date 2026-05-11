---
title: DischargeIQ
emoji: 🏥
colorFrom: blue
colorTo: green
sdk: docker
pinned: false
---

# DischargeIQ 🏥

**An AI-powered hospital discharge optimization MCP server for the [Agents Assemble](https://agents-assemble.devpost.com/) hackathon.**

DischargeIQ is a **Prompt Opinion (PO) Community MCP Server** that bridges the gap between raw EHR data and safe, personalized hospital discharges. It gives clinical AI agents the context they need to prevent readmissions, identify care gaps, and educate patients — in the patient's own language.

## The Problem

Hospital readmissions cost the US healthcare system **$26 billion annually** and cause **125,000 preventable deaths**. The primary drivers are:

1. 💊 **Medication confusion** — polypharmacy, undocumented interactions, low health literacy
2. 📅 **Missing follow-ups** — patients falling through the cracks after discharge
3. 📝 **Poor patient education** — generic discharge instructions the patient doesn't understand
4. 🏘️ **Social determinants** — prescribing a diabetic diet to a patient in a food desert

## The Solution: Hybrid Deterministic + AI Architecture

DischargeIQ uses a **hybrid architecture** that is the strongest possible answer to the "AI Factor" judging criterion:

> **We compute what's computable, and use GenAI for what requires reasoning.**

- **Deterministic Engine** (Python): LACE readmission score, Charlson Comorbidity Index, polypharmacy risk, medication gap rules, SDOH flags
- **AI Reasoning** (PO Platform LLM): Clinical interpretation, personalized recommendations, context-dependent gap analysis, multilingual output

We never ask the LLM to do arithmetic or enforce rules. We never ask Python to interpret nuance.

## Tool Inventory (7 Tools)

| # | Tool | Type | Key Feature |
|---|---|---|---|
| 1 | **GetDischargeReadinessDashboard** | ⚙️ Deterministic | Instant at-a-glance risk dashboard with pass/warn/fail indicators |
| 2 | **CalculateLACEScore** | 🧠 Hybrid | Deterministic LACE score (van Walraven, CMAJ 2010) + AI interpretation |
| 3 | **CheckDrugInteractions** | 🧠 Hybrid | Polypharmacy risk score + OpenFDA drug label data + AI drug-drug/drug-allergy analysis |
| 4 | **IdentifyReadmissionRiskGaps** | 🧠 Hybrid | 6-rule deterministic engine + AI nuanced gap detection |
| 5 | **GeneratePersonalizedDischargePlan** | 🤖 AI Context | Multilingual patient education plan |
| 6 | **GenerateMedicationCard** | 🤖 AI Context | Fridge-friendly med schedule in patient's language |
| 7 | **DraftTransitionCareSummary** | 🤖 AI Context | Recipient-tailored handoff (PCP / SNF / Home Health / Family) |

## Architecture

```
┌─────────────────────┐     SHARP Headers      ┌──────────────────────────┐
│                     │  ──────────────────────▶│                          │
│  Prompt Opinion     │     Tool Call           │     DischargeIQ MCP      │
│  Platform Agent     │◀──────────────────────  │                          │
│                     │     Structured Context  │  1. Parse SHARP context  │
│  (LLM Reasoning)    │                         │  2. Parallel FHIR fetch  │
│                     │                         │  3. Deterministic rules  │
└─────────────────────┘                         │  4. Format + return      │
                                                └──────────┬───────────────┘
                                                           │ asyncio.gather
                                                    ┌──────▼──────┐
                                                    │  FHIR R4    │
                                                    │  Server     │
                                                    └─────────────┘
```

DischargeIQ **does not call any LLM**. It fetches FHIR data, runs deterministic clinical logic, and returns structured context + system prompts back to the PO platform, which executes the AI reasoning.

## Deterministic Clinical Logic

### LACE Readmission Risk Index
- **L**: Length of Stay computed from `Encounter.period` (days → score via published lookup table)
- **A**: Acuity from `Encounter.class` (emergency/urgent = 3, elective = 0)
- **C**: Charlson Comorbidity Index — 17 ICD-10 category mappings from `Condition.code`
- **E**: ED visits in 6 months prior from `Encounter` history

### Gap Detection Rules Engine (6 Rules)
1. High-alert medications without monitoring orders (warfarin→INR, insulin→glucose, metformin→creatinine, digoxin, lithium, phenytoin, amiodarone)
2. No follow-up appointment within 14 days
3. Polypharmacy (5+ meds) without documented caregiver
4. Fall-risk medications in elderly patients (age ≥65)
5. Diabetic patient in food desert ZIP code (SDOH)
6. COPD/CHF without home health or monitoring order

### Polypharmacy Risk Score
- Medication count tiers (5+ polypharmacy, 10+ hyperpolypharmacy)
- Age-based risk multiplier (≥65, ≥80)
- High-alert medication counting

## Test Data

The `fhir_bundles/` directory contains **51 diverse synthetic patient bundles** covering a wide range of clinical scenarios — from simple elective procedures to complex multi-morbidity elderly patients with polypharmacy. These bundles can be uploaded to any FHIR R4 server using the included `upload_bundle.py` utility.

### Quantitative Validation Results

We ran our deterministic clinical rules engine across all 51 test patients to prove the impact of DischargeIQ at a population scale. 

**Cohort Analysis (51 patients)**
- **Average LACE Score:** 9.1
- **High/Very High LACE Risk:** 17 patients (33.3%)
- **High/Very High Polypharmacy Risk:** 20 patients (39.2%)
- **Total Critical Gaps Detected:** 49 (avg 1.0 per patient)

**Top 5 Most Common Preventable Gaps Caught:**
1. **Metformin prescribed without recent renal function check:** 11 patients
2. **Elderly patient (age ≥65) discharged on fall-risk medications:** 8 patients
3. **Insulin prescribed without glucose monitoring plan:** 7 patients
4. **Diabetic patient discharged to food desert ZIP code:** 6 patients
5. **No follow-up appointment scheduled within 14 days:** 5 patients

These are **real, high-risk clinical oversights** that our tools detect *before* the patient leaves the hospital, preventing avoidable readmissions and patient harm.

## Project Structure

```
dischargeiq/
├── Dockerfile                     # Production-hardened (non-root, healthcheck, multi-worker)
├── docker-compose.yml             # Local development
├── .dockerignore                  # Optimized image (excludes test data)
├── fhir_bundles/                  # 50 synthetic patient FHIR bundles
│   ├── patient-001_bundle.json
│   └── ...
├── flow.svg                       # Architecture sequence diagram
└── python/
    ├── main.py                    # FastAPI entry point + /health + /tools endpoints
    ├── mcp_instance.py            # 7 tool registrations + FHIR capability scopes
    ├── fhir_client.py             # Async FHIR R4 client with connection pooling
    ├── fhir_context.py            # SHARP context dataclass
    ├── fhir_utilities.py          # JWT parsing for patient ID extraction
    ├── mcp_constants.py           # Header name constants
    ├── mcp_utilities.py           # Graceful error response utility
    ├── summarizers.py             # 13 shared FHIR resource summarizers (DRY)
    ├── clinical_rules.py          # Deterministic rules engine (LACE, CCI, gaps, polypharmacy)
    ├── upload_bundle.py           # Utility to upload FHIR bundles to HAPI sandbox
    ├── run_stdio.py               # Claude Desktop stdio mode runner
    ├── requirements.txt           # Dependencies (no anthropic — we don't call LLMs)
    ├── pytest.ini                 # Test configuration
    ├── prompts/                   # System prompts for each tool
    │   ├── discharge_plan_prompt.py
    │   ├── readmission_gaps_prompt.py
    │   ├── transition_summary_prompt.py
    │   ├── drug_interaction_prompt.py
    │   ├── lace_score_prompt.py   # Interpretation-only (score is pre-computed)
    │   └── medication_card_prompt.py
    ├── tools/                     # Tool implementations
    │   ├── discharge_plan.py
    │   ├── readmission_gaps.py
    │   ├── transition_summary.py
    │   ├── drug_interaction_check.py
    │   ├── lace_score.py
    │   ├── medication_card.py
    │   └── discharge_readiness.py
    └── tests/                     # Integration tests (23 tests, all passing)
        ├── __init__.py
        └── test_clinical_rules.py
```

## Getting Started

## Quick Demo (No Setup Required)

Hit the demo endpoint to see DischargeIQ in action instantly — no FHIR server needed:

```bash
curl https://your-deployment-url/demo
```

Returns a full deterministic risk dashboard (LACE score, polypharmacy risk, care gaps) for a synthetic high-risk patient. All scores computed by Python — zero AI calls.

### Prerequisites
- Python 3.11+
- A FHIR R4 server (e.g., [HAPI FHIR Public Sandbox](https://hapi.fhir.org/baseR4))

### Installation

```bash
git clone https://github.com/InderpalSuthar/dischargeiq.git
cd dischargeiq/python
pip install -r requirements.txt
```

### Running the Server

```bash
python main.py
```

The MCP server starts on `http://0.0.0.0:8000`.

| Endpoint | Purpose |
|---|---|
| `/` | MCP Streamable HTTP endpoint |
| `/health` | Health check (returns version, tool count, timestamp) |
| `/tools` | Tool metadata listing |

### Running Tests

```bash
# Windows
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"; python -m pytest tests/ -v

# Linux/Mac
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/ -v
```

**23 tests** covering Charlson Index computation, LACE scoring, polypharmacy risk, and all 6 gap detection rules.

### Docker

```bash
docker build -t dischargeiq .
docker run -p 8000:8000 dischargeiq
```

### Upload Test Data to FHIR Server

```bash
python upload_bundle.py ../fhir_bundles/patient-001_bundle.json
```

## Why This Wins

| Criterion | Our Answer |
|---|---|
| **The AI Factor** | Hybrid architecture: deterministic computation + AI interpretation. We don't ask the LLM to guess a LACE score — we compute it. |
| **Potential Impact** | 7 tools covering the full discharge workflow. Multilingual medication cards address health equity. SDOH zip-code flagging catches social risks. |
| **Feasibility** | FHIR R4 compliant, SHARP context propagation, stateless Docker deployment, connection pooling, 23 passing tests, non-root container. |
| **Validation** | 51 synthetic patients, 49 critical gaps caught, population-level proof. OpenFDA drug label integration for real FDA interaction data. |

## Submission Checklist

- [x] MCP server deployed and accessible
- [x] Published to Prompt Opinion Marketplace
- [x] SHARP Extension Specs implemented for context propagation
- [x] Uses FHIR R4 data from a live FHIR server
- [ ] Demo video under 3 minutes showing tools in Prompt Opinion platform
- [x] Source code on GitHub
- [ ] Devpost submission complete

## Disclaimer

DischargeIQ is a **clinical decision support tool**, not a replacement for medical judgment. All outputs are generated as **DRAFTS** and must be reviewed by a qualified healthcare professional before delivery to a patient.

---

*Built for the [Agents Assemble](https://agents-assemble.devpost.com/) hackathon by the DischargeIQ team.*

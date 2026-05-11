---
name: discharge-iq
description: >
  AI-powered hospital discharge optimization via FHIR R4 MCP server. Computes LACE readmission risk,
  Charlson Comorbidity Index, polypharmacy scores, and SDOH flags deterministically. Detects care gaps
  (missing labs, follow-ups, fall risk, food deserts). Checks drug interactions against real OpenFDA
  drug label data. Generates multilingual discharge plans,
  medication cards, and transition care summaries. Use when a patient is approaching discharge,
  auditing encounter data for safety gaps, or building clinical decision-support workflows.
license: Apache-2.0
compatibility: >
  Requires Python 3.11+, FHIR R4 server access, and an MCP-compatible platform (e.g., Prompt Opinion).
  Docker recommended for production. Tested with 51 synthetic patient bundles.
metadata:
  author: DischargeIQ-Team
  version: "1.0.0"
  clinical-standard: FHIR-R4
  logic-type: hybrid-deterministic-ai
  hackathon: agents-assemble
allowed-tools: Bash Read Glob Grep
---

# DischargeIQ

Hospital readmissions cost the US healthcare system **$26 billion annually** and cause **125,000 preventable deaths**. DischargeIQ bridges the gap between raw EHR data and safe, personalized hospital discharges.

## Architecture: "Compute What's Computable"

DischargeIQ uses a **hybrid deterministic + AI** design:

- **Deterministic engine** (Python): Clinical math that must be accurate — LACE scores, Charlson Index, polypharmacy risk, gap detection, SDOH flagging.
- **AI reasoning** (LLM via MCP): Nuanced interpretation, patient education, multilingual content generation.

**Rule:** Never ask the LLM to compute scores. Never ask Python to interpret clinical nuance. Each does what it's best at.

Every output section is tagged with confidence markers: `[DETERMINISTIC]` (Python rules engine), `[AI-INTERPRETED]` (LLM reasoning), or `[FDA-SOURCED]` (OpenFDA drug label data). Clinicians always know the provenance of each data point.

## Seven Tools

| # | Tool | Type | Purpose |
|---|------|------|---------|
| 1 | `GetDischargeReadinessDashboard` | Deterministic | Instant at-a-glance risk dashboard (LACE, polypharmacy, gaps, follow-ups) |
| 2 | `CalculateLACEScore` | Hybrid | Van Walraven readmission risk index + AI interpretation |
| 3 | `CheckDrugInteractions` | Hybrid | Polypharmacy risk scoring + OpenFDA drug label data + AI drug-drug/drug-allergy analysis |
| 4 | `IdentifyReadmissionRiskGaps` | Hybrid | 6-rule deterministic engine + AI nuanced gap detection |
| 5 | `GeneratePersonalizedDischargePlan` | AI Context | Multilingual plain-language patient education |
| 6 | `GenerateMedicationCard` | AI Context | Fridge-friendly medication schedule in patient's language |
| 7 | `DraftTransitionCareSummary` | AI Context | Recipient-tailored handoff (PCP / SNF / home health / family) |

## Step-by-Step Instructions

### Step 1 — Start the server

Ensure the MCP server is running:

```bash
cd python && pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or via Docker:

```bash
docker compose up
```

Health check: `GET /health` returns version, tool count, and timestamp.

### Step 2 — Load patient data

Upload FHIR R4 bundles to your FHIR server:

```bash
cd python && python upload_bundle.py ../fhir_bundles/patient-001_bundle.json
```

51 synthetic patient bundles are available in `fhir_bundles/`. Each contains: Patient, Encounter, Condition, MedicationRequest, Observation, ServiceRequest, Appointment, CarePlan, RelatedPerson, AllergyIntolerance.

### Step 3 — Always start with the Dashboard

Run `GetDischargeReadinessDashboard` first. It returns a deterministic overview:

- LACE score and risk tier (low/moderate/high/very-high)
- Polypharmacy risk level
- Critical care gaps (missing labs, follow-ups, SDOH flags)
- Scheduled appointments

**Do not skip this step.** It determines which subsequent tools to call.

### Step 4 — Follow the risk signals

Based on dashboard results:

| Signal | Action |
|--------|--------|
| LACE >= 10 (high risk) | Run `CalculateLACEScore` for detailed breakdown and AI interpretation |
| 5+ medications | Run `CheckDrugInteractions` for polypharmacy analysis |
| Any critical gaps | Run `IdentifyReadmissionRiskGaps` for full gap analysis with remediation steps |
| Patient language != English | Ensure `GeneratePersonalizedDischargePlan` and `GenerateMedicationCard` output in patient's preferred language |

### Step 5 — Generate patient-facing materials

For discharge:
1. `GeneratePersonalizedDischargePlan` — plain-language summary of stay and warning signs
2. `GenerateMedicationCard` — time-of-day organized schedule (designed for the fridge door)
3. `DraftTransitionCareSummary` — tailored to recipient (PCP, SNF, home health, family)

## Clinical Logic Reference

### LACE Readmission Risk Index

Computed from FHIR resources per van Walraven et al., CMAJ 2010:

- **L** — Length of Stay (`Encounter.period`, mapped via lookup table)
- **A** — Acuity (`Encounter.class`: emergency/urgent = 3, elective = 0)
- **C** — Charlson Comorbidity Index (17 ICD-10 categories from `Condition.code`)
- **E** — ED visits in prior 6 months (`Encounter` history)
- **Score range:** 0–19. Tiers: low (0–4), moderate (5–9), high (10–14), very high (15+)

### Gap Detection Rules (6 rules)

1. High-alert meds without monitoring (warfarin->INR, insulin->glucose, metformin->creatinine, digoxin, lithium, phenytoin, amiodarone)
2. No follow-up appointment within 14 days
3. Polypharmacy (5+ meds) without documented caregiver
4. Fall-risk medications in elderly patients (age >= 65)
5. Diabetic patient in food desert ZIP code (SDOH)
6. COPD/CHF without home health or monitoring order

### Polypharmacy Risk Score

- 5+ meds = polypharmacy, 10+ = hyperpolypharmacy
- Age multiplier (>=65, >=80)
- High-alert medication counting

## Examples

### High-Risk Elderly Patient

**Input:** Patient age 82, 12 medications, ZIP code 02119 (food desert).

**Workflow:**
1. `GetDischargeReadinessDashboard` — shows LACE 14 (high), hyperpolypharmacy, 3 critical gaps
2. `CalculateLACEScore` — breakdown: L=5, A=3, C=4, E=2
3. `CheckDrugInteractions` — flags warfarin + aspirin interaction, missing INR monitoring
4. `IdentifyReadmissionRiskGaps` — food desert flag, fall risk (age + sedatives), no follow-up
5. `GenerateMedicationCard` — 12-med schedule in patient's language
6. `DraftTransitionCareSummary` — targeted to home health agency

### Simple Elective Procedure

**Input:** Patient age 35, knee arthroscopy, 2 medications, suburban ZIP.

**Workflow:**
1. `GetDischargeReadinessDashboard` — LACE 2 (low), no gaps
2. `GeneratePersonalizedDischargePlan` — standard post-op instructions
3. Skip detailed risk tools — low-risk patients don't need full workup

## Edge Cases

- **Missing FHIR resources:** Tools handle missing resources gracefully. A patient without Conditions will get CCI = 0, not an error.
- **No encounter found:** Dashboard returns an error message — do not proceed with other tools.
- **Language detection:** If `Patient.communication` is absent, default to English.
- **Empty medication list:** Polypharmacy score = 0, drug interaction check returns "no medications to analyze."
- **FHIR server unreachable:** Tools return structured error responses. Do not retry indefinitely.
- **OpenFDA unavailable:** Drug interaction check falls back to AI-only analysis with a warning. Deterministic polypharmacy scoring still works.

## Tech Stack

| Layer | Technology | Purpose |
|-------|-----------|---------|
| **Language** | Python 3.11+ | Core application language |
| **Framework** | FastAPI >= 0.115 | Async web framework for MCP server |
| **Server** | Uvicorn >= 0.32 | ASGI server (2-worker production config, graceful shutdown) |
| **Protocol** | MCP >= 1.9 (Model Context Protocol) | Tool registration and agent integration via FastMCP |
| **Data Standard** | FHIR R4 (HL7) | Healthcare interoperability — Patient, Encounter, Condition, MedicationRequest, Observation, etc. |
| **HTTP Client** | httpx >= 0.28 | Async FHIR server requests with connection pooling |
| **Validation** | Pydantic >= 2.0 | Request/response data validation |
| **Auth** | PyJWT >= 2.10 | SHARP context header token parsing |
| **Testing** | pytest >= 8.0 + pytest-asyncio >= 0.23 | 23 integration tests for clinical rules |
| **Containerization** | Docker (python:3.11-slim) | Non-root user, health checks, multi-worker |
| **Orchestration** | Docker Compose | Local development environment |
| **Drug Data** | OpenFDA API | Real FDA drug label data for interaction checking (no API key required) |
| **Deployment** | Hugging Face Spaces / Railway | Production hosting (port 7860, $PORT env support) |
| **AI Platform** | Prompt Opinion (PO) | LLM reasoning layer via SHARP extension headers |

## Project Structure

```
DischargeIQ/
  python/
    main.py              # FastAPI + FastMCP server entry point
    mcp_instance.py      # Tool registrations + SHARP capability scopes
    clinical_rules.py    # Deterministic engine (LACE, CCI, gaps, polypharmacy)
    fhir_client.py       # Async FHIR client with connection pooling
    summarizers.py       # 13 shared FHIR resource summarizers
    prompts/             # System prompts for each tool
    tools/               # Tool implementations (one file per tool)
    tests/               # 23 integration tests
  fhir_bundles/          # 51 synthetic patient bundles
  Dockerfile             # Production container (non-root, multi-worker)
  docker-compose.yml     # Local development
```

## PO Agent Configuration

Ready-to-paste prompts for the Prompt Opinion "Configure Agent" screen:

- [System Prompt](references/system-prompt.md) — Core agent behavior, workflow order, critical rules
- [Consult Prompt](references/consult-prompt.md) — How to respond when consulted by another agent
- [Response Format](references/response-format.json) — JSON schema for structured LLM output (paste directly into PO)

## Reference Files

- [Clinical Rules Logic](../python/clinical_rules.py) — All deterministic scoring algorithms
- [MCP Tool Registrations](../python/mcp_instance.py) — Tool definitions and SHARP scopes
- [System Prompts](../python/prompts/) — LLM prompt templates for each tool
- [FHIR Client](../python/fhir_client.py) — Async FHIR R4 data fetching
- [Summarizers](../python/summarizers.py) — 13 shared FHIR resource parsers
- [Test Suite](../python/tests/test_clinical_rules.py) — Clinical rules validation

# DischargeIQ 🏥 — Grand Prize Contender for Agents Assemble

DischargeIQ is a **Prompt Opinion (PO) Community MCP Server** that bridges the gap between raw EHR data and safe, personalized hospital discharges. It gives clinical AI agents the context they need to prevent readmissions, identify care gaps, and educate patients.

Built specifically for the **Agents Assemble** hackathon, DischargeIQ uses a **hybrid architecture** that combines deterministic clinical rule engines with the reasoning capabilities of the PO platform's LLM.

## The Problem: The 30-Day Readmission Crisis
Hospital readmissions cost the US healthcare system **$26 billion annually**. The primary drivers are:
1. **Medication confusion** (polypharmacy, undocumented interactions).
2. **Missing follow-ups** (patients falling through the cracks).
3. **Poor patient education** (generic discharge instructions the patient doesn't understand).
4. **Social Determinants of Health (SDOH)** (e.g., prescribing a diabetic diet to a patient in a food desert).

## The Solution: DischargeIQ
DischargeIQ provides **7 clinical tools** to clinical agents, seamlessly pulling data from any FHIR R4 server using **SHARP** (Secure Health Access & Routing Protocol) for zero-integration credential passing.

### Why This Wins (Judging Criteria)

1. **The AI Factor:** *We compute what's computable, and we use GenAI for what requires reasoning.* We don't ask the LLM to guess a LACE score; we compute it deterministically from FHIR data and ask the LLM to *interpret* it. We use a rules engine for high-alert medication gaps, and AI for nuanced context.
2. **Potential Impact:** DischargeIQ addresses all major drivers of the $26B readmission crisis. The multilingual Medication Card alone addresses a massive health equity gap.
3. **Feasibility:** Fully standard-compliant. Uses only standard FHIR R4 resources. Stateless, Docker-deployable, and horizontally scalable. Uses the "decision-support" framing to avoid FDA medical device regulation.

## Tool Inventory (7 Tools)

DischargeIQ provides the following tools to the Prompt Opinion platform:

| Tool | Type | Description |
|---|---|---|
| **GetDischargeReadinessDashboard** | ⚙️ Deterministic | Instant, fully deterministic dashboard aggregating LACE score, polypharmacy risk, and clinical gaps with pass/warn/fail indicators. Run this first. |
| **CalculateLACEScore** | 🧠 Hybrid | Computes the evidence-based LACE readmission risk index deterministically (LOS, Acuity, Charlson Comorbidities, ED visits), then uses AI to interpret the score. |
| **CheckDrugInteractions** | 🧠 Hybrid | Computes a deterministic polypharmacy risk score, then uses AI to identify dangerous drug-drug and drug-condition interactions. |
| **IdentifyReadmissionRiskGaps** | 🧠 Hybrid | Two-layer gap analysis: (1) Deterministic rules engine finds evidence-based gaps (e.g., warfarin without INR), (2) AI finds nuanced contextual gaps. |
| **GeneratePersonalizedDischargePlan** | 🤖 AI Context | Generates a patient-friendly education plan. Supports multilingual routing based on `Patient.communication`. |
| **GenerateMedicationCard** | 🤖 AI Context | Creates a fridge-friendly, time-of-day medication schedule in the patient's primary language. |
| **DraftTransitionCareSummary** | 🤖 AI Context | Generates structured, recipient-tailored handoff documents (Primary Care vs. SNF vs. Family Caregiver). |

## Architecture

DischargeIQ implements the **Context Enrichment Pattern**. It does not call an LLM directly. Instead, it processes FHIR data and returns highly structured context and instructions back to the Prompt Opinion platform, which executes the AI reasoning.

1. **Platform Request:** PO platform agent calls a tool.
2. **Identity Propagation:** DischargeIQ parses the `X-SHARP-Token` to identify the patient and EHR context.
3. **Parallel Fetching:** DischargeIQ executes concurrent `asyncio.gather` calls against the FHIR server.
4. **Deterministic Engine:** Clinical rules are run locally (CCI mapping, LACE computation, SDOH flaggers).
5. **Context Return:** DischargeIQ returns the computed data, formatted summaries, and strict system prompts back to the PO platform.

## Getting Started

### Prerequisites
- Python 3.11+
- A FHIR R4 Server (e.g., HAPI FHIR Public Sandbox)
- Prompt Opinion Developer Account

### Installation

```bash
git clone https://github.com/InderpalSuthar/dischargeiq.git
cd dischargeiq/python

# Create virtual environment
python -m venv venv
source venv/bin/activate  # Or venv\Scripts\activate on Windows

# Install dependencies
pip install -r requirements.txt
```

### Running the Server

```bash
python main.py
```

The MCP server will start on `http://0.0.0.0:8000` with Server-Sent Events (SSE) enabled.

Endpoints:
- `/`: SSE endpoint for MCP connection.
- `/health`: Health check endpoint.
- `/tools`: Tool metadata.

### Running Integration Tests

Our deterministic clinical rules engine is fully tested:

```bash
export PYTEST_DISABLE_PLUGIN_AUTOLOAD="1"
pytest tests/test_clinical_rules.py -v
```

## Disclaimer
DischargeIQ is designed as a **clinical decision support tool**, not a replacement for medical judgment. All outputs are generated as **DRAFTS** and must be reviewed by a qualified healthcare professional before being added to a patient's chart or handed to a patient.

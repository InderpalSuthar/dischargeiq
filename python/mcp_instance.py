from mcp.server.fastmcp import FastMCP
from tools.discharge_plan import generate_personalized_discharge_plan
from tools.readmission_gaps import identify_readmission_risk_gaps
from tools.transition_summary import draft_transition_care_summary

mcp = FastMCP("DischargeIQ", stateless_http=True, host="0.0.0.0")

_original_get_capabilities = mcp._mcp_server.get_capabilities


def _patched_get_capabilities(notification_options, experimental_capabilities):
    caps = _original_get_capabilities(notification_options, experimental_capabilities)
    caps.model_extra["extensions"] = {
        "ai.promptopinion/fhir-context": {
            "scopes": [
                {"name": "patient/Patient.rs", "required": True},
                {"name": "patient/Condition.rs", "required": True},
                {"name": "patient/MedicationRequest.rs", "required": True},
                {"name": "patient/Encounter.rs", "required": True},
                {"name": "patient/Procedure.rs"},
                {"name": "patient/Observation.rs"},
                {"name": "patient/AllergyIntolerance.rs"},
                {"name": "patient/ServiceRequest.rs"},
                {"name": "patient/Appointment.rs"},
                {"name": "patient/RelatedPerson.rs"},
                {"name": "patient/CarePlan.rs"},
                {"name": "patient/CareTeam.rs"},
                {"name": "patient/DiagnosticReport.rs"},
            ]
        }
    }
    return caps


mcp._mcp_server.get_capabilities = _patched_get_capabilities


mcp.tool(
    name="GeneratePersonalizedDischargePlan",
    description=(
        "Generates a personalized discharge education plan tailored to this specific patient's "
        "diagnosis, medications, clinical history, and life situation. Reads the patient's FHIR "
        "context and produces a human-readable explanation of what happened, what the patient must "
        "do, and why. Supports multilingual output — if the patient's primary language (from "
        "Patient.communication.language) is not English, the plan is generated in their language. "
        "Output is a DRAFT for clinician review."
    ),
)(generate_personalized_discharge_plan)

mcp.tool(
    name="IdentifyReadmissionRiskGaps",
    description=(
        "Analyzes the patient's full FHIR context at discharge using a two-layer approach: "
        "(1) A deterministic rules engine identifies concrete, evidence-based gaps (missing INR "
        "monitoring for warfarin, no follow-up within 14 days, polypharmacy without caregiver, "
        "SDOH food desert flags, fall-risk meds in elderly patients), then (2) AI analysis adds "
        "nuanced, context-dependent gaps. Returns a combined list with severity, evidence, "
        "clinical risk, and specific action for each gap."
    ),
)(identify_readmission_risk_gaps)

mcp.tool(
    name="DraftTransitionCareSummary",
    description=(
        "Generates a structured, recipient-tailored Transition of Care document. The same patient "
        "produces different documents depending on recipient: primary_care (what changed), "
        "home_health (actionable nursing brief), skilled_nursing_facility (full clinical handoff), "
        "or family_caregiver (plain language guide). CMS-required document, generated in seconds."
    ),
)(draft_transition_care_summary)

# ── New tools ──────────────────────────────────────────────────────────────────

from tools.drug_interaction_check import check_drug_interactions  # noqa: E402

mcp.tool(
    name="CheckDrugInteractions",
    description=(
        "Analyzes ALL discharge medications for dangerous drug-drug interactions, drug-allergy "
        "conflicts, and drug-condition contraindications. Deterministically computes a polypharmacy "
        "risk score (medication count, high-alert meds, patient age) BEFORE AI analysis. Catches "
        "combinations like warfarin + NSAIDs (bleeding risk), ACE inhibitors + ARBs + NSAIDs "
        "(triple whammy nephrotoxicity), SSRIs + tramadol (serotonin syndrome), and prescribing "
        "cephalosporins to penicillin-allergic patients. Output for pharmacist/clinician review."
    ),
)(check_drug_interactions)

from tools.lace_score import calculate_lace_readmission_score  # noqa: E402

mcp.tool(
    name="CalculateLACEScore",
    description=(
        "Deterministically computes the LACE readmission risk index (van Walraven et al., CMAJ 2010) "
        "directly from the patient's FHIR data — no AI estimation. "
        "L = Length of Stay (from Encounter.period), "
        "A = Acuity (from Encounter.class), "
        "C = Comorbidity (Charlson Comorbidity Index, 17 ICD-10 categories mapped from Condition), "
        "E = ED Visits in prior 6 months (counted from Encounter history). "
        "Score 0-19. AI then provides personalized clinical interpretation and recommendations "
        "based on the computed score. The gold standard for discharge risk stratification."
    ),
)(calculate_lace_readmission_score)

from tools.medication_card import generate_medication_card  # noqa: E402

mcp.tool(
    name="GenerateMedicationCard",
    description=(
        "Generates a simple, fridge-friendly medication schedule that any patient or caregiver "
        "can follow regardless of health literacy. Organizes medications by time of day "
        "(morning, afternoon, evening, bedtime) using plain language. Includes specific warnings "
        "for this patient's medication combination. Multilingual — if patient's primary language "
        "is not English (from FHIR Patient.communication), generates in their language. "
        "Designed to be printed and posted at home to prevent medication errors post-discharge."
    ),
)(generate_medication_card)

from tools.discharge_readiness import get_discharge_readiness_dashboard  # noqa: E402

mcp.tool(
    name="GetDischargeReadinessDashboard",
    description=(
        "Returns an instant, fully deterministic discharge readiness dashboard — no AI generation. "
        "Aggregates all risk signals into a single at-a-glance summary: LACE readmission risk score, "
        "polypharmacy risk level, number and severity of clinical gaps, follow-up appointment status, "
        "caregiver documentation status, and care plan status. Run this FIRST for any patient to "
        "determine which other DischargeIQ tools to invoke. Fast (<2s), reliable, reproducible."
    ),
)(get_discharge_readiness_dashboard)

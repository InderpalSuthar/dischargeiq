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
        "do, and why. Output is a DRAFT for clinician review."
    ),
)(generate_personalized_discharge_plan)

mcp.tool(
    name="IdentifyReadmissionRiskGaps",
    description=(
        "Analyzes the patient's full FHIR context at discharge and identifies specific, named, "
        "actionable gaps that will cause readmission if not addressed. Not a risk score — a list "
        "of concrete issues (missing follow-ups, unmonitored high-risk meds, caregiver capacity "
        "concerns) with specific actions to resolve each gap before discharge."
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

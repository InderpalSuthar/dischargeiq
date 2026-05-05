import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.transition_summary_prompt import TRANSITION_SUMMARY_SYSTEM_PROMPT


VALID_RECIPIENT_TYPES = ("primary_care", "home_health", "skilled_nursing_facility", "family_caregiver")


async def draft_transition_care_summary(
    recipient_type: Annotated[
        str,
        Field(description="Who is receiving this patient next. One of: primary_care, home_health, skilled_nursing_facility, family_caregiver"),
    ],
    patient_id: Annotated[
        str | None,
        Field(description="The ID of the patient. Optional if patient context already exists via SHARP headers."),
    ] = None,
    encounter_id: Annotated[
        str | None,
        Field(description="The encounter ID for the current hospitalization."),
    ] = None,
    ctx: Context = None,
) -> str:
    """Generates a structured, recipient-tailored Transition of Care (ToC) document.
    The same patient data produces different documents based on recipient type.
    Supported recipients: primary_care, home_health, skilled_nursing_facility, family_caregiver.
    Output is a DRAFT for clinician review before transmission."""

    if recipient_type not in VALID_RECIPIENT_TYPES:
        return create_text_response(
            f"Invalid recipient_type '{recipient_type}'. Must be one of: {', '.join(VALID_RECIPIENT_TYPES)}",
            is_error=True,
        )

    if not patient_id:
        patient_id = get_patient_id_if_context_exists(ctx)
        if not patient_id:
            return create_text_response("No patient context found. Please provide a patient_id or ensure SHARP context headers are present.", is_error=True)

    fhir_context = get_fhir_context(ctx)
    if not fhir_context:
        return create_text_response("FHIR context could not be retrieved. Ensure X-FHIR-Server-URL header is present.", is_error=True)

    fhir_client = FhirClient(base_url=fhir_context.url, token=fhir_context.token)

    patient = await fhir_client.get_patient(patient_id)
    if not patient:
        return create_text_response(f"Patient {patient_id} not found on FHIR server.", is_error=True)

    conditions = await fhir_client.get_conditions(patient_id)
    medications = await fhir_client.get_medications(patient_id, encounter_id)
    procedures = await fhir_client.get_procedures(patient_id, encounter_id)
    observations = await fhir_client.get_observations(patient_id, "laboratory")
    service_requests = await fhir_client.get_service_requests(patient_id)
    care_plans = await fhir_client.get_care_plans(patient_id)
    care_teams = await fhir_client.get_care_teams(patient_id)
    diagnostic_reports = await fhir_client.get_diagnostic_reports(patient_id)
    allergies = await fhir_client.get_allergies(patient_id)

    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)

    clinical_context = {
        "recipient_type": recipient_type,
        "patient": _summarize_patient(patient),
        "encounter": _summarize_encounter(encounter) if encounter else "No encounter specified",
        "active_conditions": [_summarize_condition(c) for c in conditions],
        "discharge_medications": [_summarize_medication(m) for m in medications],
        "procedures_this_visit": [_summarize_procedure(p) for p in procedures],
        "recent_labs": [_summarize_observation(o) for o in observations[:15]],
        "pending_orders": [_summarize_service_request(sr) for sr in service_requests],
        "active_care_plans": [_summarize_care_plan(cp) for cp in care_plans],
        "care_team": [_summarize_care_team(ct) for ct in care_teams],
        "diagnostic_reports": [_summarize_diagnostic_report(dr) for dr in diagnostic_reports],
        "allergies": [_summarize_allergy(a) for a in allergies],
    }

    output = f"""## TRANSITION OF CARE SUMMARY CONTEXT

### Recipient Type: {recipient_type}

### Instructions for Generating the Summary
{TRANSITION_SUMMARY_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please generate a transition of care summary tailored for the **{recipient_type}** recipient using the above clinical data and instructions."""

    return output


def _summarize_patient(patient: dict) -> dict:
    name_parts = patient.get("name", [{}])[0]
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    return {
        "name": f"{given} {family}".strip(),
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
        "language": _get_language(patient),
    }


def _get_language(patient: dict) -> str:
    comms = patient.get("communication", [])
    if comms:
        lang = comms[0].get("language", {})
        return lang.get("text") or (lang.get("coding", [{}])[0].get("display", "English"))
    return "English"


def _summarize_encounter(encounter: dict) -> dict:
    return {
        "status": encounter.get("status"),
        "class": encounter.get("class", {}).get("display", encounter.get("class", {}).get("code")),
        "period": encounter.get("period"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
        "discharge_disposition": encounter.get("hospitalization", {}).get("dischargeDisposition", {}).get("text"),
    }


def _summarize_condition(condition: dict) -> dict:
    code = condition.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "clinical_status": condition.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
        "onset": condition.get("onsetDateTime"),
    }


def _summarize_medication(med: dict) -> dict:
    med_code = med.get("medicationCodeableConcept", {})
    dosage = med.get("dosageInstruction", [{}])[0] if med.get("dosageInstruction") else {}
    return {
        "name": med_code.get("text") or (med_code.get("coding", [{}])[0].get("display", "Unknown")),
        "dosage_text": dosage.get("text", ""),
        "status": med.get("status"),
        "reason": [ref.get("display", "") for ref in med.get("reasonReference", [])],
    }


def _summarize_procedure(proc: dict) -> dict:
    code = proc.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "date": proc.get("performedDateTime") or proc.get("performedPeriod", {}).get("start"),
        "status": proc.get("status"),
    }


def _summarize_observation(obs: dict) -> dict:
    code = obs.get("code", {})
    value = obs.get("valueQuantity", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "value": f"{value.get('value', '')} {value.get('unit', '')}".strip() if value else obs.get("valueString", ""),
        "date": obs.get("effectiveDateTime", ""),
        "interpretation": [i.get("text", "") for i in obs.get("interpretation", [])],
    }


def _summarize_service_request(sr: dict) -> dict:
    code = sr.get("code", {})
    return {
        "description": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "status": sr.get("status"),
        "intent": sr.get("intent"),
        "priority": sr.get("priority"),
    }


def _summarize_care_plan(cp: dict) -> dict:
    return {
        "title": cp.get("title", ""),
        "status": cp.get("status"),
        "description": cp.get("description", ""),
    }


def _summarize_care_team(ct: dict) -> dict:
    participants = []
    for p in ct.get("participant", []):
        role = p.get("role", [{}])[0].get("text", "") if p.get("role") else ""
        member = p.get("member", {}).get("display", "")
        if member:
            participants.append({"role": role, "name": member})
    return {
        "name": ct.get("name", ""),
        "participants": participants,
    }


def _summarize_diagnostic_report(dr: dict) -> dict:
    code = dr.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "status": dr.get("status"),
        "date": dr.get("effectiveDateTime", ""),
        "conclusion": dr.get("conclusion", ""),
    }


def _summarize_allergy(allergy: dict) -> dict:
    code = allergy.get("code", {})
    return {
        "substance": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "criticality": allergy.get("criticality"),
    }

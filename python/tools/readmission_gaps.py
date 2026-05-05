import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.readmission_gaps_prompt import READMISSION_GAPS_SYSTEM_PROMPT


async def identify_readmission_risk_gaps(
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
    """Analyzes the patient's full FHIR context at the moment of discharge and identifies
    specific, named, actionable gaps that will cause a readmission if not addressed before
    the patient leaves. Not a risk score — a list of specific things that are broken in
    this patient's discharge plan right now. Output is for clinical team review."""

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
    service_requests = await fhir_client.get_service_requests(patient_id)
    appointments = await fhir_client.get_appointments(patient_id)
    related_persons = await fhir_client.get_related_persons(patient_id)
    observations = await fhir_client.get_observations(patient_id, "laboratory")
    care_plans = await fhir_client.get_care_plans(patient_id)

    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)

    clinical_context = {
        "patient": _summarize_patient(patient),
        "encounter": _summarize_encounter(encounter) if encounter else "No encounter specified",
        "active_conditions": [_summarize_condition(c) for c in conditions],
        "discharge_medications": [_summarize_medication(m) for m in medications],
        "active_service_requests": [_summarize_service_request(sr) for sr in service_requests],
        "scheduled_appointments": [_summarize_appointment(a) for a in appointments],
        "caregivers": [_summarize_related_person(rp) for rp in related_persons],
        "recent_labs": [_summarize_observation(o) for o in observations[:15]],
        "active_care_plans": [_summarize_care_plan(cp) for cp in care_plans],
    }

    output = f"""## READMISSION RISK GAP ANALYSIS CONTEXT

### Instructions for Identifying Gaps
{READMISSION_GAPS_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please analyze the above clinical data and identify all specific, actionable readmission risk gaps for this patient."""

    return output


def _summarize_patient(patient: dict) -> dict:
    name_parts = patient.get("name", [{}])[0]
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    addresses = patient.get("address", [])
    postal_code = addresses[0].get("postalCode", "") if addresses else ""
    return {
        "name": f"{given} {family}".strip(),
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
        "postal_code": postal_code,
    }


def _summarize_encounter(encounter: dict) -> dict:
    return {
        "status": encounter.get("status"),
        "period": encounter.get("period"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
    }


def _summarize_condition(condition: dict) -> dict:
    code = condition.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "clinical_status": condition.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
    }


def _summarize_medication(med: dict) -> dict:
    med_code = med.get("medicationCodeableConcept", {})
    dosage = med.get("dosageInstruction", [{}])[0] if med.get("dosageInstruction") else {}
    return {
        "name": med_code.get("text") or (med_code.get("coding", [{}])[0].get("display", "Unknown")),
        "dosage_text": dosage.get("text", ""),
        "status": med.get("status"),
    }


def _summarize_service_request(sr: dict) -> dict:
    code = sr.get("code", {})
    return {
        "description": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "status": sr.get("status"),
        "intent": sr.get("intent"),
    }


def _summarize_appointment(appt: dict) -> dict:
    return {
        "status": appt.get("status"),
        "start": appt.get("start"),
        "description": appt.get("description", ""),
        "type": [t.get("text") for t in appt.get("appointmentType", {}).get("coding", [])],
    }


def _summarize_related_person(rp: dict) -> dict:
    name_parts = rp.get("name", [{}])[0]
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    relationship = rp.get("relationship", [{}])[0].get("coding", [{}])[0].get("display", "Unknown")
    return {
        "name": f"{given} {family}".strip(),
        "relationship": relationship,
    }


def _summarize_observation(obs: dict) -> dict:
    code = obs.get("code", {})
    value = obs.get("valueQuantity", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "value": f"{value.get('value', '')} {value.get('unit', '')}".strip() if value else obs.get("valueString", ""),
        "date": obs.get("effectiveDateTime", ""),
    }


def _summarize_care_plan(cp: dict) -> dict:
    return {
        "title": cp.get("title", ""),
        "status": cp.get("status"),
        "description": cp.get("description", ""),
        "categories": [cat.get("text") or (cat.get("coding", [{}])[0].get("display", "")) for cat in cp.get("category", [])],
    }

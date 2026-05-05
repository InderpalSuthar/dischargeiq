import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.discharge_plan_prompt import DISCHARGE_PLAN_SYSTEM_PROMPT


async def generate_personalized_discharge_plan(
    patient_id: Annotated[
        str | None,
        Field(description="The ID of the patient. Optional if patient context already exists via SHARP headers."),
    ] = None,
    encounter_id: Annotated[
        str | None,
        Field(description="The encounter ID for the current hospitalization. Used to scope medications and procedures to this visit."),
    ] = None,
    ctx: Context = None,
) -> str:
    """Generates a personalized discharge education plan tailored to this specific patient's
    diagnosis, medications, clinical history, and life situation. Not a template — a real,
    human-readable explanation personalized to THIS patient. Output is always a DRAFT
    for clinician review before delivery to the patient."""

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
    allergies = await fhir_client.get_allergies(patient_id)
    procedures = await fhir_client.get_procedures(patient_id, encounter_id)
    related_persons = await fhir_client.get_related_persons(patient_id)

    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)

    clinical_context = {
        "patient": _summarize_patient(patient),
        "encounter": _summarize_encounter(encounter) if encounter else "No encounter specified",
        "active_conditions": [_summarize_condition(c) for c in conditions],
        "discharge_medications": [_summarize_medication(m) for m in medications],
        "allergies": [_summarize_allergy(a) for a in allergies],
        "procedures": [_summarize_procedure(p) for p in procedures],
        "caregivers": [_summarize_related_person(rp) for rp in related_persons],
    }

    # Return structured context + instructions for the platform's LLM to generate the plan
    output = f"""## DISCHARGE PLAN GENERATION CONTEXT

### Instructions for Generating the Discharge Plan
{DISCHARGE_PLAN_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please use the above clinical data and instructions to generate a personalized discharge plan for this patient."""

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
        "address": _get_address(patient),
    }


def _get_language(patient: dict) -> str:
    comms = patient.get("communication", [])
    if comms:
        lang = comms[0].get("language", {})
        return lang.get("text") or (lang.get("coding", [{}])[0].get("display", "English"))
    return "English"


def _get_address(patient: dict) -> str:
    addresses = patient.get("address", [])
    if not addresses:
        return "Unknown"
    addr = addresses[0]
    parts = addr.get("line", []) + [addr.get("city", ""), addr.get("state", ""), addr.get("postalCode", "")]
    return ", ".join(p for p in parts if p)


def _summarize_encounter(encounter: dict) -> dict:
    return {
        "status": encounter.get("status"),
        "class": encounter.get("class", {}).get("display", encounter.get("class", {}).get("code")),
        "period": encounter.get("period"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
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
        "dosage": dosage.get("text", ""),
        "timing": dosage.get("timing", {}).get("repeat", {}),
        "duration": med.get("dispenseRequest", {}).get("expectedSupplyDuration", {}),
        "reason": med.get("reasonReference", []),
    }


def _summarize_allergy(allergy: dict) -> dict:
    code = allergy.get("code", {})
    return {
        "substance": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "reaction": [r.get("manifestation", [{}])[0].get("coding", [{}])[0].get("display") for r in allergy.get("reaction", [])],
        "severity": allergy.get("criticality"),
    }


def _summarize_procedure(proc: dict) -> dict:
    code = proc.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "date": proc.get("performedDateTime") or proc.get("performedPeriod", {}).get("start"),
        "status": proc.get("status"),
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

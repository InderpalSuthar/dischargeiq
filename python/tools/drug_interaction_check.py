import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.drug_interaction_prompt import DRUG_INTERACTION_SYSTEM_PROMPT


async def check_drug_interactions(
    patient_id: Annotated[
        str | None,
        Field(description="The ID of the patient. Optional if patient context already exists via SHARP headers."),
    ] = None,
    encounter_id: Annotated[
        str | None,
        Field(description="The encounter ID for the current hospitalization. Used to scope medications to this visit."),
    ] = None,
    ctx: Context = None,
) -> str:
    """Analyzes ALL discharge medications for dangerous drug-drug interactions, drug-allergy
    conflicts, and drug-condition contraindications. Catches combinations like warfarin + NSAIDs,
    ACE inhibitors + potassium-sparing diuretics, or prescribing a cephalosporin to a patient with
    penicillin allergy. Flags severity (CRITICAL/MAJOR/MODERATE/MINOR) with specific actions.
    Output is for pharmacist/clinician review before discharge."""

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

    medications = await fhir_client.get_medications(patient_id, encounter_id)
    allergies = await fhir_client.get_allergies(patient_id)
    conditions = await fhir_client.get_conditions(patient_id)
    observations = await fhir_client.get_observations(patient_id, "laboratory")

    if not medications:
        return create_text_response("No active medications found for this patient. No interaction check needed.")

    clinical_context = {
        "patient": _summarize_patient(patient),
        "discharge_medications": [_summarize_medication(m) for m in medications],
        "allergies": [_summarize_allergy(a) for a in allergies],
        "active_conditions": [_summarize_condition(c) for c in conditions],
        "recent_labs": [_summarize_observation(o) for o in observations[:10]],
        "medication_count": len(medications),
    }

    output = f"""## DRUG INTERACTION SAFETY CHECK CONTEXT

### Instructions for Analyzing Interactions
{DRUG_INTERACTION_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please analyze ALL medication pairs, drug-allergy cross-reactivity, and drug-condition contraindications for this patient."""

    return output


def _summarize_patient(patient: dict) -> dict:
    name_parts = patient.get("name", [{}])[0]
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    return {
        "name": f"{given} {family}".strip(),
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
    }


def _summarize_medication(med: dict) -> dict:
    med_code = med.get("medicationCodeableConcept", {})
    coding = med_code.get("coding", [{}])[0]
    dosage = med.get("dosageInstruction", [{}])[0] if med.get("dosageInstruction") else {}
    route = dosage.get("route", {})
    return {
        "name": med_code.get("text") or coding.get("display", "Unknown"),
        "code": coding.get("code", ""),
        "system": coding.get("system", ""),
        "dosage_text": dosage.get("text", ""),
        "route": route.get("text") or (route.get("coding", [{}])[0].get("display", "") if route.get("coding") else ""),
        "timing": dosage.get("timing", {}).get("repeat", {}),
        "status": med.get("status"),
    }


def _summarize_allergy(allergy: dict) -> dict:
    code = allergy.get("code", {})
    coding = code.get("coding", [{}])[0]
    reactions = []
    for r in allergy.get("reaction", []):
        for m in r.get("manifestation", []):
            display = m.get("coding", [{}])[0].get("display") or m.get("text", "")
            if display:
                reactions.append(display)
    return {
        "substance": code.get("text") or coding.get("display", "Unknown"),
        "code": coding.get("code", ""),
        "reactions": reactions,
        "criticality": allergy.get("criticality"),
        "type": allergy.get("type"),
        "category": allergy.get("category", []),
    }


def _summarize_condition(condition: dict) -> dict:
    code = condition.get("code", {})
    coding = code.get("coding", [{}])[0]
    return {
        "name": code.get("text") or coding.get("display", "Unknown"),
        "code": coding.get("code", ""),
        "system": coding.get("system", ""),
        "clinical_status": condition.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
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

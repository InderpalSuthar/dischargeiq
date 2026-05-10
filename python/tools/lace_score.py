import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.lace_score_prompt import LACE_SCORE_SYSTEM_PROMPT


async def calculate_lace_readmission_score(
    patient_id: Annotated[
        str | None,
        Field(description="The ID of the patient. Optional if patient context already exists via SHARP headers."),
    ] = None,
    encounter_id: Annotated[
        str | None,
        Field(description="The encounter ID for the current hospitalization. Required for accurate Length of Stay and Acuity calculation."),
    ] = None,
    ctx: Context = None,
) -> str:
    """Computes the evidence-based LACE readmission risk index (van Walraven et al., CMAJ 2010)
    from the patient's FHIR data. LACE = Length of stay + Acuity of admission + Comorbidities
    (Charlson Index) + Emergency department visits in prior 6 months. Returns a quantitative
    score (0-19) with risk stratification and specific clinical recommendations. Complements
    qualitative gap analysis with a validated, published metric that hospital quality teams track."""

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
    all_encounters = await fhir_client.get_all_encounters(patient_id)

    current_encounter = None
    if encounter_id:
        current_encounter = await fhir_client.get_encounter(encounter_id)

    clinical_context = {
        "patient": _summarize_patient(patient),
        "current_encounter": _summarize_encounter(current_encounter) if current_encounter else "No encounter specified — Length of Stay and Acuity cannot be calculated precisely",
        "all_conditions": [_summarize_condition(c) for c in conditions],
        "all_encounters_last_12_months": [_summarize_encounter_brief(e) for e in all_encounters],
    }

    output = f"""## LACE READMISSION RISK SCORE CONTEXT

### Instructions for Computing LACE Score
{LACE_SCORE_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please compute the LACE readmission risk score for this patient using the scoring algorithm above and the provided clinical data."""

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


def _summarize_encounter(encounter: dict) -> dict:
    enc_class = encounter.get("class", {})
    return {
        "status": encounter.get("status"),
        "class_code": enc_class.get("code"),
        "class_display": enc_class.get("display"),
        "period": encounter.get("period"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
        "admission_source": encounter.get("hospitalization", {}).get("admitSource", {}).get("text"),
        "discharge_disposition": encounter.get("hospitalization", {}).get("dischargeDisposition", {}).get("text"),
        "priority": encounter.get("priority", {}).get("coding", [{}])[0].get("display") if encounter.get("priority") else None,
    }


def _summarize_encounter_brief(encounter: dict) -> dict:
    enc_class = encounter.get("class", {})
    return {
        "class_code": enc_class.get("code"),
        "class_display": enc_class.get("display"),
        "period": encounter.get("period"),
        "status": encounter.get("status"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
    }


def _summarize_condition(condition: dict) -> dict:
    code = condition.get("code", {})
    coding = code.get("coding", [{}])[0]
    return {
        "name": code.get("text") or coding.get("display", "Unknown"),
        "icd_code": coding.get("code", ""),
        "system": coding.get("system", ""),
        "clinical_status": condition.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
        "verification_status": condition.get("verificationStatus", {}).get("coding", [{}])[0].get("code") if condition.get("verificationStatus") else None,
        "onset": condition.get("onsetDateTime"),
    }

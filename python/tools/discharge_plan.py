import asyncio
import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.discharge_plan_prompt import DISCHARGE_PLAN_SYSTEM_PROMPT
from summarizers import (
    get_patient_language_code,
    summarize_allergy,
    summarize_condition,
    summarize_encounter,
    summarize_medication,
    summarize_patient,
    summarize_procedure,
    summarize_related_person,
)


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
    for clinician review before delivery to the patient. Supports multilingual output
    based on Patient.communication.language."""

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

    # Parallel FHIR fetching — 5-7x faster than sequential
    conditions, medications, allergies, procedures, related_persons = await asyncio.gather(
        fhir_client.get_conditions(patient_id),
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_allergies(patient_id),
        fhir_client.get_procedures(patient_id, encounter_id),
        fhir_client.get_related_persons(patient_id),
    )

    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)

    # Detect patient language for multilingual output
    language_code = get_patient_language_code(patient)
    language_instruction = ""
    if language_code and language_code.lower() not in ("en", "english"):
        language_name_map = {
            "es": "Spanish", "zh": "Chinese (Simplified)", "ar": "Arabic",
            "fr": "French", "de": "German", "hi": "Hindi", "pt": "Portuguese",
            "ru": "Russian", "ko": "Korean", "ja": "Japanese", "vi": "Vietnamese",
            "tl": "Tagalog", "it": "Italian", "pl": "Polish",
        }
        lang_name = language_name_map.get(language_code.lower(), language_code)
        language_instruction = (
            f"\n\n⚠️ CRITICAL LANGUAGE REQUIREMENT: This patient's primary language is "
            f"**{lang_name}** (code: {language_code}). You MUST generate the ENTIRE discharge "
            f"plan in {lang_name}. All section headings, instructions, medication explanations, "
            f"and warnings must be in {lang_name}. Do not use English except for drug names."
        )

    clinical_context = {
        "patient": summarize_patient(patient, include_address=True, include_language=True),
        "encounter": summarize_encounter(encounter, include_hospitalization=True) if encounter else "No encounter specified",
        "active_conditions": [summarize_condition(c) for c in conditions],
        "discharge_medications": [summarize_medication(m, include_codes=False) for m in medications],
        "allergies": [summarize_allergy(a) for a in allergies],
        "procedures": [summarize_procedure(p) for p in procedures],
        "caregivers": [summarize_related_person(rp) for rp in related_persons],
    }

    output = f"""## DISCHARGE PLAN GENERATION CONTEXT

### Instructions for Generating the Discharge Plan
{DISCHARGE_PLAN_SYSTEM_PROMPT}{language_instruction}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please use the above clinical data and instructions to generate a personalized discharge plan for this patient."""

    return output

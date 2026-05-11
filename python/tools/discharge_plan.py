import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient, find_current_encounter
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.discharge_plan_prompt import DISCHARGE_PLAN_SYSTEM_PROMPT
from shared_constants import get_language_instruction
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

logger = logging.getLogger(__name__)


async def generate_personalized_discharge_plan(
    patient_id: Annotated[
        str | None,
        Field(description="The ID of the patient. Optional if patient context already exists via SHARP headers."),
    ] = None,
    encounter_id: Annotated[
        str | None,
        Field(description="The encounter ID for the current hospitalization. If not provided, the most recent encounter is auto-detected."),
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

    # Parallel FHIR fetching with error resilience
    results = await asyncio.gather(
        fhir_client.get_conditions(patient_id),
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_allergies(patient_id),
        fhir_client.get_procedures(patient_id, encounter_id),
        fhir_client.get_related_persons(patient_id),
        fhir_client.get_all_encounters(patient_id),
        return_exceptions=True,
    )

    conditions = results[0] if not isinstance(results[0], Exception) else []
    medications = results[1] if not isinstance(results[1], Exception) else []
    allergies = results[2] if not isinstance(results[2], Exception) else []
    procedures = results[3] if not isinstance(results[3], Exception) else []
    related_persons = results[4] if not isinstance(results[4], Exception) else []
    all_encounters = results[5] if not isinstance(results[5], Exception) else []

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning("FHIR fetch %d failed: %s", i, res)

    # --- Encounter auto-detection ---
    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)
    if not encounter:
        encounter = find_current_encounter(all_encounters)

    # Detect patient language for multilingual output
    language_code = get_patient_language_code(patient)
    language_instruction = get_language_instruction(language_code)

    clinical_context = {
        "patient": summarize_patient(patient, include_address=True, include_language=True),
        "encounter": summarize_encounter(encounter, include_hospitalization=True) if encounter else "No encounter found — auto-detection returned no results",
        "active_conditions": [summarize_condition(c) for c in conditions],
        "discharge_medications": [summarize_medication(m, include_codes=False) for m in medications],
        "allergies": [summarize_allergy(a) for a in allergies],
        "procedures": [summarize_procedure(p) for p in procedures],
        "caregivers": [summarize_related_person(rp) for rp in related_persons],
    }

    output = f"""## DISCHARGE PLAN GENERATION CONTEXT `[AI-GENERATED — DRAFT for clinician review]`

### Instructions for Generating the Discharge Plan
{DISCHARGE_PLAN_SYSTEM_PROMPT}{language_instruction}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, separators=(',', ':'))}

---
Please use the above clinical data and instructions to generate a personalized discharge plan for this patient."""

    return output

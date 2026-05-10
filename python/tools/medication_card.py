import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.medication_card_prompt import MEDICATION_CARD_SYSTEM_PROMPT
from shared_constants import get_language_instruction
from summarizers import (
    get_patient_age,
    get_patient_language_code,
    summarize_allergy,
    summarize_medication,
    summarize_patient,
)

logger = logging.getLogger(__name__)


async def generate_medication_card(
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
    """Generates a fridge-friendly medication schedule card organized by time of day.
    Supports multilingual output based on Patient.communication.language."""

    if not patient_id:
        patient_id = get_patient_id_if_context_exists(ctx)
        if not patient_id:
            return create_text_response("No patient context found.", is_error=True)

    fhir_context = get_fhir_context(ctx)
    if not fhir_context:
        return create_text_response("FHIR context could not be retrieved.", is_error=True)

    fhir_client = FhirClient(base_url=fhir_context.url, token=fhir_context.token)

    patient = await fhir_client.get_patient(patient_id)
    if not patient:
        return create_text_response(f"Patient {patient_id} not found.", is_error=True)

    results = await asyncio.gather(
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_allergies(patient_id),
        return_exceptions=True,
    )

    medications = results[0] if not isinstance(results[0], Exception) else []
    allergies = results[1] if not isinstance(results[1], Exception) else []

    if not medications:
        return create_text_response("No active medications found. Cannot generate medication card.")

    language_code = get_patient_language_code(patient)
    language_instruction = get_language_instruction(language_code)
    patient_age = get_patient_age(patient)

    clinical_context = {
        "patient": summarize_patient(patient, include_language=True),
        "patient_age": patient_age,
        "discharge_medications": [summarize_medication(m, include_codes=True) for m in medications],
        "allergies": [summarize_allergy(a, include_reactions=True) for a in allergies],
        "total_medications": len(medications),
    }

    output = f"""## MEDICATION CARD GENERATION CONTEXT

### Instructions
{MEDICATION_CARD_SYSTEM_PROMPT}{language_instruction}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, indent=2)}

---
Please generate a fridge-friendly medication schedule card for this patient."""

    return output

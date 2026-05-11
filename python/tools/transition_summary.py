import asyncio
import json
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

import logging

from fhir_client import FhirClient, find_current_encounter
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists

logger = logging.getLogger(__name__)
from mcp_utilities import create_text_response
from prompts.transition_summary_prompt import TRANSITION_SUMMARY_SYSTEM_PROMPT
from summarizers import (
    summarize_allergy,
    summarize_care_plan,
    summarize_care_team,
    summarize_condition,
    summarize_diagnostic_report,
    summarize_encounter,
    summarize_medication,
    summarize_observation,
    summarize_patient,
    summarize_procedure,
    summarize_service_request,
)


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
        Field(description="The encounter ID for the current hospitalization. If not provided, the most recent encounter is auto-detected."),
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

    # Parallel FHIR fetching with error resilience
    results = await asyncio.gather(
        fhir_client.get_conditions(patient_id),
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_procedures(patient_id, encounter_id),
        fhir_client.get_observations(patient_id, "laboratory"),
        fhir_client.get_service_requests(patient_id),
        fhir_client.get_care_plans(patient_id),
        fhir_client.get_care_teams(patient_id),
        fhir_client.get_diagnostic_reports(patient_id),
        fhir_client.get_allergies(patient_id),
        fhir_client.get_all_encounters(patient_id),
        return_exceptions=True,
    )

    conditions = results[0] if not isinstance(results[0], Exception) else []
    medications = results[1] if not isinstance(results[1], Exception) else []
    procedures = results[2] if not isinstance(results[2], Exception) else []
    observations = results[3] if not isinstance(results[3], Exception) else []
    service_requests = results[4] if not isinstance(results[4], Exception) else []
    care_plans = results[5] if not isinstance(results[5], Exception) else []
    care_teams = results[6] if not isinstance(results[6], Exception) else []
    diagnostic_reports = results[7] if not isinstance(results[7], Exception) else []
    allergies = results[8] if not isinstance(results[8], Exception) else []
    all_encounters = results[9] if not isinstance(results[9], Exception) else []

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning("FHIR fetch %d failed: %s", i, res)

    # --- Encounter auto-detection ---
    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)
    if not encounter:
        encounter = find_current_encounter(all_encounters)

    clinical_context = {
        "recipient_type": recipient_type,
        "patient": summarize_patient(patient, include_language=True, include_address=True),
        "encounter": summarize_encounter(encounter, include_hospitalization=True) if encounter else "No encounter specified",
        "active_conditions": [summarize_condition(c, include_icd=True) for c in conditions],
        "discharge_medications": [summarize_medication(m, include_codes=True) for m in medications],
        "procedures_this_visit": [summarize_procedure(p) for p in procedures],
        "recent_labs": [summarize_observation(o) for o in observations[:15]],
        "pending_orders": [summarize_service_request(sr) for sr in service_requests],
        "active_care_plans": [summarize_care_plan(cp) for cp in care_plans],
        "care_team": [summarize_care_team(ct) for ct in care_teams],
        "diagnostic_reports": [summarize_diagnostic_report(dr) for dr in diagnostic_reports],
        "allergies": [summarize_allergy(a) for a in allergies],
    }

    output = f"""## TRANSITION OF CARE SUMMARY CONTEXT `[AI-GENERATED — DRAFT for clinician review]`

### Recipient Type: {recipient_type}

### Instructions for Generating the Summary
{TRANSITION_SUMMARY_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, separators=(',', ':'))}

---
Please generate a transition of care summary tailored for the **{recipient_type}** recipient using the above clinical data and instructions."""

    return output

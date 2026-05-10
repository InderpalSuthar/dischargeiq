import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from clinical_rules import ClinicalGap, run_gap_rules_engine
from fhir_client import FhirClient, find_current_encounter
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.readmission_gaps_prompt import READMISSION_GAPS_SYSTEM_PROMPT
from summarizers import (
    summarize_appointment,
    summarize_care_plan,
    summarize_condition,
    summarize_encounter,
    summarize_medication,
    summarize_observation,
    summarize_patient,
    summarize_related_person,
    summarize_service_request,
)

logger = logging.getLogger(__name__)


def _format_deterministic_gaps(gaps: list[ClinicalGap]) -> str:
    if not gaps:
        return "✅ No deterministic gaps detected by rules engine."
    lines = []
    for i, gap in enumerate(gaps, 1):
        lines.append(f"""### Gap {i} — [{gap.severity}] *(Rules Engine)*
**Issue:** {gap.issue}
**Evidence:** {gap.evidence}
**Clinical Risk:** {gap.clinical_risk}
**ACTION:** {gap.action}
""")
    return "\n".join(lines)


async def identify_readmission_risk_gaps(
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
    """Analyzes the patient's full FHIR context at the moment of discharge and identifies
    specific, named, actionable gaps that will cause a readmission if not addressed before
    the patient leaves. Combines a deterministic rules engine (for reliable, evidence-based
    gaps) with AI analysis (for nuanced, context-dependent gaps). Not a risk score — a list
    of specific things that are broken in this patient's discharge plan right now."""

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
        fhir_client.get_service_requests(patient_id),
        fhir_client.get_appointments(patient_id),
        fhir_client.get_related_persons(patient_id),
        fhir_client.get_observations(patient_id, "laboratory"),
        fhir_client.get_care_plans(patient_id),
        fhir_client.get_all_encounters(patient_id),
        return_exceptions=True,
    )

    conditions = results[0] if not isinstance(results[0], Exception) else []
    medications = results[1] if not isinstance(results[1], Exception) else []
    service_requests = results[2] if not isinstance(results[2], Exception) else []
    appointments = results[3] if not isinstance(results[3], Exception) else []
    related_persons = results[4] if not isinstance(results[4], Exception) else []
    observations = results[5] if not isinstance(results[5], Exception) else []
    care_plans = results[6] if not isinstance(results[6], Exception) else []
    all_encounters = results[7] if not isinstance(results[7], Exception) else []

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning("FHIR fetch %d failed: %s", i, res)

    # --- Encounter auto-detection ---
    encounter = None
    if encounter_id:
        encounter = await fhir_client.get_encounter(encounter_id)
    if not encounter:
        encounter = find_current_encounter(all_encounters)

    # --- Deterministic rules engine runs first ---
    deterministic_gaps = run_gap_rules_engine(
        patient=patient,
        medications=medications,
        service_requests=service_requests,
        appointments=appointments,
        care_plans=care_plans,
        related_persons=related_persons,
        conditions=conditions,
        observations=observations,
    )

    deterministic_section = _format_deterministic_gaps(deterministic_gaps)
    det_count = len(deterministic_gaps)

    clinical_context = {
        "patient": summarize_patient(patient, include_address=True),
        "encounter": summarize_encounter(encounter) if encounter else "No encounter found",
        "active_conditions": [summarize_condition(c, include_icd=True) for c in conditions],
        "discharge_medications": [summarize_medication(m) for m in medications],
        "active_service_requests": [summarize_service_request(sr) for sr in service_requests],
        "scheduled_appointments": [summarize_appointment(a) for a in appointments],
        "caregivers": [summarize_related_person(rp) for rp in related_persons],
        "recent_labs": [summarize_observation(o) for o in observations[:15]],
        "active_care_plans": [summarize_care_plan(cp) for cp in care_plans],
    }

    output = f"""## READMISSION RISK GAP ANALYSIS

**Patient:** {patient_id} | **Deterministic Gaps Found:** {det_count}

---

## PART 1: Deterministic Gap Analysis (Rules Engine)
*The following gaps were identified by a deterministic rules engine based on evidence-based clinical protocols. These are reliable, reproducible findings.*

{deterministic_section}

---

## PART 2: AI-Assisted Gap Analysis

### Instructions for AI Gap Analysis
{READMISSION_GAPS_SYSTEM_PROMPT}

### Important: Avoid Duplicating Rules Engine Gaps
The following gaps were ALREADY identified by the rules engine above. Do NOT repeat them:
{chr(10).join(f"- {g.issue}" for g in deterministic_gaps) if deterministic_gaps else "- (none)"}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, separators=(',', ':'))}

---
Please analyze the above clinical data and identify any ADDITIONAL gaps not already caught by the rules engine above. Focus on nuanced, context-dependent issues that require clinical reasoning."""

    return output

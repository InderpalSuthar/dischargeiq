import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from clinical_rules import compute_lace_score, format_lace_score_table
from fhir_client import FhirClient, find_current_encounter
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from prompts.lace_score_prompt import LACE_SCORE_SYSTEM_PROMPT
from summarizers import (
    summarize_condition,
    summarize_encounter,
    summarize_patient,
)

logger = logging.getLogger(__name__)


def _summarize_encounter_brief(encounter: dict) -> dict:
    enc_class = encounter.get("class", {})
    return {
        "class_code": enc_class.get("code"),
        "class_display": enc_class.get("display"),
        "period": encounter.get("period"),
        "status": encounter.get("status"),
        "reason": [rc.get("text") or (rc.get("coding", [{}])[0].get("display")) for rc in encounter.get("reasonCode", [])],
    }


async def calculate_lace_readmission_score(
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
    """Deterministically computes the evidence-based LACE readmission risk index
    (van Walraven et al., CMAJ 2010) directly from the patient's FHIR data.
    LACE = Length of stay + Acuity of admission + Comorbidities (Charlson Index)
    + Emergency department visits in prior 6 months.
    The score (0-19) is computed in code — not estimated by AI.
    AI is then used to provide personalized clinical interpretation and recommendations."""

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
        fhir_client.get_all_encounters(patient_id),
        return_exceptions=True,
    )
    conditions = results[0] if not isinstance(results[0], Exception) else []
    all_encounters = results[1] if not isinstance(results[1], Exception) else []

    if isinstance(results[0], Exception):
        logger.warning("Failed to fetch conditions: %s", results[0])
    if isinstance(results[1], Exception):
        logger.warning("Failed to fetch encounters: %s", results[1])

    # --- Encounter auto-detection ---
    current_encounter = None
    if encounter_id:
        current_encounter = await fhir_client.get_encounter(encounter_id)
    if not current_encounter:
        current_encounter = find_current_encounter(all_encounters)

    # --- Deterministic LACE computation ---
    lace = compute_lace_score(
        current_encounter=current_encounter,
        all_encounters=all_encounters,
        conditions=conditions,
    )

    lace_table = format_lace_score_table(lace)

    data_gaps_section = ""
    if lace.data_gaps:
        data_gaps_section = "\n**Data Gaps (affected scoring accuracy):**\n" + "\n".join(
            f"- {gap}" for gap in lace.data_gaps
        )

    encounter_source = "auto-detected (most recent)" if not encounter_id else "specified"

    clinical_context = {
        "patient": summarize_patient(patient),
        "lace_score_computed": {
            "total": lace.total,
            "risk_level": lace.risk_level,
            "readmission_probability": lace.readmission_probability_pct,
            "components": {
                "L_length_of_stay_days": lace.length_of_stay_days,
                "L_score": lace.l_score,
                "A_acuity": lace.admission_acuity,
                "A_score": lace.a_score,
                "C_charlson_conditions": lace.cci_conditions_matched,
                "C_cci_score": lace.cci_score,
                "C_score": lace.c_score,
                "E_ed_visits_6mo": lace.ed_visits_6mo,
                "E_score": lace.e_score,
            },
        },
        "current_encounter": summarize_encounter(current_encounter) if current_encounter else "No encounter found",
        "encounter_source": encounter_source,
        "active_conditions": [c.get("name") for c in [summarize_condition(c) for c in conditions]],
    }

    output = f"""## LACE READMISSION RISK SCORE — Deterministic Computation

**Patient:** {patient_id} | **Encounter:** {encounter_source}

---

### Score Breakdown (Computed from FHIR Data) `[DETERMINISTIC]`

{lace_table}
{data_gaps_section}

---

## AI Clinical Interpretation `[AI-INTERPRETED — requires clinician review]`

### Instructions
{LACE_SCORE_SYSTEM_PROMPT}

### Computed Data (use this — do NOT re-compute the score)
{json.dumps(clinical_context, separators=(',', ':'))}

---
The LACE score above has been computed deterministically from FHIR data. Please provide:
1. Clinical interpretation of this specific score for THIS patient
2. Patient-specific recommendations based on their conditions and score
3. Any additional risk factors visible in the clinical data that the LACE score does not capture

Do NOT recompute the score. Use the computed values above."""

    return output

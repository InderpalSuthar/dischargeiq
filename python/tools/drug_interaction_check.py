import asyncio
import json
import logging
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from clinical_rules import compute_polypharmacy_risk
from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from openfda_client import lookup_drug_interactions
from prompts.drug_interaction_prompt import DRUG_INTERACTION_SYSTEM_PROMPT
from summarizers import (
    get_patient_age,
    summarize_allergy,
    summarize_condition,
    summarize_medication,
    summarize_observation,
    summarize_patient,
)

logger = logging.getLogger(__name__)


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
    conflicts, and drug-condition contraindications. Also computes a deterministic polypharmacy
    risk score. Catches combinations like warfarin + NSAIDs, ACE inhibitors + potassium-sparing
    diuretics, or prescribing a cephalosporin to a patient with penicillin allergy. Flags
    severity (CRITICAL/MAJOR/MODERATE/MINOR) with specific actions.
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

    # Parallel FHIR fetching with error resilience
    results = await asyncio.gather(
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_allergies(patient_id),
        fhir_client.get_conditions(patient_id),
        fhir_client.get_observations(patient_id, "laboratory"),
        return_exceptions=True,
    )

    medications = results[0] if not isinstance(results[0], Exception) else []
    allergies = results[1] if not isinstance(results[1], Exception) else []
    conditions = results[2] if not isinstance(results[2], Exception) else []
    observations = results[3] if not isinstance(results[3], Exception) else []

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning("FHIR fetch %d failed: %s", i, res)

    if not medications:
        return create_text_response("No active medications found for this patient. No interaction check needed.")

    # Deterministic polypharmacy risk computation
    patient_age = get_patient_age(patient)
    poly_risk = compute_polypharmacy_risk(medications, patient_age)

    # Extract drug names for OpenFDA lookup
    drug_names = []
    for m in medications:
        med_code = m.get("medicationCodeableConcept", {})
        name = med_code.get("text", "")
        if not name:
            codings = med_code.get("coding", [])
            name = codings[0].get("display", "") if codings else ""
        if name:
            drug_names.append(name)

    # OpenFDA drug label lookup (parallel, non-blocking)
    fda_data = {"interactions": [], "warnings": [], "boxed_warnings": [], "drugs_found": 0, "drugs_not_found": [], "source": "OpenFDA Drug Label API"}
    try:
        fda_data = await lookup_drug_interactions(drug_names)
    except Exception as e:
        logger.warning("OpenFDA lookup failed entirely, continuing without FDA data: %s", e)

    # Build FDA section
    fda_section = ""
    if fda_data["interactions"] or fda_data["boxed_warnings"]:
        fda_section = "\n\n## FDA DRUG LABEL DATA [FDA-SOURCED]\n"
        fda_section += f"*Source: OpenFDA Drug Label API | Drugs found: {fda_data['drugs_found']}/{len(drug_names)}*\n"

        if fda_data["boxed_warnings"]:
            fda_section += "\n### ⚠️ FDA BOXED WARNINGS\n"
            for bw in fda_data["boxed_warnings"]:
                fda_section += f"\n**{bw['drug']}:** {bw['warning'][:300]}\n"

        if fda_data["interactions"]:
            fda_section += "\n### FDA-Documented Drug Interactions\n"
            for ix in fda_data["interactions"]:
                fda_section += f"\n**[{ix['severity']}] {ix['drug_a']} + {ix['drug_b']}**\n"
                fda_section += f"> {ix['description']}\n"
                fda_section += f"*Source: {ix['source']}*\n"

        if fda_data["drugs_not_found"]:
            fda_section += f"\n*FDA labels not found for: {', '.join(fda_data['drugs_not_found'][:5])}*\n"
    elif drug_names:
        fda_section = "\n\n## FDA DRUG LABEL DATA [FDA-SOURCED]\n"
        fda_section += f"*OpenFDA queried for {len(drug_names)} drugs — no documented interactions found between these medications.*\n"

    poly_section = f"""## POLYPHARMACY RISK ASSESSMENT [DETERMINISTIC]

| Metric | Value |
|---|---|
| Total Medications | {poly_risk.total_medications} |
| High-Alert Medications | {', '.join(poly_risk.high_alert_medications) or 'None detected'} |
| Risk Level | **{poly_risk.risk_level}** |

**Flags:** {'; '.join(poly_risk.flags) if poly_risk.flags else 'None'}
"""

    clinical_context = {
        "patient": summarize_patient(patient),
        "patient_age": patient_age,
        "discharge_medications": [summarize_medication(m, include_codes=True) for m in medications],
        "allergies": [summarize_allergy(a, include_reactions=True) for a in allergies],
        "active_conditions": [summarize_condition(c, include_icd=True) for c in conditions],
        "recent_labs": [summarize_observation(o) for o in observations[:10]],
        "medication_count": len(medications),
        "polypharmacy_risk_level": poly_risk.risk_level,
    }

    output = f"""{poly_section}
{fda_section}

---

## DRUG INTERACTION SAFETY CHECK CONTEXT [AI-INTERPRETED]

### Instructions for Analyzing Interactions
{DRUG_INTERACTION_SYSTEM_PROMPT}

### Patient Clinical Data (from FHIR R4)
{json.dumps(clinical_context, separators=(',', ':'))}

---
Please analyze ALL medication pairs, drug-allergy cross-reactivity, and drug-condition contraindications for this patient. The polypharmacy risk assessment and FDA drug label data above are already computed — prioritize FDA-sourced interactions, then add AI-identified ones."""

    return output

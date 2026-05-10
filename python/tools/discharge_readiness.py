import asyncio
from datetime import date
from typing import Annotated

from mcp.server.fastmcp import Context
from pydantic import Field

from clinical_rules import (
    compute_lace_score,
    compute_polypharmacy_risk,
    run_gap_rules_engine,
)
from fhir_client import FhirClient
from fhir_utilities import get_fhir_context, get_patient_id_if_context_exists
from mcp_utilities import create_text_response
from summarizers import get_patient_age, summarize_patient


async def get_discharge_readiness_dashboard(
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
    """Returns a structured discharge readiness dashboard for a patient.
    Aggregates all deterministic risk signals into a single at-a-glance summary:
    LACE readmission risk score, polypharmacy risk level, number of clinical gaps found,
    and a pass/warn/fail status for each discharge readiness domain.
    Designed as a quick pre-discharge checklist for the clinical team.
    This tool runs fast (no LLM calls) and gives an instant risk overview."""

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

    # Parallel FHIR fetching — everything in one shot
    (
        conditions,
        medications,
        service_requests,
        appointments,
        related_persons,
        observations,
        care_plans,
        all_encounters,
    ) = await asyncio.gather(
        fhir_client.get_conditions(patient_id),
        fhir_client.get_medications(patient_id, encounter_id),
        fhir_client.get_service_requests(patient_id),
        fhir_client.get_appointments(patient_id),
        fhir_client.get_related_persons(patient_id),
        fhir_client.get_observations(patient_id, "laboratory"),
        fhir_client.get_care_plans(patient_id),
        fhir_client.get_all_encounters(patient_id),
    )

    current_encounter = None
    if encounter_id:
        current_encounter = await fhir_client.get_encounter(encounter_id)

    patient_info = summarize_patient(patient, include_address=True, include_language=True)
    patient_age = get_patient_age(patient)

    # --- Run all deterministic computations ---
    lace = compute_lace_score(current_encounter, all_encounters, conditions)
    poly_risk = compute_polypharmacy_risk(medications, patient_age)
    gaps = run_gap_rules_engine(
        patient=patient,
        medications=medications,
        service_requests=service_requests,
        appointments=appointments,
        care_plans=care_plans,
        related_persons=related_persons,
        conditions=conditions,
        observations=observations,
    )

    high_gaps = [g for g in gaps if g.severity == "HIGH"]
    medium_gaps = [g for g in gaps if g.severity == "MEDIUM"]
    low_gaps = [g for g in gaps if g.severity == "LOW"]

    # --- Build status indicators ---
    def status_icon(condition: bool, warn_only: bool = False) -> str:
        if condition:
            return "🟢 PASS"
        return "🟡 WARN" if warn_only else "🔴 FAIL"

    has_followup = any(
        (date.fromisoformat(a["start"][:10]) - date.today()).days <= 14
        for a in appointments
        if a.get("start") and not _date_parse_fails(a["start"])
    )
    has_caregiver = len(related_persons) > 0
    no_high_gaps = len(high_gaps) == 0
    low_poly_risk = poly_risk.risk_level in ("LOW", "MODERATE")
    low_lace = lace.total <= 9

    # Build gaps section
    gap_lines = []
    for gap in gaps:
        icon = "🔴" if gap.severity == "HIGH" else "🟡" if gap.severity == "MEDIUM" else "🔵"
        gap_lines.append(f"{icon} **[{gap.severity}]** {gap.issue}")

    gaps_section = "\n".join(gap_lines) if gap_lines else "✅ No gaps detected by rules engine."

    # Polypharmacy icon
    poly_icon = "🟢" if poly_risk.risk_level == "LOW" else "🟡" if poly_risk.risk_level == "MODERATE" else "🔴"
    lace_icon = "🟢" if lace.risk_level in ("LOW",) else "🟡" if lace.risk_level == "MODERATE" else "🔴"

    output = f"""**⚡ IMPORTANT: This dashboard is pre-computed and pre-formatted. Present it to the user exactly as shown below. Do not summarize, rephrase, or reformat the tables. Add brief commentary only after the dashboard if needed.**

---

## 🏥 DISCHARGE READINESS DASHBOARD

**Patient:** {patient_info.get('name', patient_id)} | **Age:** {patient_age or 'Unknown'} | **Date:** {date.today().isoformat()}

---

### At-a-Glance Risk Summary

| Domain | Status | Detail |
|---|---|---|
| 🏃 Follow-up Appointment (≤14 days) | {status_icon(has_followup)} | {'Appointment found' if has_followup else 'No near-term appointment'} |
| 👨‍👩‍👧 Caregiver Support | {status_icon(has_caregiver, warn_only=True)} | {f'{len(related_persons)} caregiver(s) documented' if has_caregiver else 'No caregiver documented'} |
| ⚠️ High-Priority Clinical Gaps | {status_icon(no_high_gaps)} | {f'{len(high_gaps)} HIGH, {len(medium_gaps)} MEDIUM, {len(low_gaps)} LOW' if gaps else 'No gaps'} |
| 💊 Polypharmacy Risk | {poly_icon} {poly_risk.risk_level} | {len(medications)} medications, {len(poly_risk.high_alert_medications)} high-alert |
| 📊 LACE Readmission Score | {lace_icon} {lace.risk_level} ({lace.total}/19) | ~{lace.readmission_probability_pct} 30-day readmission probability |
| 📋 Active Care Plan | {status_icon(len(care_plans) > 0, warn_only=True)} | {f'{len(care_plans)} active plan(s)' if care_plans else 'No care plan on file'} |

---

### Clinical Gaps Detected (Rules Engine)

{gaps_section}

---

### LACE Score Breakdown

| L (Length of Stay) | A (Acuity) | C (Comorbidities) | E (ED Visits 6mo) | **Total** |
|---|---|---|---|---|
| {lace.l_score} ({lace.length_of_stay_days}d) | {lace.a_score} ({lace.admission_acuity}) | {lace.c_score} (CCI={lace.cci_score}) | {lace.e_score} ({lace.ed_visits_6mo} visits) | **{lace.total} — {lace.risk_level}** |

---

### Recommended Next Steps

{"🚨 **Action Required:** " + str(len(high_gaps)) + " HIGH-priority gap(s) must be resolved before discharge." if high_gaps else "✅ No blocking high-priority gaps."}

**Suggested tools to run for full analysis:**
- `IdentifyReadmissionRiskGaps` — detailed gap analysis with AI interpretation
- `CalculateLACEScore` — full LACE score with clinical recommendations
- `CheckDrugInteractions` — medication safety review
- `GeneratePersonalizedDischargePlan` — patient education document
- `DraftTransitionCareSummary` — handoff document for receiving provider

---
*Discharge Readiness Dashboard — Deterministic analysis only. No AI generation. Fast and reliable.*
*Generated: {date.today().isoformat()} | Source: FHIR R4 via SHARP context*"""

    return output


def _date_parse_fails(date_str: str) -> bool:
    try:
        date.fromisoformat(date_str[:10])
        return False
    except Exception:
        return True

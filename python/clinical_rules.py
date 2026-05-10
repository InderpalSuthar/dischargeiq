"""
Clinical rules engine for deterministic gap detection, LACE score computation,
polypharmacy risk scoring, and SDOH flagging.

Design principle: Compute what's computable. Delegate what requires reasoning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Literal

from summarizers import (
    get_allergy_substance_lower,
    get_condition_icd_code,
    get_encounter_length_of_stay_days,
    get_medication_name_lower,
    get_patient_age,
    get_patient_postal_code,
    get_service_request_description_lower,
)


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

GapSeverity = Literal["HIGH", "MEDIUM", "LOW"]


@dataclass
class ClinicalGap:
    severity: GapSeverity
    issue: str
    evidence: str
    clinical_risk: str
    action: str
    source: Literal["RULE_ENGINE", "AI_ANALYSIS"] = "RULE_ENGINE"


# ---------------------------------------------------------------------------
# High-alert medications (require mandatory monitoring)
# ---------------------------------------------------------------------------

HIGH_ALERT_MEDS: dict[str, dict] = {
    "warfarin": {
        "monitor": "inr",
        "gap_message": "Warfarin prescribed without INR monitoring order",
        "risk": "INR-out-of-range can cause fatal bleeding or stroke. Must be checked 3–5 days post-discharge.",
        "action": "Place INR lab order and communicate results protocol to patient before discharge.",
        "severity": "HIGH",
    },
    "coumadin": {
        "monitor": "inr",
        "gap_message": "Coumadin prescribed without INR monitoring order",
        "risk": "Same as warfarin — brand name only.",
        "action": "Place INR lab order before discharge.",
        "severity": "HIGH",
    },
    "insulin": {
        "monitor": "glucose",
        "gap_message": "Insulin prescribed without glucose monitoring plan",
        "risk": "Hypoglycemia is the leading cause of preventable ED visits in diabetic patients.",
        "action": "Ensure glucometer prescription, supply, and education. Add glucose monitoring ServiceRequest.",
        "severity": "HIGH",
    },
    "metformin": {
        "monitor": "creatinine",
        "gap_message": "Metformin prescribed without recent renal function check",
        "risk": "Metformin is contraindicated in renal insufficiency (eGFR < 30). Risk of lactic acidosis.",
        "action": "Verify recent creatinine/eGFR. If eGFR < 30, hold metformin and consult pharmacy.",
        "severity": "HIGH",
    },
    "digoxin": {
        "monitor": "digoxin",
        "gap_message": "Digoxin prescribed without drug level monitoring order",
        "risk": "Narrow therapeutic index. Toxicity can cause fatal arrhythmias.",
        "action": "Place digoxin level lab order for 5–7 days post-discharge.",
        "severity": "HIGH",
    },
    "lithium": {
        "monitor": "lithium",
        "gap_message": "Lithium prescribed without serum level monitoring order",
        "risk": "Narrow therapeutic index. Toxicity risk is high.",
        "action": "Place lithium level lab order within 5 days.",
        "severity": "HIGH",
    },
    "phenytoin": {
        "monitor": "phenytoin",
        "gap_message": "Phenytoin prescribed without drug level monitoring",
        "risk": "Narrow therapeutic window. Sub-therapeutic levels risk seizure.",
        "action": "Place phenytoin level lab order.",
        "severity": "HIGH",
    },
    "amiodarone": {
        "monitor": "thyroid",
        "gap_message": "Amiodarone prescribed without thyroid monitoring",
        "risk": "Amiodarone causes thyroid dysfunction in up to 20% of patients.",
        "action": "Ensure TSH lab order within 30 days.",
        "severity": "MEDIUM",
    },
}

# Medication names that indicate high polypharmacy risk when combined
HIGH_ALERT_MED_NAMES = set(HIGH_ALERT_MEDS.keys()) | {
    "opioid", "oxycodone", "hydrocodone", "morphine", "fentanyl",
    "benzodiazepine", "diazepam", "lorazepam", "clonazepam",
    "anticoagulant", "heparin", "enoxaparin", "apixaban", "rivaroxaban",
    "dabigatran", "clopidogrel",
}


# ---------------------------------------------------------------------------
# SDOH high-risk ZIP codes (sample — real impl should use CDC/USDA data)
# Note: This is a representative sample for demo. In production, load from file.
# ---------------------------------------------------------------------------

FOOD_DESERT_ZIP_PREFIXES = {
    # Major US metro food desert ZIP prefix patterns (first 3 digits)
    "021",  # Boston, MA (Roxbury, Dorchester high-risk areas)
    "104",  # Bronx, NY
    "112",  # Brooklyn, NY (East New York, Brownsville)
    "606",  # Chicago, IL (South/West Side)
    "632",  # St. Louis, MO
    "482",  # Detroit, MI
    "701",  # New Orleans, LA
    "372",  # Memphis, TN
    "280",  # Charlotte, NC (lower income areas)
    "302",  # Atlanta, GA (South Atlanta)
    "900",  # Los Angeles, CA (Watts, Compton)
    "770",  # Houston, TX (Third Ward)
}

HIGH_POVERTY_ZIP_PREFIXES = {
    "104", "112", "606", "632", "482", "701", "372",
}


def is_food_desert_zip(postal_code: str) -> bool:
    if not postal_code or len(postal_code) < 3:
        return False
    return postal_code[:3] in FOOD_DESERT_ZIP_PREFIXES


def is_high_poverty_zip(postal_code: str) -> bool:
    if not postal_code or len(postal_code) < 3:
        return False
    return postal_code[:3] in HIGH_POVERTY_ZIP_PREFIXES


# ---------------------------------------------------------------------------
# Charlson Comorbidity Index (CCI) — ICD-10 mapping
# Based on Charlson et al. (1987) updated for ICD-10
# ---------------------------------------------------------------------------

# Maps ICD-10 code prefixes → (CCI weight, condition name)
CCI_ICD10_MAP: list[tuple[list[str], int, str]] = [
    (["I21", "I22", "I25.2"], 1, "Myocardial Infarction"),
    (["I50"], 1, "Congestive Heart Failure"),
    (["I70", "I71", "I73.9", "I77.1", "I99"], 1, "Peripheral Vascular Disease"),
    (["I60", "I61", "I62", "I63", "I64", "G45", "G46"], 1, "Cerebrovascular Disease"),
    (["F00", "F01", "F02", "F03", "G30"], 1, "Dementia"),
    (["J40", "J41", "J42", "J43", "J44", "J45", "J46", "J47", "J60", "J67"], 1, "COPD"),
    (["M05", "M06", "M32", "M33", "M34", "M35"], 1, "Connective Tissue Disease"),
    (["K25", "K26", "K27", "K28"], 1, "Peptic Ulcer Disease"),
    (["K70", "K71", "K73", "K74"], 1, "Mild Liver Disease"),
    (["E10", "E11", "E12", "E13", "E14"], 1, "Diabetes without Complications"),
    (["E10.2", "E10.3", "E10.4", "E11.2", "E11.3", "E11.4"], 2, "Diabetes with Complications"),
    (["G81", "G82", "G83"], 2, "Hemiplegia"),
    (["N18", "N19"], 2, "Moderate/Severe Renal Disease"),
    (["C0", "C1", "C2", "C3", "C4", "C5", "C6", "C7", "C80"], 2, "Solid Tumor (no metastasis)"),
    (["C77", "C78", "C79"], 6, "Metastatic Solid Tumor"),
    (["C81", "C82", "C83", "C84", "C85", "C88", "C96"], 2, "Lymphoma"),
    (["C91", "C92", "C93", "C94", "C95"], 2, "Leukemia"),
    (["K72", "K76.6"], 3, "Moderate/Severe Liver Disease"),
    (["B20", "B21", "B22", "B23", "B24"], 6, "AIDS"),
]


def compute_charlson_index(conditions: list[dict]) -> tuple[int, list[str]]:
    """
    Compute Charlson Comorbidity Index (CCI) from a list of FHIR Condition resources.
    Returns (total_cci_score, list of matched condition names).
    """
    matched: list[str] = []
    total_weight = 0
    already_counted: set[str] = set()

    for condition in conditions:
        icd_code = get_condition_icd_code(condition)
        if not icd_code:
            continue

        for prefixes, weight, name in CCI_ICD10_MAP:
            if name in already_counted:
                continue
            for prefix in prefixes:
                if icd_code.startswith(prefix):
                    matched.append(name)
                    total_weight += weight
                    already_counted.add(name)
                    break
                    
    # Handle overlap: if patient has metastatic tumor, don't also count primary solid tumor
    if "Metastatic Solid Tumor" in already_counted and "Solid Tumor (no metastasis)" in already_counted:
        total_weight -= 2
        matched.remove("Solid Tumor (no metastasis)")
        already_counted.remove("Solid Tumor (no metastasis)")

    return total_weight, matched


def _cci_to_lace_c_score(cci: int) -> int:
    """Map CCI score to LACE C component score (van Walraven 2010)."""
    if cci == 0:
        return 0
    elif cci == 1:
        return 1
    elif cci == 2:
        return 2
    elif cci == 3:
        return 3
    else:
        return 5


# ---------------------------------------------------------------------------
# LACE Score Computation
# Based on van Walraven et al., CMAJ 2010
# ---------------------------------------------------------------------------

@dataclass
class LACEComponents:
    # L — Length of Stay
    length_of_stay_days: int | None
    l_score: int

    # A — Acuity of Admission
    admission_acuity: str  # "emergency", "urgent", "elective", "unknown"
    a_score: int

    # C — Comorbidity (Charlson)
    cci_score: int
    cci_conditions_matched: list[str]
    c_score: int

    # E — ED Visits in 6 months prior
    ed_visits_6mo: int
    e_score: int

    # Total
    total: int
    risk_level: str
    readmission_probability_pct: str
    data_gaps: list[str]


def compute_lace_score(
    current_encounter: dict | None,
    all_encounters: list[dict],
    conditions: list[dict],
) -> LACEComponents:
    """
    Deterministically compute the LACE readmission risk index from FHIR data.
    L = Length of stay, A = Acuity, C = Comorbidity (Charlson), E = ED visits 6mo.
    """
    data_gaps: list[str] = []

    # --- L: Length of Stay ---
    los_days = None
    l_score = 0
    if current_encounter:
        los_days = get_encounter_length_of_stay_days(current_encounter)

    if los_days is None:
        data_gaps.append("Encounter period missing — Length of Stay cannot be computed. Assuming 1 day (score=1).")
        los_days = 1

    if los_days <= 0:
        l_score = 0
    elif los_days == 1:
        l_score = 1
    elif los_days == 2:
        l_score = 2
    elif los_days == 3:
        l_score = 3
    elif los_days <= 6:
        l_score = 4
    elif los_days <= 13:
        l_score = 5
    else:
        l_score = 7

    # --- A: Acuity of Admission ---
    a_score = 0
    acuity = "unknown"
    if current_encounter:
        class_code = (current_encounter.get("class", {}).get("code") or "").upper()
        priority_display = ""
        if current_encounter.get("priority"):
            priority_display = (
                current_encounter["priority"].get("coding", [{}])[0].get("display", "") or ""
            ).lower()

        if class_code in ("EMER", "EMERGENCY") or "emergency" in priority_display or "urgent" in priority_display:
            acuity = "emergency"
            a_score = 3
        elif class_code in ("IMP", "ACUTE", "INPATIENT"):
            # Check admit source for emergency admission clues
            admit_source = (
                current_encounter.get("hospitalization", {})
                .get("admitSource", {})
                .get("coding", [{}])[0]
                .get("code", "")
                or ""
            ).lower()
            if "emd" in admit_source or "emergency" in admit_source:
                acuity = "emergency"
                a_score = 3
            else:
                acuity = "inpatient (assumed urgent)"
                a_score = 3  # Conservative — inpatient is typically not elective
        elif class_code in ("AMB", "ELECTIVE", "OUTPATIENT"):
            acuity = "elective"
            a_score = 0
        else:
            acuity = "unknown (assumed urgent)"
            a_score = 3
            data_gaps.append("Encounter class not clearly elective/emergency. Assuming urgent (score=3) — conservative.")
    else:
        data_gaps.append("No current encounter provided — Acuity cannot be determined. Assuming urgent (score=3).")
        acuity = "unknown (assumed urgent)"
        a_score = 3

    # --- C: Comorbidity (Charlson Comorbidity Index) ---
    cci_score, cci_conditions = compute_charlson_index(conditions)
    c_score = _cci_to_lace_c_score(cci_score)
    if not conditions:
        data_gaps.append("No conditions provided — Charlson Index is 0. Score may be underestimated.")

    # --- E: ED Visits in prior 6 months ---
    ed_visits = 0
    if current_encounter:
        period = current_encounter.get("period", {})
        current_start_str = period.get("start")
        if current_start_str:
            try:
                current_start = date.fromisoformat(current_start_str[:10])
                six_months_ago = current_start - timedelta(days=182)

                for enc in all_encounters:
                    if enc is current_encounter:
                        continue
                    enc_class_code = (enc.get("class", {}).get("code") or "").upper()
                    if enc_class_code not in ("EMER", "EMERGENCY"):
                        continue
                    enc_period = enc.get("period", {})
                    enc_start_str = enc_period.get("start")
                    if not enc_start_str:
                        continue
                    enc_start = date.fromisoformat(enc_start_str[:10])
                    if six_months_ago <= enc_start < current_start:
                        ed_visits += 1
            except Exception:
                data_gaps.append("Could not parse encounter dates for ED visit counting.")
        else:
            data_gaps.append("Current encounter start date missing — ED visits in prior 6 months cannot be counted.")
    else:
        data_gaps.append("No current encounter — ED visit count cannot be computed.")

    e_score = min(ed_visits, 4)  # Capped at 4 per LACE algorithm

    # --- Total ---
    total = l_score + a_score + c_score + e_score

    # Risk stratification
    if total <= 4:
        risk_level = "LOW"
        prob = "~2%"
    elif total <= 9:
        risk_level = "MODERATE"
        prob = "~8%"
    elif total <= 14:
        risk_level = "HIGH"
        prob = "~15-20%"
    else:
        risk_level = "VERY HIGH"
        prob = "~30%+"

    return LACEComponents(
        length_of_stay_days=los_days,
        l_score=l_score,
        admission_acuity=acuity,
        a_score=a_score,
        cci_score=cci_score,
        cci_conditions_matched=cci_conditions,
        c_score=c_score,
        ed_visits_6mo=ed_visits,
        e_score=e_score,
        total=total,
        risk_level=risk_level,
        readmission_probability_pct=prob,
        data_gaps=data_gaps,
    )


def format_lace_score_table(lace: LACEComponents) -> str:
    """Format the LACE breakdown as a markdown table for inclusion in MCP response."""
    conditions_str = (
        ", ".join(lace.cci_conditions_matched) if lace.cci_conditions_matched else "None mapped"
    )
    return f"""| Component | Data Used | Score |
|---|---|---|
| **L — Length of Stay** | {lace.length_of_stay_days} days | {lace.l_score} |
| **A — Acuity** | {lace.admission_acuity} | {lace.a_score} |
| **C — Comorbidities (CCI={lace.cci_score})** | {conditions_str} | {lace.c_score} |
| **E — ED Visits (6mo)** | {lace.ed_visits_6mo} visits | {lace.e_score} |
| **TOTAL** | | **{lace.total}** |

**Risk Level: {lace.risk_level}** — Estimated 30-day readmission probability: {lace.readmission_probability_pct}"""


# ---------------------------------------------------------------------------
# Polypharmacy Risk Score
# ---------------------------------------------------------------------------

@dataclass
class PolypharmacyRisk:
    total_medications: int
    high_alert_medications: list[str]
    risk_level: str
    risk_score: int
    flags: list[str]


def compute_polypharmacy_risk(medications: list[dict], patient_age: int | None) -> PolypharmacyRisk:
    """
    Compute polypharmacy risk from medication list and patient age.
    Risk levels: LOW (0-1), MODERATE (2-3), HIGH (4-5), VERY HIGH (6+)
    """
    med_count = len(medications)
    risk_score = 0
    flags: list[str] = []
    high_alert: list[str] = []

    # Count polypharmacy tiers
    if med_count >= 10:
        risk_score += 3
        flags.append(f"Hyperpolypharmacy: {med_count} medications")
    elif med_count >= 5:
        risk_score += 1
        flags.append(f"Polypharmacy: {med_count} medications")

    # Age risk
    if patient_age and patient_age >= 80:
        risk_score += 2
        flags.append("Age ≥80 — significantly increased risk of adverse drug events")
    elif patient_age and patient_age >= 65:
        risk_score += 1
        flags.append("Age ≥65 — increased risk of adverse drug events")

    # High-alert medications
    for med in medications:
        name = get_medication_name_lower(med)
        for alert_name in HIGH_ALERT_MED_NAMES:
            if alert_name in name:
                high_alert.append(get_medication_name_lower(med))
                break

    if len(high_alert) >= 3:
        risk_score += 2
        flags.append(f"{len(high_alert)} high-alert medications present")
    elif len(high_alert) >= 1:
        risk_score += 1
        flags.append(f"{len(high_alert)} high-alert medication(s) present")

    if risk_score == 0:
        risk_level = "LOW"
    elif risk_score <= 2:
        risk_level = "MODERATE"
    elif risk_score <= 4:
        risk_level = "HIGH"
    else:
        risk_level = "VERY HIGH"

    return PolypharmacyRisk(
        total_medications=med_count,
        high_alert_medications=list(set(high_alert)),
        risk_level=risk_level,
        risk_score=risk_score,
        flags=flags,
    )


# ---------------------------------------------------------------------------
# Deterministic Readmission Gap Detection Rules Engine
# ---------------------------------------------------------------------------

def run_gap_rules_engine(
    patient: dict,
    medications: list[dict],
    service_requests: list[dict],
    appointments: list[dict],
    care_plans: list[dict],
    related_persons: list[dict],
    conditions: list[dict],
    observations: list[dict],
) -> list[ClinicalGap]:
    """
    Run deterministic rules against FHIR data to identify readmission risk gaps.
    Returns a list of ClinicalGap objects — always before LLM analysis.
    """
    gaps: list[ClinicalGap] = []
    sr_descriptions = [get_service_request_description_lower(sr) for sr in service_requests]
    med_names = [get_medication_name_lower(m) for m in medications]
    has_care_plan = len(care_plans) > 0
    patient_age = get_patient_age(patient)
    postal_code = get_patient_postal_code(patient)

    # --- Rule 1: High-alert medications without monitoring orders ---
    for alert_name, alert_config in HIGH_ALERT_MEDS.items():
        if not any(alert_name in med for med in med_names):
            continue
        # Check if monitoring keyword appears in any service request
        monitor_keyword = alert_config["monitor"]
        has_monitoring = any(monitor_keyword in sr_desc for sr_desc in sr_descriptions)
        if not has_monitoring:
            # Check if it's already covered by a care plan (avoid false positives)
            if has_care_plan and any(
                monitor_keyword in cp.get("description", "").lower() or
                monitor_keyword in cp.get("title", "").lower()
                for cp in care_plans
            ):
                continue
            gaps.append(ClinicalGap(
                severity=alert_config["severity"],
                issue=alert_config["gap_message"],
                evidence=f"Found '{alert_name}' in MedicationRequest. No '{monitor_keyword}' found in active ServiceRequests.",
                clinical_risk=alert_config["risk"],
                action=alert_config["action"],
            ))

    # --- Rule 2: No follow-up appointment within 14 days ---
    has_near_term_appointment = False
    if appointments:
        today = date.today()
        for appt in appointments:
            appt_start = appt.get("start")
            if appt_start:
                try:
                    appt_date = date.fromisoformat(appt_start[:10])
                    if (appt_date - today).days <= 14:
                        has_near_term_appointment = True
                        break
                except Exception:
                    pass

    if not has_near_term_appointment:
        gaps.append(ClinicalGap(
            severity="HIGH",
            issue="No follow-up appointment scheduled within 14 days",
            evidence=f"{'No appointments found in FHIR.' if not appointments else 'No appointments within 14 days of today found in Appointment resources.'}",
            clinical_risk="Patients without early follow-up have 2-3x higher 30-day readmission rates. Critical for detecting complications and medication issues.",
            action="Schedule PCP or specialist follow-up within 7–14 days of discharge before patient leaves.",
        ))

    # --- Rule 3: Polypharmacy without documented caregiver ---
    if len(medications) >= 5 and not related_persons:
        gaps.append(ClinicalGap(
            severity="MEDIUM",
            issue=f"{len(medications)} discharge medications with no documented caregiver",
            evidence="≥5 medications in MedicationRequest. No RelatedPerson resource found.",
            clinical_risk="Complex medication regimens without caregiver support are a top driver of post-discharge medication errors and non-adherence.",
            action="Assess caregiver availability. Consider medication reconciliation consult, pill organizer, or home health referral.",
        ))

    # --- Rule 4: Elderly patient with high-fall-risk medications ---
    FALL_RISK_MEDS = [
        "benzodiazepine", "diazepam", "lorazepam", "clonazepam", "alprazolam",
        "opioid", "oxycodone", "hydrocodone", "morphine", "codeine",
        "antipsychotic", "quetiapine", "olanzapine", "haloperidol",
        "antidepressant", "amitriptyline", "trazodone",
        "antihistamine", "diphenhydramine",
        "muscle relaxant", "cyclobenzaprine",
    ]
    if patient_age and patient_age >= 65:
        fall_risk_meds_found = [
            m for m in med_names
            if any(frm in m for frm in FALL_RISK_MEDS)
        ]
        if fall_risk_meds_found:
            gaps.append(ClinicalGap(
                severity="MEDIUM",
                issue=f"Elderly patient (age ≥65) discharged on fall-risk medications",
                evidence=f"Patient age: {patient_age}. Fall-risk medications: {', '.join(fall_risk_meds_found[:3])}.",
                clinical_risk="Falls are the #1 cause of injury-related death in adults over 65. These medications significantly increase fall risk.",
                action="Complete fall risk assessment. Provide fall prevention education. Consider physical therapy consult. Evaluate medication necessity.",
            ))

    # --- Rule 5: SDOH — Food desert with diabetic diet ---
    DIABETIC_CONDITIONS = ["diabetes", "e10", "e11", "e12", "e13", "e14"]
    has_diabetes = any(
        any(dc in get_condition_icd_code(c).lower() or dc in c.get("code", {}).get("text", "").lower()
            for dc in DIABETIC_CONDITIONS)
        for c in conditions
    )
    if has_diabetes and postal_code and is_food_desert_zip(postal_code):
        gaps.append(ClinicalGap(
            severity="MEDIUM",
            issue="Diabetic patient discharged to food desert ZIP code",
            evidence=f"Active diabetes condition. Patient ZIP code {postal_code} classified as food desert area.",
            clinical_risk="Dietary compliance with diabetic diet instructions is significantly reduced when fresh/healthy food access is limited. Elevated readmission risk from uncontrolled glucose.",
            action="Social work consult for food access resources (food banks, SNAP enrollment, meal delivery services). Connect with community health worker.",
        ))

    # --- Rule 6: COPD / CHF without oxygen or home health order ---
    HIGH_MONITORING_CONDITIONS = {
        "i50": "congestive heart failure",
        "j44": "COPD",
        "j45": "asthma",
    }
    for icd_prefix, condition_name in HIGH_MONITORING_CONDITIONS.items():
        has_condition = any(
            get_condition_icd_code(c).lower().startswith(icd_prefix)
            for c in conditions
        )
        if not has_condition:
            continue
        has_home_health = any(
            "home health" in sr_desc or "home care" in sr_desc or "oxygen" in sr_desc
            for sr_desc in sr_descriptions
        )
        if not has_home_health and not has_care_plan:
            gaps.append(ClinicalGap(
                severity="MEDIUM",
                issue=f"Patient with {condition_name} has no home health or monitoring order",
                evidence=f"Active {condition_name} condition found. No home health ServiceRequest or CarePlan found.",
                clinical_risk=f"{condition_name} is a leading cause of 30-day readmission. Without home monitoring, early deterioration goes undetected.",
                action=f"Consider home health referral or telehealth monitoring for {condition_name} management. At minimum, daily weight monitoring for CHF.",
            ))

    return gaps


# ---------------------------------------------------------------------------
# SDOH Analysis (standalone for use in other tools)
# ---------------------------------------------------------------------------

def analyze_sdoh_risks(patient: dict, conditions: list[dict]) -> list[dict]:
    """Return social determinants of health risk flags for this patient."""
    flags: list[dict] = []
    postal_code = get_patient_postal_code(patient)
    age = get_patient_age(patient)

    if postal_code and is_food_desert_zip(postal_code):
        flags.append({
            "risk": "food_access",
            "description": "Patient ZIP code indicates limited food access",
            "zip": postal_code,
        })
    if postal_code and is_high_poverty_zip(postal_code):
        flags.append({
            "risk": "economic",
            "description": "Patient ZIP code indicates high-poverty area",
            "zip": postal_code,
        })
    if age and age >= 80:
        flags.append({
            "risk": "social_isolation",
            "description": "Age ≥80 — elevated risk of social isolation and limited self-care capacity",
        })

    return flags

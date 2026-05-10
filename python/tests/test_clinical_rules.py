"""
Integration tests for DischargeIQ clinical rules engine.

Tests the deterministic logic against known inputs — no FHIR server required.
"""

import pytest
from datetime import date, timedelta

from clinical_rules import (
    ClinicalGap,
    compute_charlson_index,
    compute_lace_score,
    compute_polypharmacy_risk,
    run_gap_rules_engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_condition(icd_code: str, display: str = "", status: str = "active") -> dict:
    return {
        "resourceType": "Condition",
        "clinicalStatus": {"coding": [{"code": status}]},
        "code": {"coding": [{"code": icd_code, "display": display}], "text": display},
    }


def make_medication(name: str, status: str = "active") -> dict:
    return {
        "resourceType": "MedicationRequest",
        "status": status,
        "medicationCodeableConcept": {
            "text": name,
            "coding": [{"display": name, "system": "http://www.nlm.nih.gov/research/umls/rxnorm"}],
        },
        "dosageInstruction": [{"text": f"Take {name} as directed"}],
    }


def make_encounter(
    class_code: str = "IMP",
    start_days_ago: int = 5,
    end_days_ago: int = 0,
) -> dict:
    start = (date.today() - timedelta(days=start_days_ago)).isoformat()
    end = (date.today() - timedelta(days=end_days_ago)).isoformat()
    return {
        "resourceType": "Encounter",
        "status": "finished",
        "class": {"code": class_code, "display": class_code},
        "period": {"start": start, "end": end},
    }


def make_appointment(days_from_now: int = 10) -> dict:
    appt_date = (date.today() + timedelta(days=days_from_now)).isoformat()
    return {
        "resourceType": "Appointment",
        "status": "booked",
        "start": f"{appt_date}T09:00:00Z",
        "description": "Follow-up appointment",
    }


def make_service_request(description: str) -> dict:
    return {
        "resourceType": "ServiceRequest",
        "status": "active",
        "intent": "order",
        "code": {"text": description, "coding": [{"display": description}]},
    }


def make_patient(postal_code: str = "10001", language_code: str = "en") -> dict:
    return {
        "resourceType": "Patient",
        "name": [{"given": ["John"], "family": "Doe"}],
        "birthDate": "1955-06-15",
        "gender": "male",
        "address": [{"postalCode": postal_code, "city": "New York"}],
        "communication": [{"language": {"coding": [{"code": language_code}]}}],
    }


# ---------------------------------------------------------------------------
# Charlson Comorbidity Index
# ---------------------------------------------------------------------------

class TestCharlsonIndex:
    def test_no_conditions_returns_zero(self):
        score, matched = compute_charlson_index([])
        assert score == 0
        assert matched == []

    def test_chf_scores_one(self):
        conditions = [make_condition("I50.9", "Congestive Heart Failure")]
        score, matched = compute_charlson_index(conditions)
        assert score == 1
        assert "Congestive Heart Failure" in matched

    def test_diabetes_without_complications_scores_one(self):
        conditions = [make_condition("E11.9", "Type 2 Diabetes")]
        score, matched = compute_charlson_index(conditions)
        assert score == 1

    def test_multiple_conditions_sum_correctly(self):
        conditions = [
            make_condition("I50.9", "CHF"),
            make_condition("E11.9", "T2DM"),
            make_condition("N18.3", "CKD Stage 3"),
        ]
        score, _ = compute_charlson_index(conditions)
        # CHF=1, DM=1, CKD=2
        assert score == 4

    def test_metastatic_cancer_scores_six(self):
        conditions = [make_condition("C78.9", "Metastatic Cancer")]
        score, matched = compute_charlson_index(conditions)
        assert score == 6
        assert "Metastatic Solid Tumor" in matched

    def test_no_duplicate_counting(self):
        """Same category matched by two ICD codes should only count once."""
        conditions = [
            make_condition("I50.9", "CHF"),
            make_condition("I50.1", "Left heart failure"),  # same CCI category
        ]
        score, _ = compute_charlson_index(conditions)
        assert score == 1  # Not 2


# ---------------------------------------------------------------------------
# LACE Score
# ---------------------------------------------------------------------------

class TestLACEScore:
    def test_low_risk_patient(self):
        """2-day elective admission, no comorbidities, no ED visits → LOW risk."""
        encounter = make_encounter(class_code="AMB", start_days_ago=2, end_days_ago=0)
        lace = compute_lace_score(encounter, [encounter], [])
        assert lace.risk_level == "LOW"
        assert lace.total <= 4

    def test_high_risk_patient(self):
        """Long stay + emergency + multiple comorbidities → HIGH or VERY HIGH."""
        encounter = make_encounter(class_code="EMER", start_days_ago=10, end_days_ago=0)
        conditions = [
            make_condition("I50.9", "CHF"),
            make_condition("E11.9", "T2DM"),
            make_condition("N18.3", "CKD"),
            make_condition("I63.9", "Stroke"),
        ]
        lace = compute_lace_score(encounter, [encounter], conditions)
        assert lace.risk_level in ("HIGH", "VERY HIGH")
        assert lace.total >= 10

    def test_los_computation(self):
        """6-day admission should give L=4."""
        encounter = make_encounter(class_code="IMP", start_days_ago=6, end_days_ago=0)
        lace = compute_lace_score(encounter, [encounter], [])
        assert lace.l_score == 4
        assert lace.length_of_stay_days == 6

    def test_ed_visit_counting(self):
        """2 ED visits in prior 6 months should give E=2."""
        current = make_encounter(class_code="IMP", start_days_ago=5, end_days_ago=0)
        # Two ED visits before current admission
        ed1 = make_encounter(class_code="EMER", start_days_ago=60, end_days_ago=55)
        ed2 = make_encounter(class_code="EMER", start_days_ago=120, end_days_ago=115)
        lace = compute_lace_score(current, [current, ed1, ed2], [])
        assert lace.ed_visits_6mo == 2
        assert lace.e_score == 2

    def test_emergency_acuity_scores_three(self):
        encounter = make_encounter(class_code="EMER", start_days_ago=3, end_days_ago=0)
        lace = compute_lace_score(encounter, [encounter], [])
        assert lace.a_score == 3

    def test_no_encounter_uses_defaults(self):
        """No encounter provided should not crash, should note data gaps."""
        lace = compute_lace_score(None, [], [])
        assert len(lace.data_gaps) > 0
        assert lace.total >= 0


# ---------------------------------------------------------------------------
# Polypharmacy Risk
# ---------------------------------------------------------------------------

class TestPolypharmacyRisk:
    def test_low_med_count_is_low_risk(self):
        meds = [make_medication("Lisinopril"), make_medication("Metoprolol")]
        risk = compute_polypharmacy_risk(meds, patient_age=55)
        assert risk.risk_level == "LOW"

    def test_five_meds_triggers_polypharmacy(self):
        meds = [make_medication(f"Drug{i}") for i in range(5)]
        risk = compute_polypharmacy_risk(meds, patient_age=50)
        assert "Polypharmacy" in " ".join(risk.flags) or risk.risk_level != "LOW"

    def test_elderly_increases_risk(self):
        meds = [make_medication(f"Drug{i}") for i in range(5)]
        risk_young = compute_polypharmacy_risk(meds, patient_age=40)
        risk_elderly = compute_polypharmacy_risk(meds, patient_age=75)
        assert risk_elderly.risk_score > risk_young.risk_score

    def test_high_alert_meds_detected(self):
        meds = [make_medication("Warfarin 5mg"), make_medication("Insulin glargine")]
        risk = compute_polypharmacy_risk(meds, patient_age=70)
        assert len(risk.high_alert_medications) >= 1


# ---------------------------------------------------------------------------
# Gap Rules Engine
# ---------------------------------------------------------------------------

class TestGapRulesEngine:
    def test_warfarin_without_inr_flags_high_gap(self):
        patient = make_patient()
        meds = [make_medication("Warfarin 5mg daily")]
        gaps = run_gap_rules_engine(
            patient=patient, medications=meds, service_requests=[],
            appointments=[], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        high_gaps = [g for g in gaps if g.severity == "HIGH" and "arfarin" in g.issue]
        assert len(high_gaps) >= 1

    def test_warfarin_with_inr_no_gap(self):
        patient = make_patient()
        meds = [make_medication("Warfarin 5mg daily")]
        sr = make_service_request("INR monitoring lab draw")
        gaps = run_gap_rules_engine(
            patient=patient, medications=meds, service_requests=[sr],
            appointments=[], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        warfarin_gaps = [g for g in gaps if "arfarin" in g.issue]
        assert len(warfarin_gaps) == 0

    def test_no_followup_appointment_flags_gap(self):
        patient = make_patient()
        gaps = run_gap_rules_engine(
            patient=patient, medications=[], service_requests=[],
            appointments=[], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        appt_gaps = [g for g in gaps if "follow-up" in g.issue.lower() or "appointment" in g.issue.lower()]
        assert len(appt_gaps) >= 1

    def test_near_term_appointment_clears_gap(self):
        patient = make_patient()
        appt = make_appointment(days_from_now=7)
        gaps = run_gap_rules_engine(
            patient=patient, medications=[], service_requests=[],
            appointments=[appt], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        appt_gaps = [g for g in gaps if "follow-up" in g.issue.lower() or "appointment" in g.issue.lower()]
        assert len(appt_gaps) == 0

    def test_polypharmacy_without_caregiver_flags_gap(self):
        patient = make_patient()
        meds = [make_medication(f"Drug{i}") for i in range(6)]
        gaps = run_gap_rules_engine(
            patient=patient, medications=meds, service_requests=[],
            appointments=[], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        poly_gaps = [g for g in gaps if "caregiver" in g.issue.lower() or "medication" in g.issue.lower()]
        assert len(poly_gaps) >= 1

    def test_insulin_without_glucose_monitoring_flags(self):
        patient = make_patient()
        meds = [make_medication("Insulin glargine 20 units")]
        gaps = run_gap_rules_engine(
            patient=patient, medications=meds, service_requests=[],
            appointments=[], care_plans=[], related_persons=[],
            conditions=[], observations=[],
        )
        insulin_gaps = [g for g in gaps if "nsulin" in g.issue]
        assert len(insulin_gaps) >= 1

    def test_food_desert_zip_with_diabetes_flags_sdoh(self):
        patient = make_patient(postal_code="10451")  # High-poverty Bronx ZIP
        conditions = [make_condition("E11.9", "Type 2 Diabetes")]
        gaps = run_gap_rules_engine(
            patient=patient, medications=[], service_requests=[],
            appointments=[], care_plans=[], related_persons=[],
            conditions=conditions, observations=[],
        )
        sdoh_gaps = [g for g in gaps if "food" in g.issue.lower() or "desert" in g.issue.lower()]
        assert len(sdoh_gaps) >= 1

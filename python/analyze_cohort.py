import json
import glob
from collections import Counter
from clinical_rules import run_gap_rules_engine, compute_lace_score, compute_polypharmacy_risk

def analyze_bundles():
    bundle_files = glob.glob("../fhir_bundles/*.json")
    if not bundle_files:
        print("No bundles found.")
        return

    total_patients = len(bundle_files)
    total_gaps = 0
    high_gaps = 0
    medium_gaps = 0
    low_gaps = 0
    gap_issues = Counter()
    
    lace_scores = []
    high_lace_count = 0
    
    poly_high_count = 0

    for file_path in bundle_files:
        with open(file_path, "r", encoding="utf-8") as f:
            bundle = json.load(f)
            
        resources = [entry["resource"] for entry in bundle.get("entry", []) if "resource" in entry]
        
        patient = next((r for r in resources if r["resourceType"] == "Patient"), {})
        conditions = [r for r in resources if r["resourceType"] == "Condition"]
        medications = [r for r in resources if r["resourceType"] == "MedicationRequest"]
        service_requests = [r for r in resources if r["resourceType"] == "ServiceRequest"]
        appointments = [r for r in resources if r["resourceType"] == "Appointment"]
        care_plans = [r for r in resources if r["resourceType"] == "CarePlan"]
        related_persons = [r for r in resources if r["resourceType"] == "RelatedPerson"]
        observations = [r for r in resources if r["resourceType"] == "Observation"]
        encounters = [r for r in resources if r["resourceType"] == "Encounter"]
        
        # Run gap engine
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
        
        total_gaps += len(gaps)
        for g in gaps:
            if g.severity == "HIGH": high_gaps += 1
            elif g.severity == "MEDIUM": medium_gaps += 1
            else: low_gaps += 1
            gap_issues[g.issue] += 1
            
        # LACE
        from fhir_client import find_current_encounter
        current_encounter = find_current_encounter(encounters)
        lace = compute_lace_score(current_encounter, encounters, conditions)
        lace_scores.append(lace.total)
        if lace.risk_level in ("HIGH", "VERY HIGH"):
            high_lace_count += 1
            
        # Polypharmacy
        from summarizers import get_patient_age
        age = get_patient_age(patient)
        poly = compute_polypharmacy_risk(medications, age)
        if poly.risk_level in ("HIGH", "VERY HIGH"):
            poly_high_count += 1

    print(f"--- Cohort Analysis ({total_patients} patients) ---")
    print(f"Average LACE Score: {sum(lace_scores)/total_patients:.1f}")
    print(f"Patients with HIGH/VERY HIGH LACE Risk: {high_lace_count} ({(high_lace_count/total_patients)*100:.1f}%)")
    print(f"Patients with HIGH/VERY HIGH Polypharmacy Risk: {poly_high_count} ({(poly_high_count/total_patients)*100:.1f}%)")
    print(f"Total Gaps Detected: {total_gaps} ({total_gaps/total_patients:.1f} per patient)")
    print(f"  - HIGH: {high_gaps}")
    print(f"  - MEDIUM: {medium_gaps}")
    print(f"  - LOW: {low_gaps}")
    print("\nTop 5 Most Common Gaps:")
    for issue, count in gap_issues.most_common(5):
        print(f"  - {issue}: {count}")

if __name__ == "__main__":
    analyze_bundles()

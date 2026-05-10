"""
Shared FHIR R4 resource summarization functions.

All tools import from here — single source of truth. No duplication.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Patient
# ---------------------------------------------------------------------------

def summarize_patient(
    patient: dict,
    *,
    include_address: bool = False,
    include_language: bool = False,
) -> dict:
    name_parts = patient.get("name", [{}])[0] if patient.get("name") else {}
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    result: dict = {
        "name": f"{given} {family}".strip() or "Unknown",
        "birth_date": patient.get("birthDate"),
        "gender": patient.get("gender"),
    }
    if include_language:
        result["language"] = _extract_language(patient)
    if include_address:
        result["address"] = _extract_address(patient)
        result["postal_code"] = _extract_postal_code(patient)
    return result


def _extract_language(patient: dict) -> str:
    comms = patient.get("communication", [])
    if comms:
        lang = comms[0].get("language", {})
        return lang.get("text") or (lang.get("coding", [{}])[0].get("display", "English"))
    return "English"


def _extract_language_code(patient: dict) -> str:
    """Returns the BCP-47 language code (e.g. 'es', 'zh', 'ar')."""
    comms = patient.get("communication", [])
    if comms:
        lang = comms[0].get("language", {})
        return lang.get("coding", [{}])[0].get("code", "en")
    return "en"


def _extract_address(patient: dict) -> str:
    addresses = patient.get("address", [])
    if not addresses:
        return "Unknown"
    addr = addresses[0]
    parts = addr.get("line", []) + [
        addr.get("city", ""),
        addr.get("state", ""),
        addr.get("postalCode", ""),
    ]
    return ", ".join(p for p in parts if p)


def _extract_postal_code(patient: dict) -> str:
    addresses = patient.get("address", [])
    if addresses:
        return addresses[0].get("postalCode", "")
    return ""


def get_patient_language_code(patient: dict) -> str:
    """Public accessor for language code — used by tools for multilingual routing."""
    return _extract_language_code(patient)


def get_patient_postal_code(patient: dict) -> str:
    """Public accessor for postal code — used by SDOH gap detection."""
    return _extract_postal_code(patient)


def get_patient_age(patient: dict) -> int | None:
    """Compute patient age from birthDate. Returns None if not available."""
    from datetime import date
    birth_date_str = patient.get("birthDate")
    if not birth_date_str:
        return None
    try:
        parts = birth_date_str.split("-")
        birth = date(int(parts[0]), int(parts[1]) if len(parts) > 1 else 1, int(parts[2]) if len(parts) > 2 else 1)
        today = date.today()
        return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Encounter
# ---------------------------------------------------------------------------

def summarize_encounter(encounter: dict, *, include_hospitalization: bool = False) -> dict:
    enc_class = encounter.get("class", {})
    result: dict = {
        "status": encounter.get("status"),
        "class_code": enc_class.get("code"),
        "class_display": enc_class.get("display") or enc_class.get("code"),
        "period": encounter.get("period"),
        "reason": [
            rc.get("text") or (rc.get("coding", [{}])[0].get("display"))
            for rc in encounter.get("reasonCode", [])
        ],
    }
    if include_hospitalization:
        hosp = encounter.get("hospitalization", {})
        result["admit_source"] = hosp.get("admitSource", {}).get("text")
        result["discharge_disposition"] = hosp.get("dischargeDisposition", {}).get("text")
        result["priority"] = (
            encounter.get("priority", {}).get("coding", [{}])[0].get("display")
            if encounter.get("priority")
            else None
        )
    return result


def get_encounter_length_of_stay_days(encounter: dict) -> int | None:
    """Compute length of stay in days from Encounter.period."""
    from datetime import date
    period = encounter.get("period", {})
    start_str = period.get("start")
    end_str = period.get("end")
    if not start_str or not end_str:
        return None
    try:
        start = date.fromisoformat(start_str[:10])
        end = date.fromisoformat(end_str[:10])
        return max((end - start).days, 1)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------

def summarize_condition(condition: dict, *, include_icd: bool = False) -> dict:
    code = condition.get("code", {})
    coding = code.get("coding", [{}])[0] if code.get("coding") else {}
    result: dict = {
        "name": code.get("text") or coding.get("display", "Unknown"),
        "clinical_status": condition.get("clinicalStatus", {}).get("coding", [{}])[0].get("code"),
        "onset": condition.get("onsetDateTime"),
    }
    if include_icd:
        result["icd_code"] = coding.get("code", "")
        result["code_system"] = coding.get("system", "")
        result["verification_status"] = (
            condition.get("verificationStatus", {}).get("coding", [{}])[0].get("code")
            if condition.get("verificationStatus")
            else None
        )
    return result


def get_condition_icd_code(condition: dict) -> str:
    coding = condition.get("code", {}).get("coding", [{}])
    return coding[0].get("code", "") if coding else ""


# ---------------------------------------------------------------------------
# MedicationRequest
# ---------------------------------------------------------------------------

def summarize_medication(med: dict, *, include_codes: bool = False) -> dict:
    med_code = med.get("medicationCodeableConcept", {})
    coding = med_code.get("coding", [{}])[0] if med_code.get("coding") else {}
    dosage = med.get("dosageInstruction", [{}])[0] if med.get("dosageInstruction") else {}
    route = dosage.get("route", {})
    result: dict = {
        "name": med_code.get("text") or coding.get("display", "Unknown"),
        "dosage_text": dosage.get("text", ""),
        "timing": dosage.get("timing", {}).get("repeat", {}),
        "status": med.get("status"),
        "reason": (
            [ref.get("display", "") for ref in med.get("reasonReference", [])]
            or [rc.get("text", "") for rc in med.get("reasonCode", [])]
        ),
    }
    if include_codes:
        result["rxnorm_code"] = coding.get("code", "")
        result["code_system"] = coding.get("system", "")
        result["route"] = (
            route.get("text")
            or (route.get("coding", [{}])[0].get("display", "") if route.get("coding") else "")
        )
        result["supply_duration"] = med.get("dispenseRequest", {}).get("expectedSupplyDuration", {})
    return result


def get_medication_name_lower(med: dict) -> str:
    """Returns lowercase medication name for rule matching."""
    med_code = med.get("medicationCodeableConcept", {})
    name = med_code.get("text") or (med_code.get("coding", [{}])[0].get("display", ""))
    return name.lower()


# ---------------------------------------------------------------------------
# AllergyIntolerance
# ---------------------------------------------------------------------------

def summarize_allergy(allergy: dict, *, include_reactions: bool = True) -> dict:
    code = allergy.get("code", {})
    coding = code.get("coding", [{}])[0] if code.get("coding") else {}
    result: dict = {
        "substance": code.get("text") or coding.get("display", "Unknown"),
        "criticality": allergy.get("criticality"),
        "type": allergy.get("type"),
        "category": allergy.get("category", []),
    }
    if include_reactions:
        reactions = []
        for r in allergy.get("reaction", []):
            for m in r.get("manifestation", []):
                display = m.get("coding", [{}])[0].get("display") or m.get("text", "")
                if display:
                    reactions.append(display)
        result["reactions"] = reactions
        result["rxnorm_code"] = coding.get("code", "")
    return result


def get_allergy_substance_lower(allergy: dict) -> str:
    code = allergy.get("code", {})
    name = code.get("text") or (code.get("coding", [{}])[0].get("display", ""))
    return name.lower()


# ---------------------------------------------------------------------------
# Procedure
# ---------------------------------------------------------------------------

def summarize_procedure(proc: dict) -> dict:
    code = proc.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "date": proc.get("performedDateTime") or proc.get("performedPeriod", {}).get("start"),
        "status": proc.get("status"),
    }


# ---------------------------------------------------------------------------
# Observation
# ---------------------------------------------------------------------------

def summarize_observation(obs: dict) -> dict:
    code = obs.get("code", {})
    value = obs.get("valueQuantity", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "value": (
            f"{value.get('value', '')} {value.get('unit', '')}".strip()
            if value
            else obs.get("valueString", "")
        ),
        "date": obs.get("effectiveDateTime", ""),
        "interpretation": [i.get("text", "") for i in obs.get("interpretation", [])],
    }


# ---------------------------------------------------------------------------
# ServiceRequest
# ---------------------------------------------------------------------------

def summarize_service_request(sr: dict) -> dict:
    code = sr.get("code", {})
    return {
        "description": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "status": sr.get("status"),
        "intent": sr.get("intent"),
        "priority": sr.get("priority"),
    }


def get_service_request_description_lower(sr: dict) -> str:
    code = sr.get("code", {})
    desc = code.get("text") or (code.get("coding", [{}])[0].get("display", ""))
    return desc.lower()


# ---------------------------------------------------------------------------
# Appointment
# ---------------------------------------------------------------------------

def summarize_appointment(appt: dict) -> dict:
    return {
        "status": appt.get("status"),
        "start": appt.get("start"),
        "description": appt.get("description", ""),
        "type": [t.get("text") for t in appt.get("appointmentType", {}).get("coding", [])],
    }


# ---------------------------------------------------------------------------
# RelatedPerson
# ---------------------------------------------------------------------------

def summarize_related_person(rp: dict) -> dict:
    name_parts = rp.get("name", [{}])[0] if rp.get("name") else {}
    given = " ".join(name_parts.get("given", []))
    family = name_parts.get("family", "")
    relationship = (
        rp.get("relationship", [{}])[0].get("coding", [{}])[0].get("display")
        or rp.get("relationship", [{}])[0].get("text", "Unknown")
        if rp.get("relationship")
        else "Unknown"
    )
    return {
        "name": f"{given} {family}".strip(),
        "relationship": relationship,
    }


# ---------------------------------------------------------------------------
# CarePlan
# ---------------------------------------------------------------------------

def summarize_care_plan(cp: dict) -> dict:
    return {
        "title": cp.get("title", ""),
        "status": cp.get("status"),
        "description": cp.get("description", ""),
        "categories": [
            cat.get("text") or (cat.get("coding", [{}])[0].get("display", ""))
            for cat in cp.get("category", [])
        ],
    }


# ---------------------------------------------------------------------------
# CareTeam
# ---------------------------------------------------------------------------

def summarize_care_team(ct: dict) -> dict:
    participants = []
    for p in ct.get("participant", []):
        role = p.get("role", [{}])[0].get("text", "") if p.get("role") else ""
        member = p.get("member", {}).get("display", "")
        if member:
            participants.append({"role": role, "name": member})
    return {
        "name": ct.get("name", ""),
        "participants": participants,
    }


# ---------------------------------------------------------------------------
# DiagnosticReport
# ---------------------------------------------------------------------------

def summarize_diagnostic_report(dr: dict) -> dict:
    code = dr.get("code", {})
    return {
        "name": code.get("text") or (code.get("coding", [{}])[0].get("display", "Unknown")),
        "status": dr.get("status"),
        "date": dr.get("effectiveDateTime", ""),
        "conclusion": dr.get("conclusion", ""),
    }

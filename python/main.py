import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from clinical_rules import (
    compute_lace_score,
    compute_polypharmacy_risk,
    run_gap_rules_engine,
)
from mcp_instance import mcp
from summarizers import get_patient_age, summarize_patient

DEMO_BUNDLE_PATH = Path(__file__).resolve().parent.parent / "fhir_bundles" / "patient-001_bundle.json"

VERSION = "2.0.0"
TOOLS = [
    "GeneratePersonalizedDischargePlan",
    "IdentifyReadmissionRiskGaps",
    "DraftTransitionCareSummary",
    "CheckDrugInteractions",
    "CalculateLACEScore",
    "GenerateMedicationCard",
    "GetDischargeReadinessDashboard",
]


class AcceptHeaderMiddleware(BaseHTTPMiddleware):
    """Ensures the Accept header includes text/event-stream for MCP compatibility.
    Some MCP clients (like Prompt Opinion) may not send the required Accept header."""

    async def dispatch(self, request: Request, call_next):
        accept = request.headers.get("accept", "")
        if "text/event-stream" not in accept:
            # Mutate the scope headers to include the required Accept
            headers = dict(request.scope["headers"])
            new_accept = f"{accept}, text/event-stream" if accept else "application/json, text/event-stream"
            # Headers in ASGI scope are list of tuples of bytes
            raw_headers = [
                (k, v) for k, v in request.scope["headers"] if k != b"accept"
            ]
            raw_headers.append((b"accept", new_accept.encode()))
            request.scope["headers"] = raw_headers
        response = await call_next(request)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield


app = FastAPI(lifespan=lifespan, title="DischargeIQ MCP Server", version=VERSION)

app.add_middleware(AcceptHeaderMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return JSONResponse({
        "status": "healthy",
        "service": "DischargeIQ",
        "version": VERSION,
        "tool_count": len(TOOLS),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.get("/tools")
async def list_tools():
    return JSONResponse({
        "service": "DischargeIQ",
        "version": VERSION,
        "tools": TOOLS,
        "description": "AI-powered hospital discharge workflow tools for the PO Community MCP.",
    })


def _extract_resources_from_bundle(bundle: dict) -> dict[str, list[dict]]:
    """Extract FHIR resources from a transaction bundle, grouped by resourceType."""
    resources: dict[str, list[dict]] = {}
    for entry in bundle.get("entry", []):
        resource = entry.get("resource", {})
        rtype = resource.get("resourceType", "")
        if rtype:
            resources.setdefault(rtype, []).append(resource)
    return resources


@app.get("/demo")
async def demo_dashboard():
    """Pre-loaded demo for judges. Runs full deterministic scoring on a synthetic
    patient bundle from disk — no FHIR server needed."""

    if not DEMO_BUNDLE_PATH.exists():
        return JSONResponse({"error": "Demo bundle not found"}, status_code=500)

    bundle = json.loads(DEMO_BUNDLE_PATH.read_text(encoding="utf-8"))
    resources = _extract_resources_from_bundle(bundle)

    patient = resources.get("Patient", [{}])[0]
    encounters = resources.get("Encounter", [])
    conditions = resources.get("Condition", [])
    medications = resources.get("MedicationRequest", [])
    observations = resources.get("Observation", [])
    related_persons = resources.get("RelatedPerson", [])
    service_requests = resources.get("ServiceRequest", [])
    appointments = resources.get("Appointment", [])
    care_plans = resources.get("CarePlan", [])

    current_encounter = encounters[0] if encounters else None
    patient_info = summarize_patient(patient, include_address=True, include_language=True)
    patient_age = get_patient_age(patient)

    lace = compute_lace_score(current_encounter, encounters, conditions)
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

    return JSONResponse({
        "demo": True,
        "note": "Live demo using synthetic patient data. All scores computed deterministically — no AI, no FHIR server needed.",
        "patient": {
            "name": patient_info.get("name", "Unknown"),
            "age": patient_age,
            "gender": patient.get("gender", "unknown"),
            "language": patient_info.get("language", "English"),
            "conditions": [c.get("code", {}).get("text", "Unknown") for c in conditions],
        },
        "lace_score": {
            "total": lace.total,
            "risk_tier": lace.risk_level,
            "readmission_probability": lace.readmission_probability_pct,
            "components": {
                "L_length_of_stay": {"days": lace.length_of_stay_days, "score": lace.l_score},
                "A_acuity": {"value": lace.admission_acuity, "score": lace.a_score},
                "C_comorbidity": {"charlson_index": lace.cci_score, "score": lace.c_score},
                "E_ed_visits_6mo": {"count": lace.ed_visits_6mo, "score": lace.e_score},
            },
        },
        "polypharmacy": {
            "risk_level": poly_risk.risk_level,
            "medication_count": poly_risk.total_medications,
            "high_alert_medications": poly_risk.high_alert_medications,
            "flags": poly_risk.flags,
        },
        "care_gaps": [
            {"severity": g.severity, "issue": g.issue, "action": g.action, "source": "deterministic"}
            for g in gaps
        ],
        "tools_available": len(TOOLS),
        "architecture": "Hybrid Deterministic + AI",
        "confidence_tags": ["[DETERMINISTIC]", "[AI-INTERPRETED]", "[FDA-SOURCED]"],
    })


app.mount("/", mcp.streamable_http_app())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

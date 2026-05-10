import logging

import httpx

logger = logging.getLogger(__name__)

# Shared connection pool limits — prevents creating new TCP connections per request
_LIMITS = httpx.Limits(max_keepalive_connections=10, max_connections=20)
_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


class FhirClient:
    """Async FHIR R4 client with connection pooling and SHARP bearer token support."""

    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers: dict[str, str] = {"Accept": "application/fhir+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"
        # Shared client — reuses connections across all requests in this invocation
        self._client = httpx.AsyncClient(limits=_LIMITS, timeout=_TIMEOUT, headers=self._headers)

    async def close(self) -> None:
        """Close the underlying HTTP client. Call when done with this instance."""
        await self._client.aclose()

    def _build_url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict | None:
        url = self._build_url(path)
        try:
            response = await self._client.get(url, params=params)
            if response.status_code == 404:
                logger.debug("FHIR 404: %s", url)
                return None
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            logger.warning("FHIR HTTP error %s for %s: %s", e.response.status_code, url, e)
            raise
        except httpx.RequestError as e:
            logger.error("FHIR request error for %s: %s", url, e)
            return None

    async def read(self, path: str) -> dict | None:
        return await self._get(path)

    async def search(
        self,
        resource_type: str,
        search_parameters: dict[str, str] | None = None,
    ) -> dict | None:
        return await self._get(resource_type, params=search_parameters)

    async def get_patient(self, patient_id: str) -> dict | None:
        return await self.read(f"Patient/{patient_id}")

    async def get_conditions(self, patient_id: str) -> list[dict]:
        bundle = await self.search("Condition", {"patient": patient_id, "clinical-status": "active"})
        return self._extract_entries(bundle, "Condition")

    async def get_medications(self, patient_id: str, encounter_id: str | None = None) -> list[dict]:
        params: dict[str, str] = {"patient": patient_id, "status": "active"}
        if encounter_id:
            params["encounter"] = encounter_id
        bundle = await self.search("MedicationRequest", params)
        return self._extract_entries(bundle, "MedicationRequest")

    async def get_encounter(self, encounter_id: str) -> dict | None:
        return await self.read(f"Encounter/{encounter_id}")

    async def get_allergies(self, patient_id: str) -> list[dict]:
        bundle = await self.search("AllergyIntolerance", {"patient": patient_id})
        return self._extract_entries(bundle, "AllergyIntolerance")

    async def get_observations(self, patient_id: str, category: str | None = None) -> list[dict]:
        params: dict[str, str] = {"patient": patient_id, "_sort": "-date", "_count": "20"}
        if category:
            params["category"] = category
        bundle = await self.search("Observation", params)
        return self._extract_entries(bundle, "Observation")

    async def get_procedures(self, patient_id: str, encounter_id: str | None = None) -> list[dict]:
        params: dict[str, str] = {"patient": patient_id}
        if encounter_id:
            params["encounter"] = encounter_id
        bundle = await self.search("Procedure", params)
        return self._extract_entries(bundle, "Procedure")

    async def get_service_requests(self, patient_id: str) -> list[dict]:
        bundle = await self.search("ServiceRequest", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle, "ServiceRequest")

    async def get_appointments(self, patient_id: str) -> list[dict]:
        bundle = await self.search("Appointment", {"patient": patient_id, "status": "booked"})
        return self._extract_entries(bundle, "Appointment")

    async def get_related_persons(self, patient_id: str) -> list[dict]:
        bundle = await self.search("RelatedPerson", {"patient": patient_id})
        return self._extract_entries(bundle, "RelatedPerson")

    async def get_care_plans(self, patient_id: str) -> list[dict]:
        bundle = await self.search("CarePlan", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle, "CarePlan")

    async def get_care_teams(self, patient_id: str) -> list[dict]:
        bundle = await self.search("CareTeam", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle, "CareTeam")

    async def get_diagnostic_reports(self, patient_id: str) -> list[dict]:
        bundle = await self.search(
            "DiagnosticReport",
            {"patient": patient_id, "_sort": "-date", "_count": "10"},
        )
        return self._extract_entries(bundle, "DiagnosticReport")

    async def get_all_encounters(self, patient_id: str) -> list[dict]:
        bundle = await self.search(
            "Encounter",
            {"patient": patient_id, "_sort": "-date", "_count": "50"},
        )
        return self._extract_entries(bundle, "Encounter")

    def _extract_entries(self, bundle: dict | None, resource_type: str = "resource") -> list[dict]:
        if not bundle:
            logger.debug("No bundle returned for %s search.", resource_type)
            return []
        if not bundle.get("entry"):
            logger.debug("Empty bundle (no entries) for %s search.", resource_type)
            return []
        return [entry.get("resource", {}) for entry in bundle["entry"]]


def find_current_encounter(all_encounters: list[dict]) -> dict | None:
    """Auto-detect the current/most recent encounter when no encounter_id is provided.

    Priority:
    1. In-progress encounter (patient is currently admitted)
    2. Most recently started finished/completed encounter
    3. None if no encounters exist

    This is CRITICAL — the PO platform agent almost never passes an encounter_id.
    Without this, LACE scores and dashboards return garbage.
    """
    if not all_encounters:
        return None

    # Priority 1: in-progress encounter
    for enc in all_encounters:
        if enc.get("status") in ("in-progress", "arrived", "triaged"):
            return enc

    # Priority 2: most recent finished encounter by start date
    finished = [e for e in all_encounters if e.get("status") in ("finished", "completed", "discharged")]
    if finished:
        def _get_start(enc: dict) -> str:
            return enc.get("period", {}).get("start", "") or ""
        finished.sort(key=_get_start, reverse=True)
        return finished[0]

    # Priority 3: any encounter at all (sorted by date already from FHIR)
    return all_encounters[0] if all_encounters else None


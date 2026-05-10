import httpx


class FhirClient:
    def __init__(self, base_url: str, token: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def _build_url(self, path: str) -> str:
        path = path.lstrip("/")
        return f"{self.base_url}/{path}"

    async def _get(self, path: str, params: dict[str, str] | None = None) -> dict | None:
        headers = {"Accept": "application/fhir+json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        url = self._build_url(path)
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(url, headers=headers, params=params)
                if response.status_code == 404:
                    return None
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError:
                raise

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
        return self._extract_entries(bundle)

    async def get_medications(self, patient_id: str, encounter_id: str | None = None) -> list[dict]:
        params = {"patient": patient_id, "status": "active"}
        if encounter_id:
            params["encounter"] = encounter_id
        bundle = await self.search("MedicationRequest", params)
        return self._extract_entries(bundle)

    async def get_encounter(self, encounter_id: str) -> dict | None:
        return await self.read(f"Encounter/{encounter_id}")

    async def get_allergies(self, patient_id: str) -> list[dict]:
        bundle = await self.search("AllergyIntolerance", {"patient": patient_id})
        return self._extract_entries(bundle)

    async def get_observations(self, patient_id: str, category: str | None = None) -> list[dict]:
        params: dict[str, str] = {"patient": patient_id, "_sort": "-date", "_count": "20"}
        if category:
            params["category"] = category
        bundle = await self.search("Observation", params)
        return self._extract_entries(bundle)

    async def get_procedures(self, patient_id: str, encounter_id: str | None = None) -> list[dict]:
        params: dict[str, str] = {"patient": patient_id}
        if encounter_id:
            params["encounter"] = encounter_id
        bundle = await self.search("Procedure", params)
        return self._extract_entries(bundle)

    async def get_service_requests(self, patient_id: str) -> list[dict]:
        bundle = await self.search("ServiceRequest", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle)

    async def get_appointments(self, patient_id: str) -> list[dict]:
        bundle = await self.search("Appointment", {"patient": patient_id, "status": "booked"})
        return self._extract_entries(bundle)

    async def get_related_persons(self, patient_id: str) -> list[dict]:
        bundle = await self.search("RelatedPerson", {"patient": patient_id})
        return self._extract_entries(bundle)

    async def get_care_plans(self, patient_id: str) -> list[dict]:
        bundle = await self.search("CarePlan", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle)

    async def get_care_teams(self, patient_id: str) -> list[dict]:
        bundle = await self.search("CareTeam", {"patient": patient_id, "status": "active"})
        return self._extract_entries(bundle)

    async def get_diagnostic_reports(self, patient_id: str) -> list[dict]:
        bundle = await self.search("DiagnosticReport", {"patient": patient_id, "_sort": "-date", "_count": "10"})
        return self._extract_entries(bundle)

    async def get_all_encounters(self, patient_id: str) -> list[dict]:
        bundle = await self.search("Encounter", {"patient": patient_id, "_sort": "-date", "_count": "50"})
        return self._extract_entries(bundle)

    def _extract_entries(self, bundle: dict | None) -> list[dict]:
        if not bundle or not bundle.get("entry"):
            return []
        return [entry.get("resource", {}) for entry in bundle["entry"]]

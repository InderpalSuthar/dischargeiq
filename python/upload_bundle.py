import httpx
import sys
import json
import os

def upload_bundle(file_path):
    url = "https://hapi.fhir.org/baseR4"
    headers = {"Content-Type": "application/fhir+json"}
    
    print(f"Uploading {file_path} to {url}...")
    
    try:
        with open(file_path, 'r') as f:
            bundle = json.load(f)
            
        with httpx.Client(timeout=30.0) as client:
            response = client.post(url, json=bundle, headers=headers)
            
            if response.status_code in (200, 201):
                print("Successfully uploaded bundle!")
                print(f"Response Code: {response.status_code}")
                # Try to extract the patient ID if it was returned
                response_json = response.json()
                for entry in response_json.get("entry", []):
                    res = entry.get("response", {})
                    location = res.get("location", "")
                    if location.startswith("Patient/"):
                        print(f"Assigned Patient ID: {location.split('/')[1]}")
            elif response.status_code == 400:
                response_json = response.json()
                if response_json.get("resourceType") == "OperationOutcome":
                    diagnostics = response_json.get("issue", [{}])[0].get("diagnostics", "")
                    if "duplicating existing resource: Patient/" in diagnostics:
                        existing_id = diagnostics.split("Patient/")[1].split()[0]
                        print(f"Resource already exists. Patient ID: {existing_id}")
                    else:
                        print(f"Failed to upload. Status code: {response.status_code}")
                        print(diagnostics)
            else:
                print(f"Failed to upload. Status code: {response.status_code}")
                print(response.text)
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    bundle_path = sys.argv[1] if len(sys.argv) > 1 else "../fhir_bundles/patient-050_bundle.json"
    upload_bundle(bundle_path)

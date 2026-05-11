"""OpenFDA Drug Label API client for real drug interaction data."""

import asyncio
import logging
import re

import httpx

logger = logging.getLogger(__name__)

OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
REQUEST_TIMEOUT = 5.0


async def _fetch_drug_label(client: httpx.AsyncClient, drug_name: str) -> dict | None:
    """Fetch FDA label data for a single drug. Returns None on failure."""
    clean_name = re.sub(r"[^a-zA-Z\s]", "", drug_name).strip().lower()
    if not clean_name:
        return None
    try:
        resp = await client.get(
            OPENFDA_LABEL_URL,
            params={
                "search": f'openfda.generic_name:"{clean_name}"+openfda.brand_name:"{clean_name}"',
                "limit": 1,
            },
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        if not results:
            return None
        label = results[0]
        return {
            "drug_name": drug_name,
            "drug_interactions": label.get("drug_interactions", []),
            "warnings": label.get("warnings", []),
            "contraindications": label.get("contraindications", []),
            "boxed_warning": label.get("boxed_warning", []),
        }
    except (httpx.TimeoutException, httpx.HTTPError, Exception) as e:
        logger.warning("OpenFDA lookup failed for %s: %s", drug_name, e)
        return None


def _estimate_severity(text: str) -> str:
    """Estimate interaction severity from FDA label language."""
    text_lower = text.lower()
    if any(w in text_lower for w in ["contraindicated", "fatal", "death", "do not use", "life-threatening", "black box"]):
        return "CRITICAL"
    if any(w in text_lower for w in ["serious", "major", "significant", "avoid", "should not be used"]):
        return "MAJOR"
    if any(w in text_lower for w in ["caution", "monitor", "adjust", "may increase", "may decrease"]):
        return "MODERATE"
    return "MINOR"


def _find_cross_references(labels: list[dict]) -> list[dict]:
    """Cross-reference drug pairs: check if drug A's label mentions drug B."""
    interactions = []
    drug_names = [l["drug_name"].lower() for l in labels]

    for label in labels:
        interaction_text = " ".join(label.get("drug_interactions", []))
        warning_text = " ".join(label.get("warnings", []))
        all_text = interaction_text + " " + warning_text

        for other_name in drug_names:
            if other_name == label["drug_name"].lower():
                continue
            # Check if the other drug is mentioned in this drug's label
            if other_name in all_text.lower():
                # Extract the relevant sentence
                for sentence in re.split(r'[.;]', all_text):
                    if other_name in sentence.lower():
                        description = sentence.strip()[:300]
                        if description:
                            interactions.append({
                                "drug_a": label["drug_name"],
                                "drug_b": other_name,
                                "severity": _estimate_severity(description),
                                "description": description,
                                "source": "OpenFDA Drug Label",
                            })
                        break
    # Deduplicate (A->B and B->A)
    seen = set()
    unique = []
    for ix in interactions:
        key = tuple(sorted([ix["drug_a"].lower(), ix["drug_b"].lower()]))
        if key not in seen:
            seen.add(key)
            unique.append(ix)
    return unique


async def lookup_drug_interactions(drug_names: list[str]) -> dict:
    """Look up FDA drug label data for a list of drugs and find cross-referenced interactions.

    Returns:
        {
            "interactions": [{"drug_a", "drug_b", "severity", "description", "source"}],
            "warnings": [{"drug", "warning"}],
            "boxed_warnings": [{"drug", "warning"}],
            "drugs_found": int,
            "drugs_not_found": [str],
            "source": "OpenFDA Drug Label API"
        }
    """
    if not drug_names:
        return {"interactions": [], "warnings": [], "boxed_warnings": [], "drugs_found": 0, "drugs_not_found": [], "source": "OpenFDA Drug Label API"}

    async with httpx.AsyncClient() as client:
        tasks = [_fetch_drug_label(client, name) for name in drug_names]
        results = await asyncio.gather(*tasks)

    labels = [r for r in results if r is not None]
    not_found = [name for name, r in zip(drug_names, results) if r is None]

    # Cross-reference drug pairs
    interactions = _find_cross_references(labels)

    # Collect individual warnings
    warnings = []
    boxed_warnings = []
    for label in labels:
        for w in label.get("warnings", []):
            if len(w) > 20:  # Skip empty/trivial entries
                warnings.append({"drug": label["drug_name"], "warning": w[:500]})
        for bw in label.get("boxed_warning", []):
            if len(bw) > 20:
                boxed_warnings.append({"drug": label["drug_name"], "warning": bw[:500]})

    return {
        "interactions": interactions,
        "warnings": warnings,
        "boxed_warnings": boxed_warnings,
        "drugs_found": len(labels),
        "drugs_not_found": not_found,
        "source": "OpenFDA Drug Label API",
    }

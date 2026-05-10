"""
Shared constants and utilities used across multiple tools.
Avoids duplication of language maps and other common patterns.
"""

# BCP-47 language code → human-readable language name
# Used by discharge_plan.py and medication_card.py for multilingual routing
LANGUAGE_NAME_MAP: dict[str, str] = {
    "es": "Spanish",
    "zh": "Chinese (Simplified)",
    "ar": "Arabic",
    "fr": "French",
    "de": "German",
    "hi": "Hindi",
    "pt": "Portuguese",
    "ru": "Russian",
    "ko": "Korean",
    "ja": "Japanese",
    "vi": "Vietnamese",
    "tl": "Tagalog",
    "it": "Italian",
    "pl": "Polish",
    "bn": "Bengali",
    "sw": "Swahili",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "fa": "Farsi",
    "ne": "Nepali",
    "my": "Burmese",
    "km": "Khmer",
    "so": "Somali",
    "am": "Amharic",
    "ha": "Hausa",
}


def get_language_instruction(language_code: str | None) -> str:
    """Generate the multilingual routing instruction for a patient.

    Returns an empty string if the patient's language is English (no instruction needed).
    Returns a formatted instruction block if the patient speaks another language.
    """
    if not language_code or language_code.lower() in ("en", "english"):
        return ""

    lang_name = LANGUAGE_NAME_MAP.get(language_code.lower(), language_code)
    return (
        f"\n\n⚠️ CRITICAL LANGUAGE REQUIREMENT: This patient's primary language is "
        f"**{lang_name}** (code: {language_code}). You MUST generate the ENTIRE output "
        f"in {lang_name}. All section headings, instructions, medication explanations, "
        f"and warnings must be in {lang_name}. Do not use English except for drug names."
    )

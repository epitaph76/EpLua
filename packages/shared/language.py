from __future__ import annotations

DEFAULT_LANGUAGE = "ru"
VALID_LANGUAGES = ("ru", "en")
_LANGUAGE_NAMES = {
    "ru": "Russian",
    "en": "English",
}


def normalize_language(language: str | None) -> str:
    normalized = (language or DEFAULT_LANGUAGE).strip().lower()
    if normalized not in VALID_LANGUAGES:
        raise ValueError(f"Unsupported language: {language}")
    return normalized


def natural_language_name(language: str | None) -> str:
    return _LANGUAGE_NAMES[normalize_language(language)]

import json
import os
from pathlib import Path


LANGUAGE_NAMES = {
    "en_US": "English (US)",
    "en_GB": "English (UK)",
    "es": "Español",
    "pt_BR": "Português (Brasil)",
    "fr": "Français",
    "de": "Deutsch",
}

LANGUAGE_OPTIONS = (
    ("system", "System default"),
    *LANGUAGE_NAMES.items(),
)

_TRANSLATIONS_DIRECTORY = Path(__file__).resolve().parent / "translations"
_TRANSLATIONS = {}
for _language in LANGUAGE_NAMES:
    _catalog_path = _TRANSLATIONS_DIRECTORY / f"{_language}.json"
    _catalog = json.loads(_catalog_path.read_text(encoding="utf-8"))
    if not isinstance(_catalog, dict) or any(
        not isinstance(key, str) or not isinstance(value, str)
        for key, value in _catalog.items()
    ):
        raise ValueError(f"Invalid translation catalog: {_catalog_path}")
    _TRANSLATIONS[_language] = _catalog

_current_language = "en_US"


def resolve_language(selection, system_locale=None):
    if selection != "system" and selection not in LANGUAGE_NAMES:
        raise ValueError(f"Unsupported language selection: {selection!r}")
    if selection != "system":
        return selection

    system_locale = system_locale or next(
        (
            os.environ.get(variable)
            for variable in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG")
            if os.environ.get(variable)
        ),
        "en_US",
    )
    system_locale = (
        system_locale.split(":")[0].split(".")[0].split("@")[0].replace("-", "_")
    )
    language, _, region = system_locale.partition("_")
    if language == "pt":
        return "pt_BR"
    if language == "en":
        return "en_GB" if region.upper() == "GB" else "en_US"
    if language in ("es", "fr", "de"):
        return language
    return "en_US"


def set_language(selection, system_locale=None):
    global _current_language
    _current_language = resolve_language(selection, system_locale)


def tr(message, **values):
    translated = _TRANSLATIONS[_current_language].get(message, message)
    return translated.format(**values) if values else translated

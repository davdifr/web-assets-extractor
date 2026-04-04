from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


DECLARATION_RE = re.compile(r"(?P<property>--?[A-Za-z0-9_-]+)\s*:\s*(?P<value>[^;}{]+)")
FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*(?P<value>[^;}{]+)", re.IGNORECASE)
URL_TOKEN_RE = re.compile(r"url\((['\"]?)(.*?)\1\)", re.IGNORECASE)
GENERIC_FONT_FAMILIES = {
    "sans-serif",
    "serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-sans-serif",
    "ui-serif",
    "ui-monospace",
    "emoji",
    "math",
    "fangsong",
    "inherit",
    "initial",
    "unset",
    "revert",
}


def iter_css_declarations(text: str) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    for match in DECLARATION_RE.finditer(text):
        property_name = match.group("property").strip().lower()
        value = _clean_css_value(match.group("value"))
        if property_name and value:
            declarations.append((property_name, value))
    return declarations


def extract_font_families(text: str) -> list[str]:
    families: list[str] = []
    for match in FONT_FAMILY_RE.finditer(text):
        family = select_primary_font_family(match.group("value"))
        if family:
            families.append(family)
    return families


def extract_url_tokens(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_TOKEN_RE.finditer(text):
        candidate = match.group(2).strip()
        if candidate and not candidate.startswith("data:") and not candidate.startswith("#"):
            urls.append(candidate)
    return urls


def extract_google_font_families(url: str) -> list[str]:
    parsed = urlparse(url)
    if "fonts.googleapis.com" not in parsed.netloc.lower():
        return []

    families: list[str] = []
    for raw_family in parse_qs(parsed.query).get("family", []):
        family_name = raw_family.split(":")[0].replace("+", " ").strip()
        if family_name:
            families.append(family_name)
    return families


def select_primary_font_family(value: str) -> str | None:
    cleaned_value = _clean_css_value(value)
    if not cleaned_value:
        return None

    candidates = [
        segment.strip().strip("\"'")
        for segment in cleaned_value.split(",")
        if segment.strip()
    ]
    if not candidates:
        return None

    specific_candidates = [
        candidate
        for candidate in candidates
        if candidate.lower() not in GENERIC_FONT_FAMILIES and not candidate.lower().startswith("var(")
    ]
    if specific_candidates:
        return specific_candidates[0]

    generic_candidates = [candidate for candidate in candidates if not candidate.lower().startswith("var(")]
    if generic_candidates:
        return generic_candidates[0]
    return None


def _clean_css_value(value: str) -> str:
    return value.replace("!important", "").strip().strip(",")

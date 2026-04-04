from __future__ import annotations

from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse


def normalize_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise ValueError("The URL field is empty.")
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("Please enter a valid public URL.")
    return urlunparse(parsed)


def absolutize_url(base_url: str, candidate: str | None) -> str | None:
    if not candidate:
        return None
    resolved = urljoin(base_url, candidate.strip())
    parsed = urlparse(resolved)
    if parsed.scheme not in {"http", "https"}:
        return None
    return resolved


def extract_urls_from_srcset(srcset: str, base_url: str) -> list[str]:
    urls: list[str] = []
    for part in srcset.split(","):
        token = part.strip().split(" ")[0]
        resolved = absolutize_url(base_url, token)
        if resolved:
            urls.append(resolved)
    return urls


def guess_filename_from_url(url: str | None, fallback_stem: str) -> str:
    if not url:
        return fallback_stem
    parsed = urlparse(url)
    name = Path(parsed.path).name
    return name or fallback_stem

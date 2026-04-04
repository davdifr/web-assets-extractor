from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse


def ensure_directory(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_default_output_dir(app_folder_name: str = "web-assets-extractor") -> Path:
    preferred = Path.home() / "Downloads" / app_folder_name
    try:
        return ensure_directory(preferred)
    except OSError:
        return ensure_directory(Path.cwd() / "output")


def slugify(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return normalized or "analysis"


def sanitize_filename(value: str, default: str = "asset") -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip().strip(".")
    cleaned = re.sub(r"\s+", "-", cleaned)
    return cleaned or default


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    counter = 1
    while True:
        candidate = path.with_name(f"{stem}-{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def make_analysis_paths(base_output_dir: Path, source_url: str) -> tuple[Path, Path, Path, Path, Path]:
    ensure_directory(base_output_dir)
    parsed = urlparse(source_url)
    domain = parsed.netloc or parsed.path or "website"
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    root_dir = ensure_directory(base_output_dir / f"{timestamp}-{slugify(domain)}")
    assets_dir = ensure_directory(root_dir / "assets")
    report_json = root_dir / "report.json"
    report_markdown = root_dir / "report.md"
    assets_zip = root_dir / "assets.zip"
    return root_dir, assets_dir, report_json, report_markdown, assets_zip

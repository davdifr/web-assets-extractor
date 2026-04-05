from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class AnalysisOptions:
    analyze_fonts: bool = True
    analyze_colors: bool = True
    analyze_copy: bool = True
    analyze_assets: bool = True
    explore_site_routes: bool = False
    max_route_pages: int = 5
    zip_downloads: bool = False
    timeout_ms: int = 30_000

    def to_dict(self) -> dict[str, Any]:
        return {
            "analyze_fonts": self.analyze_fonts,
            "analyze_colors": self.analyze_colors,
            "analyze_copy": self.analyze_copy,
            "analyze_assets": self.analyze_assets,
            "explore_site_routes": self.explore_site_routes,
            "max_route_pages": self.max_route_pages,
            "zip_downloads": self.zip_downloads,
            "timeout_ms": self.timeout_ms,
        }


@dataclass(slots=True)
class ProgressUpdate:
    message: str
    current: int | None = None
    total: int | None = None
    indeterminate: bool = True


@dataclass(slots=True)
class AnalysisPaths:
    root_dir: Path
    assets_dir: Path
    report_json: Path
    report_markdown: Path
    assets_zip: Path

    def to_dict(self) -> dict[str, str]:
        return {
            "root_dir": str(self.root_dir),
            "assets_dir": str(self.assets_dir),
            "report_json": str(self.report_json),
            "report_markdown": str(self.report_markdown),
            "assets_zip": str(self.assets_zip),
        }


@dataclass(slots=True)
class FontRecord:
    family: str
    occurrences: int

    def to_dict(self) -> dict[str, Any]:
        return {"family": self.family, "occurrences": self.occurrences}


@dataclass(slots=True)
class ColorRecord:
    value: str
    source: str
    occurrences: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "source": self.source,
            "occurrences": self.occurrences,
        }


@dataclass(slots=True)
class TextSnippet:
    tag: str
    text: str
    page_url: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            "tag": self.tag,
            "text": self.text,
            "page_url": self.page_url,
        }


@dataclass(slots=True)
class CTARecord:
    text: str
    url: str | None
    tag: str
    page_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "url": self.url,
            "tag": self.tag,
            "page_url": self.page_url,
        }


@dataclass(slots=True)
class AssetRecord:
    asset_id: str
    kind: str
    filename: str
    origin: str
    page_url: str | None = None
    url: str | None = None
    mime_type: str | None = None
    alt_text: str | None = None
    inline_content: str | None = None
    downloaded: bool = False
    local_path: str | None = None
    size_bytes: int | None = None
    image_size: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "kind": self.kind,
            "filename": self.filename,
            "origin": self.origin,
            "page_url": self.page_url,
            "url": self.url,
            "mime_type": self.mime_type,
            "alt_text": self.alt_text,
            "inline_content": self.inline_content,
            "downloaded": self.downloaded,
            "local_path": self.local_path,
            "size_bytes": self.size_bytes,
            "image_size": self.image_size,
        }


@dataclass(slots=True)
class DownloadedAssetRecord:
    asset_id: str
    filename: str
    kind: str
    local_path: str
    source_url: str | None = None
    size_bytes: int | None = None
    image_size: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "filename": self.filename,
            "kind": self.kind,
            "local_path": self.local_path,
            "source_url": self.source_url,
            "size_bytes": self.size_bytes,
            "image_size": self.image_size,
        }


@dataclass(slots=True)
class AnalysisResult:
    source_url: str
    final_url: str
    page_title: str
    page_description: str | None
    status_code: int | None
    analysed_at: str
    duration_ms: int
    word_count: int
    options: AnalysisOptions
    paths: AnalysisPaths
    fonts: list[FontRecord] = field(default_factory=list)
    colors: list[ColorRecord] = field(default_factory=list)
    headlines: list[TextSnippet] = field(default_factory=list)
    ctas: list[CTARecord] = field(default_factory=list)
    copy_blocks: list[TextSnippet] = field(default_factory=list)
    assets: list[AssetRecord] = field(default_factory=list)
    downloaded_assets: list[DownloadedAssetRecord] = field(default_factory=list)
    scanned_pages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def fonts_count(self) -> int:
        return len(self.fonts)

    @property
    def colors_count(self) -> int:
        return len(self.colors)

    @property
    def headlines_count(self) -> int:
        return len(self.headlines)

    @property
    def ctas_count(self) -> int:
        return len(self.ctas)

    @property
    def copy_blocks_count(self) -> int:
        return len(self.copy_blocks)

    @property
    def assets_count(self) -> int:
        return len(self.assets)

    @property
    def downloaded_assets_count(self) -> int:
        return len(self.downloaded_assets)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": {
                "requested_url": self.source_url,
                "final_url": self.final_url,
                "page_title": self.page_title,
                "page_description": self.page_description,
                "status_code": self.status_code,
                "analysed_at": self.analysed_at,
            },
            "overview": {
                "duration_ms": self.duration_ms,
                "word_count": self.word_count,
                "fonts_count": self.fonts_count,
                "colors_count": self.colors_count,
                "headlines_count": self.headlines_count,
                "ctas_count": self.ctas_count,
                "copy_blocks_count": self.copy_blocks_count,
                "assets_count": self.assets_count,
                "downloaded_assets_count": self.downloaded_assets_count,
            },
            "options": self.options.to_dict(),
            "paths": self.paths.to_dict(),
            "fonts": [item.to_dict() for item in self.fonts],
            "colors": [item.to_dict() for item in self.colors],
            "headlines": [item.to_dict() for item in self.headlines],
            "ctas": [item.to_dict() for item in self.ctas],
            "copy_blocks": [item.to_dict() for item in self.copy_blocks],
            "assets": [item.to_dict() for item in self.assets],
            "downloaded_assets": [item.to_dict() for item in self.downloaded_assets],
            "scanned_pages": self.scanned_pages,
            "notes": self.notes,
        }

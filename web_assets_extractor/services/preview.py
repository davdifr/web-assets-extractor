from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps
from urllib3 import disable_warnings
from urllib3.exceptions import InsecureRequestWarning

from web_assets_extractor.models import AssetRecord

disable_warnings(InsecureRequestWarning)


@dataclass(slots=True)
class AssetPreview:
    asset_id: str
    mode: str
    content_bytes: bytes | None
    details: str
    media_path: str | None = None
    media_url: str | None = None
    message: str = ""


class AssetPreviewService:
    MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024
    MAX_IMAGE_SIZE = (320, 240)

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                )
            }
        )

    def load_preview(self, asset: AssetRecord) -> AssetPreview:
        details = self._build_details(asset)
        if asset.kind not in {"image", "icon", "svg", "video"} and asset.inline_content is None:
            return AssetPreview(
                asset_id=asset.asset_id,
                mode="none",
                content_bytes=None,
                details=details,
                message="Preview available for images, icons, SVG files, and videos only.",
            )

        try:
            if asset.kind == "video":
                if asset.origin == "yt-dlp[youtube-best]" and not asset.local_path:
                    return AssetPreview(
                        asset_id=asset.asset_id,
                        mode="none",
                        content_bytes=None,
                        details=details,
                        message="YouTube preview becomes available after download.",
                    )
                video_path, video_url = self._prepare_video_source(asset)
                return AssetPreview(
                    asset_id=asset.asset_id,
                    mode="video",
                    content_bytes=None,
                    details=details,
                    media_path=str(video_path) if video_path else None,
                    media_url=video_url,
                )

            if asset.inline_content is not None:
                return AssetPreview(
                    asset_id=asset.asset_id,
                    mode="svg",
                    content_bytes=asset.inline_content.encode("utf-8"),
                    details=details,
                )

            raw_bytes, source_label = self._read_asset_bytes(asset)
            if self._is_svg_asset(asset, source_label, raw_bytes):
                return AssetPreview(
                    asset_id=asset.asset_id,
                    mode="svg",
                    content_bytes=raw_bytes,
                    details=details,
                )

            thumbnail_bytes = self._build_raster_thumbnail(raw_bytes)
            return AssetPreview(
                asset_id=asset.asset_id,
                mode="pixmap",
                content_bytes=thumbnail_bytes,
                details=details,
            )
        except Exception as exc:
            return AssetPreview(
                asset_id=asset.asset_id,
                mode="none",
                content_bytes=None,
                details=details,
                message=f"Preview unavailable: {exc}",
            )

    def _prepare_video_source(self, asset: AssetRecord) -> tuple[Path | None, str | None]:
        if asset.local_path:
            local_path = Path(asset.local_path)
            if local_path.is_file():
                return local_path, None

        if not asset.url:
            raise ValueError("Missing video source URL.")

        return None, asset.url

    def _read_asset_bytes(self, asset: AssetRecord) -> tuple[bytes, str]:
        if asset.local_path:
            local_path = Path(asset.local_path)
            if local_path.is_file():
                return local_path.read_bytes(), local_path.suffix.lower()

        if not asset.url:
            raise ValueError("Missing asset source URL.")

        response, content_type = self._download_bytes(asset.url)
        return response, content_type

    def _download_bytes(self, url: str) -> tuple[bytes, str]:
        try:
            response = self._session.get(url, timeout=15, stream=True)
        except requests.exceptions.SSLError:
            response = self._session.get(url, timeout=15, stream=True, verify=False)

        response.raise_for_status()
        content_type = response.headers.get("content-type", "")

        chunks: list[bytes] = []
        total_size = 0
        for chunk in response.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            total_size += len(chunk)
            if total_size > self.MAX_DOWNLOAD_BYTES:
                raise ValueError("file too large for preview")
            chunks.append(chunk)
        return b"".join(chunks), content_type.lower()

    def _build_raster_thumbnail(self, raw_bytes: bytes) -> bytes:
        with Image.open(BytesIO(raw_bytes)) as image:
            image = ImageOps.exif_transpose(image)
            image = image.convert("RGBA")
            image.thumbnail(self.MAX_IMAGE_SIZE)
            output = BytesIO()
            image.save(output, format="PNG")
        return output.getvalue()

    def _is_svg_asset(self, asset: AssetRecord, source_label: str, raw_bytes: bytes) -> bool:
        source_value = f"{asset.url or ''} {asset.filename} {source_label} {asset.mime_type or ''}".lower()
        if ".svg" in source_value or "image/svg+xml" in source_value:
            return True

        prefix = raw_bytes[:512].decode("utf-8", errors="ignore").lower()
        return "<svg" in prefix

    def _build_details(self, asset: AssetRecord) -> str:
        rows = [
            f"ID: {asset.asset_id}",
            f"Type: {asset.kind}",
            f"Filename: {asset.filename}",
            f"Origin: {asset.origin}",
            f"Status: {'Downloaded' if asset.downloaded else 'Available'}",
        ]
        if asset.url:
            rows.append(f"Source: {asset.url}")
        if asset.alt_text:
            rows.append(f"Alt text: {asset.alt_text}")
        if asset.image_size:
            rows.append(f"Image size: {asset.image_size}")
        if asset.size_bytes is not None:
            rows.append(f"Size: {asset.size_bytes} bytes")
        return "\n".join(rows)

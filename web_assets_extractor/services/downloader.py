from __future__ import annotations

import mimetypes
import zipfile
from pathlib import Path
from typing import Callable, Sequence

import requests
from PIL import Image

from web_assets_extractor.models import AnalysisResult, AssetRecord, DownloadedAssetRecord, ProgressUpdate
from web_assets_extractor.services.exporter import ReportExporter
from web_assets_extractor.utils.files import sanitize_filename, unique_path

ProgressCallback = Callable[[ProgressUpdate], None] | None


class AssetDownloader:
    def __init__(self, exporter: ReportExporter) -> None:
        self._exporter = exporter
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

    def download_selected_assets(
        self,
        result: AnalysisResult,
        selected_asset_ids: Sequence[str],
        create_zip: bool,
        progress_callback: ProgressCallback = None,
    ) -> AnalysisResult:
        selected_lookup = set(selected_asset_ids)
        downloaded_paths: list[Path] = []
        total_steps = len(selected_lookup) + (1 if create_zip and selected_lookup else 0)
        completed_steps = 0

        for asset in result.assets:
            if asset.asset_id not in selected_lookup:
                continue
            if progress_callback:
                progress_callback(
                    ProgressUpdate(
                        message=f"Downloading {asset.filename}",
                        current=completed_steps,
                        total=total_steps,
                        indeterminate=False,
                    )
                )
            local_path = self._download_asset(asset, result.paths.assets_dir)
            downloaded_paths.append(local_path)
            self._sync_download_metadata(result, asset, local_path)
            completed_steps += 1
            if progress_callback:
                progress_callback(
                    ProgressUpdate(
                        message=f"Downloaded {asset.filename}",
                        current=completed_steps,
                        total=total_steps,
                        indeterminate=False,
                    )
                )

        if create_zip and downloaded_paths:
            if progress_callback:
                progress_callback(
                    ProgressUpdate(
                        message="Creating assets ZIP archive",
                        current=completed_steps,
                        total=total_steps,
                        indeterminate=False,
                    )
                )
            self._create_zip_archive(downloaded_paths, result.paths.assets_zip)
            completed_steps += 1
            if progress_callback:
                progress_callback(
                    ProgressUpdate(
                        message="Assets ZIP archive created",
                        current=completed_steps,
                        total=total_steps,
                        indeterminate=False,
                    )
                )
            note = f"ZIP archive created at {result.paths.assets_zip}"
            if note not in result.notes:
                result.notes.append(note)

        self._exporter.write_session_reports(result)
        return result

    def _download_asset(self, asset: AssetRecord, assets_dir: Path) -> Path:
        if asset.downloaded and asset.local_path:
            existing_path = Path(asset.local_path)
            if existing_path.exists():
                return existing_path

        if asset.inline_content is not None:
            filename = self._finalize_filename(asset.filename, asset.mime_type)
            destination = unique_path(assets_dir / filename)
            destination.write_text(asset.inline_content, encoding="utf-8")
            return destination

        if not asset.url:
            raise ValueError(f"Asset {asset.asset_id} has no downloadable source.")

        response = self._session.get(asset.url, timeout=30, stream=True)
        response.raise_for_status()

        filename = self._finalize_filename(
            asset.filename,
            response.headers.get("content-type"),
        )
        destination = unique_path(assets_dir / filename)
        with destination.open("wb") as output_stream:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    output_stream.write(chunk)
        return destination

    def _finalize_filename(self, raw_filename: str, content_type: str | None) -> str:
        candidate = sanitize_filename(raw_filename, default="asset")
        stem = Path(candidate).stem or "asset"
        suffix = Path(candidate).suffix
        if not suffix and content_type:
            guessed_suffix = mimetypes.guess_extension(content_type.split(";")[0].strip())
            suffix = guessed_suffix or ""
        return f"{stem}{suffix}"

    def _sync_download_metadata(
        self,
        result: AnalysisResult,
        asset: AssetRecord,
        local_path: Path,
    ) -> None:
        asset.downloaded = True
        asset.local_path = str(local_path)
        asset.size_bytes = local_path.stat().st_size
        asset.image_size = self._read_image_size(local_path)

        downloaded_asset = DownloadedAssetRecord(
            asset_id=asset.asset_id,
            filename=local_path.name,
            kind=asset.kind,
            local_path=str(local_path),
            source_url=asset.url,
            size_bytes=asset.size_bytes,
            image_size=asset.image_size,
        )

        existing_index = next(
            (
                index
                for index, item in enumerate(result.downloaded_assets)
                if item.asset_id == asset.asset_id
            ),
            None,
        )
        if existing_index is None:
            result.downloaded_assets.append(downloaded_asset)
        else:
            result.downloaded_assets[existing_index] = downloaded_asset

    def _read_image_size(self, path: Path) -> str | None:
        try:
            with Image.open(path) as image:
                return f"{image.width}x{image.height}"
        except Exception:
            return None

    def _create_zip_archive(self, asset_paths: Sequence[Path], zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for asset_path in asset_paths:
                archive.write(asset_path, arcname=asset_path.name)

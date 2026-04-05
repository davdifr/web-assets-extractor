from __future__ import annotations

from copy import deepcopy
import mimetypes
import shutil
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Callable, Sequence

import requests
from PIL import Image

from web_assets_extractor.models import AnalysisResult, AssetRecord, DownloadedAssetRecord, ProgressUpdate
from web_assets_extractor.services.exporter import ReportExporter
from web_assets_extractor.services.muxer import MediaMuxer, MuxJob, MuxedMediaRecord
from web_assets_extractor.services.youtube import YouTubeAssetDownloader
from web_assets_extractor.utils.files import sanitize_filename, unique_path

ProgressCallback = Callable[[ProgressUpdate], None] | None


class AssetDownloader:
    def __init__(self, exporter: ReportExporter) -> None:
        self._exporter = exporter
        self._muxer = MediaMuxer()
        self._youtube_downloader = YouTubeAssetDownloader()
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
        progress_callback: ProgressCallback = None,
    ) -> AnalysisResult:
        working_result = deepcopy(result)
        selected_lookup = set(selected_asset_ids)
        selected_assets = [asset for asset in working_result.assets if asset.asset_id in selected_lookup]
        create_zip = working_result.options.zip_downloads
        mux_plan = self._muxer.plan(selected_assets)
        if mux_plan.jobs and not self._muxer.is_available():
            raise ValueError(
                "ffmpeg is required to mux chunked audio/video assets into a final MP4. "
                "Install ffmpeg and retry."
            )

        direct_download_assets = [
            asset for asset in selected_assets if asset.asset_id not in mux_plan.skip_direct_download_ids
        ]
        downloaded_paths: list[Path] = []
        total_steps = len(direct_download_assets) + len(mux_plan.jobs)
        if create_zip and (direct_download_assets or mux_plan.jobs):
            total_steps += 1
        completed_steps = 0

        for note in mux_plan.notes:
            if note not in working_result.notes:
                working_result.notes.append(note)

        with TemporaryDirectory(
            dir=working_result.paths.root_dir,
            prefix=".download-staging-",
        ) as staging_dir_name:
            staging_dir = Path(staging_dir_name)
            staged_asset_paths: list[tuple[AssetRecord, Path]] = []
            staged_muxed_records: list[tuple[MuxJob, MuxedMediaRecord]] = []
            created_output_paths: list[Path] = []
            zip_backup_path: Path | None = None

            try:
                for asset in direct_download_assets:
                    if progress_callback:
                        progress_callback(
                            ProgressUpdate(
                                message=f"Downloading {asset.filename}",
                                current=completed_steps,
                                total=total_steps,
                                indeterminate=False,
                            )
                        )
                    local_path, download_notes = self._download_asset(asset, staging_dir)
                    staged_asset_paths.append((asset, local_path))
                    for note in download_notes:
                        if note not in working_result.notes:
                            working_result.notes.append(note)
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

                for mux_job in mux_plan.jobs:
                    if progress_callback:
                        progress_callback(
                            ProgressUpdate(
                                message=self._build_mux_progress_message(mux_job),
                                current=completed_steps,
                                total=total_steps,
                                indeterminate=False,
                            )
                        )

                    muxed_record = self._muxer.execute(mux_job, staging_dir)
                    staged_muxed_records.append((mux_job, muxed_record))
                    completed_steps += 1

                    if progress_callback:
                        progress_callback(
                            ProgressUpdate(
                                message=f"Created {muxed_record.filename}",
                                current=completed_steps,
                                total=total_steps,
                                indeterminate=False,
                            )
                        )

                for asset, staged_path in staged_asset_paths:
                    final_path, was_created = self._finalize_output_path(
                        staged_path,
                        staging_dir,
                        working_result.paths.assets_dir,
                    )
                    if was_created:
                        created_output_paths.append(final_path)
                    downloaded_paths.append(final_path)
                    self._sync_download_metadata(working_result, asset, final_path)

                for mux_job, muxed_record in staged_muxed_records:
                    final_path, was_created = self._finalize_output_path(
                        muxed_record.local_path,
                        staging_dir,
                        working_result.paths.assets_dir,
                    )
                    if was_created:
                        created_output_paths.append(final_path)
                    downloaded_paths.append(final_path)
                    self._sync_muxed_metadata(
                        working_result,
                        mux_job,
                        MuxedMediaRecord(
                            asset_id=muxed_record.asset_id,
                            kind=muxed_record.kind,
                            filename=final_path.name,
                            local_path=final_path,
                            source_url=muxed_record.source_url,
                            source_asset_ids=muxed_record.source_asset_ids,
                            note=muxed_record.note,
                        ),
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
                    staged_zip_path = staging_dir / working_result.paths.assets_zip.name
                    self._create_zip_archive(downloaded_paths, staged_zip_path)
                    if working_result.paths.assets_zip.exists():
                        zip_backup_path = staging_dir / f"{working_result.paths.assets_zip.stem}.backup.zip"
                        working_result.paths.assets_zip.replace(zip_backup_path)
                    shutil.move(staged_zip_path, working_result.paths.assets_zip)
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
                    note = f"ZIP archive created at {working_result.paths.assets_zip}"
                    if note not in working_result.notes:
                        working_result.notes.append(note)

                self._exporter.write_session_reports(working_result)
                return working_result
            except Exception:
                if working_result.paths.assets_zip.exists():
                    working_result.paths.assets_zip.unlink(missing_ok=True)
                if zip_backup_path and zip_backup_path.exists():
                    zip_backup_path.replace(working_result.paths.assets_zip)
                for path in reversed(created_output_paths):
                    path.unlink(missing_ok=True)
                raise

    def _download_asset(self, asset: AssetRecord, assets_dir: Path) -> tuple[Path, list[str]]:
        notes: list[str] = []
        if asset.downloaded and asset.local_path:
            existing_path = Path(asset.local_path)
            if existing_path.exists():
                return existing_path, notes

        if self._youtube_downloader.can_handle(asset):
            return self._youtube_downloader.download(asset, assets_dir), notes

        if asset.inline_content is not None:
            filename = self._finalize_filename(asset.filename, asset.mime_type)
            destination = unique_path(assets_dir / filename)
            destination.write_text(asset.inline_content, encoding="utf-8")
            return destination, notes

        if not asset.url:
            raise ValueError(f"Asset {asset.asset_id} has no downloadable source.")

        response, used_insecure_ssl = self._request_stream(asset.url, timeout_seconds=30)
        response.raise_for_status()
        if used_insecure_ssl:
            notes.append(
                f"SSL certificate verification failed while downloading {asset.filename}. "
                "The file was fetched without certificate verification."
            )

        filename = self._finalize_filename(
            asset.filename,
            response.headers.get("content-type"),
        )
        destination = unique_path(assets_dir / filename)
        with destination.open("wb") as output_stream:
            for chunk in response.iter_content(chunk_size=1024 * 64):
                if chunk:
                    output_stream.write(chunk)
        return destination, notes

    def _request_stream(self, url: str, timeout_seconds: int) -> tuple[requests.Response, bool]:
        try:
            response = self._session.get(url, timeout=timeout_seconds, stream=True)
            return response, False
        except requests.exceptions.SSLError:
            try:
                response = self._session.get(url, timeout=timeout_seconds, stream=True, verify=False)
                return response, True
            except requests.RequestException as exc:
                raise ValueError(f"Could not download {url}: {exc}") from exc
        except requests.RequestException as exc:
            raise ValueError(f"Could not download {url}: {exc}") from exc

    def _finalize_output_path(
        self,
        path: Path,
        staging_dir: Path,
        destination_dir: Path,
    ) -> tuple[Path, bool]:
        if not path.exists():
            return path, False
        try:
            is_staged = path.resolve().is_relative_to(staging_dir.resolve())
        except ValueError:
            is_staged = False
        if not is_staged:
            return path, False

        destination = unique_path(destination_dir / path.name)
        path.replace(destination)
        return destination, True

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

    def _sync_muxed_metadata(
        self,
        result: AnalysisResult,
        mux_job: MuxJob,
        muxed_record: MuxedMediaRecord,
    ) -> None:
        muxed_size = muxed_record.local_path.stat().st_size
        downloaded_asset = DownloadedAssetRecord(
            asset_id=muxed_record.asset_id,
            filename=muxed_record.filename,
            kind=muxed_record.kind,
            local_path=str(muxed_record.local_path),
            source_url=muxed_record.source_url,
            size_bytes=muxed_size,
            image_size=None,
        )
        existing_index = next(
            (
                index
                for index, item in enumerate(result.downloaded_assets)
                if item.asset_id == muxed_record.asset_id
            ),
            None,
        )
        if existing_index is None:
            result.downloaded_assets.append(downloaded_asset)
        else:
            result.downloaded_assets[existing_index] = downloaded_asset

        if not mux_job.video_asset.downloaded:
            mux_job.video_asset.downloaded = True
            mux_job.video_asset.local_path = str(muxed_record.local_path)
            mux_job.video_asset.size_bytes = muxed_size
            mux_job.video_asset.image_size = None

        if muxed_record.note not in result.notes:
            result.notes.append(muxed_record.note)

    def _read_image_size(self, path: Path) -> str | None:
        try:
            with Image.open(path) as image:
                return f"{image.width}x{image.height}"
        except Exception:
            return None

    def _build_mux_progress_message(self, mux_job: MuxJob) -> str:
        if mux_job.audio_asset is None:
            return f"Generating final MP4 from {mux_job.video_asset.filename}"
        return f"Muxing {mux_job.video_asset.filename} + {mux_job.audio_asset.filename}"

    def _create_zip_archive(self, asset_paths: Sequence[Path], zip_path: Path) -> None:
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for asset_path in asset_paths:
                archive.write(asset_path, arcname=asset_path.name)

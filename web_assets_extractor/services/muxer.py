from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence
from urllib.parse import urlparse

from web_assets_extractor.models import AssetRecord
from web_assets_extractor.utils.files import sanitize_filename, unique_path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)
STREAM_MARKERS = (
    ".m3u8",
    ".mpd",
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/dash+xml",
)
GENERIC_TOKENS = {
    "audio",
    "avc",
    "chunk",
    "chunks",
    "dash",
    "hls",
    "index",
    "main",
    "manifest",
    "master",
    "media",
    "muxed",
    "playlist",
    "segment",
    "segments",
    "stream",
    "streams",
    "track",
    "tracks",
    "video",
}
GENERIC_STEMS = {
    "audio",
    "index",
    "main",
    "manifest",
    "master",
    "media",
    "playlist",
    "stream",
    "video",
}


@dataclass(slots=True)
class MuxJob:
    video_asset: AssetRecord
    audio_asset: AssetRecord | None = None

    @property
    def source_asset_ids(self) -> tuple[str, ...]:
        if self.audio_asset is None:
            return (self.video_asset.asset_id,)
        return (self.video_asset.asset_id, self.audio_asset.asset_id)


@dataclass(slots=True)
class MuxPlan:
    jobs: list[MuxJob]
    skip_direct_download_ids: set[str]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class MuxedMediaRecord:
    asset_id: str
    kind: str
    filename: str
    local_path: Path
    source_url: str | None
    source_asset_ids: tuple[str, ...]
    note: str


class MediaMuxer:
    def __init__(
        self,
        ffmpeg_binary: str = "ffmpeg",
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self._ffmpeg_binary = ffmpeg_binary
        self._user_agent = user_agent

    def is_available(self) -> bool:
        return shutil.which(self._ffmpeg_binary) is not None

    def plan(self, selected_assets: Sequence[AssetRecord]) -> MuxPlan:
        videos = [asset for asset in selected_assets if asset.kind == "video"]
        audios = [asset for asset in selected_assets if asset.kind == "audio"]
        stream_videos = [asset for asset in videos if self._is_stream_asset(asset)]
        stream_audios = [asset for asset in audios if self._is_stream_asset(asset)]
        jobs: list[MuxJob] = []
        notes: list[str] = []
        paired_video_ids: set[str] = set()
        paired_audio_ids: set[str] = set()

        for video_asset, audio_asset in self._match_audio_assets(stream_videos, stream_audios, notes):
            jobs.append(MuxJob(video_asset=video_asset, audio_asset=audio_asset))
            paired_video_ids.add(video_asset.asset_id)
            paired_audio_ids.add(audio_asset.asset_id)

        for video_asset in stream_videos:
            if video_asset.asset_id in paired_video_ids:
                continue
            jobs.append(MuxJob(video_asset=video_asset))

        skip_direct_download_ids: set[str] = set()
        for job in jobs:
            skip_direct_download_ids.update(job.source_asset_ids)

        unmatched_audio_streams = [
            asset
            for asset in stream_audios
            if asset.asset_id not in paired_audio_ids and self._is_stream_asset(asset)
        ]
        if unmatched_audio_streams:
            notes.append(
                "Some selected audio streams were left as standalone downloads because they could not be paired automatically."
            )

        return MuxPlan(
            jobs=jobs,
            skip_direct_download_ids=skip_direct_download_ids,
            notes=notes,
        )

    def execute(self, job: MuxJob, assets_dir: Path) -> MuxedMediaRecord:
        ffmpeg_path = shutil.which(self._ffmpeg_binary)
        if not ffmpeg_path:
            raise ValueError(
                "ffmpeg is required to mux chunked audio/video assets into a final MP4. "
                "Install ffmpeg and retry."
            )

        destination = unique_path(assets_dir / self._build_output_filename(job))
        temp_destination = destination.with_name(f"{destination.stem}.part{destination.suffix}")

        command = [
            ffmpeg_path,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
        ]
        command.extend(self._build_input_args(job.video_asset))
        if job.audio_asset is not None:
            command.extend(self._build_input_args(job.audio_asset))
            command.extend(["-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(temp_destination)])
        else:
            command.extend(["-c", "copy", str(temp_destination)])

        try:
            subprocess.run(command, capture_output=True, text=True, check=True)
            temp_destination.replace(destination)
        except subprocess.CalledProcessError as exc:
            temp_destination.unlink(missing_ok=True)
            error_message = (exc.stderr or exc.stdout or "").strip() or str(exc)
            raise ValueError(f"Muxing failed for {self._describe_job(job)}: {error_message}") from exc

        source_ids = job.source_asset_ids
        note = (
            f"Muxed {' + '.join(source_ids)} into {destination}"
            if job.audio_asset
            else f"Generated final MP4 for {job.video_asset.asset_id} at {destination}"
        )
        return MuxedMediaRecord(
            asset_id="muxed-" + "-".join(source_ids),
            kind="video",
            filename=destination.name,
            local_path=destination,
            source_url=job.video_asset.url,
            source_asset_ids=source_ids,
            note=note,
        )

    def _build_input_args(self, asset: AssetRecord) -> list[str]:
        source_value = self._resolve_input_source(asset)
        args: list[str] = []
        if source_value.startswith(("http://", "https://")):
            args.extend(["-user_agent", self._user_agent])
        args.extend(["-i", source_value])
        return args

    def _resolve_input_source(self, asset: AssetRecord) -> str:
        if asset.local_path:
            local_path = Path(asset.local_path)
            if local_path.is_file():
                return str(local_path)
        if asset.url:
            return asset.url
        raise ValueError(f"Asset {asset.asset_id} does not have a usable media source.")

    def _build_output_filename(self, job: MuxJob) -> str:
        base_stem = self._preferred_output_stem(job.video_asset)
        if job.audio_asset is not None:
            return sanitize_filename(f"{base_stem}-muxed.mp4", default=f"{base_stem}-muxed.mp4")
        return sanitize_filename(f"{base_stem}.mp4", default=f"{base_stem}.mp4")

    def _preferred_output_stem(self, asset: AssetRecord) -> str:
        filename_stem = sanitize_filename(Path(asset.filename).stem, default="video")
        if filename_stem.lower() not in GENERIC_STEMS:
            return filename_stem

        source_path = self._source_path(asset)
        parent_name = source_path.parent.name
        if parent_name:
            parent_stem = sanitize_filename(parent_name, default="video")
            if parent_stem.lower() not in GENERIC_STEMS:
                return parent_stem

        return sanitize_filename(asset.asset_id, default="video")

    def _match_audio_assets(
        self,
        videos: Sequence[AssetRecord],
        audios: Sequence[AssetRecord],
        notes: list[str],
    ) -> list[tuple[AssetRecord, AssetRecord]]:
        if not videos or not audios:
            return []
        if len(videos) == 1 and len(audios) == 1:
            return [(videos[0], audios[0])]

        scored_pairs: list[tuple[int, int, int]] = []
        for video_index, video_asset in enumerate(videos):
            for audio_index, audio_asset in enumerate(audios):
                score = self._match_score(video_asset, audio_asset)
                if score > 0:
                    scored_pairs.append((score, video_index, audio_index))

        scored_pairs.sort(reverse=True)
        matched_videos: set[int] = set()
        matched_audios: set[int] = set()
        pairs: list[tuple[AssetRecord, AssetRecord]] = []
        for _score, video_index, audio_index in scored_pairs:
            if video_index in matched_videos or audio_index in matched_audios:
                continue
            matched_videos.add(video_index)
            matched_audios.add(audio_index)
            pairs.append((videos[video_index], audios[audio_index]))

        if (len(videos) > 1 or len(audios) > 1) and pairs and (
            len(matched_videos) < len(videos) or len(matched_audios) < len(audios)
        ):
            notes.append(
                "Only the clearest audio/video pairs were muxed automatically. Remaining selected media were downloaded without pairing."
            )

        return pairs

    def _match_score(self, video_asset: AssetRecord, audio_asset: AssetRecord) -> int:
        video_tokens = self._meaningful_tokens(video_asset)
        audio_tokens = self._meaningful_tokens(audio_asset)
        score = len(video_tokens & audio_tokens) * 10

        video_signature = self._token_signature(video_asset)
        audio_signature = self._token_signature(audio_asset)
        if video_signature and audio_signature and video_signature == audio_signature:
            score += 20
        elif video_signature and audio_signature and (
            video_signature in audio_signature or audio_signature in video_signature
        ):
            score += 6

        if self._source_path(video_asset).parent == self._source_path(audio_asset).parent:
            score += 4
        if self._is_stream_asset(video_asset) == self._is_stream_asset(audio_asset):
            score += 1
        return score

    def _meaningful_tokens(self, asset: AssetRecord) -> set[str]:
        token_source = " ".join(
            filter(
                None,
                (
                    asset.filename,
                    self._source_path(asset).as_posix(),
                ),
            )
        )
        return {
            token
            for token in re.findall(r"[a-z0-9]+", token_source.lower())
            if len(token) > 1 and token not in GENERIC_TOKENS
        }

    def _token_signature(self, asset: AssetRecord) -> str:
        ordered_tokens = [
            token
            for token in re.findall(r"[a-z0-9]+", Path(asset.filename).stem.lower())
            if len(token) > 1 and token not in GENERIC_TOKENS
        ]
        return "-".join(ordered_tokens)

    def _source_path(self, asset: AssetRecord) -> Path:
        if asset.url:
            parsed = urlparse(asset.url)
            return Path(parsed.path or asset.filename)
        if asset.local_path:
            return Path(asset.local_path)
        return Path(asset.filename)

    def _is_stream_asset(self, asset: AssetRecord) -> bool:
        source_value = " ".join(
            filter(
                None,
                (
                    asset.url,
                    asset.filename,
                    asset.mime_type,
                ),
            )
        ).lower()
        return any(marker in source_value for marker in STREAM_MARKERS)

    def _describe_job(self, job: MuxJob) -> str:
        if job.audio_asset is None:
            return job.video_asset.filename
        return f"{job.video_asset.filename} + {job.audio_asset.filename}"

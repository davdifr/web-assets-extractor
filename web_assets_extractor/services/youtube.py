from __future__ import annotations

from pathlib import Path
import shutil
from urllib.parse import urlparse

from web_assets_extractor.models import AssetRecord
from web_assets_extractor.utils.files import sanitize_filename, unique_path

YOUTUBE_VIDEO_ORIGIN = "yt-dlp[youtube-best]"
YOUTUBE_VIDEO_FORMAT_WITH_MUX = (
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best"
)
YOUTUBE_VIDEO_FORMAT_FALLBACK = "best[ext=mp4]/best"
YOUTUBE_DOMAINS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


class YouTubeAssetDownloader:
    def can_handle(self, asset: AssetRecord) -> bool:
        if asset.origin != YOUTUBE_VIDEO_ORIGIN:
            return False
        if not asset.url:
            return False
        return self._is_youtube_url(asset.url)

    def download(self, asset: AssetRecord, assets_dir: Path) -> Path:
        if not asset.url:
            raise ValueError(f"Asset {asset.asset_id} does not have a YouTube page URL.")

        destination = unique_path(assets_dir / self._build_filename(asset))
        youtube_dl = self._load_yt_dlp()

        options = {
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "format": self._format_selector(),
            "merge_output_format": "mp4",
            "outtmpl": {"default": str(destination)},
            "overwrites": True,
            "http_headers": {
                "User-Agent": DEFAULT_USER_AGENT,
                "Referer": asset.url,
            },
        }

        with youtube_dl.YoutubeDL(options) as downloader:
            downloader.download([asset.url])

        if destination.is_file():
            return destination

        fallback_path = self._find_downloaded_file(destination)
        if fallback_path is None:
            raise ValueError(f"yt-dlp finished without creating {destination.name}.")
        return fallback_path

    def _load_yt_dlp(self):
        try:
            import yt_dlp  # type: ignore
        except ImportError as exc:
            raise ValueError(
                "yt-dlp is required to download YouTube videos. Install dependencies again and retry."
            ) from exc
        return yt_dlp

    def _format_selector(self) -> str:
        if shutil.which("ffmpeg"):
            return YOUTUBE_VIDEO_FORMAT_WITH_MUX
        return YOUTUBE_VIDEO_FORMAT_FALLBACK

    def _build_filename(self, asset: AssetRecord) -> str:
        candidate = sanitize_filename(asset.filename, default="youtube-video.mp4")
        suffix = Path(candidate).suffix
        if suffix.lower() != ".mp4":
            return f"{Path(candidate).stem or 'youtube-video'}.mp4"
        return candidate

    def _find_downloaded_file(self, destination: Path) -> Path | None:
        parent = destination.parent
        stem = destination.stem
        candidates = sorted(
            parent.glob(f"{stem}*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return None

    def _is_youtube_url(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return host in YOUTUBE_DOMAINS or host.endswith(".youtube.com")

from __future__ import annotations

import unittest

from web_assets_extractor.models import AssetRecord
from web_assets_extractor.services.muxer import MediaMuxer


class MediaMuxerTests(unittest.TestCase):
    def test_progressive_media_is_not_auto_muxed(self) -> None:
        muxer = MediaMuxer()
        assets = [
            AssetRecord(
                asset_id="video-001",
                kind="video",
                filename="clip.mp4",
                origin="video[src]",
                url="https://cdn.example.com/clip.mp4",
            ),
            AssetRecord(
                asset_id="audio-001",
                kind="audio",
                filename="clip.m4a",
                origin="audio[src]",
                url="https://cdn.example.com/clip.m4a",
            ),
        ]

        plan = muxer.plan(assets)

        self.assertEqual(plan.jobs, [])
        self.assertEqual(plan.skip_direct_download_ids, set())

    def test_stream_pairs_are_muxed_and_skipped_from_direct_download(self) -> None:
        muxer = MediaMuxer()
        assets = [
            AssetRecord(
                asset_id="video-001",
                kind="video",
                filename="master.m3u8",
                origin="video[src]",
                url="https://cdn.example.com/stream/master.m3u8",
            ),
            AssetRecord(
                asset_id="audio-001",
                kind="audio",
                filename="audio.m3u8",
                origin="audio[src]",
                url="https://cdn.example.com/stream/audio.m3u8",
            ),
        ]

        plan = muxer.plan(assets)

        self.assertEqual(len(plan.jobs), 1)
        self.assertEqual(plan.jobs[0].source_asset_ids, ("video-001", "audio-001"))
        self.assertEqual(plan.skip_direct_download_ids, {"video-001", "audio-001"})


if __name__ == "__main__":
    unittest.main()

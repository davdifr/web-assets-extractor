from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from web_assets_extractor.models import AnalysisOptions, AnalysisPaths, AnalysisResult, AssetRecord
from web_assets_extractor.services.downloader import AssetDownloader
from web_assets_extractor.services.exporter import ReportExporter


class RecordingExporter(ReportExporter):
    def __init__(self) -> None:
        self.calls = 0

    def write_session_reports(self, result: AnalysisResult) -> None:
        self.calls += 1


class FailingDownloader(AssetDownloader):
    def __init__(self, exporter: ReportExporter) -> None:
        super().__init__(exporter)
        self.calls = 0

    def _download_asset(self, asset: AssetRecord, assets_dir: Path) -> tuple[Path, list[str]]:
        self.calls += 1
        if self.calls == 1:
            destination = assets_dir / asset.filename
            destination.write_bytes(b"ok")
            return destination, []
        raise RuntimeError("boom")


class AssetDownloaderTests(unittest.TestCase):
    def test_download_is_transactional_when_a_later_asset_fails(self) -> None:
        exporter = RecordingExporter()
        downloader = FailingDownloader(exporter)

        with TemporaryDirectory() as tmp_dir:
            root_dir = Path(tmp_dir)
            assets_dir = root_dir / "assets"
            assets_dir.mkdir()
            result = AnalysisResult(
                source_url="https://example.com",
                final_url="https://example.com",
                page_title="Example",
                page_description=None,
                status_code=200,
                analysed_at="2026-04-05T13:00:00",
                duration_ms=1,
                word_count=10,
                options=AnalysisOptions(),
                paths=AnalysisPaths(
                    root_dir=root_dir,
                    assets_dir=assets_dir,
                    report_json=root_dir / "report.json",
                    report_markdown=root_dir / "report.md",
                    assets_zip=root_dir / "assets.zip",
                ),
                assets=[
                    AssetRecord(
                        asset_id="asset-001",
                        kind="image",
                        filename="one.png",
                        origin="img[src]",
                        url="https://cdn.example.com/one.png",
                    ),
                    AssetRecord(
                        asset_id="asset-002",
                        kind="image",
                        filename="two.png",
                        origin="img[src]",
                        url="https://cdn.example.com/two.png",
                    ),
                ],
            )

            with self.assertRaisesRegex(RuntimeError, "boom"):
                downloader.download_selected_assets(result, ["asset-001", "asset-002"])

            self.assertEqual(exporter.calls, 0)
            self.assertEqual(result.downloaded_assets_count, 0)
            self.assertFalse(result.assets[0].downloaded)
            self.assertFalse(result.assets[1].downloaded)
            self.assertEqual(list(assets_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import traceback
from pathlib import Path
from typing import Sequence

from PySide6.QtCore import QObject, QRunnable, Signal

from web_assets_extractor.models import AnalysisOptions, AnalysisResult, AssetRecord, ProgressUpdate
from web_assets_extractor.services import AssetDownloader, AssetPreviewService, WebAnalyzer


class WorkerSignals(QObject):
    progress = Signal(object)
    finished = Signal(object)
    error = Signal(str)


class AnalysisWorker(QRunnable):
    def __init__(
        self,
        analyzer: WebAnalyzer,
        url: str,
        options: AnalysisOptions,
        output_root: Path,
    ) -> None:
        super().__init__()
        self._analyzer = analyzer
        self._url = url
        self._options = options
        self._output_root = output_root
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self._analyzer.analyze(
                self._url,
                self._options,
                self._output_root,
                progress_callback=self.signals.progress.emit,
            )
        except Exception:
            self.signals.error.emit(traceback.format_exc())
            return
        self.signals.finished.emit(result)


class DownloadWorker(QRunnable):
    def __init__(
        self,
        downloader: AssetDownloader,
        result: AnalysisResult,
        selected_asset_ids: Sequence[str],
        create_zip: bool,
    ) -> None:
        super().__init__()
        self._downloader = downloader
        self._result = result
        self._selected_asset_ids = list(selected_asset_ids)
        self._create_zip = create_zip
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            updated_result = self._downloader.download_selected_assets(
                self._result,
                self._selected_asset_ids,
                self._create_zip,
                progress_callback=self.signals.progress.emit,
            )
        except Exception:
            self.signals.error.emit(traceback.format_exc())
            return
        self.signals.finished.emit(updated_result)


class AssetPreviewWorker(QRunnable):
    def __init__(
        self,
        preview_service: AssetPreviewService,
        asset: AssetRecord,
    ) -> None:
        super().__init__()
        self._preview_service = preview_service
        self._asset = asset
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            preview = self._preview_service.load_preview(self._asset)
        except Exception:
            self.signals.error.emit(traceback.format_exc())
            return
        self.signals.finished.emit(preview)

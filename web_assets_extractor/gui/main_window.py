from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QThreadPool, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QCheckBox,
    QSplitter,
    QSpinBox,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from web_assets_extractor.gui.tabs import ResultsTabs
from web_assets_extractor.gui.workers import AnalysisWorker, DownloadWorker
from web_assets_extractor.models import AnalysisOptions, AnalysisResult, ProgressUpdate
from web_assets_extractor.services import AssetDownloader, ReportExporter, WebAnalyzer
from web_assets_extractor.utils.files import get_default_output_dir


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("web-assets-extractor")
        self.resize(1320, 860)
        self.setMinimumSize(1080, 720)

        self.thread_pool = QThreadPool.globalInstance()
        self._active_workers: list[object] = []
        self._analysis_running = False
        self._download_running = False
        self._current_result: AnalysisResult | None = None
        self._busy_panel_delay_ms = 250
        self._last_progress_message = ""

        self._exporter = ReportExporter()
        self._analyzer = WebAnalyzer(self._exporter)
        self._downloader = AssetDownloader(self._exporter)
        self._default_output_dir = get_default_output_dir()
        self._busy_delay_timer = QTimer(self)
        self._busy_delay_timer.setSingleShot(True)
        self._busy_delay_timer.timeout.connect(self._show_busy_panel_if_needed)

        self._build_ui()
        self._set_idle_state("Enter a URL and click Analyze.")
        self._refresh_action_states()

    def _build_ui(self) -> None:
        container = QWidget()
        self.setCentralWidget(container)

        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        root_layout.addLayout(self._build_header())
        root_layout.addWidget(self._build_status_panel())

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_controls_panel())
        splitter.addWidget(self._build_results_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([360, 920])
        root_layout.addWidget(splitter, stretch=1)

        status_bar = QStatusBar()
        self.setStatusBar(status_bar)

    def _build_header(self) -> QHBoxLayout:
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        intro_label = QLabel(
            "Analyze a public page, review the extracted data, and download only the assets you want."
        )
        intro_label.setWordWrap(True)

        self.analyze_button = QPushButton("Analyze")
        self.analyze_button.clicked.connect(self._start_analysis)

        header_layout.addWidget(intro_label, stretch=1)
        header_layout.addWidget(self.analyze_button)
        return header_layout

    def _build_status_panel(self) -> QWidget:
        self.status_panel = QWidget()
        layout = QVBoxLayout(self.status_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.progress_label = QLabel()
        self.progress_label.setWordWrap(True)

        self.progress_bar = QProgressBar()
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)

        layout.addWidget(self.progress_bar)
        layout.addWidget(self.progress_label)
        self.status_panel.hide()
        return self.status_panel

    def _build_controls_panel(self) -> QWidget:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)

        panel = QWidget()
        scroll_area.setWidget(panel)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        source_group = QGroupBox("Source")
        source_layout = QVBoxLayout(source_group)
        source_layout.setSpacing(8)

        source_layout.addWidget(QLabel("Public URL"))
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("https://example.com")
        self.url_input.returnPressed.connect(self._start_analysis)
        source_layout.addWidget(self.url_input)

        source_layout.addWidget(QLabel("Base output directory"))
        output_row = QHBoxLayout()
        self.output_dir_input = QLineEdit(str(self._default_output_dir))
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._choose_output_directory)
        output_row.addWidget(self.output_dir_input, stretch=1)
        output_row.addWidget(browse_button)
        source_layout.addLayout(output_row)

        scope_group = QGroupBox("Extract")
        scope_layout = QVBoxLayout(scope_group)
        scope_layout.setSpacing(6)

        self.fonts_checkbox = QCheckBox("Fonts")
        self.colors_checkbox = QCheckBox("Color palette")
        self.copy_checkbox = QCheckBox("Copy")
        self.assets_checkbox = QCheckBox("Assets")
        for checkbox in (
            self.fonts_checkbox,
            self.colors_checkbox,
            self.copy_checkbox,
            self.assets_checkbox,
        ):
            checkbox.setChecked(True)
            scope_layout.addWidget(checkbox)

        output_group = QGroupBox("Output")
        output_layout = QVBoxLayout(output_group)
        output_layout.setSpacing(6)
        self.zip_checkbox = QCheckBox("Create ZIP after selected assets download")
        output_layout.addWidget(self.zip_checkbox)

        brand_scan_group = QGroupBox("Brand Scan")
        brand_scan_layout = QVBoxLayout(brand_scan_group)
        brand_scan_layout.setSpacing(6)
        self.route_scan_checkbox = QCheckBox("Explore main internal routes for brand context")
        self.route_scan_checkbox.toggled.connect(self._sync_route_scan_controls)
        brand_scan_layout.addWidget(self.route_scan_checkbox)

        route_limit_row = QHBoxLayout()
        route_limit_row.addWidget(QLabel("Max extra routes"))
        self.route_limit_spinbox = QSpinBox()
        self.route_limit_spinbox.setRange(1, 10)
        self.route_limit_spinbox.setValue(5)
        self.route_limit_spinbox.setEnabled(False)
        route_limit_row.addWidget(self.route_limit_spinbox)
        route_limit_row.addStretch(1)
        brand_scan_layout.addLayout(route_limit_row)

        actions_group = QGroupBox("Actions")
        actions_layout = QGridLayout(actions_group)
        actions_layout.setHorizontalSpacing(8)
        actions_layout.setVerticalSpacing(8)

        self.download_button = QPushButton("Download Selected")
        self.download_button.clicked.connect(self._start_download)
        self.export_json_button = QPushButton("Export JSON")
        self.export_json_button.clicked.connect(self._export_json)
        self.export_markdown_button = QPushButton("Export Markdown")
        self.export_markdown_button.clicked.connect(self._export_markdown)
        self.open_folder_button = QPushButton("Open Folder")
        self.open_folder_button.clicked.connect(self._open_analysis_folder)

        actions_layout.addWidget(self.download_button, 0, 0)
        actions_layout.addWidget(self.export_json_button, 0, 1)
        actions_layout.addWidget(self.export_markdown_button, 1, 0)
        actions_layout.addWidget(self.open_folder_button, 1, 1)

        layout.addWidget(source_group)
        layout.addWidget(scope_group)
        layout.addWidget(output_group)
        layout.addWidget(brand_scan_group)
        layout.addWidget(actions_group)
        layout.addStretch(1)
        return scroll_area

    def _build_results_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self.results_tabs = ResultsTabs()
        layout.addWidget(self.results_tabs)
        return panel

    def _choose_output_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Choose output directory",
            self.output_dir_input.text(),
        )
        if directory:
            self.output_dir_input.setText(directory)

    def _sync_route_scan_controls(self, enabled: bool) -> None:
        self.route_limit_spinbox.setEnabled(enabled)

    def _start_analysis(self) -> None:
        if self._analysis_running or self._download_running:
            return

        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(self, "Missing URL", "Enter a public URL before starting.")
            return

        options = self._build_options()
        if not any(
            (
                options.analyze_fonts,
                options.analyze_colors,
                options.analyze_copy,
                options.analyze_assets,
            )
        ):
            QMessageBox.warning(
                self,
                "No extraction selected",
                "Select at least one extraction area before starting.",
            )
            return

        output_root = Path(self.output_dir_input.text().strip() or (Path.cwd() / "analysis_runs"))
        output_root.mkdir(parents=True, exist_ok=True)

        self._current_result = None
        self.results_tabs.clear_results()
        self.results_tabs.clear_log()
        self.results_tabs.show_log()
        self._append_log(f"Starting analysis for {url}")
        self._append_log(f"Output root: {output_root}")

        worker = AnalysisWorker(self._analyzer, url, options, output_root)
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.finished.connect(lambda result, w=worker: self._finish_analysis(w, result))
        worker.signals.error.connect(lambda error, w=worker: self._handle_worker_failed(w, "analysis", error))

        self._analysis_running = True
        self._begin_busy_state("Fetching page and extracting data...", indeterminate=True)
        self._start_worker(worker)

    def _start_download(self) -> None:
        if self._analysis_running or self._download_running:
            return
        if self._current_result is None:
            QMessageBox.information(
                self,
                "Nothing to download",
                "Analyze a page first to see and select the assets.",
            )
            return

        selected_asset_ids = self.results_tabs.selected_asset_ids()
        if not selected_asset_ids:
            QMessageBox.information(
                self,
                "No assets selected",
                "Check at least one asset in the Assets tab.",
            )
            return

        self.results_tabs.show_log()
        self._append_log(f"Preparing download for {len(selected_asset_ids)} assets")
        self._current_result.options.zip_downloads = self.zip_checkbox.isChecked()

        worker = DownloadWorker(
            self._downloader,
            self._current_result,
            selected_asset_ids,
        )
        worker.signals.progress.connect(self._handle_progress)
        worker.signals.finished.connect(lambda result, w=worker: self._finish_download(w, result))
        worker.signals.error.connect(lambda error, w=worker: self._handle_worker_failed(w, "download", error))

        self._download_running = True
        self._begin_busy_state("Preparing asset downloads...", indeterminate=False)
        self._start_worker(worker)

    def _start_worker(self, worker: object) -> None:
        self._active_workers.append(worker)
        self.thread_pool.start(worker)
        self._refresh_action_states()

    def _release_worker(self, worker: object) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def _handle_progress(self, update: object) -> None:
        progress = self._coerce_progress_update(update)
        self._apply_progress_update(progress)
        if progress.message and progress.message != self._last_progress_message:
            self._append_log(progress.message)
            self._last_progress_message = progress.message

    def _finish_analysis(self, worker: object, result: AnalysisResult) -> None:
        self._release_worker(worker)
        self._analysis_running = False
        self._current_result = result
        self.results_tabs.populate(result)
        self.results_tabs.show_overview()
        self._append_log(f"Analysis completed. Output saved to {result.paths.root_dir}")
        self._set_idle_state(f"Analysis completed in {result.duration_ms} ms")
        self._refresh_action_states()

    def _finish_download(self, worker: object, result: AnalysisResult) -> None:
        self._release_worker(worker)
        self._download_running = False
        self._current_result = result
        selected_ids = set(self.results_tabs.selected_asset_ids())
        self.results_tabs.populate(result, selected_asset_ids=selected_ids)
        self.results_tabs.show_overview()
        self._append_log(
            f"Download completed. {result.downloaded_assets_count} assets available in {result.paths.assets_dir}"
        )
        self._set_idle_state("Selected assets downloaded")
        self._refresh_action_states()

    def _handle_worker_failed(self, worker: object, operation_name: str, traceback_text: str) -> None:
        self._release_worker(worker)
        if operation_name == "analysis":
            self._analysis_running = False
        else:
            self._download_running = False

        self.results_tabs.show_log()
        self._append_log(traceback_text)
        error_line = traceback_text.strip().splitlines()[-1] if traceback_text.strip() else "Unknown error"
        self._set_idle_state(f"{operation_name.title()} failed")
        self._refresh_action_states()
        QMessageBox.critical(
            self,
            f"{operation_name.title()} failed",
            f"{error_line}\n\nCheck the Log tab for the full traceback.",
        )

    def _export_json(self) -> None:
        if self._current_result is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export JSON report",
            str(self._current_result.paths.report_json),
            "JSON Files (*.json)",
        )
        if not destination:
            return
        self._exporter.export_json(self._current_result, Path(destination))
        self._append_log(f"JSON report exported to {destination}")
        self.statusBar().showMessage("JSON report exported")

    def _export_markdown(self) -> None:
        if self._current_result is None:
            return
        destination, _ = QFileDialog.getSaveFileName(
            self,
            "Export Markdown report",
            str(self._current_result.paths.report_markdown),
            "Markdown Files (*.md)",
        )
        if not destination:
            return
        self._exporter.export_markdown(self._current_result, Path(destination))
        self._append_log(f"Markdown report exported to {destination}")
        self.statusBar().showMessage("Markdown report exported")

    def _open_analysis_folder(self) -> None:
        if self._current_result is None:
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_result.paths.root_dir)))

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.results_tabs.append_log(f"[{timestamp}] {message}")

    def _set_idle_state(self, message: str) -> None:
        self._busy_delay_timer.stop()
        self.status_panel.hide()
        self.progress_label.setText(message)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("")
        self.progress_bar.setTextVisible(False)
        self.statusBar().showMessage(message)
        self._last_progress_message = ""

    def _begin_busy_state(self, message: str, *, indeterminate: bool) -> None:
        self._apply_progress_update(
            ProgressUpdate(message=message, indeterminate=indeterminate),
            reveal=False,
        )
        self._busy_delay_timer.start(self._busy_panel_delay_ms)

    def _show_busy_panel_if_needed(self) -> None:
        if self._analysis_running or self._download_running:
            self.status_panel.show()

    def _apply_progress_update(self, update: ProgressUpdate, *, reveal: bool = True) -> None:
        self.progress_label.setText(update.message)
        self.statusBar().showMessage(update.message)
        if update.indeterminate or update.current is None or update.total is None or update.total <= 0:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setFormat("")
            self.progress_bar.setTextVisible(False)
        else:
            current = max(0, min(update.current, update.total))
            self.progress_bar.setRange(0, update.total)
            self.progress_bar.setValue(current)
            self.progress_bar.setFormat(f"{current}/{update.total}")
            self.progress_bar.setTextVisible(True)
        if reveal and not self.status_panel.isVisible() and not self._busy_delay_timer.isActive():
            self.status_panel.show()

    def _coerce_progress_update(self, update: object) -> ProgressUpdate:
        if isinstance(update, ProgressUpdate):
            return update
        return ProgressUpdate(message=str(update), indeterminate=True)

    def _refresh_action_states(self) -> None:
        busy = self._analysis_running or self._download_running
        has_result = self._current_result is not None
        self.analyze_button.setEnabled(not busy)
        self.download_button.setEnabled(has_result and not busy)
        self.export_json_button.setEnabled(has_result and not busy)
        self.export_markdown_button.setEnabled(has_result and not busy)
        self.open_folder_button.setEnabled(has_result and not busy)

    def _build_options(self) -> AnalysisOptions:
        return AnalysisOptions(
            analyze_fonts=self.fonts_checkbox.isChecked(),
            analyze_colors=self.colors_checkbox.isChecked(),
            analyze_copy=self.copy_checkbox.isChecked(),
            analyze_assets=self.assets_checkbox.isChecked(),
            explore_site_routes=self.route_scan_checkbox.isChecked(),
            max_route_pages=self.route_limit_spinbox.value(),
            zip_downloads=self.zip_checkbox.isChecked(),
        )

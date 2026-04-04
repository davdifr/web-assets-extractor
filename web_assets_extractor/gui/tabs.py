from __future__ import annotations

from html import escape

from PySide6.QtCore import QByteArray, Qt, QThreadPool, QUrl
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from web_assets_extractor.gui.workers import AssetPreviewWorker
from web_assets_extractor.models import AnalysisResult, AssetRecord
from web_assets_extractor.services import AssetPreview, AssetPreviewService


class OverviewTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._browser = QTextBrowser()
        self._browser.setOpenExternalLinks(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._browser)
        self.clear()

    def clear(self) -> None:
        self._browser.setHtml(
            """
            <h3>Ready</h3>
            <p>Enter a public URL, choose what to extract, and click Analyze.</p>
            """
        )

    def populate(self, result: AnalysisResult) -> None:
        options_text = ", ".join(
            label
            for label, enabled in (
                ("Fonts", result.options.analyze_fonts),
                ("Colors", result.options.analyze_colors),
                ("Copy", result.options.analyze_copy),
                ("Assets", result.options.analyze_assets),
            )
            if enabled
        ) or "None"

        stats_rows = "".join(
            f"<tr><td>{escape(label)}</td><td>{value}</td></tr>"
            for label, value in (
                ("Fonts", result.fonts_count),
                ("Colors", result.colors_count),
                ("Headlines", result.headlines_count),
                ("CTA", result.ctas_count),
                ("Copy blocks", result.copy_blocks_count),
                ("Assets", result.assets_count),
                ("Downloaded assets", result.downloaded_assets_count),
                ("Word count", result.word_count),
                ("Duration", f"{result.duration_ms} ms"),
            )
        )

        self._browser.setHtml(
            f"""
            <h3>{escape(result.page_title or "Untitled page")}</h3>
            <p>{escape(result.final_url)}</p>
            <h4>Source</h4>
            <table cellspacing="0" cellpadding="4">
              <tr><td>Requested URL</td><td>{escape(result.source_url)}</td></tr>
              <tr><td>Final URL</td><td>{escape(result.final_url)}</td></tr>
              <tr><td>Status code</td><td>{result.status_code if result.status_code is not None else "N/A"}</td></tr>
              <tr><td>Description</td><td>{escape(result.page_description or "N/A")}</td></tr>
              <tr><td>Analysed at</td><td>{escape(result.analysed_at)}</td></tr>
              <tr><td>Output directory</td><td>{escape(str(result.paths.root_dir))}</td></tr>
            </table>
            <h4>Overview</h4>
            <p>Scope: {escape(options_text)}</p>
            <table cellspacing="0" cellpadding="4">{stats_rows}</table>
            """
        )


class FontsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Family", "Occurrences"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)

    def clear(self) -> None:
        self._table.setRowCount(0)

    def populate(self, result: AnalysisResult) -> None:
        self._table.setRowCount(len(result.fonts))
        for row, font in enumerate(result.fonts):
            self._table.setItem(row, 0, QTableWidgetItem(font.family))
            count_item = QTableWidgetItem(str(font.occurrences))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 1, count_item)
        self._table.resizeRowsToContents()


class ColorsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Swatch", "Color", "Source", "Occurrences"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._table)

    def clear(self) -> None:
        self._table.setRowCount(0)

    def populate(self, result: AnalysisResult) -> None:
        self._table.setRowCount(len(result.colors))
        for row, color in enumerate(result.colors):
            swatch = QTableWidgetItem("")
            swatch.setBackground(QColor(color.value))
            swatch.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._table.setItem(row, 0, swatch)
            self._table.setItem(row, 1, QTableWidgetItem(color.value))
            self._table.setItem(row, 2, QTableWidgetItem(color.source))
            count_item = QTableWidgetItem(str(color.occurrences))
            count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self._table.setItem(row, 3, count_item)
        self._table.resizeRowsToContents()


class CopyTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._tabs = QTabWidget()
        self._headlines_table = self._build_table(["Tag", "Headline"], stretch_column=1)
        self._cta_table = self._build_table(["Text", "URL", "Tag"], stretch_column=1)
        self._copy_blocks_table = self._build_table(["Tag", "Copy Block"], stretch_column=1)

        self._tabs.addTab(self._headlines_table, "Headlines")
        self._tabs.addTab(self._cta_table, "CTA")
        self._tabs.addTab(self._copy_blocks_table, "Copy Blocks")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._tabs)

    def clear(self) -> None:
        for table in (self._headlines_table, self._cta_table, self._copy_blocks_table):
            table.setRowCount(0)

    def populate(self, result: AnalysisResult) -> None:
        self._populate_headlines(result)
        self._populate_ctas(result)
        self._populate_copy_blocks(result)

    def _populate_headlines(self, result: AnalysisResult) -> None:
        self._headlines_table.setRowCount(len(result.headlines))
        for row, item in enumerate(result.headlines):
            self._headlines_table.setItem(row, 0, QTableWidgetItem(item.tag))
            self._headlines_table.setItem(row, 1, QTableWidgetItem(item.text))
        self._headlines_table.resizeRowsToContents()

    def _populate_ctas(self, result: AnalysisResult) -> None:
        self._cta_table.setRowCount(len(result.ctas))
        for row, item in enumerate(result.ctas):
            self._cta_table.setItem(row, 0, QTableWidgetItem(item.text))
            self._cta_table.setItem(row, 1, QTableWidgetItem(item.url or ""))
            self._cta_table.setItem(row, 2, QTableWidgetItem(item.tag))
        self._cta_table.resizeRowsToContents()

    def _populate_copy_blocks(self, result: AnalysisResult) -> None:
        self._copy_blocks_table.setRowCount(len(result.copy_blocks))
        for row, item in enumerate(result.copy_blocks):
            self._copy_blocks_table.setItem(row, 0, QTableWidgetItem(item.tag))
            self._copy_blocks_table.setItem(row, 1, QTableWidgetItem(item.text))
        self._copy_blocks_table.resizeRowsToContents()

    def _build_table(self, labels: list[str], stretch_column: int) -> QTableWidget:
        table = QTableWidget(0, len(labels))
        table.setHorizontalHeaderLabels(labels)
        table.verticalHeader().setVisible(False)
        for index in range(len(labels)):
            mode = (
                QHeaderView.ResizeMode.Stretch
                if index == stretch_column
                else QHeaderView.ResizeMode.ResizeToContents
            )
            table.horizontalHeader().setSectionResizeMode(index, mode)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(True)
        return table


class AssetsTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._thread_pool = QThreadPool.globalInstance()
        self._preview_service = AssetPreviewService()
        self._active_workers: list[object] = []
        self._preview_request_id = 0
        self._row_assets: list[AssetRecord] = []
        self._video_audio_output = QAudioOutput(self)
        self._video_audio_output.setVolume(0.0)
        self._video_player = QMediaPlayer(self)
        self._video_player.setAudioOutput(self._video_audio_output)

        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels(
            ["Select", "ID", "Type", "Filename", "Source", "Origin", "Status"]
        )
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setAlternatingRowColors(True)

        self._selection_label = QLabel("0 assets selected")
        select_all_button = QPushButton("Select All")
        clear_selection_button = QPushButton("Clear Selection")
        select_all_button.clicked.connect(self.select_all)
        clear_selection_button.clicked.connect(self.clear_selection)

        controls_layout = QHBoxLayout()
        controls_layout.addWidget(self._selection_label)
        controls_layout.addStretch(1)
        controls_layout.addWidget(select_all_button)
        controls_layout.addWidget(clear_selection_button)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addLayout(controls_layout)
        left_layout.addWidget(self._table)

        self._preview_stack = QStackedWidget()

        preview_placeholder = QWidget()
        preview_placeholder_layout = QVBoxLayout(preview_placeholder)
        preview_placeholder_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_message_label = QLabel("Select an asset to preview.")
        self._preview_message_label.setWordWrap(True)
        self._preview_message_label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        preview_placeholder_layout.addWidget(self._preview_message_label)
        preview_placeholder_layout.addStretch(1)

        preview_image_page = QWidget()
        preview_image_layout = QVBoxLayout(preview_image_page)
        preview_image_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_image_label = QLabel()
        self._preview_image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_image_label.setMinimumSize(280, 220)
        self._preview_image_label.setFrameShape(QFrame.Shape.StyledPanel)
        preview_image_layout.addWidget(self._preview_image_label)

        preview_svg_page = QWidget()
        preview_svg_layout = QVBoxLayout(preview_svg_page)
        preview_svg_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_svg_widget = QSvgWidget()
        self._preview_svg_widget.setMinimumSize(280, 220)
        preview_svg_layout.addWidget(self._preview_svg_widget)

        preview_video_page = QWidget()
        preview_video_layout = QVBoxLayout(preview_video_page)
        preview_video_layout.setContentsMargins(0, 0, 0, 0)
        self._preview_video_widget = QVideoWidget()
        self._preview_video_widget.setMinimumSize(280, 220)
        self._video_player.setVideoOutput(self._preview_video_widget)
        self._preview_video_hint_label = QLabel("Video preview ready. Click Play.")
        self._preview_video_hint_label.setWordWrap(True)
        self._preview_video_play_button = QPushButton("Play")
        self._preview_video_play_button.clicked.connect(self._toggle_video_playback)
        preview_video_controls = QHBoxLayout()
        preview_video_controls.addWidget(self._preview_video_play_button)
        preview_video_controls.addStretch(1)
        preview_video_layout.addWidget(self._preview_video_widget)
        preview_video_layout.addWidget(self._preview_video_hint_label)
        preview_video_layout.addLayout(preview_video_controls)

        self._preview_stack.addWidget(preview_placeholder)
        self._preview_stack.addWidget(preview_image_page)
        self._preview_stack.addWidget(preview_svg_page)
        self._preview_stack.addWidget(preview_video_page)

        self._preview_details = QPlainTextEdit()
        self._preview_details.setReadOnly(True)
        self._preview_details.setPlaceholderText("Asset details will appear here.")
        self._preview_details.setMinimumHeight(180)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.addWidget(QLabel("Preview"))
        preview_layout.addWidget(self._preview_stack, stretch=1)
        preview_layout.addWidget(QLabel("Details"))
        preview_layout.addWidget(self._preview_details, stretch=0)

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_panel)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([780, 280])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(splitter)

        self._table.itemChanged.connect(self._update_selection_label)
        self._table.itemSelectionChanged.connect(self._load_selected_asset_preview)
        self._video_player.playbackStateChanged.connect(self._sync_video_controls)
        self._video_player.errorOccurred.connect(self._handle_video_error)

    def clear(self) -> None:
        self._row_assets = []
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.clearSelection()
        self._table.blockSignals(False)
        self._update_selection_label()
        self._reset_video_preview()
        self._show_preview_message("Select an asset to preview.", "")

    def populate(
        self,
        result: AnalysisResult,
        selected_asset_ids: set[str] | None = None,
    ) -> None:
        selected_asset_ids = selected_asset_ids or set()
        self._row_assets = list(result.assets)
        self._table.blockSignals(True)
        self._table.setRowCount(len(result.assets))
        for row, asset in enumerate(result.assets):
            checkbox_item = QTableWidgetItem()
            checkbox_item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsSelectable
                | Qt.ItemFlag.ItemIsUserCheckable
            )
            checkbox_item.setCheckState(
                Qt.CheckState.Checked
                if asset.asset_id in selected_asset_ids
                else Qt.CheckState.Unchecked
            )
            checkbox_item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
            self._table.setItem(row, 0, checkbox_item)
            self._table.setItem(row, 1, QTableWidgetItem(asset.asset_id))
            self._table.setItem(row, 2, QTableWidgetItem(asset.kind))
            self._table.setItem(row, 3, QTableWidgetItem(asset.filename))
            self._table.setItem(row, 4, QTableWidgetItem(asset.url or "inline SVG"))
            self._table.setItem(row, 5, QTableWidgetItem(asset.origin))
            self._table.setItem(
                row,
                6,
                QTableWidgetItem("Downloaded" if asset.downloaded else "Available"),
            )
        self._table.blockSignals(False)
        self._table.resizeRowsToContents()
        self._update_selection_label()

        if result.assets:
            self._table.selectRow(0)
            self._load_selected_asset_preview()
        else:
            self._show_preview_message("No assets available for preview.", "")

    def selected_asset_ids(self) -> list[str]:
        selected: list[str] = []
        for row in range(self._table.rowCount()):
            checkbox_item = self._table.item(row, 0)
            if checkbox_item and checkbox_item.checkState() == Qt.CheckState.Checked:
                asset_id = checkbox_item.data(Qt.ItemDataRole.UserRole)
                if isinstance(asset_id, str):
                    selected.append(asset_id)
        return selected

    def select_all(self) -> None:
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Checked)
        self._table.blockSignals(False)
        self._update_selection_label()

    def clear_selection(self) -> None:
        self._table.blockSignals(True)
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item:
                item.setCheckState(Qt.CheckState.Unchecked)
        self._table.blockSignals(False)
        self._update_selection_label()

    def _load_selected_asset_preview(self) -> None:
        row = self._current_row()
        if row is None:
            self._show_preview_message("Select an asset to preview.", "")
            return

        asset = self._row_assets[row]
        self._preview_request_id += 1
        request_id = self._preview_request_id
        self._show_preview_message("Loading preview...", self._build_asset_details(asset))

        worker = AssetPreviewWorker(self._preview_service, asset)
        worker.signals.finished.connect(
            lambda preview, w=worker, rid=request_id: self._handle_preview_loaded(w, rid, preview)
        )
        worker.signals.error.connect(
            lambda error, w=worker, rid=request_id: self._handle_preview_failed(w, rid, error)
        )
        self._active_workers.append(worker)
        self._thread_pool.start(worker)

    def _handle_preview_loaded(self, worker: object, request_id: int, preview: AssetPreview) -> None:
        self._release_worker(worker)
        if request_id != self._preview_request_id:
            return

        self._preview_details.setPlainText(preview.details)
        self._reset_video_preview(clear_message=False)
        if preview.mode == "pixmap" and preview.content_bytes:
            pixmap = QPixmap()
            pixmap.loadFromData(preview.content_bytes, "PNG")
            if not pixmap.isNull():
                self._preview_image_label.setPixmap(pixmap)
                self._preview_stack.setCurrentIndex(1)
                return

        if preview.mode == "svg" and preview.content_bytes:
            self._preview_svg_widget.load(QByteArray(preview.content_bytes))
            self._preview_stack.setCurrentIndex(2)
            return

        if preview.mode == "video" and preview.media_path:
            self._preview_video_hint_label.setText("Video preview ready. Click Play.")
            self._preview_video_play_button.setText("Play")
            self._video_player.setSource(QUrl.fromLocalFile(preview.media_path))
            self._preview_stack.setCurrentIndex(3)
            return

        self._show_preview_message(preview.message or "Preview unavailable.", preview.details)

    def _handle_preview_failed(self, worker: object, request_id: int, error: str) -> None:
        self._release_worker(worker)
        if request_id != self._preview_request_id:
            return
        self._show_preview_message("Preview unavailable.", error)

    def _show_preview_message(self, message: str, details: str) -> None:
        self._preview_message_label.setText(message)
        self._preview_image_label.clear()
        self._preview_svg_widget.load(QByteArray())
        self._reset_video_preview(clear_message=False)
        self._preview_details.setPlainText(details)
        self._preview_stack.setCurrentIndex(0)

    def _build_asset_details(self, asset: AssetRecord) -> str:
        rows = [
            f"ID: {asset.asset_id}",
            f"Type: {asset.kind}",
            f"Filename: {asset.filename}",
            f"Origin: {asset.origin}",
            f"Status: {'Downloaded' if asset.downloaded else 'Available'}",
        ]
        if asset.url:
            rows.append(f"Source: {asset.url}")
        return "\n".join(rows)

    def _current_row(self) -> int | None:
        selection_model = self._table.selectionModel()
        if selection_model is None:
            return None
        selected_rows = selection_model.selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        if 0 <= row < len(self._row_assets):
            return row
        return None

    def _release_worker(self, worker: object) -> None:
        if worker in self._active_workers:
            self._active_workers.remove(worker)

    def _update_selection_label(self) -> None:
        count = len(self.selected_asset_ids())
        self._selection_label.setText(f"{count} assets selected")

    def _toggle_video_playback(self) -> None:
        if self._video_player.source().isEmpty():
            return
        if self._video_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._video_player.pause()
        else:
            self._video_player.play()

    def _sync_video_controls(self, state: QMediaPlayer.PlaybackState) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._preview_video_play_button.setText("Pause")
            self._preview_video_hint_label.setText("Playing local preview copy.")
        else:
            self._preview_video_play_button.setText("Play")
            if not self._video_player.source().isEmpty():
                self._preview_video_hint_label.setText("Video preview ready. Click Play.")

    def _handle_video_error(self, _error: QMediaPlayer.Error, error_message: str) -> None:
        if error_message:
            self._preview_video_hint_label.setText(f"Video preview unavailable: {error_message}")

    def _reset_video_preview(self, *, clear_message: bool = True) -> None:
        self._video_player.stop()
        self._video_player.setSource(QUrl())
        self._preview_video_play_button.setText("Play")
        if clear_message:
            self._preview_video_hint_label.setText("Video preview ready. Click Play.")


class LogTab(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._editor = QPlainTextEdit()
        self._editor.setReadOnly(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._editor)

    def clear(self) -> None:
        self._editor.clear()

    def append_log(self, message: str) -> None:
        self._editor.appendPlainText(message)


class ResultsTabs(QTabWidget):
    def __init__(self) -> None:
        super().__init__()
        self.overview_tab = OverviewTab()
        self.fonts_tab = FontsTab()
        self.colors_tab = ColorsTab()
        self.copy_tab = CopyTab()
        self.assets_tab = AssetsTab()
        self.log_tab = LogTab()

        self.addTab(self.overview_tab, "Overview")
        self.addTab(self.fonts_tab, "Fonts")
        self.addTab(self.colors_tab, "Colors")
        self.addTab(self.copy_tab, "Copy")
        self.addTab(self.assets_tab, "Assets")
        self.addTab(self.log_tab, "Log")

    def clear_results(self) -> None:
        self.overview_tab.clear()
        self.fonts_tab.clear()
        self.colors_tab.clear()
        self.copy_tab.clear()
        self.assets_tab.clear()

    def populate(
        self,
        result: AnalysisResult,
        selected_asset_ids: set[str] | None = None,
    ) -> None:
        self.overview_tab.populate(result)
        self.fonts_tab.populate(result)
        self.colors_tab.populate(result)
        self.copy_tab.populate(result)
        self.assets_tab.populate(result, selected_asset_ids=selected_asset_ids)

    def append_log(self, message: str) -> None:
        self.log_tab.append_log(message)

    def clear_log(self) -> None:
        self.log_tab.clear()

    def selected_asset_ids(self) -> list[str]:
        return self.assets_tab.selected_asset_ids()

    def show_log(self) -> None:
        self.setCurrentWidget(self.log_tab)

    def show_overview(self) -> None:
        self.setCurrentWidget(self.overview_tab)

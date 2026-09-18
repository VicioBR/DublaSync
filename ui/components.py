from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QDialog, QTableWidget,
    QTableWidgetItem, QPushButton, QHBoxLayout, QHeaderView, QFileDialog,
    QProgressBar, QTextEdit
)
from PySide6.QtCore import Qt, Signal, QEvent
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from utils.translations import tr

class DragDropCard(QFrame):
    file_dropped = Signal(str)
    file_cleared = Signal()

    def __init__(self, title: str, icon_text: str):
        super().__init__()
        self.setAcceptDrops(True)
        self.setObjectName("DDCard")
        self.setMaximumHeight(140)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._current_style = ""
        self._has_file = False

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(5)

        self.icon_label = QLabel(icon_text)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.title_label.installEventFilter(self)
        self.info_label = QLabel(tr("card_hint"))
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        title_layout = QHBoxLayout()
        title_layout.setContentsMargins(0, 0, 0, 0)
        title_layout.setSpacing(6)
        title_layout.addStretch()
        title_layout.addWidget(self.icon_label)
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()

        layout.addLayout(title_layout)
        layout.addWidget(self.info_label)
        self.set_theme(True)

    def set_texts(self, title: str, hint: str) -> None:
        self.title_label.setText(title)
        if not self._has_file:
            self.info_label.setText(hint)

    def reset(self):
        self._has_file = False
        self.setToolTip("")
        self.info_label.setToolTip("")
        self.info_label.setText(tr("card_hint"))
        self.info_label.setStyleSheet(f"color: {self.info_color}; font-size: 12px;")
        hover_border = getattr(self, "border_hover", "#007acc")
        self._current_style = (
            f"#DDCard {{ border: 2px dashed {self.border_normal}; border-radius: 10px; "
            f"background-color: {self.bg_normal}; }}"
            f"#DDCard:hover {{ border: 2px dashed {hover_border}; background-color: {self.bg_hover}; }}"
        )
        self.setStyleSheet(self._current_style)

    def set_theme(self, is_dark: bool):
        self.is_dark = is_dark
        if is_dark:
            self.bg_normal, self.bg_hover, self.bg_active = "#222223", "#2b2c30", "#222223"
            self.border_normal, self.title_color, self.info_color = "#665a43", "#e7d9b8", "#beb18f"
            self.border_hover = "#c9a968"
            self.accent_color = "#c9a968"
        else:
            self.bg_normal, self.bg_hover, self.bg_active = "#fffdf8", "#faf4e9", "#fffdf8"
            self.border_normal, self.title_color, self.info_color = "#e5d9c7", "#443b32", "#786b5c"
            self.border_hover = "#007acc"
            self.accent_color = "#007acc"

        self.title_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {self.title_color};")
        self.icon_label.setStyleSheet(f"font-size: 20px; color: {self.title_color};")

        if self._has_file:
            self.update_info(self.info_label.text())
        else:
            self._current_style = (
                f"#DDCard {{ border: 2px dashed {self.border_normal}; border-radius: 10px; "
                f"background-color: {self.bg_normal}; }}"
                f"#DDCard:hover {{ border: 2px dashed {self.border_hover}; background-color: {self.bg_hover}; }}"
            )
            self.setStyleSheet(self._current_style)
            self.info_label.setStyleSheet(f"color: {self.info_color}; font-size: 12px;")

    def update_info(self, text: str, tooltip: str = ""):
        self._has_file = True
        self.info_label.setText(text)
        self.setToolTip(tooltip)
        self.info_label.setToolTip(tooltip)
        self.info_label.setStyleSheet(f"color: {self.accent_color}; font-weight: bold; font-size: 12px;")
        hover_border = getattr(self, "border_hover", "#007acc")
        self._current_style = (
            f"#DDCard {{ border: 2px dashed {self.accent_color}; border-radius: 10px; "
            f"background-color: {self.bg_active}; }}"
            f"#DDCard:hover {{ border: 2px dashed {hover_border}; background-color: {self.bg_hover}; }}"
        )
        self.setStyleSheet(self._current_style)

    def eventFilter(self, watched, event):
        if watched is self.title_label and event.type() == QEvent.Type.ToolTip:
            return True
        return super().eventFilter(watched, event)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            hover_border = getattr(self, "border_hover", "#007acc")
            self.setStyleSheet(f"#DDCard {{ border: 2px dashed {hover_border}; background-color: {self.bg_hover}; border-radius: 10px; }}")

    def dragLeaveEvent(self, event):
        if self._has_file:
            self.setStyleSheet(self._current_style)
        else:
            self.setStyleSheet(f"#DDCard {{ border: 2px dashed {self.border_normal}; border-radius: 10px; background-color: {self.bg_normal}; }}")

    def dropEvent(self, event: QDropEvent):
        for url in event.mimeData().urls():
            file_path = url.toLocalFile()
            if file_path:
                self.file_dropped.emit(file_path)
                break

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            file_path, _ = QFileDialog.getOpenFileName(self, tr("dlg_selecionar_arquivo"), "", tr("filtro_midia"))
            if file_path:
                self.file_dropped.emit(file_path)
        elif event.button() == Qt.MouseButton.RightButton:
            if self._has_file:
                self.file_cleared.emit()

class TrackSelectionDialog(QDialog):
    def __init__(self, streams: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_faixa_titulo"))
        self.setMinimumWidth(500)
        self.resize(540, 170)
        self.selected_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        self.table = QTableWidget(len(streams), 5)
        self.table.setHorizontalHeaderLabels([
            tr("col_faixa"), tr("col_idioma"), tr("col_codec"), tr("col_canais"), tr("col_titulo")
        ])
        header = self.table.horizontalHeader()
        compact_column_widths = (72, 88, 70, 82)
        for column, width in enumerate(compact_column_widths):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, width)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        self.table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)

        for row, stream in enumerate(streams):
            idx = stream.get('index', row) # Índice global apenas para exibição na UI
            tags = stream.get('tags', {})
            self.table.setItem(row, 0, QTableWidgetItem(str(idx)))
            self.table.setItem(row, 1, QTableWidgetItem(tags.get('language', 'und').upper()))
            self.table.setItem(row, 2, QTableWidgetItem(stream.get('codec_name', '???').upper()))
            self.table.setItem(row, 3, QTableWidgetItem(str(stream.get('channels', 2))))
            self.table.setItem(row, 4, QTableWidgetItem(tags.get('title', '')))

        visible_rows = min(max(len(streams), 1), 6)
        table_height = (
            self.table.horizontalHeader().height()
            + visible_rows * self.table.verticalHeader().defaultSectionSize()
            + 2
        )
        self.table.setMinimumHeight(table_height)
        self.table.cellDoubleClicked.connect(self._select_row)
        layout.addWidget(self.table, 1)

        btn_layout = QHBoxLayout()
        self.btn_select = QPushButton(tr("btn_selecionar_faixa"))
        self.btn_select.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_select.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_select)
        layout.addLayout(btn_layout)

    def _select_row(self, row: int, column: int) -> None:
        """Confirma a faixa clicada duas vezes, como alternativa ao botão."""
        self.table.setCurrentCell(row, column)
        self.table.selectRow(row)
        self.accept()

    def get_selected_index(self):
        row = self.table.currentRow()
        if row >= 0:
            return row  # <--- CORRIGIDO: Retorna o índice relativo (0, 1, 2...) em vez do índice global
        return 0

class FFmpegInstallerDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_ffmpeg_titulo"))
        self.resize(680, 400)

        layout = QVBoxLayout(self)
        self.progress_lbl = QLabel(tr("dlg_ffmpeg_preparando"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)

        btn_layout = QHBoxLayout()
        self.btn_reset = QPushButton(tr("btn_redefinir_config_ffmpeg"))
        self.btn_reset.setFixedWidth(190)
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setToolTip(tr("tooltip_redefinir_config_ffmpeg"))
        self.btn_reset.setEnabled(False)
        self.btn_cancel = QPushButton(tr("btn_cancelar"))
        self.btn_cancel.setFixedWidth(100)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close = QPushButton(tr("btn_fechar"))
        self.btn_close.setFixedWidth(100)
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)

        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_reset)
        btn_layout.addWidget(self.btn_cancel)
        btn_layout.addWidget(self.btn_close)

        layout.addWidget(self.progress_lbl)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.log_text)
        layout.addLayout(btn_layout)

    def append_log(self, message: str) -> None:
        self.log_text.append(message)

    def set_progress(self, message: str, value: int) -> None:
        self.progress_lbl.setText(message)
        self.progress_bar.setValue(max(0, min(100, int(value))))
        self.log_text.append(message)

    def set_working(self) -> None:
        self.btn_reset.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_close.setEnabled(False)

    def set_finished(self) -> None:
        self.btn_reset.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_close.setEnabled(True)

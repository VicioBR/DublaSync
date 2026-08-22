from PySide6.QtWidgets import (
    QFrame, QVBoxLayout, QLabel, QDialog, QTableWidget,
    QTableWidgetItem, QPushButton, QHBoxLayout, QHeaderView, QFileDialog,
    QProgressBar, QTextEdit
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from utils.translations import tr

class DragDropCard(QFrame):
    file_dropped = Signal(str)

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
        
        self.title_label = QLabel(title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.icon_label = QLabel(icon_text)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.info_label = QLabel(tr("card_hint"))
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        layout.addWidget(self.title_label)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.info_label)
        
        self.set_theme(True)

    def set_texts(self, title: str, hint: str) -> None:
        self.title_label.setText(title)
        if not self._has_file:
            self.info_label.setText(hint)

    def set_theme(self, is_dark: bool):
        self.is_dark = is_dark
        if is_dark:
            self.bg_normal, self.bg_hover, self.bg_active = "#252526", "#2d2d30", "#1e1e1e"
            self.border_normal, self.title_color, self.info_color = "#555", "#e0e0e0", "#888"
        else:
            self.bg_normal, self.bg_hover, self.bg_active = "#ffffff", "#f5f5f5", "#fafafa"
            self.border_normal, self.title_color, self.info_color = "#ccc", "#333333", "#666666"
            
        self.title_label.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {self.title_color};")
        self.icon_label.setStyleSheet(f"font-size: 32px; color: {self.title_color};")
        
        if "border: 2px solid #007acc" in self.styleSheet() or "border: 2px solid #007acc" in self._current_style:
            self.update_info(self.info_label.text())
        else:
            self._current_style = (
                f"#DDCard {{ border: 2px dashed {self.border_normal}; border-radius: 10px; "
                f"background-color: {self.bg_normal}; }}"
                f"#DDCard:hover {{ border-color: #007acc; background-color: {self.bg_hover}; }}"
            )
            self.setStyleSheet(self._current_style)
            self.info_label.setStyleSheet(f"color: {self.info_color}; font-size: 12px;")

    def update_info(self, text: str):
        self._has_file = True
        self.info_label.setText(text)
        self.info_label.setStyleSheet("color: #007acc; font-weight: bold; font-size: 12px;")
        self._current_style = (
            f"#DDCard {{ border: 2px solid #007acc; border-radius: 10px; "
            f"background-color: {self.bg_active}; }}"
        )
        self.setStyleSheet(self._current_style)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setStyleSheet(f"#DDCard {{ border: 2px solid #007acc; background-color: {self.bg_hover}; border-radius: 10px; }}")

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

class TrackSelectionDialog(QDialog):
    def __init__(self, streams: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_faixa_titulo"))
        self.resize(700, 300)
        self.selected_index = 0
        
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(streams), 5)
        self.table.setHorizontalHeaderLabels([
            tr("col_faixa"), tr("col_idioma"), tr("col_codec"), tr("col_canais"), tr("col_titulo")
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        
        for row, stream in enumerate(streams):
            idx = stream.get('index', row)
            tags = stream.get('tags', {})
            self.table.setItem(row, 0, QTableWidgetItem(str(idx)))
            self.table.setItem(row, 1, QTableWidgetItem(tags.get('language', 'und').upper()))
            self.table.setItem(row, 2, QTableWidgetItem(stream.get('codec_name', '???').upper()))
            self.table.setItem(row, 3, QTableWidgetItem(str(stream.get('channels', 2))))
            self.table.setItem(row, 4, QTableWidgetItem(tags.get('title', '')))
            
        layout.addWidget(self.table)
        
        btn_layout = QHBoxLayout()
        self.btn_select = QPushButton(tr("btn_selecionar_faixa"))
        self.btn_select.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_select)
        layout.addLayout(btn_layout)

    def get_selected_index(self):
        row = self.table.currentRow()
        if row >= 0:
            return int(self.table.item(row, 0).text())
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
        self.btn_cancel = QPushButton(tr("btn_cancelar"))
        self.btn_cancel.setFixedWidth(100)
        self.btn_close = QPushButton(tr("btn_fechar"))
        self.btn_close.setFixedWidth(100)
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        
        btn_layout.addStretch()
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
        self.btn_cancel.setEnabled(True)
        self.btn_close.setEnabled(False)

    def set_finished(self) -> None:
        self.btn_cancel.setEnabled(False)
        self.btn_close.setEnabled(True)
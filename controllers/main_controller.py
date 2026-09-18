import os
import re
import tempfile
import time
import datetime
import subprocess
import json
import threading
import numpy as np
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from statistics import median
from PySide6.QtCore import QObject, Qt, QTimer, QPoint, QPointF, QRect, QThread, Signal, QSettings, QEvent
from PySide6.QtGui import (QAction, QColor, QPen, QPainter, QPixmap, QPolygon,
                           QDoubleValidator, QPalette)
from PySide6.QtWidgets import (QMessageBox, QPushButton, QHBoxLayout, QDialog, QVBoxLayout,
                               QTextEdit, QRadioButton, QButtonGroup, QLabel,
                               QComboBox, QGroupBox, QLineEdit, QLayout, QWidget,
                               QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
                               QApplication, QStyledItemDelegate, QStyle)
from ui.main_window import MainWindow
from ui.components import TrackSelectionDialog, FFmpegInstallerDialog
from ui.styles import (
    obter_estilo_btn_analyze,
    obter_estilo_btn_convert,
    obter_estilo_btn_cancel,
    obter_estilo_btn_mux,
    obter_estilo_btn_segmented,
    obter_estilo_btn_sync_audio,
)
from utils.core_logic import (get_file_info, format_time, format_elapsed_time, check_dependencies,
                              check_rubberband_available, select_audio_output, get_ffmpeg_audio_encoders,
                              ajustar_corte_para_silencio)
from utils import ffmpeg_tools as ft
from utils import mkvtoolnix_handler as mth
from utils.translations import tr, get_idioma
from workers.tasks import SyncWorker, FFmpegWorker, SegmentedSyncWorker
from workers.installer_worker import FFmpegVerifier, FFmpegInstaller


# ====================== HELPERS VISUAIS ======================
def _criar_icone_mensagem(tipo: str, tamanho: int = 32) -> QPixmap:
    if tipo == "aviso":
        tamanho = int(tamanho * 0.72)
    cores = {
        "erro": "#e74c3c",
        "sucesso": "#2ecc71",
        "aviso": "#f1c40f",
        "info": "#007acc"
    }
    pix = QPixmap(tamanho, tamanho)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if tipo == "aviso":
        amarelo, escuro = QColor(cores["aviso"]), QColor("#2b2b2b")
        margem = int(tamanho * 0.16)
        p.setPen(QPen(
            amarelo,
            tamanho * 0.16,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        ))
        p.setBrush(amarelo)
        p.drawPolygon(QPolygon([
            QPoint(tamanho // 2, margem),
            QPoint(tamanho - margem, tamanho - margem),
            QPoint(margem, tamanho - margem)
        ]))
        p.setPen(QPen(
            escuro,
            max(2, tamanho // 9),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap
        ))
        p.drawLine(
            tamanho // 2,
            int(tamanho * 0.34),
            tamanho // 2,
            int(tamanho * 0.62)
        )
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(escuro)
        p.drawEllipse(tamanho // 2 - 2, int(tamanho * 0.68), 4, 4)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(cores.get(tipo, "#007acc")))
        p.drawEllipse(0, 0, tamanho, tamanho)
        branco, traco = QColor("#ffffff"), QPen(QColor("#ffffff"), max(2, tamanho // 10))
        traco.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(traco)
        if tipo == "erro":
            m = int(tamanho * 0.30)
            p.drawLine(m, m, tamanho - m, tamanho - m)
            p.drawLine(tamanho - m, m, m, tamanho - m)
        elif tipo == "sucesso":
            p.drawLine(int(tamanho * 0.27), int(tamanho * 0.53), int(tamanho * 0.44), int(tamanho * 0.70))
            p.drawLine(int(tamanho * 0.44), int(tamanho * 0.70), int(tamanho * 0.75), int(tamanho * 0.32))
        else:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(branco)
            p.drawEllipse(tamanho // 2 - 2, int(tamanho * 0.22), 4, 4)
            p.setPen(traco)
            p.drawLine(
                tamanho // 2,
                int(tamanho * 0.42),
                tamanho // 2,
                int(tamanho * 0.76)
            )
    p.end()
    return pix


def _tr_seg(chave: str, fallback: str) -> str:
    txt = tr(chave)
    return txt if txt != chave else fallback


@dataclass(frozen=True)
class SpeedCorrectionResult:
    """Resultado comum após corrigir a velocidade e medir o offset final."""

    corrected_audio_path: str
    final_offset: float
    analysis: dict
    selected_filter: str
    tempo_str: str


def _parse_time_text(texto: str):
    t = (texto or "").strip()
    if t.startswith("~"):
        t = t[1:].strip()
    if not t:
        return None
    t = t.replace(",", ".")
    m = re.fullmatch(r"(\d+):(\d{1,2}):(\d{1,2}(?:\.\d+)?)", t)
    if m:
        h, mi, s = m.groups()
        return int(h) * 3600 + int(mi) * 60 + float(s)
    m = re.fullmatch(r"(\d{1,2}):(\d{1,2}(?:\.\d+)?)", t)
    if m:
        mi, s = m.groups()
        return int(mi) * 60 + float(s)
    m = re.fullmatch(r"(\d+(?:\.\d+)?)", t)
    if m:
        return float(m.group(1))
    return None


_AUDIO_SYNC_TEXTS = {
    "pt": {
        "btn": "Sincronizar Áudio",
        "titulo": "Sincronizar Áudio",
        "arquivo": "Arquivo dublado: ",
        "offset_lbl": "Ajuste de sincronização (ms): ",
        "offset_nota": (
            "Valores positivos atrasam o áudio, adicionando silêncio ao início.\n"
            "Valores negativos adiantam o áudio, removendo o início.\n\n"
            "Uma análise anterior foi encontrada. O ajuste de sincronização foi "
            "preenchido automaticamente, mas você pode alterá-lo antes de prosseguir."
        ),
        "offset_nota_sem_analise": (
            "Valores positivos atrasam o áudio, adicionando silêncio ao início.\n"
            "Valores negativos adiantam o áudio, removendo o início."
        ),
        "offset_placeholder": "1000 ou -1000",
        "offset_invalido": "Valor inválido. Digite um número em milissegundos.",
        "recode_titulo": "Recodificação necessária",
        "recode_msg": (
            "O áudio precisa ser recodificado para aplicar o offset.\n\n"
            "Arquivo: {arquivo}\n"
            "Codec de entrada: {entrada}\n"
            "Codec de saída: {saida}\n"
            "Extensão de saída: {ext}\n\n"
            "Deseja prosseguir?"
        ),
        "btn_prosseguir": "Prosseguir",
        "inicio": "Iniciando sincronização de áudio com offset {v} ms...",
        "concluido": "Áudio sincronizado com sucesso: {arquivo}",
        "concluido_sem_recode": "[ÁUDIO] Processo concluído SEM recodificação (stream copy).",
        "concluido_com_recode": "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).",
        "synclog_titulo": "[SINCRONIZAÇÃO DE ÁUDIO]",
        "synclog_origem": "Arquivo de origem:",
        "synclog_saida": "Arquivo gerado:",
        "synclog_offset": "Offset aplicado:",
        "synclog_status": "Status:",
        "synclog_props": "Propriedades:",
        "status_sem_recode": "SEM recodificação (stream copy)",
        "status_com_recode": "COM recodificação (codec: {codec})",
        "sem_arquivo": "Nenhum arquivo dublado carregado.",
        "sem_analise": "Execute primeiro a análise para liberar o botão Sincronizar Áudio.",
        "obtendo_ext": "Determinando extensão de saída...",
        "falha_props": "Não foi possível ler as propriedades de áudio do arquivo dublado.",
    },
    "en": {
        "btn": "Sync Audio",
        "titulo": "Sync Audio",
        "arquivo": "Dubbed file: ",
        "offset_lbl": "Sync adjustment (ms): ",
        "offset_nota": (
            "Positive values delay the audio by adding silence at the beginning.\n"
            "Negative values advance the audio by removing the beginning.\n\n"
            "A previous analysis was found. The sync adjustment was filled in "
            "automatically, but you can change it before proceeding."
        ),
        "offset_nota_sem_analise": (
            "Positive values delay the audio by adding silence at the beginning.\n"
            "Negative values advance the audio by removing the beginning."
        ),
        "offset_placeholder": "1000 or -1000",
        "offset_invalido": "Invalid value. Enter a number in milliseconds.",
        "recode_titulo": "Re-encoding required",
        "recode_msg": (
            "The audio must be re-encoded to apply the offset.\n\n"
            "File: {arquivo}\n"
            "Input codec: {entrada}\n"
            "Output codec: {saida}\n"
            "Output extension: {ext}\n\n"
            "Do you want to proceed?"
        ),
        "btn_prosseguir": "Proceed",
        "inicio": "Starting audio sync with offset {v} ms...",
        "concluido": "Audio synced successfully: {arquivo}",
        "concluido_sem_recode": "[AUDIO] Process completed WITHOUT re-encoding (stream copy).",
        "concluido_com_recode": "[AUDIO] Process completed WITH re-encoding (codec: {codec}).",
        "synclog_titulo": "[AUDIO SYNCHRONIZATION]",
        "synclog_origem": "Source file:",
        "synclog_saida": "Output file:",
        "synclog_offset": "Applied offset:",
        "synclog_status": "Status:",
        "synclog_props": "Properties:",
        "status_sem_recode": "WITHOUT re-encoding (stream copy)",
        "status_com_recode": "WITH re-encoding (codec: {codec})",
        "sem_arquivo": "No dubbed file loaded.",
        "sem_analise": "Run the analysis first to enable the Sync Audio button.",
        "obtendo_ext": "Determining output extension...",
        "falha_props": "Could not read audio properties from the dubbed file.",
    },
    "es": {
        "btn": "Sincronizar Audio",
        "titulo": "Sincronizar Audio",
        "arquivo": "Archivo doblado: ",
        "offset_lbl": "Ajuste de sincronización (ms): ",
        "offset_nota": (
            "Los valores positivos retrasan el audio, añadiendo silencio al inicio.\n"
            "Los valores negativos adelantan el audio, eliminando el inicio.\n\n"
            "Se encontró un análisis anterior. El ajuste de sincronización se "
            "rellenó automáticamente, pero puede modificarlo antes de continuar."
        ),
        "offset_nota_sem_analise": (
            "Los valores positivos retrasan el audio, añadiendo silencio al inicio.\n"
            "Los valores negativos adelantan el audio, eliminando el inicio."
        ),
        "offset_placeholder": "1000 o -1000",
        "offset_invalido": "Valor inválido. Escriba un número en milisegundos.",
        "recode_titulo": "Recodificación necessária",
        "recode_msg": (
            "El audio debe recodificarse para aplicar el offset.\n\n"
            "Archivo: {arquivo}\n"
            "Códec de entrada: {entrada}\n"
            "Códec de salida: {saida}\n"
            "Extensión de salida: {ext}\n\n"
            "¿Desea continuar?"
        ),
        "btn_prosseguir": "Continuar",
        "inicio": "Iniciando sincronización de audio con offset {v} ms...",
        "concluido": "Audio sincronizado con éxito: {arquivo}",
        "concluido_sem_recode": "[AUDIO] Proceso completado SIN recodificación (stream copy).",
        "concluido_com_recode": "[AUDIO] Proceso completado CON recodificación (codec: {codec}).",
        "synclog_titulo": "[SINCRONIZACIÓN DE AUDIO]",
        "synclog_origem": "Archivo de origen:",
        "synclog_saida": "Archivo generado:",
        "synclog_offset": "Offset aplicado:",
        "synclog_status": "Estado:",
        "synclog_props": "Propiedades:",
        "status_sem_recode": "SIN recodificación (stream copy)",
        "status_com_recode": "CON recodificación (codec: {codec})",
        "sem_arquivo": "No hay archivo doblado cargado.",
        "sem_analise": "Ejecute primero el análisis para habilitar el botón Sincronizar Audio.",
        "obtendo_ext": "Determinando extensión de salida...",
        "falha_props": "No fue posible leer las propiedades de audio del archivo doblado.",
    },
}


def _as_tr(key: str, fallback: str = "") -> str:
    try:
        lang = get_idioma()
    except Exception:
        lang = "pt"
    lang = lang if lang in _AUDIO_SYNC_TEXTS else "pt"
    return _AUDIO_SYNC_TEXTS.get(lang, _AUDIO_SYNC_TEXTS["pt"]).get(key, fallback)


class AudioSyncCancelled(Exception):
    pass


class AudioSyncOffsetDialog(QDialog):
    def __init__(self, initial_ms: float, arquivo: str, parent=None, has_analysis: bool = True):
        super().__init__(parent)
        self.setWindowTitle(_as_tr("titulo", "Sincronizar Áudio"))
        self.setMinimumWidth(360)
        self._valor = None

        is_dark = True
        if parent and hasattr(parent, 'result_label') and hasattr(parent.result_label, 'is_dark'):
            is_dark = parent.result_label.is_dark
        else:
            is_dark = QSettings("Vicio", "DublaSync").value("tema", "escuro") != "claro"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)

        lbl_arquivo = QLabel(f"{_as_tr('arquivo', 'Arquivo dublado:')} {os.path.basename(arquivo)}")
        lbl_arquivo.setWordWrap(True)
        lbl_arquivo.setStyleSheet("font-weight: bold;")

        lbl_offset = QLabel(_as_tr("offset_lbl", "Ajuste de sincronização (ms):"))

        self.edit = QLineEdit()
        self.edit.setMaxLength(16)
        placeholder = _as_tr("offset_placeholder", "1000 ou -1000")
        self.edit.setPlaceholderText(placeholder)

        if has_analysis:
            self.edit.setText(str(int(round(initial_ms))))
        else:
            self.edit.clear()

        # Ajuste da largura do campo para comportar 16 caracteres (e a marca d'água sem corte)
        fm = self.edit.fontMetrics()
        w_16_chars = max(fm.horizontalAdvance("0" * 16), fm.horizontalAdvance(placeholder)) + 28
        self.edit.setFixedWidth(w_16_chars)

        # Estilo do campo e cor da marca d'água para ambos os temas
        bg_edit = "#252526" if is_dark else "#ffffff"
        fg_edit = "#e7d9b8" if is_dark else "#000000"
        border_edit = "#665a43" if is_dark else "#cccccc"
        ph_color = "#a89772" if is_dark else "#767676"
        focus_border = "#c9a968" if is_dark else "#007acc"

        pal = self.edit.palette()
        pal.setColor(QPalette.PlaceholderText, QColor(ph_color))
        self.edit.setPalette(pal)

        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {bg_edit};
                color: {fg_edit};
                border: 1px solid {border_edit};
                border-radius: 4px;
                padding: 4px 8px;
                font-size: 13px;
                placeholder-text-color: {ph_color};
            }}
            QLineEdit:focus {{
                border: 1px solid {focus_border};
            }}
        """)

        offset_box = QVBoxLayout()
        offset_box.setSpacing(4)
        offset_box.addWidget(lbl_offset)
        offset_box.addWidget(self.edit)

        nota_key = "offset_nota" if has_analysis else "offset_nota_sem_analise"
        nota = QLabel(_as_tr(nota_key, ""))
        nota.setWordWrap(True)
        nota_cor = "#beb18f" if is_dark else "#666666"
        nota.setStyleSheet(f"color: {nota_cor}; font-size: 12px;")

        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(tr("btn_cancelar"))
        btn_ok = QPushButton("OK")
        btn_ok.setDefault(True)
        btn_ok.setFocus()
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)

        layout.addWidget(lbl_arquivo)
        layout.addLayout(offset_box)
        layout.addWidget(nota)
        layout.addLayout(btn_layout)

        self.adjustSize()

    def showEvent(self, event):
        super().showEvent(event)
        self.adjustSize()

    def _parse(self):
        texto = (self.edit.text() or "").strip().replace(",", ".")
        if not texto:
            return 0.0
        if texto.startswith("+"):
            texto = texto[1:]
        return float(texto)

    def get_offset_ms(self) -> float:
        if self._valor is None:
            return 0.0
        return float(self._valor)

    def accept(self):
        try:
            self._valor = float(self._parse())
            super().accept()
        except Exception:
            QMessageBox.warning(
                self,
                _as_tr("titulo", "Sincronizar Áudio"),
                _as_tr("offset_invalido", "Valor inválido. Digite um número em milissegundos.")
            )


class AudioSyncWorker(QThread):
    progress = Signal(str, float)
    log = Signal(str)
    finished = Signal(str)
    error = Signal(str)
    request_recode = Signal(object)

    COPY_RULES = {
        "ac3": {
            "encoders": ["ac3"],
            "formats": ["ac3"],
            "elem_ext": ".ac3",
            "out_ext": ".ac3",
            "containers": [".mkv", ".mp4", ".m4a", ".ts", ".mov"],
            "frame_method": "frames"
        },
        "eac3": {
            "encoders": ["eac3"],
            "formats": ["eac3"],
            "elem_ext": ".eac3",
            "out_ext": ".eac3",
            "containers": [".mkv", ".mp4", ".m4a", ".ts", ".mov"],
            "frame_method": "frames"
        },
        "mp3": {
            "encoders": ["libmp3lame", "mp3"],
            "formats": ["mp3"],
            "elem_ext": ".mp3",
            "out_ext": ".mp3",
            "containers": [".mkv", ".mp4", ".ts"],
            "frame_method": "frames"
        },
        "mp2": {
            "encoders": ["mp2", "libtwolame"],
            "formats": ["mp2"],
            "elem_ext": ".mp2",
            "out_ext": ".mp2",
            "containers": [".mkv", ".mp4", ".ts"],
            "frame_method": "frames"
        },
        "aac": {
            "encoders": ["aac", "libfdk_aac"],
            "formats": ["aac"],
            "elem_ext": ".aac",
            "out_ext": ".m4a",
            "containers": [".m4a"],
            "frame_method": "frames"
        },
        "dts": {
            "encoders": ["dca"],
            "formats": ["dts"],
            "elem_ext": ".dts",
            "out_ext": ".dts",
            "containers": [".mkv", ".mp4", ".ts"],
            "frame_method": "frames"
        },
    }

    def __init__(self, input_path: str, audio_index: int, offset_ms: float, output_path: str):
        super().__init__()
        self.input_path = input_path
        self.audio_index = int(audio_index or 0)
        self.offset_ms = float(offset_ms)
        self.output_path = output_path
        self._cancelled = False
        self._recode_event = threading.Event()
        self._recode_allowed = False
        self.process = None
        self.reencode_status = _as_tr("status_sem_recode", "SEM recodificação (stream copy)")
        self.last_props = {}

    # ============================ INFRA ============================
    def cancel(self):
        self._cancelled = True
        self._recode_event.set()
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

    def allow_recode(self, allowed: bool):
        self._recode_allowed = bool(allowed)
        self._recode_event.set()

    def _check_cancelled(self):
        if self._cancelled:
            raise AudioSyncCancelled()

    @staticmethod
    def _creationflags():
        return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    @staticmethod
    def _run_static(cmd, timeout=30):
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=AudioSyncWorker._creationflags()
        )

    @staticmethod
    def _obter_encoders():
        try:
            return get_ffmpeg_audio_encoders()
        except Exception:
            return set()

    def _add_hide(self, cmd):
        if not cmd:
            return cmd
        nome = os.path.basename(str(cmd[0])).lower()
        if nome in ("ffmpeg", "ffmpeg.exe"):
            return [cmd[0], "-hide_banner", "-loglevel", "error"] + cmd[1:]
        return cmd

    def _add_progress(self, cmd):
        if not cmd:
            return cmd
        nome = os.path.basename(str(cmd[0])).lower()
        if nome in ("ffmpeg", "ffmpeg.exe"):
            return [cmd[0], "-hide_banner", "-loglevel", "error", "-progress", "pipe:1", "-nostats"] + cmd[1:]
        return cmd

    def _run_short(self, cmd):
        self._check_cancelled()
        cmd = self._add_hide(cmd)
        try:
            p = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._creationflags()
            )
        except FileNotFoundError as e:
            raise RuntimeError(str(e))
        self._check_cancelled()
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()
            return p.returncode, err[-2000:]
        return 0, ""

    def _run_progress(self, cmd, total_ms, stage):
        self._check_cancelled()
        cmd = self._add_progress(cmd)
        tail = []
        proc = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._creationflags()
            )
            self.process = proc
            atual_ms = 0.0
            for line in proc.stdout:
                if self._cancelled:
                    break
                line = line.strip()
                if not line:
                    continue
                tail.append(line)
                if len(tail) > 200:
                    tail.pop(0)
                if line.startswith("out_time_us="):
                    try:
                        atual_ms = float(line.split("=", 1)[1]) / 1000.0
                    except Exception:
                        pass
                elif line.startswith("out_time_ms="):
                    try:
                        atual_ms = float(line.split("=", 1)[1]) / 1000.0
                    except Exception:
                        pass
                elif line.startswith("progress="):
                    if total_ms and total_ms > 0:
                        pct = min(100.0, atual_ms / total_ms * 100.0)
                        self.progress.emit(f"{stage}    {pct:5.1f}%", pct)
                    else:
                        self.progress.emit(stage, 0.0)
            proc.wait()
        except FileNotFoundError as e:
            raise RuntimeError(str(e))
        finally:
            self.process = None
        if self._cancelled:
            if proc:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            raise AudioSyncCancelled()
        if proc is None:
            raise RuntimeError("FFmpeg não iniciado.")
        if proc.returncode != 0:
            return proc.returncode, "\n".join(tail)[-2000:]
        return 0, ""

    def _ask_recode(self, info: dict) -> bool:
        self._check_cancelled()
        self._recode_event.clear()
        self._recode_allowed = False
        self.request_recode.emit(info)
        self._recode_event.wait()
        self._check_cancelled()
        return bool(self._recode_allowed)

    def _adjust_output_path(self, ext: str) -> str:
        base, _ = os.path.splitext(self.output_path)
        new_path = f"{base}{ext}"
        if new_path != self.output_path:
            self.log.emit(f"Extensão de saída ajustada para: {ext}")
            self.output_path = new_path
        return self.output_path

    # ============================ PROPS ============================
    @staticmethod
    def _frame_normativo(props):
        codec = props.get("codec_name", "")
        profile = props.get("codec_profile", "")
        try:
            sr = int(props.get("sample_rate", 0))
        except Exception:
            sr = 0
        if sr <= 0:
            return None, None
        if codec in ("ac3", "eac3"):
            samples = 1536
        elif codec in ("mp3", "mp2"):
            samples = 1152 if sr in (32000, 44100, 48000) else 576
        elif codec == "aac":
            samples = 2048 if "he" in profile else 1024
        elif codec == "dts":
            samples = 512
        elif codec.startswith("pcm_"):
            samples = 1
        else:
            return None, None
        return samples, (samples / sr) * 1000.0

    @staticmethod
    def _frame_pacotes(arquivo, audio_index):
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", f"a:{audio_index}",
            "-show_packets",
            "-show_entries", "packet=duration_time",
            "-read_intervals", "%+#30",
            "-of", "json",
            arquivo
        ]
        try:
            r = AudioSyncWorker._run_static(cmd, timeout=20)
            if r.returncode != 0:
                return None
            data = json.loads(r.stdout)
            valores = []
            for p in data.get("packets", []):
                try:
                    dt = float(p.get("duration_time"))
                except Exception:
                    continue
                if 0.0005 <= dt <= 1.0:
                    valores.append(dt * 1000.0)
            if not valores:
                return None
            return median(valores) if len(valores) >= 3 else valores[0]
        except Exception:
            return None

    @staticmethod
    def _info_frame(arquivo, audio_index, props):
        codec = props.get("codec_name", "")
        samples_norm, ms_norm = AudioSyncWorker._frame_normativo(props)
        ms_pacotes = AudioSyncWorker._frame_pacotes(arquivo, audio_index)
        try:
            sr = int(props.get("sample_rate", 0))
        except Exception:
            sr = 0
        if codec.startswith("pcm_") and ms_norm:
            return ms_norm, "normativa (1 amostra)", samples_norm
        if ms_pacotes:
            usar_medido = codec in ("eac3", "aac", "opus", "vorbis", "flac", "dts")
            if not usar_medido and ms_norm and abs(ms_pacotes - ms_norm) > max(0.15 * ms_norm, 0.5):
                usar_medido = True
            if usar_medido:
                samples = int(round(ms_pacotes * sr / 1000.0)) if sr > 0 else None
                return ms_pacotes, "medida nos pacotes", samples
        if ms_norm:
            return ms_norm, "normativa do codec", samples_norm
        if ms_pacotes:
            samples = int(round(ms_pacotes * sr / 1000.0)) if sr > 0 else None
            return ms_pacotes, "medida nos pacotes", samples
        return None, None, None

    @staticmethod
    def _probe_props(arquivo, audio_index):
        cmd = [
            "ffprobe",
            "-v", "error",
            "-select_streams", f"a:{audio_index}",
            "-show_entries",
            "stream=codec_name,codec_profile,sample_rate,channels,channel_layout,bit_rate,sample_fmt:format=format_name,duration,bit_rate",
            "-of", "json",
            arquivo
        ]
        try:
            r = AudioSyncWorker._run_static(cmd, timeout=30)
        except Exception:
            return None
        if r.returncode != 0:
            return None
        try:
            dados = json.loads(r.stdout)
        except Exception:
            return None
        streams = dados.get("streams") or []
        if not streams:
            return None
        s = streams[0]
        fmt = dados.get("format") or {}
        codec_name = (s.get("codec_name") or "unknown").lower()
        codec_profile = (s.get("codec_profile") or "").lower()
        sample_rate = str(s.get("sample_rate") or "48000")
        try:
            channels = int(s.get("channels") or 2)
        except Exception:
            channels = 2
        channel_layout = s.get("channel_layout") or ""
        format_name = (fmt.get("format_name") or "").lower()
        sample_fmt = (s.get("sample_fmt") or "").lower()
        bitrate = None
        try:
            br = int(s.get("bit_rate") or fmt.get("bit_rate") or 0)
            if br > 0:
                bitrate = f"{br // 1000}k"
        except Exception:
            pass
        if channels == 1:
            layout = "mono"
        elif channels == 2:
            layout = "stereo"
        elif channels == 6:
            layout = "5.1"
        elif channels == 8:
            layout = "7.1"
        else:
            layout = channel_layout or f"{channels}c"
        props = {
            "arquivo": arquivo,
            "codec_name": codec_name,
            "codec_profile": codec_profile,
            "sample_rate": sample_rate,
            "channels": channels,
            "layout": layout,
            "bitrate": bitrate,
            "sample_fmt": sample_fmt,
            "format_name": format_name,
            "ext": os.path.splitext(arquivo)[1].lower(),
        }
        frame_ms, frame_fonte, frame_samples = AudioSyncWorker._info_frame(arquivo, audio_index, props)
        props.update(
            frame_ms=frame_ms,
            frame_fonte=frame_fonte,
            frame_samples=frame_samples
        )
        return props

    @staticmethod
    def _duracao_ms(arquivo):
        cmd = [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            arquivo
        ]
        try:
            r = AudioSyncWorker._run_static(cmd, timeout=20)
            return float(r.stdout.strip()) * 1000.0
        except Exception:
            return None

    @staticmethod
    def get_output_extension(input_path: str, audio_index: int) -> str:
        props = AudioSyncWorker._probe_props(input_path, audio_index)
        if not props:
            return ".wav"
        encoders = AudioSyncWorker._obter_encoders()
        strat = AudioSyncWorker._estrategia_copy(props, encoders)
        if strat:
            return strat["out_ext"]
        _, ext, _ = AudioSyncWorker._saida_reencode(props, encoders)
        return ext or ".wav"

    # ============================ REGRAS ============================
    @staticmethod
    def _opcoes_encoder(encoder, props):
        bitrate = props.get("bitrate")
        if encoder in ("aac", "libfdk_aac"):
            return ["-b:a", bitrate or "256k"]
        if encoder in ("libmp3lame", "mp3"):
            return ["-b:a", bitrate or "192k"]
        if encoder in ("mp2", "libtwolame"):
            return ["-b:a", bitrate or "192k"]
        if encoder in ("ac3", "eac3"):
            return ["-b:a", bitrate or "640k"]
        if encoder == "dca":
            return ["-b:a", bitrate or "768k", "-strict", "-2"]
        if encoder == "flac":
            return ["-compression_level", "5"]
        if encoder == "libopus":
            return ["-b:a", bitrate or "160k"]
        if encoder == "libvorbis":
            return ["-q:a", "6"]
        return []

    @staticmethod
    def _params_saida(props):
        params = ["-ar", str(props.get("sample_rate", "48000"))]
        if props.get("layout"):
            params += ["-channel_layout", props["layout"]]
        return params

    @staticmethod
    def _encoder_pcm_para(props):
        fmt = (props.get("sample_fmt") or "").lower()
        if "s32" in fmt:
            return "pcm_s32le"
        if "s24" in fmt:
            return "pcm_s24le"
        if "dbl" in fmt:
            return "pcm_f64le"
        if "flt" in fmt:
            return "pcm_f32le"
        return "pcm_s16le"

    @staticmethod
    def _calcular_frames(offset_ms, frame_ms):
        if frame_ms is None or frame_ms <= 0:
            return 0, abs(offset_ms), 0.0
        frames = int(abs(offset_ms) / frame_ms)
        duracao = frames * frame_ms
        resto = abs(offset_ms) - duracao
        return frames, duracao, resto

    @staticmethod
    def _extensao_saida_para_codec(props):
        codec = props.get("codec_name", "")
        ext = props.get("ext", "")
        if codec.startswith("pcm_"):
            return ".wav"
        if codec == "flac":
            return ".flac"
        if codec == "alac":
            return ".m4a"
        if codec == "opus":
            return ".opus"
        if codec == "vorbis":
            return ".ogg"
        rule = AudioSyncWorker.COPY_RULES.get(codec)
        if rule:
            return rule["out_ext"]
        return ext or ".wav"

    @staticmethod
    def _estrategia_copy(props, encoders):
        codec = props["codec_name"]
        fmt = props.get("format_name", "")
        ext = props.get("ext", "")
        profile = props.get("codec_profile", "")
        if codec.startswith("pcm_") and (ext == ".wav" or "wav" in fmt):
            if codec in encoders:
                return {
                    "encoder": codec,
                    "elem_ext": ".wav",
                    "out_ext": ".wav",
                    "raw_input": True,
                    "frame_method": "duration"
                }
            return None
        rule = AudioSyncWorker.COPY_RULES.get(codec)
        if not rule:
            return None
        elem_ext = rule["elem_ext"]
        raw_input = (ext == elem_ext)
        alias = (
            (codec == "eac3" and ext == ".ac3") or
            (codec == "ac3" and ext == ".eac3")
        )
        allowed = raw_input or alias or ext in rule.get("containers", [])
        if not allowed:
            return None
        if codec == "aac" and "he" in profile:
            return None
        if codec == "eac3":
            _, norm_ms = AudioSyncWorker._frame_normativo(props)
            frame_ms = props.get("frame_ms")
            if (
                props.get("frame_fonte") == "medida nos pacotes"
                and frame_ms
                and norm_ms
                and abs(frame_ms - norm_ms) > max(0.15 * norm_ms, 0.5)
            ):
                return None
        encoder = next((e for e in rule["encoders"] if e in encoders), None)
        if not encoder:
            return None
        return {
            "encoder": encoder,
            "elem_ext": elem_ext,
            "out_ext": rule["out_ext"],
            "raw_input": raw_input,
            "frame_method": rule.get("frame_method", "duration"),
        }

    @staticmethod
    def _saida_reencode(props, encoders):
        codec = props["codec_name"]
        ext_saida = AudioSyncWorker._extensao_saida_para_codec(props)
        if codec.startswith("pcm_"):
            encoder = AudioSyncWorker._encoder_pcm_para(props)
            if encoder not in encoders:
                encoder = "pcm_s16le"
            return encoder, ".wav", AudioSyncWorker._opcoes_encoder(encoder, props)
        if codec == "flac" and "flac" in encoders:
            return "flac", ".flac", AudioSyncWorker._opcoes_encoder("flac", props)
        if codec == "alac" and "alac" in encoders:
            return "alac", ".m4a", AudioSyncWorker._opcoes_encoder("alac", props)
        if codec == "aac":
            encoder = next((e for e in ("aac", "libfdk_aac") if e in encoders), None)
            if encoder:
                return encoder, ext_saida, AudioSyncWorker._opcoes_encoder(encoder, props)
        if codec == "mp3":
            encoder = next((e for e in ("libmp3lame", "mp3") if e in encoders), None)
            if encoder:
                return encoder, ext_saida, AudioSyncWorker._opcoes_encoder(encoder, props)
        if codec == "mp2":
            encoder = next((e for e in ("mp2", "libtwolame") if e in encoders), None)
            if encoder:
                return encoder, ext_saida, AudioSyncWorker._opcoes_encoder(encoder, props)
        if codec in ("ac3", "eac3") and codec in encoders:
            return codec, ext_saida, AudioSyncWorker._opcoes_encoder(codec, props)
        if codec == "dts" and "dca" in encoders:
            return "dca", ext_saida, AudioSyncWorker._opcoes_encoder("dca", props)
        if codec == "opus":
            encoder = next((e for e in ("libopus", "opus") if e in encoders), None)
            if encoder:
                return encoder, ext_saida, AudioSyncWorker._opcoes_encoder(encoder, props)
        if codec == "vorbis":
            encoder = next((e for e in ("libvorbis", "vorbis") if e in encoders), None)
            if encoder:
                return encoder, ext_saida, AudioSyncWorker._opcoes_encoder(encoder, props)
        if "flac" in encoders:
            return "flac", ".flac", AudioSyncWorker._opcoes_encoder("flac", props)
        encoder_fallback = AudioSyncWorker._encoder_pcm_para(props)
        if encoder_fallback not in encoders:
            encoder_fallback = "pcm_s16le"
        return encoder_fallback, ".wav", []

    # ============================ OPERAÇÕES ============================
    def _copy_stream(self, entrada, saida, audio_index):
        self._check_cancelled()
        cmd = [
            "ffmpeg", "-y",
            "-i", entrada,
            "-map", f"0:a:{audio_index}",
            "-vn", "-sn", "-dn",
            "-map_metadata", "0",
            "-c:a", "copy",
            saida
        ]
        code, err = self._run_short(cmd)
        if code == 0:
            return True
        self.log.emit(f"Falha ao copiar stream de áudio: {err}")
        return False

    def _concatenate(self, arquivos, saida):
        self._check_cancelled()
        fd, lista = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            with open(lista, "w", encoding="utf-8") as f:
                for arq in arquivos:
                    caminho = arq.replace("\\", "/").replace("'", "'\\''")
                    f.write(f"file '{caminho}'\n")
            cmd = [
                "ffmpeg", "-y",
                "-f", "concat",
                "-safe", "0",
                "-i", lista,
                "-map", "0:a:0",
                "-vn", "-sn", "-dn",
                "-map_metadata", "0",
                "-c:a", "copy",
                saida
            ]
            code, err = self._run_short(cmd)
            if code == 0:
                return True
            self.log.emit(f"Falha ao concatenar sem recodificar: {err}")
            return False
        finally:
            try:
                os.unlink(lista)
            except Exception:
                pass

    def _processar_positivo_copy(self, props, strat, encoders, offset_ms):
        self._check_cancelled()
        elem_ext = strat["elem_ext"]
        out_ext = strat["out_ext"]
        frame_ms = props.get("frame_ms")
        expected_ms = offset_ms
        if strat["frame_method"] == "frames" and frame_ms is not None:
            frames, dur_ef, resto = self._calcular_frames(offset_ms, frame_ms)
            self.log.emit(f"Offset solicitado: +{offset_ms:.3f} ms")
            self.log.emit(f"Duração do frame: {frame_ms:.3f} ms")
            self.log.emit(f"Frames usados: {frames}")
            self.log.emit(f"Sincronia aplicada: +{dur_ef:.3f} ms")
            self.log.emit(f"Precisão restante: {resto:.3f} ms")
            if frames <= 0:
                self.log.emit("Offset menor que 1 frame. Apenas copiando/encapsulando...")
                return self._copy_stream(self.input_path, self.output_path, self.audio_index)
            silence_args = ["-frames:a", str(frames)]
            expected_ms = dur_ef
        else:
            if frame_ms is not None:
                frames, dur_ef, resto = self._calcular_frames(offset_ms, frame_ms)
                self.log.emit(f"Offset solicitado: +{offset_ms:.3f} ms")
                self.log.emit(f"Duração do frame: {frame_ms:.3f} ms")
                self.log.emit(f"Sincronia aplicada: +{dur_ef:.3f} ms")
                self.log.emit(f"Precisão restante: {resto:.3f} ms")
                if frames <= 0:
                    self.log.emit("Offset menor que 1 unidade. Apenas copiando/encapsulando...")
                    return self._copy_stream(self.input_path, self.output_path, self.audio_index)
                expected_ms = dur_ef
            else:
                self.log.emit(f"Offset solicitado: +{offset_ms:.3f} ms")
                expected_ms = offset_ms
            if expected_ms <= 0:
                self.log.emit("Offset efetivo muito pequeno. Apenas copiando/encapsulando...")
                return self._copy_stream(self.input_path, self.output_path, self.audio_index)
            silence_args = ["-t", f"{expected_ms / 1000.0:.6f}"]
        temp_dir = tempfile.mkdtemp()
        try:
            if strat["raw_input"]:
                base_elem = self.input_path
            else:
                base_elem = os.path.join(temp_dir, f"base{elem_ext}")
                self.log.emit(f"Extraindo stream para {elem_ext} sem recodificar...")
                if not self._copy_stream(self.input_path, base_elem, self.audio_index):
                    return False
            silencio = os.path.join(temp_dir, f"silencio{elem_ext}")
            src = f"anullsrc=r={props['sample_rate']}:cl={props['layout']}"
            opts = self._opcoes_encoder(strat["encoder"], props)
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", src
            ] + silence_args + ["-c:a", strat["encoder"]] + opts + [silencio]
            code, err = self._run_short(cmd)
            if code != 0:
                self.log.emit(f"Erro ao gerar silêncio: {err}")
                return False
            need_remux = Path(self.output_path).suffix.lower() != elem_ext
            alvo = os.path.join(temp_dir, f"sync{elem_ext}") if need_remux else self.output_path
            self.log.emit("Aplicando sincronia no stream elementar...")
            if not self._concatenate([silencio, base_elem], alvo):
                return False
            if need_remux:
                self.log.emit(f"Encapsulando para {out_ext}...")
                if not self._copy_stream(alvo, self.output_path, 0):
                    return False
            return True
        finally:
            try:
                for f in os.listdir(temp_dir):
                    os.unlink(os.path.join(temp_dir, f))
                os.rmdir(temp_dir)
            except Exception:
                pass

    def _executar_positivo_reencode(self, props, offset_ms, codec, ext_saida, opts):
        self._check_cancelled()
        saida = self._adjust_output_path(ext_saida)
        input_ms = self._duracao_ms(self.input_path)
        delay_ms = int(round(offset_ms))
        silence_s = offset_ms / 1000.0
        total_adelay = input_ms + delay_ms if input_ms is not None else None
        total_concat = input_ms + offset_ms if input_ms is not None else None
        if total_adelay is not None and total_adelay <= 0:
            total_adelay = None
        if total_concat is not None and total_concat <= 0:
            total_concat = None
        sr = props.get("sample_rate", "48000")
        layout = props.get("layout", "stereo")
        channels = max(1, int(props.get("channels", 2)))
        af = "adelay=" + "|".join([str(delay_ms)] * channels)
        filter_complex = (
            f"[0:a]aformat=sample_rates={sr}:channel_layouts={layout}[s];"
            f"[1:a:{self.audio_index}]aformat=sample_rates={sr}:channel_layouts={layout}[a];"
            f"[s][a]concat=n=2:v=0:a=1[out]"
        )

        def montar(codec_alvo, opts_alvo, saida_alvo, usar_concat):
            if usar_concat:
                return [
                    "ffmpeg", "-y",
                    "-f", "lavfi",
                    "-t", f"{silence_s:.6f}",
                    "-i", f"anullsrc=r={sr}:cl={layout}",
                    "-i", self.input_path,
                    "-filter_complex", filter_complex,
                    "-map", "[out]",
                    "-vn", "-sn", "-dn",
                    "-c:a", codec_alvo
                ] + opts_alvo + self._params_saida(props) + [saida_alvo]
            return [
                "ffmpeg", "-y",
                "-i", self.input_path,
                "-map", f"0:a:{self.audio_index}",
                "-vn", "-sn", "-dn",
                "-af", af,
                "-c:a", codec_alvo
            ] + opts_alvo + self._params_saida(props) + [saida_alvo]

        if delay_ms > 0:
            self.log.emit("Recodificando com adelay...")
            cmd = montar(codec, opts, saida, False)
            code, err = self._run_progress(cmd, total_adelay, "Recodificando com adelay")
            if code == 0:
                self.reencode_status = _as_tr("status_com_recode", "COM recodificação (codec: {codec})").format(codec=codec)
                self.log.emit(_as_tr("concluido_com_recode", "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).").format(codec=codec))
                return True
            self.log.emit(f"Método adelay falhou. Tentando concat filter...\n{err}")
        else:
            self.log.emit("Offset pequeno demais para adelay inteiro. Usando concat filter exato...")
        cmd2 = montar(codec, opts, saida, True)
        code2, err2 = self._run_progress(cmd2, total_concat, "Recodificando com concat filter")
        if code2 == 0:
            self.reencode_status = _as_tr("status_com_recode", "COM recodificação (codec: {codec})").format(codec=codec)
            self.log.emit(_as_tr("concluido_com_recode", "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).").format(codec=codec))
            return True
        self.log.emit(f"Erro na recodificação principal.\n{err2}")
        if not codec.startswith("pcm_"):
            if "flac" in self._obter_encoders():
                encoder_fallback = "flac"
                ext_fallback = ".flac"
                opts_fallback = self._opcoes_encoder("flac", props)
            else:
                encoder_fallback = self._encoder_pcm_para(props)
                if encoder_fallback not in self._obter_encoders():
                    encoder_fallback = "pcm_s16le"
                ext_fallback = ".wav"
                opts_fallback = []
            info = {
                "arquivo": os.path.basename(self.input_path),
                "entrada": props.get("codec_name", ""),
                "saida": encoder_fallback,
                "ext": ext_fallback,
            }
            if not self._ask_recode(info):
                return None
            saida_fallback = self._adjust_output_path(ext_fallback)
            usar_concat_fallback = delay_ms <= 0
            total_fallback = total_concat if usar_concat_fallback else total_adelay
            cmd3 = montar(encoder_fallback, opts_fallback, saida_fallback, usar_concat_fallback)
            code3, err3 = self._run_progress(
                cmd3,
                total_fallback,
                f"Recodificando fallback {encoder_fallback}"
            )
            if code3 == 0:
                self.reencode_status = _as_tr("status_com_recode", "COM recodificação (codec: {codec})").format(codec=encoder_fallback)
                self.log.emit(_as_tr("concluido_com_recode", "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).").format(codec=encoder_fallback))
                return True
            self.log.emit(f"Erro no fallback {encoder_fallback}.\n{err3}")
        return False

    def _processar_negativo(self, props, encoders, offset_ms):
        self._check_cancelled()
        frame_ms = props.get("frame_ms")
        if frame_ms is not None:
            frames, dur_ef, resto = self._calcular_frames(offset_ms, frame_ms)
            self.log.emit(f"Offset solicitado: -{abs(offset_ms):.3f} ms")
            self.log.emit(f"Duração do frame: {frame_ms:.3f} ms")
            self.log.emit(f"Frames usados: {frames}")
            self.log.emit(f"Corte aplicado: -{dur_ef:.3f} ms")
            self.log.emit(f"Precisão restante: {resto:.3f} ms")
            if frames <= 0:
                self.log.emit("Offset menor que 1 frame. Copiando sem alteração.")
                self.reencode_status = _as_tr("status_sem_recode", "SEM recodificação (stream copy)")
                ok = self._copy_stream(self.input_path, self.output_path, self.audio_index)
                if ok:
                    self.log.emit(_as_tr("concluido_sem_recode", "[ÁUDIO] Processo concluído SEM recodificação (stream copy)."))
                return ok
            tempo_corte = dur_ef
        else:
            tempo_corte = abs(offset_ms)
            self.log.emit(f"Corte solicitado: {tempo_corte:.3f} ms")
            self.log.emit("Não foi possível determinar frame específico para este áudio.")
        self.log.emit("Tentando cortar sem recodificar...")
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{tempo_corte / 1000.0:.6f}",
            "-i", self.input_path,
            "-map", f"0:a:{self.audio_index}",
            "-vn", "-sn", "-dn",
            "-map_metadata", "0",
            "-c:a", "copy",
            self.output_path
        ]
        code, err = self._run_short(cmd)
        if code == 0:
            self.reencode_status = _as_tr("status_sem_recode", "SEM recodificação (stream copy)")
            self.log.emit(_as_tr("concluido_sem_recode", "[ÁUDIO] Processo concluído SEM recodificação (stream copy)."))
            return True
        self.log.emit(f"Falha ao cortar sem recodificar: {err}")
        codec, ext_saida, opts = self._saida_reencode(props, encoders)
        info = {
            "arquivo": os.path.basename(self.input_path),
            "entrada": props.get("codec_name", ""),
            "saida": codec,
            "ext": ext_saida,
        }
        if not self._ask_recode(info):
            return None
        return self._executar_negativo_reencode(props, tempo_corte, codec, ext_saida, opts)

    def _executar_negativo_reencode(self, props, tempo_ms, codec, ext_saida, opts):
        self._check_cancelled()
        saida = self._adjust_output_path(ext_saida)
        input_ms = self._duracao_ms(self.input_path)
        total_ms = max(0.0, input_ms - tempo_ms) if input_ms is not None else None
        if total_ms is not None and total_ms <= 0:
            total_ms = None
        af = f"atrim=start={tempo_ms / 1000.0:.6f},asetpts=PTS-STARTPTS"
        cmd = [
            "ffmpeg", "-y",
            "-i", self.input_path,
            "-map", f"0:a:{self.audio_index}",
            "-vn", "-sn", "-dn",
            "-af", af,
            "-c:a", codec
        ] + opts + self._params_saida(props) + [saida]
        code, err = self._run_progress(cmd, total_ms, "Recodificando com corte")
        if code == 0:
            self.reencode_status = _as_tr("status_com_recode", "COM recodificação (codec: {codec})").format(codec=codec)
            self.log.emit(_as_tr("concluido_com_recode", "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).").format(codec=codec))
            return True
        self.log.emit(f"Erro na recodificação principal.\n{err}")
        if not codec.startswith("pcm_"):
            if "flac" in self._obter_encoders():
                encoder_fallback = "flac"
                ext_fallback = ".flac"
                opts_fallback = self._opcoes_encoder("flac", props)
            else:
                encoder_fallback = self._encoder_pcm_para(props)
                if encoder_fallback not in self._obter_encoders():
                    encoder_fallback = "pcm_s16le"
                ext_fallback = ".wav"
                opts_fallback = []
            info = {
                "arquivo": os.path.basename(self.input_path),
                "entrada": props.get("codec_name", ""),
                "saida": encoder_fallback,
                "ext": ext_fallback,
            }
            if not self._ask_recode(info):
                return None
            saida_fallback = self._adjust_output_path(ext_fallback)
            cmd_fallback = [
                "ffmpeg", "-y",
                "-i", self.input_path,
                "-map", f"0:a:{self.audio_index}",
                "-vn", "-sn", "-dn",
                "-af", af,
                "-c:a", encoder_fallback
            ] + opts_fallback + self._params_saida(props) + [saida_fallback]
            code2, err2 = self._run_progress(
                cmd_fallback,
                total_ms,
                f"Recodificando fallback {encoder_fallback}"
            )
            if code2 == 0:
                self.reencode_status = _as_tr("status_com_recode", "COM recodificação (codec: {codec})").format(codec=encoder_fallback)
                self.log.emit(_as_tr("concluido_com_recode", "[ÁUDIO] Processo concluído COM recodificação (codec: {codec}).").format(codec=encoder_fallback))
                return True
            self.log.emit(f"Erro no fallback {encoder_fallback}.\n{err2}")
        return False

    # ============================ RUN ============================
    def run(self):
        try:
            self._check_cancelled()
            encoders = self._obter_encoders()
            props = self._probe_props(self.input_path, self.audio_index)
            if not props:
                self.error.emit(_as_tr("falha_props", "Não foi possível ler as propriedades de áudio do arquivo dublado."))
                return
            self.last_props = props
            self.log.emit(f"Processando: {os.path.basename(self.input_path)}")
            self.log.emit(f"Codec: {props.get('codec_name', '')}")
            self.log.emit(f"Sample rate: {props.get('sample_rate', '')} Hz")
            self.log.emit(f"Canais: {props.get('layout', '')}")
            self.log.emit(f"Bitrate: {props.get('bitrate') or 'desconhecido'}")
            self.log.emit(f"Sample fmt: {props.get('sample_fmt') or 'desconhecido'}")
            self.log.emit(f"Formato: {props.get('format_name', '')}")
            if props.get("frame_ms") is not None:
                self.log.emit(f"Frame: {props['frame_ms']:.3f} ms ({props.get('frame_fonte', 'desconhecida')})")
            else:
                self.log.emit("Frame: não determinado")
            offset_ms = float(self.offset_ms)
            if offset_ms == 0:
                self.log.emit("Offset zero. Copiando arquivo original.")
                self.reencode_status = _as_tr("status_sem_recode", "SEM recodificação (stream copy)")
                if self._copy_stream(self.input_path, self.output_path, self.audio_index):
                    self.log.emit(_as_tr("concluido_sem_recode", "[ÁUDIO] Processo concluído SEM recodificação (stream copy)."))
                    self.finished.emit(self.output_path)
                else:
                    self.error.emit("Falha ao copiar o áudio.")
                return
            if offset_ms > 0:
                strat = self._estrategia_copy(props, encoders)
                if strat:
                    self.log.emit("Tentando inserir silêncio sem recodificar...")
                    resultado = self._processar_positivo_copy(props, strat, encoders, offset_ms)
                    if resultado is True:
                        self.reencode_status = _as_tr("status_sem_recode", "SEM recodificação (stream copy)")
                        self.log.emit(_as_tr("concluido_sem_recode", "[ÁUDIO] Processo concluído SEM recodificação (stream copy)."))
                        self.finished.emit(self.output_path)
                        return
                    if resultado is None:
                        self.error.emit(tr("op_cancelada_usuario"))
                        return
                    self.log.emit("Modo sem recodificar falhou. Tentando recodificação compatível...")
                codec, ext_saida, opts = self._saida_reencode(props, encoders)
                info = {
                    "arquivo": os.path.basename(self.input_path),
                    "entrada": props.get("codec_name", ""),
                    "saida": codec,
                    "ext": ext_saida,
                }
                if not self._ask_recode(info):
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                resultado = self._executar_positivo_reencode(props, offset_ms, codec, ext_saida, opts)
                if resultado is True:
                    self.finished.emit(self.output_path)
                elif resultado is None:
                    self.error.emit(tr("op_cancelada_usuario"))
                else:
                    self.error.emit("Falha na sincronização do áudio.")
                return
            resultado = self._processar_negativo(props, encoders, offset_ms)
            if resultado is True:
                self.finished.emit(self.output_path)
            elif resultado is None:
                self.error.emit(tr("op_cancelada_usuario"))
            else:
                self.error.emit("Falha na sincronização do áudio.")
        except AudioSyncCancelled:
            self.error.emit(tr("op_cancelada_usuario"))
        except Exception as e:
            if self._cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
            else:
                self.error.emit(str(e))


class SegmentedHybridCorrectionWorker(QThread):
    """Aplica quebras pontuais sem recodificar a dublagem inteira.

    As partes distantes de uma quebra são remuxadas por stream copy. Apenas uma
    pequena janela ao redor dela é decodificada/recodificada, permitindo
    inserir silêncio, repetir um trecho local ou remover um intervalo com
    precisão de amostra.
    """

    progress = Signal(str, float)
    finished = Signal(str)
    error = Signal(str)

    WINDOW_RADIUS = 1.5
    MIN_CHUNK_DURATION = 0.015
    LOOP_SOURCE_MAX_DURATION = 1.5

    def __init__(self, input_path, audio_index, audio_duration, first_offset,
                 transitions, output_path, props, strategy):
        super().__init__()
        self.input_path = input_path
        self.audio_index = int(audio_index or 0)
        self.audio_duration = float(audio_duration)
        self.first_offset = float(first_offset)
        self.transitions = list(transitions)
        self.output_path = output_path
        self.props = dict(props)
        self.strategy = dict(strategy)
        self._cancelled = False
        self.process = None

    def cancel(self):
        self._cancelled = True
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

    @staticmethod
    def _creationflags():
        return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0

    def _check_cancelled(self):
        if self._cancelled:
            raise AudioSyncCancelled()

    def _run_command(self, cmd, text, percent):
        self._check_cancelled()
        self.progress.emit(text, max(0.0, min(99.0, float(percent))))
        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=self._creationflags()
            )
            output, _ = self.process.communicate()
        except FileNotFoundError as e:
            raise RuntimeError(str(e))
        finally:
            process = self.process
            self.process = None
        self._check_cancelled()
        if process is None or process.returncode != 0:
            detail = (output or "").strip()[-2000:]
            raise RuntimeError(detail or "Falha ao processar a correção segmentada híbrida.")

    def _encoder_args(self):
        encoder = self.strategy["encoder"]
        args = ["-c:a", encoder]
        args += AudioSyncWorker._opcoes_encoder(encoder, self.props)
        sample_rate = self.props.get("sample_rate")
        if sample_rate:
            args += ["-ar", str(sample_rate)]
        return args

    def _copy_chunk(self, directory, number, start, end, percent):
        if end is not None and end - start < self.MIN_CHUNK_DURATION:
            return None
        path = os.path.join(directory, f"copy_{number:03d}.mka")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{max(0.0, start):.6f}",
            "-i", self.input_path,
        ]
        if end is not None:
            cmd += ["-t", f"{max(0.0, end - start):.6f}"]
        cmd += [
            "-map", f"0:a:{self.audio_index}",
            "-vn", "-sn", "-dn", "-map_metadata", "-1",
            "-c:a", "copy", "-f", "matroska", path
        ]
        self._run_command(cmd, "Preservando trecho de áudio sem recodificar...", percent)
        return path

    def _silence_chunk(self, directory, number, duration, percent):
        if duration < self.MIN_CHUNK_DURATION:
            return None
        path = os.path.join(directory, f"silence_{number:03d}.mka")
        sample_rate = self.props.get("sample_rate", "48000")
        layout = self.props.get("layout", "stereo")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"anullsrc=r={sample_rate}:cl={layout}",
            "-t", f"{duration:.6f}",
        ] + self._encoder_args() + ["-f", "matroska", path]
        self._run_command(cmd, "Gerando silêncio para a correção...", percent)
        return path

    def _window_chunk(self, directory, number, start, end, operation, percent):
        """Recodifica a única janela que contém uma emenda de sincronia."""
        path = os.path.join(directory, f"window_{number:03d}.mka")
        duration = max(0.0, end - start)
        low = operation["low"] - start
        high = operation["high"] - start
        jump = operation["jump"]
        fill_mode = operation.get("fill_mode", "silence")
        if low < 0 or high > duration or high < low:
            raise RuntimeError("Janela híbrida inválida para a quebra de sincronia.")

        # Em saltos positivos, a pessoa escolhe entre silêncio (padrão) e
        # repetir o trecho imediatamente anterior ao corte. Em negativos, a
        # faixa entre low/high é removida e não há tempo a preencher.
        if jump > 0 and fill_mode == "loop":
            loop_duration = min(float(jump), self.LOOP_SOURCE_MAX_DURATION, low)
            if loop_duration < self.MIN_CHUNK_DURATION:
                raise RuntimeError("Não há trecho suficiente antes da quebra para aplicar o loop local.")
            sample_rate = self.props.get("sample_rate", 48000)
            try:
                loop_samples = max(1, int(round(loop_duration * float(sample_rate))))
            except (TypeError, ValueError):
                loop_samples = max(1, int(round(loop_duration * 48000)))
            loop_start = low - loop_duration
            filter_complex = (
                "[0:a]asetpts=PTS-STARTPTS,asplit=3[p][l][q];"
                f"[p]atrim=start=0:end={low:.6f},asetpts=PTS-STARTPTS[a];"
                f"[l]atrim=start={loop_start:.6f}:end={low:.6f},asetpts=PTS-STARTPTS,"
                f"aloop=loop=-1:size={loop_samples},atrim=start=0:end={jump:.6f},"
                "asetpts=PTS-STARTPTS[loop];"
                f"[q]atrim=start={high:.6f}:end={duration:.6f},asetpts=PTS-STARTPTS[b];"
                "[a][loop][b]concat=n=3:v=0:a=1[out]"
            )
            progress_text = "Aplicando loop local na quebra..."
        else:
            post_filter = ""
            if jump > 0:
                post_filter = f",adelay=delays={int(round(jump * 1000))}:all=1"
            filter_complex = (
                "[0:a]asetpts=PTS-STARTPTS,asplit=2[p][q];"
                f"[p]atrim=start=0:end={low:.6f},asetpts=PTS-STARTPTS[a];"
                f"[q]atrim=start={high:.6f}:end={duration:.6f},asetpts=PTS-STARTPTS"
                f"{post_filter}[b];"
                "[a][b]concat=n=2:v=0:a=1[out]"
            )
            progress_text = "Recodificando somente a janela da quebra..."
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{start:.6f}", "-t", f"{duration:.6f}",
            "-i", self.input_path,
            "-filter_complex", filter_complex,
            "-map", "[out]", "-vn", "-sn", "-dn",
        ] + self._encoder_args() + ["-f", "matroska", path]
        self._run_command(cmd, progress_text, percent)
        return path

    def _build_operations(self, source_start):
        operations = []
        previous_end = source_start
        for transition in self.transitions:
            jump = float(transition["jump"])
            cut = float(transition["cut"])
            if jump > 0:
                low = high = cut
            else:
                low, high = cut + jump, cut
            if low <= previous_end + self.MIN_CHUNK_DURATION or high >= self.audio_duration:
                raise RuntimeError("Os pontos de quebra não deixam trechos válidos para a correção híbrida.")
            fill_mode = "loop" if jump > 0 and transition.get("fill_mode") == "loop" else "silence"
            operations.append({
                "low": low,
                "high": high,
                "jump": jump,
                "fill_mode": fill_mode,
            })
            previous_end = high
        return operations

    def _build_windows(self, operations, source_start):
        starts = [max(source_start, op["low"] - self.WINDOW_RADIUS) for op in operations]
        ends = [min(self.audio_duration, op["high"] + self.WINDOW_RADIUS) for op in operations]
        for i in range(len(operations) - 1):
            # Janelas próximas são encurtadas até o meio do trecho preservado.
            # Se as operações se sobrepõem, o fallback integral preserva a
            # correção sem arriscar uma emenda incorreta.
            if operations[i]["high"] > operations[i + 1]["low"]:
                raise RuntimeError("Quebras de sincronia muito próximas para correção híbrida.")
            seam = (operations[i]["high"] + operations[i + 1]["low"]) / 2.0
            ends[i] = min(ends[i], seam)
            starts[i + 1] = max(starts[i + 1], seam)
        for op, start, end in zip(operations, starts, ends):
            if start > op["low"] or end < op["high"] or end - start < self.MIN_CHUNK_DURATION:
                raise RuntimeError("Não há janela suficiente em torno de uma quebra de sincronia.")
        return list(zip(starts, ends))

    def _concatenate(self, pieces, directory, percent):
        list_path = os.path.join(directory, "parts.txt")
        with open(list_path, "w", encoding="utf-8") as handle:
            for path in pieces:
                normalized = path.replace("\\", "/").replace("'", "'\\''")
                handle.write(f"file '{normalized}'\n")
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0", "-i", list_path,
            "-map", "0:a:0", "-vn", "-sn", "-dn", "-map_metadata", "-1",
            "-c:a", "copy", self.output_path
        ]
        self._run_command(cmd, "Unindo trechos de áudio sem recodificar...", percent)

    def run(self):
        try:
            source_start = abs(self.first_offset) if self.first_offset < 0 else 0.0
            if source_start >= self.audio_duration - self.MIN_CHUNK_DURATION:
                raise RuntimeError("Offset inicial maior que a duração disponível do áudio.")
            operations = self._build_operations(source_start)
            windows = self._build_windows(operations, source_start)
            total_steps = 2 * len(operations) + 3
            step = 0

            with tempfile.TemporaryDirectory(prefix="dublasync_segmented_") as directory:
                pieces = []
                number = 0
                if self.first_offset > 0:
                    step += 1
                    silence = self._silence_chunk(
                        directory, number, self.first_offset, step * 95.0 / total_steps
                    )
                    number += 1
                    if silence:
                        pieces.append(silence)

                cursor = source_start
                for operation, (window_start, window_end) in zip(operations, windows):
                    step += 1
                    copied = self._copy_chunk(
                        directory, number, cursor, window_start, step * 95.0 / total_steps
                    )
                    number += 1
                    if copied:
                        pieces.append(copied)
                    step += 1
                    pieces.append(self._window_chunk(
                        directory, number, window_start, window_end, operation,
                        step * 95.0 / total_steps
                    ))
                    number += 1
                    cursor = window_end

                step += 1
                copied = self._copy_chunk(
                    directory, number, cursor, None, step * 95.0 / total_steps
                )
                if copied:
                    pieces.append(copied)
                if not pieces:
                    raise RuntimeError("A correção híbrida não gerou trechos de áudio.")
                self._concatenate(pieces, directory, 99.0)

            self.progress.emit("Correção segmentada concluída.", 100.0)
            self.finished.emit(self.output_path)
        except AudioSyncCancelled:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass
            self.error.emit(tr("op_cancelada_usuario"))
        except Exception as e:
            if os.path.exists(self.output_path):
                try:
                    os.remove(self.output_path)
                except Exception:
                    pass
            self.error.emit(str(e))


# ====================== DIÁLOGOS ======================
class FilterSelectionDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_filtro_titulo"))
        self.setFixedSize(380, 180)
        layout = QVBoxLayout(self)
        lbl = QLabel(tr("dlg_filtro_pergunta"))
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 14px; margin-bottom: 10px;")
        layout.addWidget(lbl)
        self.btn_group = QButtonGroup(self)
        self.radio_rubberband = QRadioButton(tr("filtro_rubberband"))
        self.radio_rubberband.setChecked(True)
        self.radio_rubberband.setStyleSheet("font-size: 13px; margin-bottom: 5px;")
        self.radio_atempo = QRadioButton(tr("filtro_atempo"))
        self.radio_atempo.setStyleSheet("font-size: 13px; margin-bottom: 15px;")
        self.btn_group.addButton(self.radio_rubberband, 1)
        self.btn_group.addButton(self.radio_atempo, 2)
        layout.addWidget(self.radio_rubberband)
        layout.addWidget(self.radio_atempo)
        btn_layout = QHBoxLayout()
        btn_cancel = QPushButton(tr("btn_cancelar"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_ok.setDefault(True)
        btn_ok.setFocus()
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_selected_filter(self) -> str:
        return "atempo" if self.radio_atempo.isChecked() else "rubberband"


class SpeedFactorDialog(QDialog):
    CUSTOM_INDEX = 6

    def __init__(self, initial_factor: float, detected_key: str = None,
                 detected_ok: bool = False, audio_dur: float = 0.0,
                 selected_filter: str = "rubberband", cli_callback=None,
                 is_dark: bool = True, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("dlg_fator_titulo"))
        self.setMinimumWidth(470)
        self.audio_dur = audio_dur
        self.cli_callback = cli_callback
        self.is_dark = is_dark
        self.selected_filter = selected_filter
        self._updating = False
        self._tempo_expr = None
        pal = self.palette()
        pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#9e9e9e") if is_dark else QColor("#757575"))
        self.setPalette(pal)
        self.presets = [
            ("ntsc_24", Fraction(24, 1) / Fraction(24000, 1001), "23.976", "24", "24/(24000/1001)", "24 / (24000/1001)"),
            ("ntsc_25", Fraction(25, 1) / Fraction(24000, 1001), "23.976", "25", "25/(24000/1001)", "25 / (24000/1001)"),
            ("24_ntsc", Fraction(24000, 1001) / Fraction(24, 1), "24", "23.976", "(24000/1001)/24", "(24000/1001) / 24"),
            ("24_25", Fraction(25, 24), "24", "25", "25/24", "25/24"),
            ("25_ntsc", Fraction(24000, 1001) / Fraction(25, 1), "25", "23.976", "(24000/1001)/25", "(24000/1001) / 25"),
            ("25_24", Fraction(24, 25), "25", "24", "24/25", "24/25"),
        ]
        layout = QVBoxLayout(self)
        self.lbl_nova_duracao = QLabel(tr("dlg_fator_nova_duracao").format(v="--:--:--.---"))
        accent_color = "#c9a968" if is_dark else "#007acc"
        self.lbl_nova_duracao.setStyleSheet(
            f"font-size: 13px; font-weight: bold; color: {accent_color};"
        )
        layout.addWidget(self.lbl_nova_duracao)
        group = QGroupBox(tr("dlg_fator_grupo"))
        g_layout = QVBoxLayout(group)
        g_layout.addWidget(QLabel(tr("dlg_fator_preset")))
        self.combo_preset = QComboBox()
        for _key, _ratio, o, d, _expr, label_f in self.presets:
            self.combo_preset.addItem(tr("dlg_fator_item").format(o=o, d=d, f=label_f))
        self.combo_preset.addItem(tr("dlg_fator_personalizado"))
        g_layout.addWidget(self.combo_preset)
        g_layout.addWidget(QLabel(tr("dlg_fator_fator")))
        self.factor_edit = QLineEdit()
        self.factor_edit.setValidator(QDoubleValidator(0.1, 10.0, 6, self.factor_edit))
        g_layout.addWidget(self.factor_edit)
        frac_layout = QHBoxLayout()
        frac_layout.addWidget(QLabel(tr("dlg_fator_origem")))
        self.edit_origem = QLineEdit()
        self.edit_origem.setPlaceholderText("23.976")
        frac_layout.addWidget(self.edit_origem)
        frac_layout.addWidget(QLabel(tr("dlg_fator_destino")))
        self.edit_destino = QLineEdit()
        self.edit_destino.setPlaceholderText("25")
        frac_layout.addWidget(self.edit_destino)
        g_layout.addLayout(frac_layout)
        self.lbl_pct = QLabel("")
        g_layout.addWidget(self.lbl_pct)
        nota = QLabel(tr("dlg_fator_nota_pitch"))
        nota.setWordWrap(True)
        nota.setStyleSheet(
            f"font-size: 11px; color: {'#beb18f' if is_dark else '#888888'};"
        )
        g_layout.addWidget(nota)
        layout.addWidget(group)
        btn_layout = QHBoxLayout()
        self.btn_cli = QPushButton(tr("dlg_fator_btn_cli"))
        self.btn_cli.setObjectName("btnCli")
        self.btn_cli.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cli.clicked.connect(self._mostrar_cli)
        btn_layout.addWidget(self.btn_cli)
        btn_layout.addStretch()
        btn_cancel = QPushButton(tr("btn_cancelar"))
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton("OK")
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)
        self.combo_preset.currentIndexChanged.connect(self._on_combo_changed)
        self.factor_edit.textChanged.connect(self._on_factor_edited)
        self.edit_origem.textChanged.connect(self._on_frac_edited)
        self.edit_destino.textChanged.connect(self._on_frac_edited)
        keys = [k for k, *_ in self.presets]
        if detected_ok and detected_key in keys:
            idx = keys.index(detected_key)
            self.combo_preset.setCurrentIndex(idx)
            self._set_factor_text(float(self.presets[idx][1]))
            self._tempo_expr = self.presets[idx][4]
        else:
            self.combo_preset.setCurrentIndex(self.CUSTOM_INDEX)
            self._set_factor_text(float(initial_factor))
            self._tempo_expr = f"{float(initial_factor):.6f}"
        self._atualizar_preview()
        layout.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)

    def _set_factor_text(self, value: float):
        self._updating = True
        self.factor_edit.setText(f"{value:.6f}")
        self._updating = False

    def _clear_frac_fields(self):
        self._updating = True
        self.edit_origem.clear()
        self.edit_destino.clear()
        self._updating = False

    @staticmethod
    def _fps_para_componente(texto: str):
        t = texto.strip().replace(",", ".")
        if not t:
            return None
        try:
            valor = float(t)
        except ValueError:
            return None
        if valor <= 0:
            return None
        if abs(valor - 24000 / 1001) < 0.001:
            return valor, "(24000/1001)"
        if abs(valor - 30000 / 1001) < 0.001:
            return valor, "(30000/1001)"
        if valor == int(valor):
            return valor, str(int(valor))
        decimais = len(t.split(".")[1].rstrip("0")) if "." in t else 0
        decimais = max(decimais, 1)
        den = 10 ** decimais
        num = int(round(valor * den))
        return valor, f"({num}/{den})"

    def _on_combo_changed(self, index: int):
        if self._updating:
            return
        if index < len(self.presets):
            self._set_factor_text(float(self.presets[index][1]))
            self._tempo_expr = self.presets[index][4]
        else:
            f = self._get_fator()
            self._tempo_expr = f"{f:.6f}" if f else None
        self._clear_frac_fields()
        self._atualizar_preview()

    def _on_factor_edited(self, _text: str):
        if self._updating:
            return
        self._updating = True
        if self.combo_preset.currentIndex() != self.CUSTOM_INDEX:
            self.combo_preset.setCurrentIndex(self.CUSTOM_INDEX)
        self.edit_origem.clear()
        self.edit_destino.clear()
        self._updating = False
        f = self._get_fator()
        self._tempo_expr = f"{f:.6f}" if f else None
        self._atualizar_preview()

    def _on_frac_edited(self, _text: str):
        if self._updating:
            return
        for edit in (self.edit_origem, self.edit_destino):
            t = edit.text().strip()
            if re.fullmatch(r"\d{4,}", t):
                edit.setText(t[:2] + "." + t[2:])
                return
        comp_o = self._fps_para_componente(self.edit_origem.text())
        comp_d = self._fps_para_componente(self.edit_destino.text())
        if comp_o and comp_d:
            valor_o, str_o = comp_o
            valor_d, str_d = comp_d
            self._updating = True
            if self.combo_preset.currentIndex() != self.CUSTOM_INDEX:
                self.combo_preset.setCurrentIndex(self.CUSTOM_INDEX)
            self._updating = False
            self._set_factor_text(valor_d / valor_o)
            self._tempo_expr = f"{str_d}/{str_o}"
            self._atualizar_preview()

    def _atualizar_preview(self):
        fator = self._get_fator()
        if fator and fator > 0:
            pct = (fator - 1) * 100
            self.lbl_pct.setText(tr("dlg_fator_velocidade").format(v=f"{pct:+.4f}"))
            if self.audio_dur > 0:
                self.lbl_nova_duracao.setText(
                    tr("dlg_fator_nova_duracao").format(v=format_time(self.audio_dur / fator))
                )
        else:
            self.lbl_pct.setText(tr("dlg_fator_velocidade").format(v="---"))

    def _get_fator(self):
        try:
            return float(self.factor_edit.text().replace(",", "."))
        except ValueError:
            return None

    def get_factor(self) -> str:
        if self._tempo_expr:
            return self._tempo_expr
        f = self._get_fator()
        return f"{f:.6f}" if f else "1.0"

    def _mostrar_cli(self):
        if not self.cli_callback:
            return
        texto = self.cli_callback(self.get_factor())
        te_bg, te_fg, te_border = (
            ("#1e1e1e", "#e7d9b8", "#665a43")
            if self.is_dark
            else ("#fffdf8", "#443b32", "#e5d9c7")
        )
        prev = QDialog(self)
        prev.setWindowTitle(tr("dlg_cli_titulo"))
        prev.resize(850, 260)
        if not self.is_dark:
            prev.setStyleSheet("QDialog { background-color: #f7f3ea; }")
        lay = QVBoxLayout(prev)
        te = QTextEdit()
        te.setReadOnly(True)
        te.setPlainText(texto)
        te.setStyleSheet(
            f"background-color: {te_bg}; color: {te_fg}; font-family: Consolas, monospace; font-size: 13px; border: 1px solid {te_border};"
        )
        lay.addWidget(te)
        bl = QHBoxLayout()
        btn_close = QPushButton(tr("btn_fechar"))
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(prev.accept)
        bl.addStretch()
        bl.addWidget(btn_close)
        lay.addLayout(bl)
        prev.exec()

    def accept(self):
        f = self._get_fator()
        if f is None or f <= 0:
            return
        super().accept()


# ====================== GRÁFICO DE RESÍDUOS & ALINHAMENTO ======================
class ResidualChartWidget(QWidget):
    def __init__(self, parent=None, is_dark: bool = True):
        super().__init__(parent)
        self.is_dark = is_dark
        self._data = None
        self._pts = []
        self._selected = -1
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_data(self, anchors: list, slope: float, intercept: float, scale: float):
        self._data = (anchors, slope, intercept, scale)
        self._selected = -1
        self.update()

    def mousePressEvent(self, event):
        if not self._pts:
            return
        pos = event.position()
        best, bestd = -1, 14.0
        for i, (x, y, _a, _ok) in enumerate(self._pts):
            d = ((x - pos.x()) ** 2 + (y - pos.y()) ** 2) ** 0.5
            if d <= bestd:
                best, bestd = i, d
        self._selected = best if best != self._selected else -1
        self.update()

    @staticmethod
    def _fmt_t(t: float) -> str:
        t = int(t)
        h, rem = divmod(t, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        try:
            self._draw(p)
        finally:
            p.end()

    def _draw(self, p: QPainter):
        w, h = self.width(), self.height()
        if self.is_dark:
            bg, grid, txt_dim, txt_hi = "#1e1e1e", "#333333", "#8b94a7", "#e6eaf2"
            green, red, blue = "#2ecc71", "#e74c3c", "#3b82f6"
            tip_bg, tip_border = "#252526", "#3b82f6"
        else:
            bg, grid, txt_dim, txt_hi = "#f0f0f0", "#cccccc", "#666666", "#222222"
            green, red, blue = "#27ae60", "#c0392b", "#007acc"
            tip_bg, tip_border = "#ffffff", "#007acc"
        p.fillRect(0, 0, w, h, QColor(bg))
        if not self._data:
            return
        anchors, slope, intercept, scale = self._data
        ml, mr, mt, mb = 64, 14, 34, 28
        iw, ih = w - ml - mr, h - mt - mb
        pts = []
        for a in anchors:
            dev_ms = a["offset"] * 1000.0
            ok = bool(a.get("good", False) and a.get("inlier", False))
            pts.append((a["t"], dev_ms, ok, a))
        xs = [x for x, _y, _o, _a in pts]
        valid_ys = [y for _x, y, ok, _a in pts if ok]
        if valid_ys:
            valid_min, valid_max = min(valid_ys), max(valid_ys)
            mid = (valid_max + valid_min) / 2
            v_range = max(valid_max - valid_min, 300)
            ymin = mid - max(v_range * 1.5, 500)
            ymax = mid + max(v_range * 1.5, 500)
        else:
            ys = [y for _x, y, _o, _a in pts]
            ymin, ymax = min(ys + [0.0]), max(ys + [0.0])
        pad = (ymax - ymin) * 0.10
        ymin -= pad
        ymax += pad
        xmin, xmax = 0.0, max(xs)
        if xmax <= xmin:
            xmax = xmin + 1

        def mx(t):
            return ml + (t - xmin) / (xmax - xmin) * iw

        def my(v):
            y_val = mt + (1 - (v - ymin) / (ymax - ymin)) * ih
            return max(mt, min(mt + ih, y_val))

        fnt = p.font()
        fnt.setPointSize(8)
        p.setFont(fnt)
        y0 = int(my(0))
        for i in range(3):
            v = ymin + (ymax - ymin) * i / 2
            y = int(my(v))
            p.setPen(QPen(QColor(grid), 1, Qt.PenStyle.DashLine))
            p.drawLine(ml, y, ml + iw, y)
            if mt <= y0 <= mt + ih and abs(y - y0) < 14:
                continue
            p.setPen(QPen(QColor(txt_dim), 1))
            lbl_v = f"{v/1000:+.1f}s" if abs(v) >= 2000 else f"{v:+.0f}ms"
            p.drawText(4, y + 4, lbl_v)
        if mt <= y0 <= mt + ih:
            p.setPen(QPen(QColor(grid), 1, Qt.PenStyle.DashLine))
            p.drawLine(ml, y0, ml + iw, y0)
            p.setPen(QPen(QColor(txt_dim), 1))
            p.drawText(4, y0 + 4, "0ms")
        t_val = 0.0
        while t_val <= xmax:
            x = int(mx(t_val))
            p.setPen(QPen(QColor(grid), 1, Qt.PenStyle.DashLine))
            p.drawLine(x, mt, x, mt + ih)
            lbl = self._fmt_t(t_val)
            lw = p.fontMetrics().horizontalAdvance(lbl) + 8
            lx = max(2, min(w - lw - 2, x - lw // 2))
            p.setPen(QPen(QColor(txt_dim), 1))
            p.drawText(lx, h - mb + 8, lw, 16, Qt.AlignmentFlag.AlignCenter, lbl)
            t_val += 600.0
        p.setPen(QPen(QColor(blue), 2))
        valid_pts = [pt for pt in pts if pt[2]]
        if valid_pts:
            pts_sorted = sorted(valid_pts, key=lambda item: item[0])
            first_t, first_v, _, _ = pts_sorted[0]
            if first_t > 0:
                p.drawLine(QPointF(mx(0), my(first_v)), QPointF(mx(first_t), my(first_v)))
            for k in range(len(pts_sorted) - 1):
                t1, v1, _, _ = pts_sorted[k]
                t2, v2, _, _ = pts_sorted[k + 1]
                p.drawLine(QPointF(mx(t1), my(v1)), QPointF(mx(t2), my(v2)))
        else:
            p.drawLine(ml, int(my(0)), ml + iw, int(my(0)))
        self._pts = []
        for i, (t, v, ok, a) in enumerate(pts):
            x, y = mx(t), my(v)
            self._pts.append((x, y, a, ok))
            p.setPen(Qt.PenStyle.NoPen)
            glow = QColor(green if ok else red)
            glow.setAlpha(60)
            p.setBrush(glow)
            p.drawEllipse(QPointF(x, y), 8, 8)
            p.setBrush(QColor(green if ok else red))
            p.setPen(QPen(QColor("#ffffff"), 1.5))
            r = 6.5 if i == self._selected else 4.5
            p.drawEllipse(QPointF(x, y), r, r)
            if i == self._selected:
                p.setPen(QPen(QColor(txt_hi), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(QPointF(x, y), 9.5, 9.5)
        p.setPen(QPen(QColor(txt_dim), 1))
        good_count = sum(1 for _x, _y, _a, ok in self._pts if ok)
        p.drawText(w - 200, 12, _tr_seg("seg2_inliers", "Válidos: {a}/{b}").format(a=good_count, b=len(self._pts)))
        p.drawText(w - 200, 24, _tr_seg("seg2_escala_lbl", "Escala: {v}").format(v=f"{scale:.6f}"))
        if 0 <= self._selected < len(self._pts):
            x, y, a, ok = self._pts[self._selected]
            linhas = [
                f"Tempo: {self._fmt_t(a['t'])}",
                f"Desvio: {a['offset'] * 1000:+.1f} ms",
                f"Resíduo: {(a['offset'] - (intercept + slope * a['t'])) * 1000:+.1f} ms",
            ]
            status_txt = _tr_seg("seg_status_valido", "Válido") if ok else _tr_seg("seg_status_anormal", "Anormal")
            fm = p.fontMetrics()
            line_h = fm.height()
            pad = 8
            todas = linhas + [f"Status: {status_txt}"]
            tw = max(fm.horizontalAdvance(t) for t in todas) + pad * 2
            th = line_h * len(todas) + pad * 2
            tx = max(4, min(w - tw - 4, x - tw / 2))
            ty = y - th - 10
            if ty < 4:
                ty = min(h - th - 4, y + 10)
            p.setPen(QPen(QColor(tip_border), 1.5))
            p.setBrush(QColor(tip_bg))
            p.drawRoundedRect(int(tx), int(ty), int(tw), int(th), 8, 8)
            for j, ln in enumerate(linhas):
                p.setPen(QPen(QColor(txt_hi), 1))
                p.drawText(int(tx) + pad, int(ty) + pad + line_h * j + fm.ascent(), ln)
            p.setPen(QPen(QColor(green if ok else red), 1))
            p.drawText(
                int(tx) + pad,
                int(ty) + pad + line_h * len(linhas) + fm.ascent(),
                f"Status: {status_txt}"
            )


# ====================== JANELA DE ANÁLISE SEGMENTADA ======================
class SegmentedAnalysisDialog(QDialog):
    def __init__(self, parent=None, is_dark: bool = True):
        super().__init__(parent)
        self.is_dark = is_dark
        self.setWindowTitle(_tr_seg("seg3_titulo", "Checkpoints Segmentados"))
        self.resize(650, 560)
        self.setMinimumSize(560, 480)
        card_bg = "#252526" if is_dark else "#ffffff"
        border = "#665a43" if is_dark else "#cccccc"
        text_fg = "#e7d9b8" if is_dark else "#111111"
        dim = "#beb18f" if is_dark else "#333333"
        accent_bg = "#6b5631" if is_dark else "#007acc"
        accent_text = "#f3e7c8" if is_dark else "white"
        layout = QVBoxLayout(self)
        header = QHBoxLayout()
        self.lbl_titulo = QLabel(_tr_seg("seg3_titulo", "Checkpoints Segmentados"))
        self.lbl_titulo.setStyleSheet(f"font-size:16px; font-weight:bold; color:{text_fg};")
        self.badge_conf = QLabel("—")
        self.badge_conf.setStyleSheet(
            f"background-color:{accent_bg}; color:{accent_text}; border-radius:4px; padding:3px 10px; font-weight:bold;"
        )
        header.addWidget(self.lbl_titulo)
        header.addStretch()
        header.addWidget(self.badge_conf)
        layout.addLayout(header)
        self.lbl_explicacao = QLabel(tr("seg3_explicacao"))
        self.lbl_explicacao.setWordWrap(True)
        self.lbl_explicacao.setStyleSheet(f"font-size:12px; color:{dim}; margin-bottom:10px;")
        layout.addWidget(self.lbl_explicacao)
        self.lbl_status = QLabel(_tr_seg("seg_progress", "Verificando assincronia no meio do vídeo..."))
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setStyleSheet(f"color:{text_fg};")
        layout.addWidget(self.lbl_status)
        self.lbl_det = QLabel("")
        self.lbl_det.setWordWrap(True)
        self.lbl_det.setStyleSheet(f"font-size:11px; color:{dim}; font-family:Consolas,monospace;")
        layout.addWidget(self.lbl_det)
        self.list_text = QTextEdit()
        self.list_text.setReadOnly(True)
        self.list_text.setMaximumHeight(110)
        self.list_text.setStyleSheet(
            f"background-color:{card_bg}; color:{text_fg}; font-family:Consolas,monospace; "
            f"font-size:12px; border:1px solid {border}; border-radius:4px;"
        )
        layout.addWidget(self.list_text)
        self.lbl_titulo_grafico = QLabel(_tr_seg("seg3_titulo_grafico", "Variação da Sincronização"))
        self.lbl_titulo_grafico.setStyleSheet(f"font-size:14px; font-weight:bold; color:{text_fg};")
        layout.addWidget(self.lbl_titulo_grafico)
        self.lbl_subtitulo_grafico = QLabel(_tr_seg("seg3_subtitulo_grafico", "Sincronia medida ao longo do vídeo"))
        self.lbl_subtitulo_grafico.setStyleSheet(f"font-size:11px; color:{dim};")
        layout.addWidget(self.lbl_subtitulo_grafico)
        self.chart = ResidualChartWidget(self, is_dark)
        self.chart.setMinimumSize(560, 260)
        layout.addWidget(self.chart)
        bl = QHBoxLayout()
        self.btn_apply = QPushButton(_tr_seg("seg_btn_aplicar", "Aplicar correção"))
        self.btn_apply.setStyleSheet(obter_estilo_btn_convert(is_dark, font_size=None))
        self.btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_apply.hide()
        self.btn_cancel = QPushButton(tr("btn_cancelar"))
        self.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(is_dark, font_size=None))
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_close = QPushButton(tr("btn_fechar"))
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.reject)
        self.btn_apply.setFixedHeight(self.btn_close.sizeHint().height())
        self.btn_cancel.setFixedHeight(self.btn_close.sizeHint().height())
        bl.addStretch()
        for b in (self.btn_apply, self.btn_cancel, self.btn_close):
            bl.addWidget(b)
        layout.addLayout(bl)

    def set_status(self, text: str):
        self.lbl_status.setText(text)

    def set_running(self, running: bool):
        self.btn_close.setEnabled(not running)
        self.btn_cancel.setVisible(running)
        if running:
            self.btn_apply.hide()
            self.lbl_status.setText(_tr_seg("seg_progress", "Verificando assincronia no meio do vídeo..."))

    def set_finished(self, can_apply: bool):
        self.btn_cancel.hide()
        self.btn_close.setEnabled(True)
        self.btn_apply.setVisible(can_apply)

    def populate(self, r: dict):
        self.badge_conf.setText(r.get("confidence", "—"))
        trans = r.get("transitions", [])
        if trans:
            self.lbl_status.setText(
                _tr_seg("seg3_achou", "Encontramos {n} ponto(s) de assincronia.").format(n=len(trans))
            )
        else:
            self.lbl_status.setText(_tr_seg("seg3_nada", "Nenhuma assincronia no meio do vídeo. ✔"))
        linhas = []
        for i, t in enumerate(trans, start=1):
            linhas.append(
                f"~{format_time(t['time'])[:12]}: {i}º ponto de assincronia (quebra de sincronia) - {t['jump'] * 1000:+.0f}ms"
            )
        if linhas:
            linhas.append(
                _tr_seg(
                    "seg3_offset_aplicado",
                    "Offset global do início também aplicado: {v} ms - o áudio entregue já está totalmente sincronizado."
                ).format(v=f"{r.get('first_offset', 0.0) * 1000:+.0f}")
            )
        self.list_text.setPlainText("\n".join(linhas))
        self.list_text.setVisible(bool(linhas))
        self.lbl_det.setText(
            _tr_seg(
                "seg3_det_linha",
                "score {score} | âncoras {ancoras} | resíduo {residuo} | escala {escala} | confiança {conf}"
            ).format(
                score=f"{r.get('score', 0.0):.1f}",
                ancoras=f"{r.get('good', 0)}/{r.get('total', 0)}",
                residuo=f"{r.get('residual_ms', 0.0):.1f} ms",
                escala=f"{r.get('scale', 1.0):.6f}",
                conf=r.get("confidence", "—")
            )
        )
        self.chart.set_data(
            r.get("anchors", []),
            r.get("slope", 0.0),
            r.get("intercept", 0.0),
            r.get("scale", 1.0)
        )


# ====================== JANELA: SELEÇÃO DE PONTOS ======================
class _CheckableTableCellDelegate(QStyledItemDelegate):
    """Alterna checkboxes de tabela ao clicar em qualquer ponto da célula."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        if not (index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return

        style = option.widget.style() if option.widget else QApplication.style()
        indicator_width = style.pixelMetric(
            QStyle.PixelMetric.PM_IndicatorWidth,
            None,
            option.widget,
        )
        indicator_height = style.pixelMetric(
            QStyle.PixelMetric.PM_IndicatorHeight,
            None,
            option.widget,
        )
        indicator_rect = QRect(
            option.rect.left() + 3,
            option.rect.top() + (option.rect.height() - indicator_height) // 2,
            indicator_width,
            indicator_height,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(QColor("#000000"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(indicator_rect.adjusted(0, 0, -1, -1))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not (index.flags() & Qt.ItemFlag.ItemIsUserCheckable):
            return super().editorEvent(event, model, option, index)

        if event.type() == QEvent.Type.MouseButtonPress:
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            current_state = index.data(Qt.ItemDataRole.CheckStateRole)
            is_checked = current_state in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            new_state = Qt.CheckState.Unchecked if is_checked else Qt.CheckState.Checked
            model.setData(index, new_state, Qt.ItemDataRole.CheckStateRole)
            return True

        return super().editorEvent(event, model, option, index)


class SegmentedPointSelectionDialog(QDialog):
    def __init__(self, transitions: list, dubbed_dur: float = 0.0, parent=None, is_dark: bool = True):
        super().__init__(parent)
        self.setWindowTitle(_tr_seg("seg_sel_titulo", "Selecionar pontos de correção"))
        self.resize(640, 400)
        self._dubbed_dur = float(dubbed_dur or 0.0)
        self._original_times = [float(t.get("time", 0.0)) for t in transitions]
        self._default_fg = None
        self._formatting = False
        layout = QVBoxLayout(self)
        lbl = QLabel(_tr_seg(
            "seg_sel_explicacao",
            "Marque os pontos de assincronia que você deseja aplicar.\n"
            "Os pontos desmarcados serão ignorados.\n\n"
            "O programa tentará aplicar cada ponto em uma troca de cena ou em um trecho de silêncio, "
            "para evitar cortes no meio da fala."
        ))
        lbl.setWordWrap(True)
        lbl.setStyleSheet("font-size: 13px; margin-bottom: 6px;")
        layout.addWidget(lbl)
        lbl_dica = QLabel(_tr_seg(
            "seg_sel_dica",
            "Dica: os separadores (: e .) são inseridos automaticamente enquanto você digita.\n"
            "Pontos editados ficam destacados em amarelo e têm prioridade sobre o ajuste automático."
        ))
        lbl_dica.setWordWrap(True)
        dica_color = "#f1c40f" if is_dark else "#005999"
        lbl_dica.setStyleSheet(f"font-size: 12px; color: {dica_color}; margin-bottom: 10px;")
        layout.addWidget(lbl_dica)
        self.table = QTableWidget(len(transitions), 4)
        self._checkbox_delegate = _CheckableTableCellDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self._checkbox_delegate)
        self.table.setItemDelegateForColumn(3, self._checkbox_delegate)
        self.table.setHorizontalHeaderLabels([
            _tr_seg("seg_sel_col_aplicar", "Aplicar"),
            _tr_seg("seg_sel_col_tempo", "Nesse tempo"),
            _tr_seg("seg_sel_col_salto", "Salto"),
            _tr_seg("seg_sel_col_loop", "Loop"),
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        for row, trn in enumerate(transitions):
            check_item = QTableWidgetItem()
            check_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check_item.setCheckState(Qt.CheckState.Checked)
            time_item = QTableWidgetItem(f"{format_time(trn.get('time', 0.0))[:12]}")
            time_item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsEditable)
            if self._default_fg is None:
                self._default_fg = time_item.foreground()
            jump = float(trn.get('jump', 0.0))
            jump_item = QTableWidgetItem(f"{jump * 1000:+.0f} ms")
            jump_item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            loop_item = QTableWidgetItem()
            if jump > 0:
                loop_item.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
                loop_item.setCheckState(Qt.CheckState.Unchecked)
                loop_item.setToolTip(_tr_seg(
                    "seg_sel_loop_tooltip",
                    "Repete um trecho local em vez de inserir silêncio. Desmarcado por segurança."
                ))
            else:
                loop_item.setFlags(Qt.ItemFlag.NoItemFlags)
                loop_item.setText("—")
                loop_item.setToolTip(_tr_seg(
                    "seg_sel_loop_indisponivel",
                    "Loop só pode ser usado quando o salto adiciona tempo ao áudio."
                ))
            self.table.setItem(row, 0, check_item)
            self.table.setItem(row, 1, time_item)
            self.table.setItem(row, 2, jump_item)
            self.table.setItem(row, 3, loop_item)
        layout.addWidget(self.table)
        self.table.itemChanged.connect(self._on_time_cell_changed)
        btn_layout = QHBoxLayout()
        btn_all = QPushButton(_tr_seg("seg_sel_marcar_todos", "Marcar todos"))
        btn_none = QPushButton(_tr_seg("seg_sel_desmarcar_todos", "Desmarcar todos"))
        btn_restore = QPushButton(_tr_seg("seg_sel_restaurar", "Restaurar tempos detectados"))
        btn_cancel = QPushButton(tr("btn_cancelar"))
        btn_ok = QPushButton("OK")
        btn_all.clicked.connect(lambda: self._set_all(Qt.CheckState.Checked))
        btn_none.clicked.connect(lambda: self._set_all(Qt.CheckState.Unchecked))
        btn_restore.clicked.connect(self._restore_times)
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        btn_layout.addWidget(btn_all)
        btn_layout.addWidget(btn_none)
        btn_layout.addWidget(btn_restore)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    @staticmethod
    def _auto_format_time(texto: str) -> str:
        digits = re.sub(r"\D", "", texto or "")[:9]
        if not digits:
            return ""
        resultado = ""
        limites = (2, 2, 2, 3)
        separadores = (":", ":", ".")
        pos = 0
        for i, tam in enumerate(limites):
            if pos >= len(digits):
                break
            resultado += digits[pos:pos + tam]
            pos += tam
            if pos < len(digits):
                resultado += separadores[i]
        return resultado

    def _on_time_cell_changed(self, item):
        if item is None or item.column() != 1:
            return
        if self._formatting:
            return
        self._formatting = True
        formatado = self._auto_format_time(item.text())
        if formatado != item.text():
            item.setText(formatado)
        self._formatting = False
        self._check_item_color(item)

    def _check_item_color(self, item):
        if item is None or item.column() != 1:
            return
        row = item.row()
        if row >= len(self._original_times):
            return
        original_text = f"{format_time(self._original_times[row])[:12]}"
        if item.text().strip() != original_text:
            item.setForeground(QColor("#f1c40f"))
            item.setToolTip(_tr_seg("seg_sel_tooltip_detectado", "Detectado pela análise: {v}").format(v=original_text))
        else:
            if self._default_fg is not None:
                item.setForeground(self._default_fg)
            item.setToolTip("")

    def _set_all(self, state):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 0)
            if item:
                item.setCheckState(state)

    def _restore_times(self):
        for row in range(self.table.rowCount()):
            item = self.table.item(row, 1)
            if item and row < len(self._original_times):
                item.setText(f"{format_time(self._original_times[row])[:12]}")

    def _avisar(self, texto: str):
        msg = QMessageBox(self)
        msg.setWindowTitle(tr("popup_aviso"))
        msg.setText(texto)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg.exec()

    def accept(self):
        checked_rows = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                checked_rows.append(row)
        prev_t = None
        for row in checked_rows:
            item = self.table.item(row, 1)
            texto = item.text() if item else ""
            t = _parse_time_text(texto)
            if t is None:
                self._avisar(_tr_seg(
                    "seg_sel_erro_formato",
                    "Tempo inválido na linha {n}: '{v}'.\nUse HH:MM:SS.mmm, MM:SS.mmm ou segundos."
                ).format(n=row + 1, v=texto))
                return
            if self._dubbed_dur > 0 and (t < 0.0 or t > self._dubbed_dur):
                self._avisar(_tr_seg(
                    "seg_sel_erro_limite",
                    "O tempo da linha {n} está fora da duração do arquivo ({v})."
                ).format(n=row + 1, v=format_time(self._dubbed_dur)[:12]))
                return
            if prev_t is not None and t <= prev_t + 1.0:
                self._avisar(_tr_seg(
                    "seg_sel_erro_ordem",
                    "Os tempos marcados devem estar em ordem crescente e com pelo menos 1s de distância (linha {n})."
                ).format(n=row + 1))
                return
            prev_t = t
        super().accept()

    def get_selected(self) -> list:
        selected = []
        for row in range(self.table.rowCount()):
            chk = self.table.item(row, 0)
            if chk and chk.checkState() == Qt.CheckState.Checked:
                item = self.table.item(row, 1)
                original_text = f"{format_time(self._original_times[row])[:12]}"
                edited = bool(item) and item.text().strip() != original_text
                t = _parse_time_text(item.text()) if item else None
                loop_item = self.table.item(row, 3)
                selected.append({
                    "index": row,
                    "time": (t if edited else None),
                    "loop": bool(loop_item and loop_item.checkState() == Qt.CheckState.Checked),
                })
        return selected
        # ====================== CONTROLLER PRINCIPAL ======================
class MainController(QObject):
    SCENE_SEARCH_RADIUS = 6.0
    SCENE_THRESHOLD = 0.08
    SILENCE_RADIUS_SCENE = 1.5
    SILENCE_RADIUS_NO_SCENE = 4.0
    SILENCE_MAX_MOVE_SCENE = 1.5
    SILENCE_MAX_MOVE_NO_SCENE = 4.0
    SYNCLOG_RETENTION_DAYS = 10
    SYNCLOG_CLEANUP_CHECK_MS = 60 * 60 * 1000
    EMOJI_PATTERN = re.compile(
        "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\U0000FE0F\U0000200D]"
    )

    def __init__(self, view: MainWindow):
        super().__init__()
        self.view = view
        self.view.controller = self
        self._synclog_history = ""
        self._carregar_synclog()
        self.view.event_log_changed.connect(self._sincronizar_synclog)
        self._synclog_cleanup_timer = QTimer(self)
        self._synclog_cleanup_timer.timeout.connect(self._limpar_synclog_expirado)
        self._synclog_cleanup_timer.start(self.SYNCLOG_CLEANUP_CHECK_MS)
        self.guide_path = self.dubbed_path = None
        self.guide_dur = self.dubbed_dur = 0
        self.guide_fps = 0.0
        self.guide_audio_idx = 0
        self.guide_has_video = False
        self.audio_info = self.analysis_result = {}
        self._corrected_audio = self._process_start_time = self._last_completed_type = self._last_completed_time = None
        self._segmented_audio = None
        self._segmented_global_offset = 0.0
        self._segmented_result = None
        self._segmented_prescan = None
        self._segmented_baked = True
        self._segmented_hybrid_active = False
        self._segmented_full_reencode = None
        self._analysis_done = False
        self._analysis_progress = None
        self._console_last_pct = -10
        self._sync_audio_corrected = None
        self._speed_correction_state = None
        self._operation_generation = 0
        self.prescan_worker = None
        self.segmented_worker = None
        self.segmented_ff_worker = None
        self.segmented_dialog = None
        self.audio_sync_worker = None
        self.mkvmerge_worker = None
        ft.refresh_path()
        try:
            check_dependencies()
            if not check_rubberband_available():
                self.view.log_message(tr("log_rubberband_nao_encontrado"))
        except Exception:
            self.view.log_message(tr("log_ffmpeg_nao_encontrado"))
        self.setup_cli_button()
        self.setup_mux_button()
        self.setup_sync_audio_button()
        self.setup_segmented_button()
        self.connect_signals()
        self._setup_ffmpeg_menu_action()
        self._setup_mkvtoolnix_menu_action()

    def _is_dark(self) -> bool:
        return getattr(self.view, 'is_dark', True)

    def _carregar_synclog(self) -> None:
        """Prepara o histórico persistente sem preencher o Log de Eventos."""
        self._limpar_synclog_expirado()
        try:
            with open("SyncLog.log", "r", encoding="utf-8") as file:
                original_content = file.read()
            self._synclog_history = self._sanitizar_log_arquivo(original_content).strip()
            if self._synclog_history != original_content.strip():
                with open("SyncLog.log", "w", encoding="utf-8") as file:
                    file.write(self._synclog_history)
        except FileNotFoundError:
            self._synclog_history = ""
        except OSError:
            self._synclog_history = ""
        self._atualizar_status_synclog()

    def _atualizar_status_synclog(self) -> None:
        if hasattr(self.view, "set_synclog_status"):
            path = os.path.abspath("SyncLog.log")
            has_history = os.path.isfile(path) and os.path.getsize(path) > 0
            self.view.set_synclog_status(path, has_history)

    def _sincronizar_synclog(self, content: str) -> None:
        """Acrescenta a sessão atual ao histórico persistente do SyncLog."""
        current_log = self._sanitizar_log_arquivo(content).strip()
        if current_log and self._synclog_history:
            combined_log = (
                f"{current_log}\n\n{self.view.LOG_SEPARATOR}\n\n{self._synclog_history}"
            )
        else:
            combined_log = current_log or self._synclog_history
        try:
            with open("SyncLog.log", "w", encoding="utf-8") as file:
                file.write(combined_log)
            self._atualizar_status_synclog()
        except OSError:
            pass

    @classmethod
    def _sanitizar_log_arquivo(cls, content: str) -> str:
        """Mantém os logs em texto simples, sem emoticons ou emojis."""
        return cls.EMOJI_PATTERN.sub("", content)

    def _limpar_synclog_expirado(self) -> None:
        """Exclui o histórico ao completar dez dias desde a última limpeza."""
        settings = QSettings("Vicio", "DublaSync")
        now = datetime.datetime.now()
        saved_value = settings.value("synclog_last_cleanup", "")
        try:
            last_cleanup = datetime.datetime.fromisoformat(str(saved_value))
        except (TypeError, ValueError):
            try:
                last_cleanup = datetime.datetime.fromtimestamp(
                    os.path.getmtime("SyncLog.log")
                )
            except OSError:
                last_cleanup = now

        was_cleaned = now - last_cleanup >= datetime.timedelta(
            days=self.SYNCLOG_RETENTION_DAYS
        )
        if was_cleaned:
            try:
                os.remove("SyncLog.log")
            except FileNotFoundError:
                pass
            except OSError:
                return
            self._synclog_history = ""
            last_cleanup = now

        settings.setValue("synclog_last_cleanup", last_cleanup.isoformat())
        self._atualizar_status_synclog()
        if was_cleaned and hasattr(self.view, "log_text"):
            self._sincronizar_synclog(self.view.log_text.toPlainText())

    def _iniciar_grupo_log_atual(self) -> None:
        """Mantém no topo o bloco referente ao arquivo atualmente processado."""
        source_path = self.guide_path or self.dubbed_path
        if source_path and hasattr(self.view, "start_log_group"):
            self.view.start_log_group(source_path)

    # ====================== CONSOLE ======================
    def _console_start(self):
        self._console_last_pct = -10
        if hasattr(self.view, "console_start"):
            self.view.console_start()

    def _console_line(self, kind: str, text: str):
        if kind == "tech" and text.lower().startswith("ffmpeg "):
            if hasattr(self.view, "log_technical_message"):
                self.view.log_technical_message(text)
        if hasattr(self.view, "console_line"):
            self.view.console_line(kind, text)

    def _console_progress(self, text: str, percent: float):
        pct = int(percent)
        if pct >= 100 or (pct - self._console_last_pct) >= 5:
            self._console_last_pct = pct
            self._console_line("progress", text)

    def _console_end(self):
        if hasattr(self.view, "console_end"):
            self.view.console_end()

    def _registrar_conversao_audio(self, contexto: str) -> None:
        """Registra cada recodificação de áudio que o fluxo realmente inicia."""
        self.view.log_message(f"[ÁUDIO] Conversão de áudio iniciada: {contexto}.")

    # ====================== HELPERS DE ESTADO E FFmpeg ======================
    def _resetar_estado_processo(self):
        # Invalida callbacks enfileirados de workers pertencentes à operação
        # anterior. Isso evita que uma análise cancelada altere a UI depois que
        # a pessoa já carregou outros arquivos.
        self._operation_generation += 1
        self._process_start_time = None
        self._last_completed_type = None
        self._last_completed_time = None
        self._analysis_progress = None

    def _callback_da_operacao_atual(self, callback):
        """Retorna um slot que ignora sinais de uma geração já encerrada."""
        generation = self._operation_generation

        def guarded_callback(*args):
            if generation == self._operation_generation:
                callback(*args)

        return guarded_callback

    def _clear_segmented_state(self):
        self._segmented_audio = None
        self._segmented_global_offset = 0.0
        self._segmented_result = None
        self._segmented_prescan = None
        self._segmented_baked = True

    def _cancel_segmented_workers(self):
        for attr in ('prescan_worker', 'segmented_worker', 'segmented_ff_worker'):
            w = getattr(self, attr, None)
            if w and w.isRunning():
                w.cancel()

    def _get_audio_encoding_info(self):
        encoders = get_ffmpeg_audio_encoders()
        bitrate_val = self.audio_info.get('bitrate')
        bitrate = f"{int(int(bitrate_val)/1000)}k" if bitrate_val else None
        return select_audio_output(self.audio_info.get('codec_name', 'ac3'), bitrate, encoders)

    def _build_audio_ffmpeg_cmd(self, input_path: str, output_path: str, filter_str: str) -> list:
        enc, ext, sup_bit = self._get_audio_encoding_info()
        audio_idx = self.audio_info.get('index', 0)
        cmd = [
            'ffmpeg', '-y', '-v', 'warning', '-progress', 'pipe:1', '-nostats',
            '-i', input_path, '-map', f'0:a:{audio_idx}', '-vn', '-sn', '-dn', '-c:a', enc
        ]
        if enc == 'dca':
            cmd.extend(['-strict', '-2'])
        bitrate_val = self.audio_info.get('bitrate')
        if sup_bit and bitrate_val:
            cmd.extend(['-b:a', f"{int(int(bitrate_val)/1000)}k"])
        if self.audio_info.get('sample_rate'):
            cmd.extend(['-ar', self.audio_info['sample_rate']])
        cmd.extend(['-filter:a', filter_str, output_path])
        return cmd

    @staticmethod
    def _build_speed_filter(selected_filter: str, tempo_str: str) -> str:
        """Monta o filtro de velocidade usado por todos os fluxos de correção."""
        if selected_filter == "atempo":
            return f'atempo={tempo_str}'
        return f'rubberband=tempo={tempo_str}:channels=together'

    def _create_speed_correction_temp_path(self, prefix: str) -> str:
        """Reserva um nome temporário exclusivo; o chamador continua dono da limpeza."""
        _, ext, _ = self._get_audio_encoding_info()
        descriptor, path = tempfile.mkstemp(prefix=f"{prefix}_", suffix=ext)
        os.close(descriptor)
        # FFmpeg recebe o nome reservado, mas cria o arquivo por conta própria.
        os.unlink(path)
        return path

    def _start_speed_correction_pipeline(
        self,
        *,
        selected_filter: str,
        tempo_str: str,
        temporary_path: str,
        on_complete,
    ) -> None:
        """Executa a parte comum: corrigir velocidade e recalcular o offset.

        O callback recebe ``SpeedCorrectionResult``. Ele decide se o áudio será
        salvo isoladamente ou encaminhado ao mux e, portanto, também preserva a
        responsabilidade pela limpeza do temporário.
        """
        filter_str = self._build_speed_filter(selected_filter, tempo_str)
        cmd = self._build_audio_ffmpeg_cmd(self.dubbed_path, temporary_path, filter_str)
        self._registrar_conversao_audio(
            "correção de velocidade em arquivo temporário"
        )
        self._speed_correction_state = {
            "temporary_path": temporary_path,
            "selected_filter": selected_filter,
            "tempo_str": tempo_str,
            "on_complete": on_complete,
        }
        self._console_line("tech", " ".join(cmd))
        self.ff_worker = FFmpegWorker(
            cmd,
            self.dubbed_dur,
            temporary_path,
            tr("mkv_corrigindo")
        )
        self.ff_worker.progress.connect(self._callback_da_operacao_atual(self.update_progress))
        self.ff_worker.finished.connect(self._callback_da_operacao_atual(self._on_speed_correction_encoded))
        self.ff_worker.error.connect(self._callback_da_operacao_atual(self._on_speed_correction_error))
        self.ff_worker.start()

    def _on_speed_correction_encoded(self, corrected_path: str) -> None:
        state = getattr(self, "_speed_correction_state", None)
        if not state:
            self.operation_error("Correção de velocidade concluída sem contexto ativo.")
            return
        state["corrected_audio_path"] = corrected_path
        self.view.log_message(tr("mkv_analise2"))
        try:
            corrected_duration, _, _, _ = get_file_info(corrected_path)
        except Exception as e:
            self._speed_correction_state = None
            self.operation_error(str(e))
            return

        self._worker2 = SyncWorker(
            self.guide_path,
            corrected_path,
            self.guide_dur,
            corrected_duration,
            self.guide_audio_idx,
            0
        )
        self._worker2.progress.connect(self._callback_da_operacao_atual(self.update_progress))
        self._worker2.finished.connect(self._callback_da_operacao_atual(self._on_speed_correction_reanalysed))
        self._worker2.error.connect(self._callback_da_operacao_atual(self._on_speed_correction_error))
        self._worker2.start()

    def _on_speed_correction_reanalysed(self, analysis: dict) -> None:
        state = getattr(self, "_speed_correction_state", None)
        if not state:
            self.operation_error("Segunda análise concluída sem contexto de correção.")
            return

        result = SpeedCorrectionResult(
            corrected_audio_path=state["corrected_audio_path"],
            final_offset=float(analysis.get("offset", 0.0)),
            analysis=analysis,
            selected_filter=state["selected_filter"],
            tempo_str=state["tempo_str"],
        )
        on_complete = state["on_complete"]
        self._speed_correction_state = None
        try:
            on_complete(result)
        except Exception as e:
            self.operation_error(str(e))

    def _on_speed_correction_error(self, error: str) -> None:
        self._speed_correction_state = None
        self.operation_error(error)

    def _apresentar_segunda_analise_velocidade(self, result: dict) -> None:
        """Atualiza diagnóstico e logs compartilhados pelos dois destinos finais."""
        texto_final, bloco_log, _ = self._montar_relatorio(result)
        self.save_analysis_log(bloco_log)
        self._console_line("step", tr("mkv_analise2"))
        self.view.result_label.setText(
            f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>"
        )
        if hasattr(self.view, 'log_diagnostic_top'):
            self.view.log_diagnostic_top(bloco_log)
        else:
            self.view.log_message(bloco_log)

    def _formatar_delay_html(self, offset_A: float) -> str:
        delay_formatado = f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}"
        delay_ms = round(abs(offset_A) * 1000)

        # Mantém o resultado do diagnóstico nas mesmas faixas visuais da
        # tabela profissional da aba Lip-Sync.
        if delay_ms <= 40:
            cor = "#2ecc71"  # verde: perfeita/excelente
        elif delay_ms <= 60:
            cor = "#f1c40f"  # amarelo: pode merecer ajuste fino
        elif delay_ms <= 100:
            cor = "#e67e22"  # laranja: assincronia evidente
        else:
            cor = "#e74c3c"  # vermelho: ajuste recomendado

        return (
            f"<span style='color: {cor}; font-weight: bold;'>"
            f"{delay_formatado} segundos"
            "</span>"
        )

    def _encontrar_fps_match(self, speed_factor: float, spread: float = float('inf'),
                             dur_ratio: float = None, video_fps: float = None,
                             anchor_count: int = 0, inlier_count: int = 0,
                             speed_factor_ci_low: float = None,
                             speed_factor_ci_high: float = None):
        NTSC_DROP = Fraction(24000, 1001)
        CINEMA = Fraction(24, 1)
        PAL = Fraction(25, 1)
        NTSC = Fraction(30000, 1001)
        VIDEO_30 = Fraction(30, 1)
        fps_ratios = {
            "24_25": {"ratio": PAL / CINEMA, "expr": "25/24", "tr_key": "fps_24_25"},
            "ntsc_25": {"ratio": PAL / NTSC_DROP, "expr": "25/(24000/1001)", "tr_key": "fps_ntsc_25"},
            "ntsc_24": {"ratio": CINEMA / NTSC_DROP, "expr": "24/(24000/1001)", "tr_key": "fps_ntsc_24"},
            "25_24": {"ratio": CINEMA / PAL, "expr": "24/25", "tr_key": "fps_25_24"},
            "25_ntsc": {"ratio": NTSC_DROP / PAL, "expr": "(24000/1001)/25", "tr_key": "fps_25_ntsc"},
            "24_ntsc": {"ratio": NTSC_DROP / CINEMA, "expr": "(24000/1001)/24", "tr_key": "fps_24_ntsc"},
            "23976_2997": {"ratio": NTSC / NTSC_DROP, "expr": "(30000/1001)/(24000/1001)", "tr_key": "fps_23976_2997"},
            "2997_23976": {"ratio": NTSC_DROP / NTSC, "expr": "(24000/1001)/(30000/1001)", "tr_key": "fps_2997_23976"},
            "23976_30": {"ratio": VIDEO_30 / NTSC_DROP, "expr": "30/(24000/1001)", "tr_key": "fps_23976_30"},
            "30_23976": {"ratio": NTSC_DROP / VIDEO_30, "expr": "(24000/1001)/30", "tr_key": "fps_30_23976"},
            "24_2997": {"ratio": NTSC / CINEMA, "expr": "(30000/1001)/24", "tr_key": "fps_24_2997"},
            "2997_24": {"ratio": CINEMA / NTSC, "expr": "24/(30000/1001)", "tr_key": "fps_2997_24"},
            "24_30": {"ratio": VIDEO_30 / CINEMA, "expr": "30/24", "tr_key": "fps_24_30"},
            "30_24": {"ratio": CINEMA / VIDEO_30, "expr": "24/30", "tr_key": "fps_30_24"},
            "25_2997": {"ratio": NTSC / PAL, "expr": "(30000/1001)/25", "tr_key": "fps_25_2997"},
            "2997_25": {"ratio": PAL / NTSC, "expr": "25/(30000/1001)", "tr_key": "fps_2997_25"},
            "25_30": {"ratio": VIDEO_30 / PAL, "expr": "30/25", "tr_key": "fps_25_30"},
            "30_25": {"ratio": PAL / VIDEO_30, "expr": "25/30", "tr_key": "fps_30_25"},
            "2997_30": {"ratio": VIDEO_30 / NTSC, "expr": "30/(30000/1001)", "tr_key": "fps_2997_30"},
            "30_2997": {"ratio": NTSC / VIDEO_30, "expr": "(30000/1001)/30", "tr_key": "fps_30_2997"},
        }
        best_match, best_expr, best_key, best_ratio, min_error = (
            None, None, None, None, float('inf')
        )
        for name, data in fps_ratios.items():
            ratio_val = float(data["ratio"])
            error = abs(speed_factor - ratio_val)
            if dur_ratio is not None:
                error_dur = abs(dur_ratio - ratio_val)
                if error_dur < 0.002 and error_dur < error and error < 0.05 and (speed_factor - 1) * (dur_ratio - 1) > 0:
                    error = error_dur
            if error < min_error:
                min_error = error
                best_match = tr(data["tr_key"])
                best_expr = data["expr"]
                best_key = name
                best_ratio = ratio_val
        factor_ci_width = float('inf')
        candidate_in_interval = False
        if speed_factor_ci_low is not None and speed_factor_ci_high is not None:
            factor_ci_width = max(0.0, speed_factor_ci_high - speed_factor_ci_low)
            candidate_in_interval = (
                best_ratio is not None
                and speed_factor_ci_low <= best_ratio <= speed_factor_ci_high
            )
        confidence = self._calcular_confianca(
            min_error, spread, anchor_count, inlier_count,
            candidate_in_interval, factor_ci_width,
        )
        return best_match, best_expr, best_key, min_error, confidence

    def _calcular_confianca(self, min_error: float, spread: float,
                             anchor_count: int = 0, inlier_count: int = 0,
                             candidate_in_interval: bool = False,
                             factor_ci_width: float = float('inf')) -> str:
        """Classifica a conversão pelo padrão de FPS e pela concordância das âncoras."""
        if anchor_count <= 0 or inlier_count <= 0 or not candidate_in_interval:
            return tr("conf_indeterminada")
        inlier_ratio = inlier_count / anchor_count
        if (
            min_error < 0.003 and spread < 2.0 and factor_ci_width < 0.003
            and anchor_count >= 7 and inlier_ratio >= 0.85
        ):
            return tr("conf_alta")
        if (
            min_error < 0.008 and spread < 5.0 and factor_ci_width < 0.008
            and anchor_count >= 6 and inlier_ratio >= 0.75
        ):
            return tr("conf_media")
        if (
            min_error < 0.030 and spread < 15.0 and factor_ci_width < 0.030
            and anchor_count >= 5 and inlier_ratio >= 0.60
        ):
            return tr("conf_baixa")
        return tr("conf_indeterminada")

    def _inferir_fps_origem_dublagem(self, speed_factor: float):
        if not self.guide_fps or self.guide_fps <= 0 or speed_factor <= 0:
            return None
        return self.guide_fps / speed_factor

    # ====================== VALIDAÇÃO E UI BÁSICA ======================
    def _setup_ffmpeg_menu_action(self) -> None:
        menu = self.view.btn_config.menu()
        if menu is None:
            return
        self.action_ffmpeg = QAction(tr("menu_ffmpeg"), self.view)
        self.action_ffmpeg.triggered.connect(self.open_ffmpeg_installer)
        menu.addAction(self.action_ffmpeg)

    def _setup_mkvtoolnix_menu_action(self) -> None:
        """Adiciona a configuração opcional do mkvmerge ao menu já existente."""
        menu = self.view.btn_config.menu()
        if menu is None:
            return
        self.action_mkvtoolnix = QAction(tr("menu_mkvtoolnix"), self.view, checkable=True)
        valor_salvo = QSettings("Vicio", "DublaSync").value("usar_mkvtoolnix", None)
        if valor_salvo is None:
            habilitado = bool(mth.caminho_configurado_mkvmerge())
        else:
            habilitado = str(valor_salvo).strip().lower() not in {"false", "0", "no"}
        self.action_mkvtoolnix.setChecked(habilitado)
        self.action_mkvtoolnix.toggled.connect(
            lambda ativa: QSettings("Vicio", "DublaSync").setValue(
                "usar_mkvtoolnix", ativa
            )
        )
        self.action_mkvtoolnix.triggered.connect(self._ao_alternar_mkvtoolnix)
        menu.addAction(self.action_mkvtoolnix)

    def _ao_alternar_mkvtoolnix(self, habilitado: bool) -> None:
        """Abre a seleção do executável ao habilitar o MKVToolNix."""
        if habilitado:
            self._escolher_mkvmerge()

    def _usar_mkvtoolnix(self) -> bool:
        """Indica se o usuário prefere o MKVToolNix ao gerar arquivos MKV."""
        action = getattr(self, "action_mkvtoolnix", None)
        return action is None or action.isChecked()

    def _log_mkvtoolnix(self, message: str) -> None:
        """Centraliza a entrega de eventos técnicos do módulo à UI atual."""
        self.view.log_message(message)
        self._console_line("tech", message)

    def _escolher_mkvmerge(self) -> None:
        """Permite informar manualmente o executável mkvmerge em qualquer sistema."""
        from PySide6.QtWidgets import QFileDialog

        configured = mth.caminho_configurado_mkvmerge()
        initial_dir = str(Path(configured).parent) if configured else ""
        selected_dir = QFileDialog.getExistingDirectory(
            self.view,
            tr("dlg_selecionar_mkvmerge"),
            initial_dir,
        )
        if not selected_dir:
            self.action_mkvtoolnix.setChecked(False)
            return
        executable_path = mth.localizar_mkvmerge(selected_dir)
        if not executable_path:
            self._log_mkvtoolnix(
                tr("log_mkvtoolnix_pasta_sem_exe").format(path=selected_dir)
            )
            self._mostrar_mensagem(
                "aviso",
                tr("popup_mkvtoolnix_invalido"),
                tr("mkvtoolnix_exe_nao_encontrado"),
            )
            return

        validation = mth.validar_mkvmerge(executable_path)
        if validation.valid:
            mth.salvar_caminho_mkvmerge(validation.path)
            self._log_mkvtoolnix(
                tr("log_mkvtoolnix_validado").format(
                    path=validation.path,
                    version=validation.version or "mkvmerge",
                )
            )
            self._mostrar_mensagem(
                "sucesso",
                tr("popup_mkvtoolnix_configurado"),
                tr("mkvtoolnix_configurado").format(
                    path=validation.path,
                    version=validation.version or "mkvmerge",
                ),
            )
            return

        self._log_mkvtoolnix(
            tr("log_mkvtoolnix_indisponivel").format(erro=validation.error)
        )
        if validation.path:
            self._log_mkvtoolnix(f"[MKVTOOLNIX] Caminho informado: {validation.path}")
        self._mostrar_mensagem(
            "aviso",
            tr("popup_mkvtoolnix_invalido"),
            tr("mkvtoolnix_invalido"),
        )

    def _is_ffmpeg_missing(self, e: object) -> bool:
        msg = str(e).lower()
        return any(p in msg for p in (
            "winerror 2",
            "winerror 3",
            "não pode encontrar o caminho",
            "o sistema não pode encontrar o arquivo",
            "the system cannot find",
            "no such file or directory",
            "não encontrados no sistema"
        ))

    def _eh_cancelamento(self, msg: str) -> bool:
        return any(p in msg.lower() for p in ("cancelad", "canceled", "cancelled"))

    def _estilizar_msgbox(self, msg: QMessageBox, nivel: str) -> None:
        msg.setIconPixmap(_criar_icone_mensagem(nivel))
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        dlg_bg, dlg_fg = ("#2d2d30", "#e7d9b8") if is_dark else ("#f0f0f0", "#333333")
        msg.setStyleSheet(
            f"QMessageBox {{ background-color: {dlg_bg}; color: {dlg_fg}; }} "
            f"QMessageBox QLabel {{ color: {dlg_fg}; background: transparent; }}"
        )

    def _mostrar_mensagem(self, nivel: str, titulo: str, texto: str) -> None:
        msg = QMessageBox(self.view)
        msg.setWindowTitle(titulo)
        msg.setText(texto)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._estilizar_msgbox(msg, nivel)
        msg.exec()

    def _mostrar_sucesso_com_pasta(self, titulo: str, texto: str, file_path: str) -> None:
        msg = QMessageBox(self.view)
        msg.setWindowTitle(titulo)
        msg.setText(texto)
        btn_pasta = msg.addButton(tr("btn_abrir_pasta"), QMessageBox.ButtonRole.AcceptRole)
        btn_ok = msg.addButton("OK", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_ok)
        self._estilizar_msgbox(msg, "sucesso")
        msg.exec()
        if msg.clickedButton() == btn_pasta:
            try:
                subprocess.Popen(f'explorer /select,"{os.path.normpath(file_path)}"')
            except Exception as e:
                self.view.log_message(f"[ERRO] Falha ao abrir pasta: {e}")

    def _check_and_get_output_path(self, desired_path: str):
        if not os.path.exists(desired_path):
            return desired_path
        msg = QMessageBox(self.view)
        msg.setWindowTitle(tr("dlg_arquivo_existe_titulo"))
        msg.setText(tr("dlg_arquivo_existe_msg").format(arquivo=os.path.basename(desired_path)))
        btn_sobrescrever = msg.addButton(tr("btn_sobrescrever"), QMessageBox.ButtonRole.AcceptRole)
        btn_novo = msg.addButton(tr("btn_gerar_novo"), QMessageBox.ButtonRole.AcceptRole)
        btn_cancelar = msg.addButton(tr("btn_cancelar"), QMessageBox.ButtonRole.RejectRole)
        self._estilizar_msgbox(msg, "aviso")
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_cancelar or clicked is None:
            return None
        if clicked == btn_sobrescrever:
            return desired_path
        base, ext = os.path.splitext(desired_path)
        contador, novo_path = 1, f"{base} (1){ext}"
        while os.path.exists(novo_path):
            contador += 1
            novo_path = f"{base} ({contador}){ext}"
        return novo_path

    def _validar_arquivo_midia(self, filepath: str, tipo_guia: str) -> bool:
        data_hora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        if not os.path.exists(filepath):
            self._registrar_rejeicao(filepath, tipo_guia, data_hora, "Arquivo não encontrado.")
            return False
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        cmd = ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', filepath]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=15,
                creationflags=creationflags
            )
            if result.returncode != 0:
                erro_tecnico = result.stderr.strip() or "Erro desconhecido do FFprobe."
                self.view.log_message(
                    f"[{data_hora}] [{tipo_guia}] Falha ao analisar arquivo: {filepath} | Erro FFprobe: {erro_tecnico}"
                )
                self._mostrar_erro_arquivo_incompativel()
                return False
            data = json.loads(result.stdout)
            codecs_imagem = {
                'mjpeg', 'png', 'bmp', 'tiff', 'gif', 'webp',
                'ppm', 'pgm', 'pbm', 'pam', 'j2k', 'j2kp', 'jpeg2000', 'jpegls'
            }
            tem_audio = any(s.get('codec_type') == 'audio' for s in data.get('streams', []))
            tem_video = any(
                s.get('codec_type') == 'video' and s.get('codec_name', '').lower() not in codecs_imagem
                for s in data.get('streams', [])
            )
            if not tem_audio and not tem_video:
                self._registrar_rejeicao(
                    filepath,
                    tipo_guia,
                    data_hora,
                    "Nenhum stream de áudio ou vídeo válido encontrado."
                )
                return False
            return True
        except Exception as e:
            motivo = "Tempo limite excedido." if isinstance(e, subprocess.TimeoutExpired) else f"Erro inesperado: {str(e)}"
            self.view.log_message(
                f"[{data_hora}] [{tipo_guia}] Falha ao analisar arquivo: {filepath} | Erro FFprobe: {motivo}"
            )
            self._mostrar_erro_arquivo_incompativel()
            return False

    def _registrar_rejeicao(self, filepath, tipo_guia, data_hora, motivo):
        self.view.log_message(f"[{data_hora}] [{tipo_guia}] Arquivo incompatível: {filepath} | Motivo: {motivo}")
        self._mostrar_erro_arquivo_incompativel()

    def _mostrar_erro_arquivo_incompativel(self):
        self._mostrar_mensagem(
            "erro",
            "Arquivo incompatível",
            "O arquivo selecionado não é um arquivo de vídeo ou áudio compatível com o programa.\n\n"
            "Selecione um arquivo de vídeo ou áudio válido e tente novamente."
        )

    # ====================== INSTALAÇÃO DO FFmpeg ======================
    def open_ffmpeg_installer(self) -> None:
        self.ffmpeg_dialog = FFmpegInstallerDialog(self.view)
        self.ffmpeg_dialog.btn_cancel.clicked.connect(self._cancel_ffmpeg_job)
        self.ffmpeg_dialog.btn_reset.clicked.connect(self._confirmar_reset_ffmpeg)
        self.ffmpeg_dialog.set_working()
        self.ffmpeg_dialog.show()
        if ft.ffmpeg_requer_configuracao():
            self.ffmpeg_dialog.set_progress(tr("ffmpeg_config_necessaria"), 0)
            self._mostrar_opcoes_ffmpeg(
                self.ffmpeg_dialog, tr("ffmpeg_reconfigurar_pergunta")
            )
            return
        self.ffmpeg_dialog.append_log(tr("ffmpeg_verificando"))
        self._start_ffmpeg_verify()

    def _confirmar_reset_ffmpeg(self) -> None:
        """Esquece o caminho salvo do FFmpeg após confirmação do usuário."""
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg:
            return

        msg = QMessageBox(dlg)
        msg.setWindowTitle(tr("popup_redefinir_ffmpeg"))
        msg.setText(tr("ffmpeg_reset_confirmacao"))
        self._estilizar_msgbox(msg, "aviso")
        btn_reset = msg.addButton(
            tr("btn_redefinir_config_ffmpeg"), QMessageBox.ButtonRole.AcceptRole
        )
        msg.addButton(tr("btn_cancelar"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        if msg.clickedButton() is not btn_reset:
            return

        ft.remover_caminho_ffmpeg()
        dlg.set_progress(tr("ffmpeg_config_redefinida"), 0)
        dlg.append_log(tr("ffmpeg_reset_log"))
        dlg.btn_reset.setEnabled(False)
        dlg.btn_cancel.setEnabled(False)
        dlg.btn_close.setEnabled(True)
        self.view.log_message(tr("ffmpeg_reset_log"))

    def _start_ffmpeg_verify(self) -> None:
        self.ffmpeg_verifier = FFmpegVerifier()
        self.ffmpeg_verifier.finished.connect(self._on_ffmpeg_verified)
        self.ffmpeg_verifier.start()

    def _on_ffmpeg_verified(self, info: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg:
            return
        if info["ok"]:
            ft.marcar_ffmpeg_configurado()
            dlg.set_progress(tr("ffmpeg_pronto"), 100)
            dlg.append_log(tr("log_caminho").format(path=info['path']))
            dlg.append_log(tr("log_versao").format(version=info['version']))
            dlg.append_log(tr("log_rb_disponivel"))
            dlg.set_finished()
            self.view.log_message(tr("log_ffmpeg_verificado").format(path=info['path']))
            return
        dlg.append_log(tr("log_ffmpeg_encontrado_sem_rb") if info["installed"] else tr("log_ffmpeg_nao_encontrado_x"))
        pergunta = tr("ffmpeg_pergunta_sem_rb") if info["installed"] else tr("ffmpeg_pergunta_nao_encontrado")
        self._mostrar_opcoes_ffmpeg(dlg, pergunta)

    def _mostrar_opcoes_ffmpeg(self, dlg, pergunta: str) -> None:
        """Exibe as opções de instalação ou seleção do FFmpeg."""
        msg = QMessageBox(dlg)
        msg.setWindowTitle(tr("popup_instalar_ffmpeg"))
        msg.setText(pergunta)
        self._estilizar_msgbox(msg, "info")
        btn_install = msg.addButton(tr("btn_instalar_agora"), QMessageBox.ButtonRole.AcceptRole)
        btn_pick = msg.addButton(tr("btn_escolher_pasta"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(tr("btn_mais_tarde"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == btn_install:
            dlg.set_working()
            self.ffmpeg_installer = FFmpegInstaller(use_fallback=True)
            self.ffmpeg_installer.progress.connect(dlg.set_progress)
            self.ffmpeg_installer.finished.connect(self._on_ffmpeg_installed)
            self.ffmpeg_installer.error.connect(self._on_ffmpeg_error)
            self.ffmpeg_installer.start()
        elif clicked == btn_pick:
            self._escolher_pasta_ffmpeg(dlg)
        else:
            dlg.append_log(tr("ffmpeg_instalacao_recusada"))
            dlg.set_progress(tr("verificacao_concluida"), 100)
            dlg.set_finished()

    def _escolher_pasta_ffmpeg(self, dlg) -> None:
        from PySide6.QtWidgets import QFileDialog
        pasta = QFileDialog.getExistingDirectory(dlg, tr("dlg_selecionar_pasta_ffmpeg"))
        if not pasta:
            return
        exe_path = ft.find_ffmpeg_exe_in(pasta)
        if not exe_path:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_ffmpeg_nao_encontrado"),
                tr("ffmpeg_nenhum_exe_pasta").format(pasta=pasta)
            )
            return
        info = ft.verify(exe_path)
        if not info["ok"]:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_ffmpeg_nao_encontrado"),
                tr("ffmpeg_pergunta_sem_rb"),
            )
            return
        bin_dir = str(Path(exe_path).parent)
        ft.salvar_caminho_ffmpeg(bin_dir)
        ft.refresh_path()
        ft.add_to_user_path(bin_dir)
        linhas = [
            tr("log_caminho").format(path=info['path']),
            tr("log_versao").format(version=info['version']),
            tr("log_rb_disponivel"),
            tr("log_rb_salvo_path")
        ]
        self.view.log_message(tr("log_ffmpeg_configurado").format(path=info['path']))
        if dlg:
            for l in linhas:
                dlg.append_log(l)
            dlg.set_progress(tr("popup_ffmpeg_configurado"), 100)
            dlg.set_finished()
        else:
            self._mostrar_mensagem("sucesso", tr("popup_ffmpeg_configurado"), "\n".join(linhas))

    def _on_ffmpeg_installed(self, result: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg:
            return
        if result.get("installed"):
            ft.marcar_ffmpeg_configurado()
        if result.get("ok"):
            dlg.set_progress(tr("ffmpeg_instalado_verificado"), 100)
            dlg.append_log(tr("log_caminho").format(path=result['path']))
            dlg.append_log(tr("log_versao").format(version=result['version']))
            dlg.append_log(tr("log_rb_disponivel"))
            self.view.log_message(tr("log_ffmpeg_instalado").format(path=result['path']))
        else:
            dlg.set_progress(tr("ffmpeg_instalado_pendencias"), 100)
            dlg.append_log(tr("log_rb_nao_disponivel"))
            self.view.log_message(tr("log_ffmpeg_sem_rb"))
        dlg.set_finished()

    def _on_ffmpeg_error(self, message: str) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg:
            return
        dlg.append_log(message)
        if self._eh_cancelamento(message):
            dlg.progress_lbl.setText(tr("cancelado"))
        else:
            dlg.set_progress(tr("falha_operacao"), 100)
            self.view.log_message(tr("log_ffmpeg_erro").format(erro=message))
        dlg.set_finished()

    def _cancel_ffmpeg_job(self) -> None:
        installer = getattr(self, "ffmpeg_installer", None)
        if installer and installer.isRunning():
            installer.cancel()
        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg:
            dlg.append_log(tr("cancelado_fechar"))
            dlg.progress_lbl.setText(tr("cancelado"))
            dlg.set_finished()

    # ====================== INJEÇÃO DE BOTÕES NA UI ======================
    def _inserir_botao_na_barra_acoes(self, widget, fallback_index: int = 3, after_widget=None):
        parent = self.view.btn_convert.parentWidget()
        if parent is not None and parent.layout() is not None:
            outer = parent.layout()
            for i in range(outer.count()):
                item = outer.itemAt(i)
                if item is not None and item.layout() is not None and item.layout().indexOf(self.view.btn_convert) >= 0:
                    layout = item.layout()
                    if after_widget is not None and layout.indexOf(after_widget) >= 0:
                        layout.insertWidget(layout.indexOf(after_widget) + 1, widget)
                    else:
                        layout.insertWidget(min(fallback_index, layout.count()), widget)
                    return
            outer.addWidget(widget)
            return
        for layout in self.view.findChildren(QLayout):
            if layout.indexOf(self.view.btn_convert) >= 0:
                if after_widget is not None and layout.indexOf(after_widget) >= 0:
                    layout.insertWidget(layout.indexOf(after_widget) + 1, widget)
                else:
                    layout.insertWidget(min(fallback_index, layout.count()), widget)
                return
        if self.view.layout() is not None:
            self.view.layout().addWidget(widget)

    def _sync_audio_button_text(self) -> str:
        return _as_tr("btn", "Sincronizar Áudio")

    def setup_cli_button(self):
        self.btn_cmd = QPushButton(tr("btn_cmd"), self.view)
        self.btn_cmd.setObjectName("btnCmd")
        self.btn_cmd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cmd.hide()
        self.btn_cmd.clicked.connect(self.show_cli_window)
        if hasattr(self.view, 'progress_lbl'):
            parent = self.view.progress_lbl.parentWidget()
            if parent and parent.layout():
                layout = parent.layout()
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    if item and item.widget() == self.view.progress_lbl:
                        hbox = QHBoxLayout()
                        hbox.setContentsMargins(0, 0, 0, 0)
                        layout.takeAt(i)
                        hbox.addWidget(self.view.progress_lbl)
                        hbox.addStretch()
                        hbox.addWidget(self.btn_cmd)
                        layout.insertLayout(i, hbox)
                        break

    def setup_mux_button(self):
        self.btn_mux = QPushButton(tr("btn_gerar_mkv"), self.view)
        self.btn_mux.setObjectName("btnMux")
        self.btn_mux.setMinimumHeight(45)
        self.btn_mux.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_mux.setToolTip(tr("tooltip_sincronizar_mkv"))
        self.btn_mux.setStyleSheet(obter_estilo_btn_mux(self._is_dark()))
        self.btn_mux.hide()
        self.btn_mux.clicked.connect(self.start_mux)
        parent = self.view.btn_convert.parentWidget()
        if parent is not None and parent.layout() is not None:
            outer = parent.layout()
            for i in range(outer.count()):
                item = outer.itemAt(i)
                if item is not None and item.layout() is not None and item.layout().indexOf(self.view.btn_convert) >= 0:
                    item.layout().insertWidget(2, self.btn_mux)
                    break
            else:
                outer.addWidget(self.btn_mux)

    def setup_sync_audio_button(self):
        self.btn_sync_audio = QPushButton(self._sync_audio_button_text(), self.view)
        self.btn_sync_audio.setObjectName("btnSyncAudio")
        self.btn_sync_audio.setMinimumHeight(45)
        self.btn_sync_audio.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_sync_audio.setToolTip(tr("tooltip_sincronizar_audio"))
        self.btn_sync_audio.setStyleSheet(obter_estilo_btn_sync_audio(self._is_dark()))
        self.btn_sync_audio.hide()
        self.btn_sync_audio.clicked.connect(self.start_sync_audio)
        self._inserir_botao_na_barra_acoes(self.btn_sync_audio, fallback_index=3)

    def setup_segmented_button(self):
        self.btn_segmented = QPushButton(tr("btn_analise_segmentada"), self.view)
        self.btn_segmented.setObjectName("btnSegmented")
        self.btn_segmented.setMinimumHeight(45)
        self.btn_segmented.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_segmented.setToolTip(tr("tooltip_analise_segmentada"))
        self.btn_segmented.setStyleSheet(obter_estilo_btn_segmented(self._is_dark()))
        self.btn_segmented.hide()
        self.btn_segmented.clicked.connect(self.open_segmented_analysis)
        after = getattr(self, "btn_sync_audio", None)
        self._inserir_botao_na_barra_acoes(
            self.btn_segmented,
            fallback_index=4,
            after_widget=after
        )

    # ====================== FLUXO DE ARQUIVOS (CARDS) ======================
    def connect_signals(self):
        self.view.card_guide.file_dropped.connect(self.handle_guide_dropped)
        self.view.card_dubbed.file_dropped.connect(self.handle_dubbed_dropped)
        self.view.card_guide.file_cleared.connect(self.handle_guide_cleared)
        self.view.card_dubbed.file_cleared.connect(self.handle_dubbed_cleared)
        self.view.btn_analyze.clicked.connect(self.start_analysis)
        self.view.btn_convert.clicked.connect(self.start_conversion)
        self.view.btn_cancel.clicked.connect(self.cancel_operation)
        self.view.idioma_changed.connect(self.retraduzir_relatorio)

    def _atualizar_card_guia(self) -> None:
        info = getattr(self, "_guide_info", None)
        if not info:
            return
        self.view.card_guide.update_info(
            f"<b>{info['nome']}</b><br>{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_fps').format(v=info['fps'] or 'N/A')}<br>{tr('card_faixa_selecionada').format(v=info['faixa'])}",
            info['nome'],
        )

    def _atualizar_card_dublado(self) -> None:
        info = getattr(self, "_dubbed_info", None)
        if not info:
            return
        self.view.card_dubbed.update_info(
            f"<b>{info['nome']}</b><br>{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_codec').format(v=info['codec'])}<br>{tr('card_canais').format(v=info['canais'])}",
            info['nome'],
        )

    def handle_guide_dropped(self, path: str):
        if not self._validar_arquivo_midia(path, "GUIA"):
            return
        try:
            dur, fps, data, streams = get_file_info(path)
            self._resetar_estado_processo()
            self.analysis_result = {}
            self.guide_path, self.guide_dur, self.guide_fps = path, dur, fps or 0.0
            self._iniciar_grupo_log_atual()
            self.guide_has_video = any(s.get('codec_type') == 'video' for s in data.get('streams', []))
            selected_idx = 0
            if len(streams) > 1:
                dialog = TrackSelectionDialog(streams, self.view)
                if dialog.exec():
                    selected_idx = dialog.get_selected_index()
            self.guide_audio_idx = selected_idx
            self._guide_info = {
                "nome": os.path.basename(path),
                "dur": dur,
                "fps": fps,
                "faixa": selected_idx
            }
            self._atualizar_card_guia()
            self.check_ready()
        except Exception as e:
            if self._is_ffmpeg_missing(e):
                self.open_ffmpeg_installer()
            else:
                self._mostrar_mensagem("erro", tr("popup_erro"), tr("falha_ler_guia").format(erro=e))

    def handle_guide_cleared(self):
        self._cancel_all_workers()
        self._limpar_audio_temp()
        self._limpar_sync_audio_temp()
        self._resetar_estado_processo()
        self.analysis_result = {}
        self._analysis_done = False
        self._console_end()
        self.guide_path = None
        self.guide_dur = 0
        self.guide_fps = 0.0
        self.guide_audio_idx = 0
        self.guide_has_video = False
        self._guide_info = None
        self.view.card_guide.reset()
        self.check_ready()

    def handle_dubbed_dropped(self, path: str):
        if not self._validar_arquivo_midia(path, "DUBLAGEM"):
            return
        try:
            dur, fps, data, streams = get_file_info(path)
            self._resetar_estado_processo()
            self.analysis_result = {}
            self.dubbed_path, self.dubbed_dur = path, dur
            selected_idx = 0
            if len(streams) > 1:
                dialog = TrackSelectionDialog(streams, self.view)
                if dialog.exec():
                    selected_idx = dialog.get_selected_index()
            stream = streams[selected_idx]
            self.audio_info = {
                'index': selected_idx,
                'codec_name': stream.get('codec_name', 'ac3'),
                'sample_rate': stream.get('sample_rate', '48000'),
                'channels': stream.get('channels', 2),
                'bitrate': stream.get('bit_rate') or data.get('format', {}).get('bit_rate')
            }
            self._dubbed_info = {
                "nome": os.path.basename(path),
                "dur": dur,
                "codec": self.audio_info['codec_name'].upper(),
                "canais": self.audio_info['channels']
            }
            self._atualizar_card_dublado()
            self.check_ready()
        except Exception as e:
            if self._is_ffmpeg_missing(e):
                self.open_ffmpeg_installer()
            else:
                self._mostrar_mensagem("erro", tr("popup_erro"), tr("falha_ler_dublado").format(erro=e))

    def handle_dubbed_cleared(self):
        self._cancel_all_workers()
        self._limpar_audio_temp()
        self._limpar_sync_audio_temp()
        self._resetar_estado_processo()
        self.analysis_result = {}
        self._analysis_done = False
        self._console_end()
        self.dubbed_path = None
        self.dubbed_dur = 0
        self.audio_info = {}
        self._dubbed_info = None
        self.view.card_dubbed.reset()
        self.check_ready()

    def check_ready(self):
        self._clear_segmented_state()
        self._cancel_segmented_workers()
        self._analysis_done = False
        self._console_end()
        self.view.btn_analyze.show()
        self.view.btn_convert.hide()
        self.view.btn_cancel.hide()
        if hasattr(self, 'btn_cmd'):
            self.btn_cmd.hide()
        if hasattr(self, 'btn_mux'):
            self.btn_mux.hide()
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.hide()
        if hasattr(self, 'btn_sync_audio'):
            if self.dubbed_path and not self.guide_path:
                self.btn_sync_audio.show()
                self.btn_sync_audio.setEnabled(True)
            else:
                self.btn_sync_audio.hide()
        if self.guide_path and self.dubbed_path:
            self.view.btn_analyze.setEnabled(True)
            self.view.btn_analyze.setStyleSheet(obter_estilo_btn_analyze(self._is_dark(), enabled=True))
            self.view.result_label.setText(tr("status_pronto"))
        else:
            self.view.btn_analyze.setEnabled(False)
            self.view.btn_analyze.setStyleSheet(obter_estilo_btn_analyze(self._is_dark(), enabled=False))
            self.view.result_label.setText(tr("status_aguardando"))
            self.view.progress_bar.setValue(0)
            self.view.progress_lbl.setText(tr("status_operacao"))

    # ====================== ANÁLISE E RELATÓRIO ======================
    def start_analysis(self):
        self._resetar_estado_processo()
        self._iniciar_grupo_log_atual()
        self._clear_segmented_state()
        self._cancel_segmented_workers()
        self._analysis_done = False
        self._pending_analysis_result = None
        self._prescan_finished = False
        self._segmentada_automatica_nesta_analise = self._analise_segmentada_automatica_habilitada()
        self._iniciar_progresso_analise()
        self._process_start_time = time.time()
        self.view.btn_analyze.setEnabled(False)
        if hasattr(self, 'btn_mux'):
            self.btn_mux.hide()
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.hide()
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.hide()
        self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
        self.view.btn_cancel.show()
        self._console_start()
        self._console_line("step", _tr_seg("con_inicio_analise", "Iniciando análise de sincronia..."))
        self.worker = SyncWorker(
            self.guide_path,
            self.dubbed_path,
            self.guide_dur,
            self.dubbed_dur,
            self.guide_audio_idx,
            self.audio_info.get('index', 0)
        )
        self.worker.progress.connect(self._callback_da_operacao_atual(self._atualizar_progresso_sincronia))
        self.worker.finished.connect(self._callback_da_operacao_atual(self.analysis_finished))
        self.worker.error.connect(self._callback_da_operacao_atual(self.operation_error))
        self.worker.start()
        self._start_prescan()

    def _analise_segmentada_automatica_habilitada(self) -> bool:
        getter = getattr(self.view, "analise_segmentada_automatica_habilitada", None)
        return True if getter is None else bool(getter())

    def _usar_analise_segmentada_automatica(self) -> bool:
        return getattr(
            self,
            "_segmentada_automatica_nesta_analise",
            self._analise_segmentada_automatica_habilitada(),
        )

    def _estimar_pontos_analise_completa(self) -> int:
        """Estima os pontos da pré-análise rápida do motor segmentado."""
        anchor_dur = min(self.guide_dur, self.dubbed_dur)
        if anchor_dur < 240.0:
            return 0
        inicio = min(180.0, anchor_dur * 0.10)
        fim = anchor_dur * 0.90
        if fim - inicio < 600.0:
            inicio, fim = 0.0, anchor_dur
        limite = max(inicio + 10.0, min(fim, anchor_dur - 10.0) - SegmentedSyncWorker.REF_LEN)
        janela = max(1.0, limite - inicio)
        passo = max(120.0, janela / 16.0)
        return min(16, max(8, int(janela // passo) + 1))

    def _iniciar_progresso_analise(self) -> None:
        # A análise de sincronia mede nove âncoras. A pré-análise segmentada
        # rápida mede de 8 a 16; os pesos refletem essa grade prevista.
        self._analysis_progress = {
            "sync_percent": 0.0,
            "complete_percent": 0.0,
            "sync_weight": 9.0,
            "complete_weight": (
                float(self._estimar_pontos_analise_completa())
                if self._usar_analise_segmentada_automatica()
                else 0.0
            ),
        }
        self.view.progress_bar.setValue(0)

    def _atualizar_progresso_sincronia(self, text: str, percent: float) -> None:
        self._atualizar_progresso_analise("sync", text, percent)

    def _atualizar_progresso_analise_completa(self, _text: str, percent: float) -> None:
        texto = tr("status_analise_completa")
        self._atualizar_progresso_analise("complete", texto, percent)

    def _atualizar_progresso_analise(self, etapa: str, text: str, percent: float) -> None:
        progresso = self._analysis_progress
        if not progresso:
            self.update_progress(text, percent)
            return

        chave_percentual = f"{etapa}_percent"
        valor = max(0.0, min(100.0, float(percent)))
        progresso[chave_percentual] = max(progresso[chave_percentual], valor)
        peso_total = progresso["sync_weight"] + progresso["complete_weight"]
        if peso_total <= 0:
            percentual_total = valor
        else:
            percentual_total = (
                progresso["sync_percent"] * progresso["sync_weight"]
                + progresso["complete_percent"] * progresso["complete_weight"]
            ) / peso_total

        # O 100% pertence exclusivamente ao encerramento das duas análises.
        self.update_progress(text, min(99.0, percentual_total))

    def update_progress(self, text: str, percent: float):
        self.view.progress_lbl.setText(text)
        self.view.progress_bar.setValue(int(percent))
        self._console_progress(text, percent)

    def _montar_relatorio(self, result: dict, prescan_linha: str = None):
        speed_factor, diff_percent, offset_A = result['speed_factor'], result['diff_percent'], result['offset']
        audio_dur, video_dur, video_fps = self.dubbed_dur, self.guide_dur, self.guide_fps
        slope = 1.0 - speed_factor
        diferenca_bruta = audio_dur - video_dur
        effective_video_dur = video_dur - max(0, offset_A)
        projected_diff_seconds = effective_video_dur * (speed_factor - 1)
        spread = result.get('spread', float('inf'))
        residual_mad = result.get('residual_mad', float('inf'))
        anchor_count = result.get('anchor_count', 0)
        inlier_count = result.get('inlier_count', 0)
        requested_anchor_count = result.get('requested_anchor_count', anchor_count)
        retried_anchor_count = result.get('retried_anchor_count', 0)
        speed_factor_ci_low = result.get('speed_factor_ci_low')
        speed_factor_ci_high = result.get('speed_factor_ci_high')
        lines = [
            tr("rel_dur_audio").format(t=format_time(audio_dur)),
            tr("rel_dur_video").format(t=format_time(video_dur))
        ]
        if video_fps:
            lines.append(tr("rel_fps_real").format(fps=f"{video_fps:.3f}"))
        dur_ratio = None
        if audio_dur and video_dur and video_dur > 0:
            dur_ratio = audio_dur / video_dur
        best_match, best_expr, best_key, min_error, confidence = self._encontrar_fps_match(
            speed_factor,
            spread,
            dur_ratio,
            video_fps,
            anchor_count,
            inlier_count,
            speed_factor_ci_low,
            speed_factor_ci_high,
        )
        calc_factor = speed_factor
        if dur_ratio and abs(dur_ratio - speed_factor) < 0.05:
            calc_factor = dur_ratio
        origem_temporal = self._inferir_fps_origem_dublagem(calc_factor)
        lines.append(tr("rel_dif_total").format(d=f"{diferenca_bruta:.3f}"))
        if offset_A > 0.5:
            lines.append(tr("rel_inicio_video").format(v=f"{offset_A:.2f}"))
        elif offset_A < -0.5:
            lines.append(tr("rel_inicio_audio").format(v=f"{abs(offset_A):.2f}"))
        else:
            lines.append(tr("rel_inicio_ok").format(v=f"{offset_A:.3f}"))
        lines.extend([
            tr("rel_regressao").format(v=f"{slope:.6f}"),
            tr("rel_regressao_nota"),
            "-" * 70,
            tr("rel_dif_pct").format(v=f"{abs(diff_percent):.3f}"),
            tr("rel_fator").format(v=f"{speed_factor:.5f}")
        ])
        if projected_diff_seconds > 0.05:
            lines.append(tr("rel_status_longo"))
        elif projected_diff_seconds < -0.05:
            lines.append(tr("rel_status_curto"))
        if not self._usar_analise_segmentada_automatica():
            lines.extend([
                "",
                _tr_seg(
                    "seg2_prescan_desativada",
                    "Análise segmentada automática desativada."
                ),
            ])
        self.analysis_result['fps_match_key'] = best_key
        self.analysis_result['min_error'] = min_error
        self.analysis_result['confidence'] = confidence
        is_fps_change_needed = True
        if abs(diff_percent) <= 0.05:
            lines.append(tr("rel_diag_ok"))
            lines.extend([
                tr("rel_delay").format(delay=self._formatar_delay_html(offset_A)),
                tr("rel_sem_fps")
            ])
            is_fps_change_needed = False
            self.analysis_result['tempo_str'] = "1.0"
        else:
            if origem_temporal and origem_temporal > 0:
                lines.append(tr("rel_origem_temporal").format(v=f"{origem_temporal:.3f}"))
            if min_error < 0.005:
                lines.append(tr("rel_diag_padrao").format(m=best_match))
                self.analysis_result['tempo_str'] = best_expr
            else:
                lines.extend([
                    tr("rel_diag_atipica1").format(v=f"{abs(diff_percent):.2f}"),
                    tr("rel_diag_atipica2"),
                    tr("rel_forcar").format(v=f"{speed_factor:.6f}")
                ])
                self.analysis_result['tempo_str'] = f"{speed_factor:.6f}"
            lines.append(tr("rel_confianca").format(v=confidence))
        texto_final = "\n".join(lines)
        nome_arquivo = os.path.basename(self.guide_path) if self.guide_path else "Desconhecido"
        log_linhas = [tr("rel_log_analisado").format(f=nome_arquivo)]
        if not is_fps_change_needed:
            log_linhas.extend([
                tr("rel_log_diag_ok"),
                tr("rel_log_delay").format(v=f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}")
            ])
        else:
            if origem_temporal and origem_temporal > 0:
                log_linhas.append(tr("rel_origem_temporal").format(v=f"{origem_temporal:.3f}"))
            if min_error < 0.005:
                log_linhas.append(tr("rel_log_diag_padrao").format(m=best_match))
            else:
                log_linhas.extend([
                    tr("rel_log_diag_atipica").format(v=f"{abs(diff_percent):.2f}"),
                    tr("rel_log_fator").format(v=f"{speed_factor:.6f}")
                ])
            log_linhas.append(tr("rel_confianca").format(v=confidence))
        factor_ci_text = "indisponível"
        if speed_factor_ci_low is not None and speed_factor_ci_high is not None:
            factor_ci_text = f"[{speed_factor_ci_low:.6f}, {speed_factor_ci_high:.6f}]"
        log_linhas.append(
            f"[DEBUG] speed_factor={speed_factor:.6f} | min_error={min_error:.6f} | "
            f"spread={spread:.3f}s | mad={residual_mad:.3f}s | "
            f"âncoras={inlier_count}/{anchor_count} boas (de {requested_anchor_count}; "
            f"refeitas={retried_anchor_count}) | "
            f"faixa_fator={factor_ci_text} | "
            f"confiança={confidence}"
        )
        if prescan_linha:
            log_linhas.append(prescan_linha)
        elif not self._usar_analise_segmentada_automatica():
            log_linhas.append(_tr_seg(
                "seg2_prescan_desativada",
                "Análise segmentada automática desativada."
            ))
        return texto_final, "\n".join(log_linhas), is_fps_change_needed

    def _obter_texto_prescan(self, result: dict) -> str:
        if not result or not result.get('ok', False):
            return None
        if result.get('found', False):
            return _tr_seg(
                "seg2_prescan_sim",
                "Análise completa: {n} ponto(s) de assincronia no meio do vídeo."
            ).format(n=len(result.get('transitions', [])))
        else:
            return _tr_seg(
                "seg2_prescan_nao",
                "Análise completa: nenhuma assincronia no meio do vídeo."
            )

    def analysis_finished(self, result: dict):
        self.analysis_result = result
        if (
            hasattr(self, 'prescan_worker')
            and self.prescan_worker is not None
            and self.prescan_worker.isRunning()
            and not getattr(self, '_prescan_finished', False)
        ):
            self._pending_analysis_result = result
            self.view.progress_lbl.setText(
                tr("status_finalizando_analise_completa")
            )
            QTimer.singleShot(8000, self._check_timeout_prescan)
            return
        self._finalizar_analise_completa(result)

    def _check_timeout_prescan(self):
        if getattr(self, '_pending_analysis_result', None) is not None:
            worker = getattr(self, 'prescan_worker', None)
            if worker is not None and worker.isRunning():
                QTimer.singleShot(8000, self._check_timeout_prescan)
                return
            res = self._pending_analysis_result
            self._pending_analysis_result = None
            self._prescan_finished = True
            self._finalizar_analise_completa(res)

    def _finalizar_analise_completa(self, result: dict):
        elapsed = time.time() - (getattr(self, '_process_start_time', None) or time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "analysis"
        self._analysis_done = True
        status_msg = tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        self.analysis_result = result

        prescan = getattr(self, '_segmented_prescan', None)
        prescan_linha = self._obter_texto_prescan(prescan) if prescan else None

        texto_final, bloco_log, is_fps_change_needed = self._montar_relatorio(result, prescan_linha=prescan_linha)
        self.save_analysis_log(bloco_log)
        self._console_line("ok", status_msg)
        self._console_end()
        self.view.result_label.setText(
            f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>"
        )
        if hasattr(self.view, 'log_diagnostic_top'):
            self.view.log_diagnostic_top(bloco_log)
        else:
            self.view.log_message(bloco_log)
        self.view.progress_lbl.setText(status_msg)
        self.view.progress_bar.setValue(100)
        self._analysis_progress = None
        self.view.btn_analyze.hide()
        self.view.btn_cancel.hide()
        if is_fps_change_needed:
            self.view.btn_convert.show()
            self.view.btn_convert.setEnabled(True)
            self.view.btn_convert.setStyleSheet(obter_estilo_btn_convert(self._is_dark()))
            if hasattr(self, 'btn_cmd'):
                self.btn_cmd.show()
        else:
            self.view.btn_convert.hide()
            if hasattr(self, 'btn_cmd'):
                self.btn_cmd.hide()
        if hasattr(self, 'btn_mux'):
            if getattr(self, 'guide_has_video', False):
                self.btn_mux.show()
                self.btn_mux.setEnabled(True)
            else:
                self.btn_mux.hide()
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.show()
            self.btn_sync_audio.setEnabled(True)
        self._atualizar_botao_segmentado()

    def _atualizar_bloco_analise_com_prescan(self):
        if not self.analysis_result:
            return
        prescan = getattr(self, '_segmented_prescan', None)
        prescan_linha = self._obter_texto_prescan(prescan) if prescan else None
        if not prescan_linha:
            return
        current_text = self.view.log_text.toPlainText()
        if prescan_linha in current_text:
            return
        _, bloco_log, _ = self._montar_relatorio(self.analysis_result, prescan_linha=prescan_linha)
        _, bloco_sem_prescan, _ = self._montar_relatorio(self.analysis_result, prescan_linha=None)
        if not self.view.replace_active_log_fragment(bloco_sem_prescan, bloco_log):
            self.view.log_diagnostic_top(bloco_log)

    def _salvar_log_prependido(self, bloco_log: str) -> None:
        """Mantém a compatibilidade com os fluxos que já salvavam diagnósticos."""
        self._sincronizar_synclog(self.view.log_text.toPlainText())

    def save_analysis_log(self, bloco_log: str):
        self._salvar_log_prependido(bloco_log)

    def save_sync_audio_log(self, bloco_log: str):
        self._salvar_log_prependido(bloco_log)

    # ====================== CONVERSÃO E MKV ======================
    def _abrir_dialogo_fator(self, selected_filter: str) -> str:
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        dlg = SpeedFactorDialog(
            initial_factor=self.analysis_result.get('speed_factor', 1.0),
            detected_key=self.analysis_result.get('fps_match_key'),
            detected_ok=self.analysis_result.get('min_error', 1.0) < 0.005,
            audio_dur=self.dubbed_dur,
            selected_filter=selected_filter,
            cli_callback=lambda tempo_str: self.get_cli_command(
                filter_type=selected_filter,
                tempo_override=tempo_str
            ),
            is_dark=is_dark,
            parent=self.view
        )
        if not dlg.exec():
            return None
        return dlg.get_factor()

    def start_conversion(self):
        dialog = FilterSelectionDialog(self.view)
        if not dialog.exec():
            return
        selected_filter = dialog.get_selected_filter()
        tempo_str = self._abrir_dialogo_fator(selected_filter)
        if tempo_str is None:
            return
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        eff_dur = self.analysis_result.get(
            'effective_video_dur',
            self.guide_dur - max(0, self.analysis_result['offset'])
        )
        dir_name = os.path.dirname(self.dubbed_path)
        base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))
        enc, ext, sup_bit = self._get_audio_encoding_info()
        out_name = self._check_and_get_output_path(
            os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}")
        )
        if not out_name:
            return
        try:
            filter_str = self._build_speed_filter(selected_filter, tempo_str)
            cmd = self._build_audio_ffmpeg_cmd(self.dubbed_path, out_name, filter_str)
            self.view.btn_convert.setEnabled(False)
            self.view.btn_analyze.hide()
            if hasattr(self, 'btn_mux'):
                self.btn_mux.hide()
            if hasattr(self, 'btn_sync_audio'):
                self.btn_sync_audio.hide()
            self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
            self.view.btn_cancel.show()
            self._registrar_conversao_audio("correção manual de velocidade")
            self.view.log_message(tr("log_exportacao").format(arquivo=os.path.basename(out_name)))
            self._console_start()
            self._console_line(
                "step",
                _tr_seg(
                    "con_inicio_conversao",
                    "Iniciando correção de velocidade ({filtro})..."
                ).format(filtro=selected_filter)
            )
            self._console_line("tech", " ".join(cmd))
            self.ff_worker = FFmpegWorker(cmd, eff_dur, out_name, tr("mkv_corrigindo"))
            self.ff_worker.progress.connect(self._callback_da_operacao_atual(self.update_progress))
            self.ff_worker.finished.connect(self._callback_da_operacao_atual(self.conversion_finished))
            self.ff_worker.error.connect(self._callback_da_operacao_atual(self.operation_error))
            self.ff_worker.start()
        except Exception as e:
            self.operation_error(str(e))

    def conversion_finished(self, output_path: str):
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "conversion"
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))
        self._console_line(
            "ok",
            _tr_seg(
                "con_conversao_concluida",
                "Correção concluída — arquivo salvo: {arquivo}"
            ).format(arquivo=os.path.basename(output_path))
        )
        self._console_end()
        self.reset_ui(
            status_text=tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        )
        self.view.progress_bar.setValue(100)
        self._mostrar_sucesso_com_pasta(
            tr("popup_sucesso"),
            tr("proc_concluido_salvo").format(caminho=output_path),
            output_path
        )

    def _precisa_correcao_velocidade(self) -> bool:
        return abs(self.analysis_result.get('diff_percent', 0.0)) > 0.05

    def start_mux(self):
        self._start_sync_process(action="mux")

    # ====================== SINCRONIZAR ÁUDIO (NOVO FLUXO COMPLETO) ======================
    def start_sync_audio(self):
        if not self.dubbed_path:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_aviso"),
                _as_tr("sem_arquivo", "Nenhum arquivo dublado carregado.")
            )
            return

        if self.analysis_result and self._precisa_correcao_velocidade():
            self._start_sync_audio_with_speed_correction()
        else:
            has_analysis = bool(self.analysis_result)
            segmented = getattr(self, '_segmented_audio', None)
            if has_analysis and segmented and os.path.exists(segmented):
                audio_source = segmented
                audio_index = 0
                offset_default_ms = float(getattr(self, '_segmented_global_offset', 0.0)) * 1000.0
            elif has_analysis:
                audio_source = self.dubbed_path
                audio_index = self.audio_info.get("index", 0)
                offset_default_ms = float(self.analysis_result.get("offset", 0.0)) * 1000.0
            else:
                audio_source = self.dubbed_path
                audio_index = self.audio_info.get("index", 0)
                offset_default_ms = 0.0

            dlg = AudioSyncOffsetDialog(offset_default_ms, self.dubbed_path, self.view, has_analysis=has_analysis)
            if not dlg.exec():
                return
            offset_ms = dlg.get_offset_ms()

            self.view.progress_lbl.setText(_as_tr("obtendo_ext", "Determinando extensão de saída..."))
            try:
                ext = AudioSyncWorker.get_output_extension(audio_source, audio_index)
            except Exception as e:
                self.operation_error(str(e))
                return

            base, _ = os.path.splitext(self.dubbed_path)
            sufixo = "_extract" if abs(offset_ms) < 1e-6 else "_sync"
            desired = f"{base}{sufixo}{ext}"
            out_name = self._check_and_get_output_path(desired)
            if not out_name:
                return

            self._resetar_estado_processo()
            self._process_start_time = time.time()
            self._preparar_ui_sync_audio()
            self._console_start()
            self._executar_audio_sync_final(audio_source, audio_index, offset_ms, out_name)

    def _preparar_ui_sync_audio(self):
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setEnabled(False)
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.setEnabled(False)
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.hide()
        self.view.btn_convert.hide()
        self.view.btn_analyze.hide()
        self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
        self.view.btn_cancel.show()

    def _start_sync_audio_with_speed_correction(self):
        dialog = FilterSelectionDialog(self.view)
        if not dialog.exec():
            return
        selected_filter = dialog.get_selected_filter()

        tempo_str = self._abrir_dialogo_fator(selected_filter)
        if tempo_str is None:
            return

        self._resetar_estado_processo()
        self._process_start_time = time.time()
        self._preparar_ui_sync_audio()
        self._console_start()

        try:
            self._sync_audio_corrected = self._create_speed_correction_temp_path(
                "DublaSync_sync_corrigido"
            )
            self.view.log_message(tr("mkv_corrigindo"))
            self._console_line("step", tr("mkv_corrigindo"))
            self._start_speed_correction_pipeline(
                selected_filter=selected_filter,
                tempo_str=tempo_str,
                temporary_path=self._sync_audio_corrected,
                on_complete=self._continuar_sync_audio_corrigido,
            )
        except Exception as e:
            self.operation_error(str(e))

    def _continuar_sync_audio_corrigido(self, correction: SpeedCorrectionResult) -> None:
        self._apresentar_segunda_analise_velocidade(correction.analysis)
        offset_default_ms = correction.final_offset * 1000.0
        dlg = AudioSyncOffsetDialog(offset_default_ms, self.dubbed_path, self.view, has_analysis=True)
        if not dlg.exec():
            self._limpar_sync_audio_temp()
            self.reset_ui(status_text=tr("op_cancelada_usuario"))
            return
        offset_ms = dlg.get_offset_ms()

        try:
            ext = AudioSyncWorker.get_output_extension(correction.corrected_audio_path, 0)
        except Exception as e:
            self._limpar_sync_audio_temp()
            self.operation_error(str(e))
            return

        base, _ = os.path.splitext(self.dubbed_path)
        sufixo = "_extract" if abs(offset_ms) < 1e-6 else "_sync"
        desired = f"{base}{sufixo}{ext}"
        out_name = self._check_and_get_output_path(desired)
        if not out_name:
            self._limpar_sync_audio_temp()
            self.reset_ui(status_text=tr("op_cancelada_usuario"))
            return

        self._executar_audio_sync_final(correction.corrected_audio_path, 0, offset_ms, out_name)

    def _executar_audio_sync_final(self, source: str, audio_index: int, offset_ms: float, out_name: str):
        if abs(offset_ms) < 1e-6:
            msg_inicio = "Iniciando extração de áudio..."
        else:
            msg_inicio = _as_tr("inicio", "Iniciando sincronização de áudio com offset {v} ms...").format(
                v=f"{offset_ms:+.3f}"
            )
        self._iniciar_grupo_log_atual()
        self.view.set_active_log_output(out_name)
        self._console_line("step", msg_inicio)
        self._current_audio_sync_lines = [msg_inicio]
        self.view.log_message(msg_inicio)
        self.audio_sync_worker = AudioSyncWorker(
            source,
            audio_index,
            offset_ms,
            out_name
        )
        self.audio_sync_worker.progress.connect(self._callback_da_operacao_atual(self._audio_sync_progress))
        self.audio_sync_worker.log.connect(self._callback_da_operacao_atual(self._audio_sync_log))
        self.audio_sync_worker.finished.connect(self._callback_da_operacao_atual(self._audio_sync_finished))
        self.audio_sync_worker.error.connect(self._callback_da_operacao_atual(self._audio_sync_error))
        self.audio_sync_worker.request_recode.connect(self._callback_da_operacao_atual(self._audio_sync_request_recode))
        self.audio_sync_worker.start()

    def _limpar_sync_audio_temp(self):
        path = getattr(self, '_sync_audio_corrected', None)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        self._sync_audio_corrected = None

    # ====================== SINCRONIZAR ÁUDIO (CALLBACKS) ======================
    def _audio_sync_progress(self, text: str, percent: float):
        self.update_progress(text, percent)

    def _audio_sync_log(self, text: str):
        if hasattr(self, '_current_audio_sync_lines'):
            self._current_audio_sync_lines.append(text)
            self.view.log_message(text)
        self._console_line("progress", text)

    def _audio_sync_request_recode(self, info: object):
        worker = getattr(self, "audio_sync_worker", None)
        if worker is None:
            return
        info = info or {}
        msg = QMessageBox(self.view)
        msg.setWindowTitle(_as_tr("recode_titulo", "Recodificação necessária"))
        msg.setText(
            _as_tr("recode_msg", "").format(
                arquivo=info.get("arquivo", ""),
                entrada=info.get("entrada", ""),
                saida=info.get("saida", ""),
                ext=info.get("ext", "")
            )
        )
        btn_ok = msg.addButton(
            _as_tr("btn_prosseguir", "Prosseguir"),
            QMessageBox.ButtonRole.AcceptRole
        )
        msg.addButton(tr("btn_cancelar"), QMessageBox.ButtonRole.RejectRole)
        self._estilizar_msgbox(msg, "aviso")
        msg.exec()
        worker.allow_recode(msg.clickedButton() == btn_ok)

    def _audio_sync_finished(self, output_path: str):
        self.audio_sync_worker = None
        self._limpar_sync_audio_temp()
        elapsed = time.time() - (getattr(self, '_process_start_time', None) or time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "audio_sync"

        msg_sucesso = tr("log_sucesso").format(arquivo=os.path.basename(output_path))
        if not hasattr(self, '_current_audio_sync_lines'):
            self._current_audio_sync_lines = []
        self._current_audio_sync_lines.append(msg_sucesso)
        self.view.log_message(msg_sucesso)
        self.save_sync_audio_log("\n".join(self._current_audio_sync_lines))

        self._console_line(
            "ok",
            _as_tr(
                "concluido",
                "Áudio sincronizado com sucesso: {arquivo}"
            ).format(arquivo=os.path.basename(output_path))
        )
        self._console_end()
        self.reset_ui(
            status_text=tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        )
        self.view.progress_bar.setValue(100)
        self._mostrar_sucesso_com_pasta(
            tr("popup_sucesso"),
            tr("proc_concluido_salvo").format(caminho=output_path),
            output_path
        )

    def _audio_sync_error(self, err: str):
        self.audio_sync_worker = None
        self._limpar_sync_audio_temp()
        if hasattr(self, '_current_audio_sync_lines') and self._current_audio_sync_lines:
            self._current_audio_sync_lines.append(f"[ERRO] {err}")
            self.view.log_message(f"[ERRO] {err}")
            self.save_sync_audio_log("\n".join(self._current_audio_sync_lines))
        self.operation_error(str(err))

    # ====================== MKV E ÁUDIO SINCRONIZADO (EXISTENTE) ======================
    def _start_sync_process(self, action="mux"):
        if not self.guide_path or not self.dubbed_path or not self.analysis_result:
            return
        self._current_sync_action = action
        if action == "mux" and not getattr(self, 'guide_has_video', False):
            self._mostrar_mensagem("aviso", tr("popup_aviso"), tr("mkv_erro_sem_video"))
            return
        self._resetar_estado_processo()
        self._iniciar_grupo_log_atual()
        dir_name = os.path.dirname(self.guide_path)
        base = os.path.splitext(os.path.basename(self.guide_path))[0]
        if action == "mux":
            out_name = self._check_and_get_output_path(os.path.join(dir_name, f"{base}_sync.mkv"))
            if not out_name:
                return
            self._mkv_out_name = out_name
        else:
            _, ext, _ = self._get_audio_encoding_info()
            out_name = self._check_and_get_output_path(os.path.join(dir_name, f"{base}_sync{ext}"))
            if not out_name:
                return
            self._audio_out_name = out_name
        self.view.set_active_log_output(out_name)
        segmented = getattr(self, '_segmented_audio', None)
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setEnabled(False)
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.setEnabled(False)
        self.view.btn_convert.hide()
        self.view.btn_analyze.hide()
        msg_inicio = (
            _tr_seg("con_inicio_mux", "Iniciando geração do MKV...")
            if action == "mux"
            else "Iniciando geração do áudio sincronizado..."
        )
        if segmented and os.path.exists(segmented) and not self._precisa_correcao_velocidade():
            self._process_start_time = time.time()
            self._console_start()
            self._console_line("step", msg_inicio)
            if action == "mux":
                self._start_mux_with_audio(segmented, getattr(self, '_segmented_global_offset', 0.0))
            else:
                self._start_sync_audio_final(segmented, getattr(self, '_segmented_global_offset', 0.0))
            return
        if not self._precisa_correcao_velocidade():
            self._process_start_time = time.time()
            self._console_start()
            self._console_line("step", msg_inicio)
            if action == "mux":
                self._start_mux_with_audio(self.dubbed_path, self.analysis_result.get('offset', 0.0))
            else:
                self._start_sync_audio_final(self.dubbed_path, self.analysis_result.get('offset', 0.0))
        else:
            self._gerar_audio_corrigido_temp()

    def _gerar_audio_corrigido_temp(self):
        dialog = FilterSelectionDialog(self.view)
        if not dialog.exec():
            self.reset_ui(status_text=tr("op_cancelada_usuario"))
            return
        tempo_str = self._abrir_dialogo_fator(dialog.get_selected_filter())
        if tempo_str is None:
            self.reset_ui(status_text=tr("op_cancelada_usuario"))
            return
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setEnabled(False)
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.setEnabled(False)
        self.view.btn_convert.hide()
        self.view.btn_analyze.hide()
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        selected_filter = dialog.get_selected_filter()
        try:
            self._corrected_audio = self._create_speed_correction_temp_path(
                "DublaSync_mux_corrigido"
            )
            self.view.log_message(tr("mkv_corrigindo"))
            self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
            self.view.btn_cancel.show()
            self._console_start()
            self._console_line("step", _tr_seg("con_inicio_mux", "Iniciando geração do MKV..."))
            self._console_line("step", tr("mkv_corrigindo"))
            self._start_speed_correction_pipeline(
                selected_filter=selected_filter,
                tempo_str=tempo_str,
                temporary_path=self._corrected_audio,
                on_complete=self._continuar_sync_process_corrigido,
            )
        except Exception as e:
            self.operation_error(str(e))

    def _continuar_sync_process_corrigido(self, correction: SpeedCorrectionResult) -> None:
        self._apresentar_segunda_analise_velocidade(correction.analysis)
        if getattr(self, '_current_sync_action', 'mux') == 'audio':
            self._start_sync_audio_final(correction.corrected_audio_path, correction.final_offset)
        else:
            self._start_mux_with_audio(correction.corrected_audio_path, correction.final_offset)

    def _start_mux_with_audio(self, audio_source: str, offset: float):
        delay = round(offset, 3)
        out_name = getattr(self, '_mkv_out_name', None) or os.path.join(
            os.path.dirname(self.guide_path),
            f"{os.path.splitext(os.path.basename(self.guide_path))[0]}_sync.mkv"
        )
        dubbed_audio_index = (
            int(self.audio_info.get('index', 0))
            if os.path.normcase(os.path.abspath(audio_source))
            == os.path.normcase(os.path.abspath(self.dubbed_path))
            else 0
        )
        self._active_mux_request = (audio_source, delay, out_name, dubbed_audio_index)
        self.view.log_message(tr("mkv_muxando"))
        self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
        self.view.btn_cancel.show()
        self._console_line("progress", _tr_seg("con_delay", "Delay aplicado: {v} s").format(v=f"{delay:.3f}"))
        self._console_line("step", tr("mkv_muxando"))
        self._start_container_mux(audio_source, delay, out_name, dubbed_audio_index)

    def _start_container_mux(
        self,
        audio_source: str,
        delay: float,
        out_name: str,
        dubbed_audio_index: int,
    ):
        """Único ponto de decisão entre MKVToolNix e o muxador FFmpeg existente."""
        if Path(out_name).suffix.lower() == ".mkv" and self._usar_mkvtoolnix():
            validation = mth.validar_mkvmerge_configurado()
            if validation.valid:
                self._start_mkvtoolnix_mux(
                    audio_source,
                    delay,
                    out_name,
                    validation,
                    dubbed_audio_index,
                )
                return
            self._log_mkvtoolnix(
                tr("log_mkvtoolnix_indisponivel").format(erro=validation.error)
            )
            if validation.path:
                self._log_mkvtoolnix(f"[MKVTOOLNIX] Caminho configurado: {validation.path}")
        elif Path(out_name).suffix.lower() == ".mkv":
            self._log_mkvtoolnix(tr("log_mkvtoolnix_desativado"))
        self._start_ffmpeg_mux(audio_source, delay, out_name, dubbed_audio_index)

    def _start_mkvtoolnix_mux(
        self,
        audio_source: str,
        delay: float,
        out_name: str,
        validation: mth.MkvmergeValidation,
        dubbed_audio_index: int,
    ) -> None:
        self._active_muxer = "mkvtoolnix"
        self._log_mkvtoolnix(
            tr("log_mkvtoolnix_validado").format(
                path=validation.path,
                version=validation.version or "mkvmerge",
            )
        )
        self._log_mkvtoolnix(tr("log_mkvtoolnix_iniciando"))
        worker = mth.MkvmergeWorker(
            validation.path,
            self.guide_path,
            audio_source,
            out_name,
            delay,
            tr("mkv_muxando"),
            dubbed_audio_index,
        )
        self.mkvmerge_worker = worker
        worker.progress.connect(self._callback_da_operacao_atual(self.update_progress))
        worker.log.connect(self._callback_da_operacao_atual(self._log_mkvtoolnix))
        worker.completed.connect(self._callback_da_operacao_atual(self._on_mkvmerge_completed))
        worker.finished.connect(lambda current_worker=worker: self._finalizar_mkvmerge_worker(current_worker))
        worker.start()

    def _finalizar_mkvmerge_worker(self, worker=None) -> None:
        """Libera a thread somente após a conclusão real de sua execução."""
        worker = worker or getattr(self, "mkvmerge_worker", None)
        if worker is not None and not worker.isRunning() and self.mkvmerge_worker is worker:
            self.mkvmerge_worker = None
            worker.deleteLater()

    def _on_mkvmerge_completed(self, result: mth.MkvmergeResult) -> None:
        if result.cancelled:
            self.operation_error(tr("op_cancelada_usuario"))
            return
        if result.success:
            self._sync_finished(result.output_path)
            return

        if result.message:
            self._log_mkvtoolnix(f"[MKVTOOLNIX] {result.message}")
        self._log_mkvtoolnix(tr("log_mkvtoolnix_fallback"))
        request = getattr(self, "_active_mux_request", None)
        if not request:
            self.operation_error(result.message or tr("falha_operacao"))
            return
        self._start_ffmpeg_mux(*request, fallback=True)

    def _start_ffmpeg_mux(
        self,
        audio_source: str,
        delay: float,
        out_name: str,
        dubbed_audio_index: int,
        fallback: bool = False,
    ) -> None:
        self._active_muxer = "ffmpeg_fallback" if fallback else "ffmpeg"
        cmd = ['ffmpeg', '-y', '-fflags', '+genpts', '-v', 'warning', '-progress', 'pipe:1', '-nostats']
        if delay >= 0:
            cmd += ['-i', self.guide_path, '-itsoffset', f'{delay:.3f}', '-i', audio_source]
        else:
            cmd += ['-itsoffset', f'{abs(delay):.3f}', '-i', self.guide_path, '-i', audio_source]
        cmd += [
            '-map', '0:v:0',
            '-map', f'1:a:{dubbed_audio_index}',
            '-map', '0:a?',
            '-map', '0:s?',
            '-map', '0:t?',
            '-map_chapters', '0',
            '-map_metadata', '0',
            '-c', 'copy',
            '-disposition:a', '0',
            '-metadata:s:a:0', 'title=DublaSync',
            '-metadata:s:a:0', 'language=por',
            '-disposition:a:0', 'default',
            out_name
        ]
        self.view.log_message(tr("log_ffmpeg_mux_iniciando"))
        self._console_line("step", tr("log_ffmpeg_mux_iniciando"))
        self._console_line("tech", " ".join(cmd))
        self.ff_worker = FFmpegWorker(cmd, self.guide_dur, out_name, tr("mkv_muxando"))
        self.ff_worker.progress.connect(self._callback_da_operacao_atual(self.update_progress))
        self.ff_worker.finished.connect(self._callback_da_operacao_atual(self._sync_finished))
        self.ff_worker.error.connect(self._callback_da_operacao_atual(self.operation_error))
        self.ff_worker.start()

    def _start_sync_audio_final(self, audio_source: str, offset: float):
        delay = round(offset, 3)
        out_name = getattr(self, '_audio_out_name', None)
        if not out_name:
            _, ext, _ = self._get_audio_encoding_info()
            dir_name = os.path.dirname(self.guide_path)
            base = os.path.splitext(os.path.basename(self.guide_path))[0]
            out_name = os.path.join(dir_name, f"{base}_sync{ext}")
        audio_idx = self.audio_info.get('index', 0) if audio_source == self.dubbed_path else 0
        enc, _, sup_bit = self._get_audio_encoding_info()
        cmd = ['ffmpeg', '-y', '-v', 'warning', '-progress', 'pipe:1', '-nostats']
        if delay < 0:
            cmd += ['-ss', f'{abs(delay):.3f}']
        cmd += ['-i', audio_source]
        filter_str = None
        if delay > 0:
            filter_str = f"adelay=delays={int(round(delay * 1000))}:all=1"
        cmd += ['-map', f'0:a:{audio_idx}', '-vn', '-sn', '-dn', '-c:a', enc]
        if enc == 'dca':
            cmd.extend(['-strict', '-2'])
        bitrate_val = self.audio_info.get('bitrate')
        if sup_bit and bitrate_val:
            cmd.extend(['-b:a', f"{int(int(bitrate_val)/1000)}k"])
        if self.audio_info.get('sample_rate'):
            cmd.extend(['-ar', str(self.audio_info.get('sample_rate'))])
        if filter_str:
            cmd.extend(['-filter:a', filter_str])
        cmd.append(out_name)
        msg_gerando = "Gerando áudio sincronizado..."
        self._registrar_conversao_audio("aplicação da sincronia no áudio")
        self.view.log_message(msg_gerando)
        self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
        self.view.btn_cancel.show()
        self._console_line("progress", _tr_seg("con_delay", "Delay aplicado: {v} s").format(v=f"{delay:.3f}"))
        self._console_line("step", msg_gerando)
        self._console_line("tech", " ".join(cmd))
        self.ff_worker = FFmpegWorker(cmd, self.guide_dur, out_name, msg_gerando)
        self.ff_worker.progress.connect(self._callback_da_operacao_atual(self.update_progress))
        self.ff_worker.finished.connect(self._callback_da_operacao_atual(self._sync_finished))
        self.ff_worker.error.connect(self._callback_da_operacao_atual(self.operation_error))
        self.ff_worker.start()

    def _sync_finished(self, output_path: str):
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        action = getattr(self, '_current_sync_action', 'mux')
        self._last_completed_type = action
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))
        if action == "mux":
            if getattr(self, "_active_muxer", "ffmpeg") == "mkvtoolnix":
                self._log_mkvtoolnix(tr("log_mkvtoolnix_concluido"))
            else:
                self.view.log_message(tr("log_ffmpeg_mux_concluido"))
        self._limpar_audio_temp()
        if action == "mux":
            msg_ok = _tr_seg(
                "con_mux_concluido",
                "MKV sincronizado com sucesso: {arquivo}"
            ).format(arquivo=os.path.basename(output_path))
        else:
            msg_ok = f"Áudio sincronizado com sucesso: {os.path.basename(output_path)}"
        self._console_line("ok", msg_ok)
        self._console_end()
        self.reset_ui(
            status_text=tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        )
        self.view.progress_bar.setValue(100)
        self._mostrar_sucesso_com_pasta(
            tr("popup_sucesso"),
            tr("mkv_sucesso").format(caminho=output_path),
            output_path
        )

    def _limpar_audio_temp(self) -> None:
        path = getattr(self, '_corrected_audio', None)
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass
        self._corrected_audio = None
    # ====================== ANÁLISE COMPLETA SEGMENTADA (PARALELA) ======================
    def _start_prescan(self):
        self._segmented_prescan = None
        self._prescan_finished = False
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.hide()
        if not self.guide_path or not self.dubbed_path:
            self._prescan_finished = True
            return
        if not self._usar_analise_segmentada_automatica():
            self.prescan_worker = None
            self._prescan_finished = True
            return
        self.prescan_worker = SegmentedSyncWorker(
            self.guide_path,
            self.dubbed_path,
            self.guide_dur,
            self.dubbed_dur,
            self.guide_audio_idx,
            self.audio_info.get('index', 0),
            initial_offset=None,
            quick=True,
        )
        self.prescan_worker.progress.connect(self._callback_da_operacao_atual(self._atualizar_progresso_analise_completa))
        self.prescan_worker.finished.connect(self._callback_da_operacao_atual(self._on_prescan_finished))
        self.prescan_worker.error.connect(self._callback_da_operacao_atual(self._on_prescan_error))
        self.prescan_worker.start()

    def _on_prescan_finished(self, result: dict):
        self._segmented_prescan = result
        self._prescan_finished = True
        self._atualizar_progresso_analise_completa("", 100.0)
        if getattr(self, '_pending_analysis_result', None) is not None:
            res = self._pending_analysis_result
            self._pending_analysis_result = None
            self._finalizar_analise_completa(res)
        elif getattr(self, '_analysis_done', False) and getattr(self, '_last_completed_type', '') == 'analysis':
            self._atualizar_bloco_analise_com_prescan()
            self._atualizar_botao_segmentado()
        else:
            self._atualizar_botao_segmentado()

    def _on_prescan_error(self, err: str):
        self._segmented_prescan = None
        self._prescan_finished = True
        self._atualizar_progresso_analise_completa("", 100.0)
        if getattr(self, '_pending_analysis_result', None) is not None:
            res = self._pending_analysis_result
            self._pending_analysis_result = None
            self._finalizar_analise_completa(res)
        else:
            if not self._eh_cancelamento(err):
                self.view.log_message(tr("log_aviso").format(erro=err))
            self._atualizar_botao_segmentado()

    def _atualizar_botao_segmentado(self):
        if not hasattr(self, 'btn_segmented'):
            return
        prescan = getattr(self, '_segmented_prescan', None)
        analise_manual_disponivel = (
            not self._usar_analise_segmentada_automatica()
            or (
                prescan is not None
                and prescan.get('ok', False)
                and prescan.get('found', False)
            )
        )
        if (
            self._analysis_done
            and analise_manual_disponivel
            and not self._precisa_correcao_velocidade()
        ):
            self.btn_segmented.show()
            self.btn_segmented.setEnabled(True)
        else:
            self.btn_segmented.hide()

    # ====================== ANÁLISE SEGMENTADA (JANELA) ======================
    def open_segmented_analysis(self):
        if not self.guide_path or not self.dubbed_path:
            return
        if self.segmented_worker and self.segmented_worker.isRunning():
            return
        self.segmented_dialog = None
        self.view.btn_analyze.hide()
        self.view.btn_convert.hide()
        if hasattr(self, 'btn_mux'):
            self.btn_mux.hide()
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.hide()
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.hide()
        self.view.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(self._is_dark()))
        self.view.btn_cancel.show()
        self.view.progress_bar.setValue(0)
        self.view.progress_lbl.setText(
            _tr_seg("seg_progress", "Verificando assincronia no meio do vídeo...")
        )
        self._console_start()
        self._console_line("step", _tr_seg("con_inicio_segmentada", "Iniciando análise segmentada..."))
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        self._start_segmented_worker()

    def _start_segmented_worker(self):
        initial_offset = self.analysis_result.get('offset', 0.0) if self.analysis_result else None
        self.segmented_worker = SegmentedSyncWorker(
            self.guide_path,
            self.dubbed_path,
            self.guide_dur,
            self.dubbed_dur,
            self.guide_audio_idx,
            self.audio_info.get('index', 0),
            initial_offset=initial_offset,
            quick=False
        )
        self.segmented_worker.progress.connect(self._callback_da_operacao_atual(self._segmented_progress))
        self.segmented_worker.finished.connect(self._callback_da_operacao_atual(self._segmented_finished))
        self.segmented_worker.error.connect(self._callback_da_operacao_atual(self._segmented_error))
        self.segmented_worker.start()

    def _segmented_progress(self, text: str, percent: float):
        if self.segmented_dialog:
            self.segmented_dialog.set_status(text)
        self.view.progress_lbl.setText(text)
        self.view.progress_bar.setValue(int(percent))
        self._console_progress(text, percent)

    def _segmented_error(self, err: str):
        self.view.btn_cancel.hide()
        self.segmented_dialog = None
        if self._eh_cancelamento(err):
            self._console_line("warn", tr("op_cancelada_usuario"))
            self.reset_ui(status_text=tr("cancelado"))
        else:
            self._console_line("error", str(err))
            self.view.log_message(tr("log_erro").format(erro=err))
            self._mostrar_mensagem("erro", tr("popup_erro"), err)
            self.reset_ui(status_text=tr("falha_operacao"))
        self._console_end()

    def _montar_log_segmentado(self, result: dict) -> str:
        linhas = []
        linhas.append(_tr_seg("seglog_titulo", "ANÁLISE SEGMENTADA (CHECKPOINTS)"))
        linhas.append(_tr_seg("seglog_preparando", "Preparando análise"))
        linhas.append(
            _tr_seg(
                "seglog_comparando",
                "Comparando faixas e medindo pontos (arquivos grandes podem levar alguns minutos)"
            )
        )
        trans = result.get("transitions", [])
        for i, t in enumerate(trans, start=1):
            linhas.append(
                _tr_seg(
                    "seglog_transicao",
                    "Análise completa: transição ~{tempo}, salto {salto} ({n}º ponto de assincronia - quebra de sincronia)"
                ).format(
                    tempo=format_time(t["time"])[:12],
                    salto=f"{t['jump'] * 1000:+.0f}ms",
                    n=i
                )
            )
        if trans:
            linhas.append(
                _tr_seg(
                    "seg3_offset_aplicado",
                    "Offset global do início também aplicado: {v} ms - o áudio entregue já está totalmente sincronizado."
                ).format(v=f"{result.get('first_offset', 0.0) * 1000:+.0f}")
            )
        linhas.append("")
        linhas.append(
            _tr_seg(
                "seglog_ancoras_titulo",
                "Pontos de ancoragem (lista completa - {g}/{t} válidos):"
            ).format(
                g=result.get("good", 0),
                t=result.get("total", 0)
            )
        )
        for idx, a in enumerate(result.get("anchors", []), start=1):
            status = (
                _tr_seg("seg_status_valido", "Válido")
                if a.get("good")
                else _tr_seg("seg_status_anormal", "Anormal")
            ).upper()
            tol = (
                _tr_seg("seglog_consistente", "consistente")
                if a.get("inlier")
                else _tr_seg("seglog_fora_tol", "fora da tolerância")
            )
            linhas.append(
                f"[{idx:02d}] ~{format_time(a['t'])[:12]} | desvio: {a['offset'] * 1000:+.0f}ms | {status} | {tol}"
            )
        linhas.append(
            _tr_seg(
                "seg3_det_linha",
                "score {score} | âncoras {ancoras} | resíduo {residuo} | escala {escala} | confiança {conf}"
            ).format(
                score=f"{result.get('score', 0.0):.1f}",
                ancoras=f"{result.get('good', 0)}/{result.get('total', 0)}",
                residuo=f"{result.get('residual_ms', 0.0):.1f} ms",
                escala=f"{result.get('scale', 1.0):.6f}",
                conf=result.get("confidence", "—")
            )
        )
        return "\n".join(linhas)

    def _segmented_finished(self, result: dict):
        self._segmented_result = result
        self._segmented_prescan = result
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "segmented"
        status_msg = tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        self.view.btn_cancel.hide()
        self.reset_ui(status_text=status_msg)
        self.view.progress_bar.setValue(100)
        self._console_line("ok", status_msg)
        self._console_end()
        self.save_analysis_log(self._montar_log_segmentado(result))
        self.view.log_message(
            _tr_seg(
                "seg2_log_resumo",
                "Análise segmentada: score {score} | confiança {conf} | âncoras {ancoras} | transições {trans}"
            ).format(
                score=f"{result.get('score', 0.0):.1f}",
                conf=result.get("confidence", "—"),
                ancoras=f"{result.get('good', 0)}/{result.get('total', 0)}",
                trans=len(result.get("transitions", []))
            )
        )
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        self.segmented_dialog = SegmentedAnalysisDialog(self.view, is_dark)
        self.segmented_dialog.btn_apply.clicked.connect(self._open_segmented_point_selection)
        self.segmented_dialog.rejected.connect(self._cancel_segmented_jobs)
        self.segmented_dialog.populate(result)
        self.segmented_dialog.set_finished(self._segmented_can_apply(result))
        self.segmented_dialog.exec()

    def _segmented_can_apply(self, result: dict) -> bool:
        if not result or not result.get('ok', False):
            return False
        if result.get('confidence') not in (tr("conf_alta"), tr("conf_media")):
            return False
        if self._precisa_correcao_velocidade():
            return False
        trans = result.get('transitions', [])
        cuts = result.get('cuts', [])
        if not trans or len(trans) != len(cuts):
            return False
        if not result.get('cuts_ok', False):
            return False
        if len(trans) > 8:
            return False
        if any(abs(t['jump']) > 15.0 for t in trans):
            return False
        return True

    # ====================== SELEÇÃO DE PONTOS ======================
    def _open_segmented_point_selection(self):
        r = self._segmented_result
        if not self._segmented_can_apply(r):
            self._mostrar_mensagem(
                "aviso",
                tr("popup_aviso"),
                _tr_seg(
                    "seg2_sem_correcao",
                    "Nenhuma correção automática disponível (confiança/limites)."
                )
            )
            return
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        dlg = SegmentedPointSelectionDialog(
            r.get('transitions', []),
            self.dubbed_dur,
            self.view,
            is_dark
        )
        if not dlg.exec():
            return
        selected = dlg.get_selected()
        if not selected:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_aviso"),
                _tr_seg("seg_sel_nenhum", "Nenhum ponto selecionado.")
            )
            return
        self._apply_segmented_correction(selected)

    # ====================== CORTE SEGURO (CENA/SILÊNCIO) ======================
    def _detect_scene_times(self, start: float, duration: float) -> list:
        if not self.guide_path or not getattr(self, 'guide_has_video', False):
            return []
        start = max(0.0, float(start))
        duration = max(0.1, min(float(duration), max(0.1, self.guide_dur - start)))
        if duration <= 0.1:
            return []
        cmd = [
            'ffmpeg', '-hide_banner', '-nostats', '-v', 'info',
            '-ss', f'{start:.3f}',
            '-t', f'{duration:.3f}',
            '-i', self.guide_path,
            '-vf', f"select='gt(scene,{self.SCENE_THRESHOLD})',showinfo",
            '-an', '-f', 'null', '-'
        ]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=30,
                creationflags=creationflags
            )
        except Exception:
            return []
        times = []
        for line in (result.stderr or '').splitlines():
            m = re.search(r'pts_time:\s*([0-9]+(?:\.[0-9]+)?)', line)
            if m:
                try:
                    t_rel = float(m.group(1))
                    times.append(start + t_rel)
                except Exception:
                    pass
        return sorted(set(round(t, 3) for t in times))

    def _find_nearest_scene_time(self, target_time: float, radius: float = None):
        if radius is None:
            radius = self.SCENE_SEARCH_RADIUS
        times = self._detect_scene_times(target_time - radius, radius * 2.0)
        if not times:
            return None
        return min(times, key=lambda x: abs(x - target_time))

    def _snap_cut_to_silence(self, cut: float, radius: float = 4.0, max_move: float = 4.0) -> float:
        return ajustar_corte_para_silencio(
            self.dubbed_path,
            self.dubbed_dur,
            self.audio_info.get('index', 0),
            cut,
            radius,
            max_move,
        )

    def _compute_selected_segmented_cuts(self, selected_items: list):
        r = self._segmented_result
        if not r:
            return None, None, _tr_seg("seg_sel_erro", "Não foi possível aplicar os pontos selecionados.")
        transitions = r.get('transitions', [])
        first_offset = float(r.get('first_offset', 0.0))
        selected_items = sorted(selected_items, key=lambda x: x.get("index", 0))
        selected_items = [x for x in selected_items if 0 <= x.get("index", -1) < len(transitions)]
        if not selected_items:
            return None, None, _tr_seg("seg_sel_nenhum", "Nenhum ponto selecionado.")
        adjusted_trans = []
        cuts = []
        cum = first_offset
        s_prev = 0.0
        for item in selected_items:
            trn = transitions[item["index"]]
            jump = float(trn.get('jump', 0.0))
            if abs(jump) < 0.01:
                continue
            off_after = cum + jump
            base = cum if jump > 0 else off_after
            original_time = float(trn.get('time', 0.0))
            original_cut = original_time - base
            edited_time = item.get("time")
            if edited_time is not None:
                candidate_cut = float(edited_time) - base
            else:
                scene_time = self._find_nearest_scene_time(original_time, self.SCENE_SEARCH_RADIUS)
                if scene_time is not None:
                    candidate_cut = scene_time - base
                    candidate_cut = self._snap_cut_to_silence(
                        candidate_cut,
                        radius=self.SILENCE_RADIUS_SCENE,
                        max_move=self.SILENCE_MAX_MOVE_SCENE
                    )
                else:
                    candidate_cut = self._snap_cut_to_silence(
                        original_cut,
                        radius=self.SILENCE_RADIUS_NO_SCENE,
                        max_move=self.SILENCE_MAX_MOVE_NO_SCENE
                    )
            # Para adiantar (salto negativo), o trecho entre
            # ``candidate_cut + jump`` e ``candidate_cut`` será removido.
            # Portanto, ambos os lados precisam ficar depois do corte anterior.
            min_cut = s_prev + 0.5 + max(0.0, -jump)
            if candidate_cut <= min_cut or candidate_cut >= self.dubbed_dur - 0.5:
                candidate_cut = original_cut
            if candidate_cut <= min_cut or candidate_cut >= self.dubbed_dur - 0.5:
                return None, None, _tr_seg(
                    "seg_sel_erro_cortes",
                    "Não foi possível gerar cortes válidos para os pontos selecionados."
                )
            adjusted_time = candidate_cut + base
            adjusted_trans.append({
                "time": float(adjusted_time),
                "jump": jump,
                "cut": float(candidate_cut),
                "fill_mode": "loop" if jump > 0 and item.get("loop") else "silence",
                "manual": edited_time is not None,
                "detected": original_time,
            })
            cuts.append(float(candidate_cut))
            cum = off_after
            s_prev = candidate_cut
        if not adjusted_trans:
            return None, None, _tr_seg("seg_sel_nenhum_valido", "Nenhum ponto válido foi selecionado.")
        return adjusted_trans, cuts, None

    # ====================== CORREÇÃO SEGMENTADA ======================
    def _build_segmented_full_reencode(self, audio_idx, first_offset, adjusted_trans,
                                       enc, sup_bit, out_path):
        """Fallback integral, executado somente após confirmação explícita."""
        source_start = abs(first_offset) if first_offset < 0 else 0.0
        if source_start >= self.dubbed_dur - 0.01:
            raise ValueError("Offset inicial maior que a duração disponível do áudio.")

        ranges = []
        cursor = source_start
        pending_fill = (
            {"duration": first_offset, "mode": "silence", "loop_end": None}
            if first_offset > 0 else None
        )
        for transition in adjusted_trans:
            jump = float(transition["jump"])
            cut = float(transition["cut"])
            # Para adiantar o áudio após a quebra, descarta-se o intervalo
            # [cut + jump, cut]. Para atrasar, os dois lados usam o mesmo corte
            # e o delay é inserido no próximo trecho.
            before = cut if jump > 0 else cut + jump
            after = cut
            if before <= cursor + 0.01 or after >= self.dubbed_dur:
                raise ValueError("Não foi possível montar trechos válidos para a correção segmentada.")
            ranges.append({"start": cursor, "end": before, "fill": pending_fill})
            cursor = after
            pending_fill = (
                {
                    "duration": jump,
                    "mode": "loop" if transition.get("fill_mode") == "loop" else "silence",
                    "loop_end": cut,
                }
                if jump > 0 else None
            )
        if cursor >= self.dubbed_dur - 0.01:
            raise ValueError("Não restou áudio após a última quebra de sincronia.")
        ranges.append({"start": cursor, "end": self.dubbed_dur, "fill": pending_fill})

        loop_fills = [item["fill"] for item in ranges if item["fill"] and item["fill"]["mode"] == "loop"]
        stream_labels = [f"[s{i}]" for i in range(len(ranges))]
        stream_labels.extend(f"[l{i}]" for i in range(len(loop_fills)))
        parts = [f"[0:a:{audio_idx}]asplit={len(stream_labels)}" + "".join(stream_labels)]
        concat_labels = []
        loop_index = 0
        try:
            sample_rate = float(self.audio_info.get("sample_rate", 48000))
        except (TypeError, ValueError):
            sample_rate = 48000.0

        for index, item in enumerate(ranges):
            fill = item["fill"]
            segment = (
                f"[s{index}]atrim=start={item['start']:.6f}:end={item['end']:.6f},"
                "asetpts=PTS-STARTPTS"
            )
            if fill and fill["mode"] == "silence":
                segment += f",adelay=delays={int(round(fill['duration'] * 1000))}:all=1"
            parts.append(segment + f"[a{index}]")

            if fill and fill["mode"] == "loop":
                loop_duration = min(
                    float(fill["duration"]),
                    SegmentedHybridCorrectionWorker.LOOP_SOURCE_MAX_DURATION,
                    float(fill["loop_end"]),
                )
                if loop_duration < SegmentedHybridCorrectionWorker.MIN_CHUNK_DURATION:
                    raise ValueError("Não há trecho suficiente antes da quebra para aplicar o loop local.")
                loop_end = float(fill["loop_end"])
                loop_start = loop_end - loop_duration
                loop_samples = max(1, int(round(loop_duration * sample_rate)))
                parts.append(
                    f"[l{loop_index}]atrim=start={loop_start:.6f}:end={loop_end:.6f},"
                    "asetpts=PTS-STARTPTS,"
                    f"aloop=loop=-1:size={loop_samples},atrim=start=0:end={fill['duration']:.6f},"
                    f"asetpts=PTS-STARTPTS[loop{loop_index}]"
                )
                concat_labels.append(f"[loop{loop_index}]")
                loop_index += 1
            concat_labels.append(f"[a{index}]")
        parts.append("".join(concat_labels) + f"concat=n={len(concat_labels)}:v=0:a=1[out]")
        target_dur = max(
            0.1,
            self.dubbed_dur + first_offset + sum(float(item["jump"]) for item in adjusted_trans)
        )
        cmd = [
            "ffmpeg", "-y", "-v", "warning", "-progress", "pipe:1", "-nostats",
            "-i", self.dubbed_path,
            "-filter_complex", ";".join(parts),
            "-map", "[out]", "-vn", "-sn", "-dn", "-c:a", enc
        ]
        if enc == "dca":
            cmd.extend(["-strict", "-2"])
        bitrate_val = self.audio_info.get("bitrate")
        if sup_bit and bitrate_val:
            cmd.extend(["-b:a", f"{int(int(bitrate_val) / 1000)}k"])
        if self.audio_info.get("sample_rate"):
            cmd.extend(["-ar", str(self.audio_info["sample_rate"])])
        cmd.append(out_path)
        return cmd, target_dur

    def _start_segmented_full_reencode(self):
        """Inicia o plano integral já aprovado pela pessoa usuária."""
        fallback = getattr(self, "_segmented_full_reencode", None)
        if not fallback:
            self._segmented_ff_error("Não há fallback disponível para a correção segmentada.")
            return
        cmd, target_dur, out_path = fallback
        self._segmented_hybrid_active = False
        self._registrar_conversao_audio("correção segmentada integral")
        self.segmented_ff_worker = FFmpegWorker(
            cmd,
            target_dur,
            out_path,
            _tr_seg("seg_aplicando", "Aplicando correção segmentada...")
        )
        self.segmented_ff_worker.progress.connect(self._callback_da_operacao_atual(self._segmented_ff_progress))
        self.segmented_ff_worker.finished.connect(self._callback_da_operacao_atual(self._segmented_ff_finished))
        self.segmented_ff_worker.error.connect(self._callback_da_operacao_atual(self._segmented_ff_error))
        self.segmented_ff_worker.start()

    def _confirm_segmented_full_reencode(self, reason: str) -> bool:
        """Pede autorização antes de trocar a correção local pela integral."""
        msg = QMessageBox(self.view)
        msg.setIcon(QMessageBox.Icon.Warning)
        msg.setWindowTitle(_tr_seg("seg_recode_titulo", "Recodificação completa necessária"))
        msg.setText(_tr_seg(
            "seg_recode_texto",
            "Esta correção não pode ser feita somente na janela da quebra."
        ))
        msg.setInformativeText(_tr_seg(
            "seg_recode_info",
            "Para continuar, será necessário recodificar o áudio inteiro. Deseja prosseguir?"
        ))
        if reason:
            msg.setDetailedText(str(reason))
        btn_recode = msg.addButton(
            _tr_seg("seg_recode_continuar", "Recodificar áudio inteiro"),
            QMessageBox.ButtonRole.AcceptRole
        )
        btn_cancel = msg.addButton(tr("btn_cancelar"), QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_cancel)
        msg.exec()
        return msg.clickedButton() is btn_recode

    def _apply_segmented_correction(self, selected_items=None):
        r = self._segmented_result
        if not self._segmented_can_apply(r):
            self._mostrar_mensagem(
                "aviso",
                tr("popup_aviso"),
                _tr_seg(
                    "seg2_sem_correcao",
                    "Nenhuma correção automática disponível (confiança/limites)."
                )
            )
            return
        if selected_items is None:
            self._open_segmented_point_selection()
            return
        adjusted_trans, cuts, erro = self._compute_selected_segmented_cuts(selected_items)
        if not adjusted_trans:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_aviso"),
                erro or _tr_seg("seg_sel_erro", "Não foi possível aplicar os pontos selecionados.")
            )
            return
        first_offset = float(r.get('first_offset', 0.0))
        audio_idx = self.audio_info.get('index', 0)
        enc, ext, sup_bit = self._get_audio_encoding_info()
        dir_name = os.path.dirname(self.dubbed_path)
        base = os.path.splitext(os.path.basename(self.dubbed_path))[0]
        desired = os.path.join(dir_name, f"{base}_sync{ext}")
        out_path = self._check_and_get_output_path(desired)
        if not out_path:
            return
        baked = True
        if first_offset < 0:
            first_transition_start = (
                adjusted_trans[0]['cut'] + min(0.0, adjusted_trans[0]['jump'])
            )
            if abs(first_offset) >= first_transition_start - 1.0:
                baked = False
        effective_first_offset = first_offset if baked else 0.0
        try:
            fallback_cmd, fallback_dur = self._build_segmented_full_reencode(
                audio_idx,
                effective_first_offset,
                adjusted_trans,
                enc,
                sup_bit,
                out_path
            )
        except ValueError as exc:
            self._mostrar_mensagem("aviso", tr("popup_aviso"), str(exc))
            return

        # O modo híbrido preserva os trechos fora das quebras. Se ele não for
        # compatível com a mídia, a recodificação integral só acontece após a
        # confirmação explícita da pessoa usuária.
        props = AudioSyncWorker._probe_props(self.dubbed_path, audio_idx)
        strategy = None
        if props:
            strategy = AudioSyncWorker._estrategia_copy(props, get_ffmpeg_audio_encoders())
        self._segmented_full_reencode = (fallback_cmd, fallback_dur, out_path)
        self._segmented_hybrid_active = False
        self._segmented_baked = baked
        if self.segmented_dialog:
            self.segmented_dialog.set_running(True)
        self.view.log_message(
            _tr_seg(
                "seg2_correcao_multi",
                "Correção segmentada: {n} transição(ões) aplicada(s)."
            ).format(n=len(adjusted_trans))
        )
        for i, trn in enumerate(adjusted_trans, start=1):
            if trn.get("manual"):
                self.view.log_message(
                    _tr_seg(
                        "seg_ponto_manual",
                        "Ponto {n} aplicado MANUALMENTE em ~{tempo} (detectado: ~{det})"
                    ).format(
                        n=i,
                        tempo=format_time(trn["time"])[:12],
                        det=format_time(trn.get("detected", trn["time"]))[:12]
                    )
                )
            else:
                self.view.log_message(
                    _tr_seg(
                        "seg_ponto_aplicado_em",
                        "Ponto {n} aplicado em ~{tempo}"
                    ).format(
                        n=i,
                        tempo=format_time(trn["time"])[:12]
                    )
                )
        if baked:
            self.view.log_message(
                _tr_seg("seg2_offset_global", "Offset global do início embutido: {v} ms.").format(
                    v=int(round(effective_first_offset * 1000))
                )
            )
        self.view.log_message(tr("log_exportacao").format(arquivo=os.path.basename(out_path)))
        if strategy:
            self._segmented_hybrid_active = True
            self._registrar_conversao_audio(
                "correção segmentada parcial nas janelas das quebras"
            )
            self.view.log_message(
                "Correção híbrida: preservando os trechos fora das quebras por cópia direta."
            )
            self.segmented_ff_worker = SegmentedHybridCorrectionWorker(
                self.dubbed_path,
                audio_idx,
                self.dubbed_dur,
                effective_first_offset,
                adjusted_trans,
                out_path,
                props,
                strategy
            )
            self.segmented_ff_worker.progress.connect(self._callback_da_operacao_atual(self._segmented_ff_progress))
            self.segmented_ff_worker.finished.connect(self._callback_da_operacao_atual(self._segmented_ff_finished))
            self.segmented_ff_worker.error.connect(self._callback_da_operacao_atual(self._segmented_ff_error))
            self.segmented_ff_worker.start()
        else:
            reason = _tr_seg(
                "seg_recode_motivo_codec",
                "O formato deste áudio não permite copiar e unir os trechos locais com segurança."
            )
            if self._confirm_segmented_full_reencode(reason):
                self.view.log_message(
                    "Recodificação integral confirmada para concluir a correção segmentada."
                )
                self._start_segmented_full_reencode()
            else:
                self.view.log_message("Correção segmentada cancelada: recodificação integral não autorizada.")
                if self.segmented_dialog:
                    self.segmented_dialog.set_finished(self._segmented_can_apply(self._segmented_result))
                    self.segmented_dialog.set_status("Correção cancelada. O áudio original não foi alterado.")

    def _segmented_ff_progress(self, text: str, percent: float):
        if self.segmented_dialog:
            self.segmented_dialog.set_status(text)
        self.view.progress_lbl.setText(text)
        self.view.progress_bar.setValue(int(percent))

    def _segmented_ff_finished(self, output_path: str):
        self._segmented_hybrid_active = False
        self._segmented_audio = output_path
        self._segmented_global_offset = (
            0.0
            if getattr(self, '_segmented_baked', True)
            else float(self._segmented_result.get('first_offset', 0.0))
            if self._segmented_result
            else 0.0
        )
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))
        self.view.log_message(
            _tr_seg(
                "seg_aplicado_modo",
                "Correção segmentada aplicada. O Gerar MKV usará este áudio com delay 0."
            )
        )
        if self.segmented_dialog:
            self.segmented_dialog.set_finished(False)
            self.segmented_dialog.set_status(
                _tr_seg(
                    "seg_sucesso",
                    "Áudio corrigido salvo em:\n{caminho}"
                ).format(caminho=output_path).split("\n")[0]
            )
        self._mostrar_sucesso_com_pasta(
            tr("popup_sucesso"),
            _tr_seg(
                "seg_sucesso",
                "Áudio corrigido salvo em:\n{caminho}"
            ).format(caminho=output_path),
            output_path
        )

    def _segmented_ff_error(self, err: str):
        if (
            getattr(self, '_segmented_hybrid_active', False)
            and not self._eh_cancelamento(err)
            and getattr(self, '_segmented_full_reencode', None)
        ):
            self._segmented_hybrid_active = False
            if self._confirm_segmented_full_reencode(str(err)):
                self.view.log_message(
                    "Recodificação integral confirmada após a falha da correção local."
                )
                self._start_segmented_full_reencode()
            else:
                self.view.log_message("Correção segmentada cancelada: recodificação integral não autorizada.")
                if self.segmented_dialog:
                    self.segmented_dialog.set_finished(self._segmented_can_apply(self._segmented_result))
                    self.segmented_dialog.set_status("Correção cancelada. O áudio original não foi alterado.")
            return
        if self.segmented_dialog:
            self.segmented_dialog.set_finished(self._segmented_can_apply(self._segmented_result))
            self.segmented_dialog.set_status(str(err))
        self.view.log_message(tr("log_erro").format(erro=err))
        if not self._eh_cancelamento(err):
            self._mostrar_mensagem("erro", tr("popup_erro"), err)

    def _cancel_segmented_jobs(self):
        for attr in ('segmented_worker', 'segmented_ff_worker'):
            w = getattr(self, attr, None)
            if w and w.isRunning():
                w.cancel()

    # ====================== CLI ======================
    def get_mux_command(self, audio_source: str, offset: float) -> str:
        delay = round(offset, 3)
        out_name = os.path.join(
            os.path.dirname(self.guide_path),
            f"{os.path.splitext(os.path.basename(self.guide_path))[0]}_sync.mkv"
        )
        head = (
            f'ffmpeg -y -fflags +genpts -v warning -progress pipe:1 -nostats '
            f'-i "{self.guide_path}" -itsoffset {delay:.3f} -i "{audio_source}"'
            if delay >= 0
            else
            f'ffmpeg -y -fflags +genpts -v warning -progress pipe:1 -nostats '
            f'-itsoffset {abs(delay):.3f} -i "{self.guide_path}" -i "{audio_source}"'
        )
        return (
            f'{head} -map 0:v:0 -map 1:a:0 -map 0:a? -map 0:s? -map 0:t? '
            f'-map_chapters 0 -map_metadata 0 -c copy '
            f'-metadata:s:a:0 title="DublaSync" -metadata:s:a:0 language=por '
            f'-disposition:a:0 default "{out_name}"'
        )

    def get_cli_command(self, filter_type: str = "rubberband", tempo_override: str = None):
        try:
            tempo_str = tempo_override or self.analysis_result.get(
                'tempo_str',
                f"{self.analysis_result.get('speed_factor', 1.0):.6f}"
            )
            dir_name = os.path.dirname(self.dubbed_path)
            base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))
            enc, ext, sup_bit = self._get_audio_encoding_info()
            out_name = os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}")
            cli = f"[1] {tr('cli_correcao_avancada_atempo' if filter_type == 'atempo' else 'cli_correcao_avancada')}\n"
            cli += (
                f'ffmpeg -y -v warning -progress pipe:1 -nostats '
                f'-i "{self.dubbed_path}" '
                f'-map 0:a:{self.audio_info.get("index", 0)} '
                f'-vn -sn -dn -c:a {enc}'
            )
            if enc == 'dca':
                cli += ' -strict -2'
            bitrate_val = self.audio_info.get('bitrate')
            if sup_bit and bitrate_val:
                cli += f' -b:a {f"{int(int(bitrate_val)/1000)}k"}'
            if self.audio_info.get('sample_rate'):
                cli += f' -ar {self.audio_info["sample_rate"]}'
            if filter_type == "rubberband":
                cli += f' -filter:a "rubberband=tempo={tempo_str}:channels=together" "{out_name}"'
            else:
                cli += f' -filter:a "atempo={tempo_str}" "{out_name}"'
            if self.analysis_result and getattr(self, 'guide_has_video', False):
                cli += "\n\n"
                if not self._precisa_correcao_velocidade():
                    cli += f"{tr('cli_mux_mkv')}\n{self.get_mux_command(self.dubbed_path, self.analysis_result.get('offset', 0.0))}"
            return cli
        except Exception as e:
            return tr("cli_erro").format(erro=e)

    def show_cli_window(self):
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        te_bg, te_fg, te_border = (
            ("#1e1e1e", "#e7d9b8", "#665a43")
            if is_dark
            else ("#fffdf8", "#443b32", "#e5d9c7")
        )
        dialog = QDialog(self.view)
        dialog.setWindowTitle(tr("dlg_cli_titulo"))
        dialog.resize(850, 240)
        if not is_dark:
            dialog.setStyleSheet("QDialog { background-color: #f7f3ea; }")
        layout = QVBoxLayout(dialog)
        radio_layout = QHBoxLayout()
        radio_rb = QRadioButton(tr("cli_rb_btn"))
        radio_rb.setChecked(True)
        radio_atempo = QRadioButton(tr("cli_atempo_btn"))
        radio_layout.addWidget(radio_rb)
        radio_layout.addWidget(radio_atempo)
        radio_layout.addStretch()
        layout.addLayout(radio_layout)
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet(
            f"background-color: {te_bg}; color: {te_fg}; "
            f"font-family: Consolas, monospace; font-size: 13px; border: 1px solid {te_border};"
        )

        def update_text():
            text_edit.setPlainText(
                self.get_cli_command("atempo" if radio_atempo.isChecked() else "rubberband")
            )

        radio_rb.toggled.connect(update_text)
        radio_atempo.toggled.connect(update_text)
        update_text()
        layout.addWidget(text_edit)
        btn_layout = QHBoxLayout()
        btn_close = QPushButton(tr("btn_fechar"))
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(dialog.accept)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)
        dialog.exec()

    # ====================== RETRADUÇÃO E RESET ======================
    def retraduzir_relatorio(self, _codigo: str = ""):
        if hasattr(self, 'action_ffmpeg'):
            self.action_ffmpeg.setText(tr("menu_ffmpeg"))
        if hasattr(self, 'action_mkvtoolnix'):
            self.action_mkvtoolnix.setText(tr("menu_mkvtoolnix"))
        if hasattr(self, 'btn_cmd'):
            self.btn_cmd.setText(tr("btn_cmd"))
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setText(tr("btn_gerar_mkv"))
            self.btn_mux.setToolTip(tr("tooltip_sincronizar_mkv"))
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.setText(self._sync_audio_button_text())
            self.btn_sync_audio.setToolTip(tr("tooltip_sincronizar_audio"))
        if hasattr(self, 'btn_segmented'):
            self.btn_segmented.setText(tr("btn_analise_segmentada"))
            self.btn_segmented.setToolTip(tr("tooltip_analise_segmentada"))
        if getattr(self, '_last_completed_type', None) and getattr(self, '_last_completed_time', None):
            self.view.progress_lbl.setText(
                tr("status_concluido_tempo").format(tempo=self._last_completed_time)
            )
        else:
            texto_atual = self.view.progress_lbl.text()
            if texto_atual in ("Análise Concluída!", "Analysis Complete!", "¡Análisis Completado!"):
                self.view.progress_lbl.setText(tr("prog_analise_concluida"))
            elif texto_atual in (
                "Conversão Finalizada com Sucesso!",
                "Conversion Completed Successfully!",
                "¡Conversión Finalizada con Éxito!"
            ):
                self.view.progress_lbl.setText(tr("status_conversao_ok"))
        self._atualizar_card_guia()
        self._atualizar_card_dublado()
        if not self.analysis_result:
            return
        texto_final, _, _ = self._montar_relatorio(self.analysis_result)
        self.view.result_label.setText(
            f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>"
        )

    def _cancel_all_workers(self):
        for attr in [
            'worker',
            '_worker2',
            'ff_worker',
            'mkvmerge_worker',
            'prescan_worker',
            'segmented_worker',
            'segmented_ff_worker',
            'audio_sync_worker'
        ]:
            w = getattr(self, attr, None)
            if w and w.isRunning():
                w.cancel()

    def cancel_operation(self):
        self._resetar_estado_processo()
        self._speed_correction_state = None
        self._cancel_all_workers()
        self._limpar_audio_temp()
        self._limpar_sync_audio_temp()
        self._console_line("warn", tr("op_cancelada_usuario"))
        self._console_end()
        self.reset_ui()

    def operation_error(self, err: str):
        self._resetar_estado_processo()
        self._speed_correction_state = None
        self._limpar_audio_temp()
        self._limpar_sync_audio_temp()
        if self._eh_cancelamento(err):
            self.view.log_message(tr("log_aviso").format(erro=err))
            self._mostrar_mensagem("aviso", tr("popup_aviso"), err)
            self._console_line("warn", tr("op_cancelada_usuario"))
            status = tr("cancelado")
        else:
            self.view.log_message(tr("log_erro").format(erro=err))
            self._console_line("error", str(err))
            if self._is_ffmpeg_missing(err):
                self.open_ffmpeg_installer()
            else:
                self._mostrar_mensagem("erro", tr("popup_erro"), err)
            status = tr("falha_operacao")
        self._console_end()
        self.reset_ui(status_text=status)

    def reset_ui(self, status_text: str = None):
        self.view.btn_cancel.hide()
        self.view.btn_convert.hide()
        if hasattr(self, 'btn_cmd'):
            self.btn_cmd.hide()
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setEnabled(True)
            if self.analysis_result and getattr(self, 'guide_has_video', False):
                self.btn_mux.show()
            else:
                self.btn_mux.hide()
        if hasattr(self, 'btn_sync_audio'):
            self.btn_sync_audio.setEnabled(True)
            if self.analysis_result:
                self.btn_sync_audio.show()
            elif self.dubbed_path and not self.guide_path:
                self.btn_sync_audio.show()
            else:
                self.btn_sync_audio.hide()
        self._atualizar_botao_segmentado()
        self.view.btn_analyze.show()
        self.view.btn_analyze.setEnabled(bool(self.guide_path and self.dubbed_path))
        if status_text:
            self.view.progress_lbl.setText(status_text)
        else:
            self.view.progress_bar.setValue(0)
            self.view.progress_lbl.setText(tr("status_nova_operacao"))

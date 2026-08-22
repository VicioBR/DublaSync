import os
import re
import html
import tempfile
import time
import datetime
import subprocess
import json
from fractions import Fraction
from pathlib import Path
from PySide6.QtCore import QObject, Qt, QTimer, QPoint
from PySide6.QtGui import QAction, QColor, QPen, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import QMessageBox, QPushButton, QHBoxLayout, QDialog, QVBoxLayout, QTextEdit, QRadioButton, QButtonGroup, QLabel
from ui.main_window import MainWindow
from ui.components import TrackSelectionDialog, FFmpegInstallerDialog
from utils.core_logic import get_file_info, format_time, format_elapsed_time, check_dependencies, check_rubberband_available, select_audio_output, get_ffmpeg_audio_encoders
from utils import ffmpeg_tools as ft
from utils.translations import tr
from workers.tasks import SyncWorker, FFmpegWorker
from workers.installer_worker import FFmpegVerifier, FFmpegInstaller

# ====================== HELPERS VISUAIS ======================
def _misturar_cor(cor_hex: str, fundo_hex: str = "#f0f0f0", alpha: float = 0.25) -> str:
    cr, cg, cb = (int(cor_hex[i:i + 2], 16) for i in (1, 3, 5))
    fr, fg, fb = (int(fundo_hex[i:i + 2], 16) for i in (1, 3, 5))
    r = round(cr * alpha + fr * (1 - alpha))
    g = round(cg * alpha + fg * (1 - alpha))
    b = round(cb * alpha + fb * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"

def _criar_icone_mensagem(tipo: str, tamanho: int = 32) -> QPixmap:
    if tipo == "aviso": tamanho = int(tamanho * 0.72)
    cores = {"erro": "#e74c3c", "sucesso": "#2ecc71", "aviso": "#f1c40f", "info": "#007acc"}
    pix = QPixmap(tamanho, tamanho)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    
    if tipo == "aviso":
        amarelo, escuro = QColor(cores["aviso"]), QColor("#2b2b2b")
        margem = int(tamanho * 0.16)
        p.setPen(QPen(amarelo, tamanho * 0.16, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(amarelo)
        p.drawPolygon(QPolygon([QPoint(tamanho // 2, margem), QPoint(tamanho - margem, tamanho - margem), QPoint(margem, tamanho - margem)]))
        p.setPen(QPen(escuro, max(2, tamanho // 9), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(tamanho // 2, int(tamanho * 0.34), tamanho // 2, int(tamanho * 0.62))
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(escuro)
        p.drawEllipse(tamanho // 2 - 2, int(tamanho * 0.68), 4, 4)
    else:
        p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(cores.get(tipo, "#007acc")))
        p.drawEllipse(0, 0, tamanho, tamanho)
        branco, traco = QColor("#ffffff"), QPen(QColor("#ffffff"), max(2, tamanho // 10))
        traco.setCapStyle(Qt.PenCapStyle.RoundCap); p.setPen(traco)
        if tipo == "erro":
            m = int(tamanho * 0.30)
            p.drawLine(m, m, tamanho - m, tamanho - m); p.drawLine(tamanho - m, m, m, tamanho - m)
        elif tipo == "sucesso":
            p.drawLine(int(tamanho * 0.27), int(tamanho * 0.53), int(tamanho * 0.44), int(tamanho * 0.70))
            p.drawLine(int(tamanho * 0.44), int(tamanho * 0.70), int(tamanho * 0.75), int(tamanho * 0.32))
        else:
            p.setPen(Qt.PenStyle.NoPen); p.setBrush(branco)
            p.drawEllipse(tamanho // 2 - 2, int(tamanho * 0.22), 4, 4)
            p.setPen(traco); p.drawLine(tamanho // 2, int(tamanho * 0.42), tamanho // 2, int(tamanho * 0.76))
    p.end()
    return pix

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
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_cancel)
        btn_layout.addWidget(btn_ok)
        layout.addLayout(btn_layout)

    def get_selected_filter(self) -> str:
        return "atempo" if self.radio_atempo.isChecked() else "rubberband"

# ====================== CONTROLLER PRINCIPAL ======================
class MainController(QObject):
    def __init__(self, view: MainWindow):
        super().__init__()
        self.view = view
        self.guide_path = self.dubbed_path = None
        self.guide_dur = self.dubbed_dur = 0
        self.guide_fps = 0.0
        self.guide_audio_idx = 0
        self.guide_has_video = False
        self.audio_info = self.analysis_result = {}
        self._corrected_audio = self._process_start_time = self._last_completed_type = self._last_completed_time = None
        
        ft.refresh_path()
        try:
            check_dependencies()
            if not check_rubberband_available(): self.view.log_message(tr("log_rubberband_nao_encontrado"))
        except Exception:
            self.view.log_message(tr("log_ffmpeg_nao_encontrado"))
            
        self.setup_cli_button()
        self.setup_mux_button()
        self.connect_signals()
        self._setup_ffmpeg_menu_action()

    # ====================== HELPERS DE ESTADO E FFmpeg ======================
    def _resetar_estado_processo(self):
        """Centraliza o reset das variáveis de controle de processo."""
        self._process_start_time = None
        self._last_completed_type = None
        self._last_completed_time = None

    def _get_audio_encoding_info(self):
        """Retorna (encoder, extensao, suporta_bitrate) com base no áudio atual."""
        encoders = get_ffmpeg_audio_encoders()
        bitrate_val = self.audio_info.get('bitrate')
        bitrate = f"{int(int(bitrate_val)/1000)}k" if bitrate_val else None
        return select_audio_output(self.audio_info.get('codec_name', 'ac3'), bitrate, encoders)

    def _build_audio_ffmpeg_cmd(self, input_path: str, output_path: str, filter_str: str) -> list:
        """Monta o comando base do FFmpeg para processamento de áudio (preservando a lógica exata)."""
        enc, ext, sup_bit = self._get_audio_encoding_info()
        audio_idx = self.audio_info.get('index', 0)
        
        cmd = ['ffmpeg', '-y', '-v', 'warning', '-progress', 'pipe:1', '-nostats',
               '-i', input_path, '-map', f'0:a:{audio_idx}', '-vn', '-sn', '-dn', '-c:a', enc]
        
        bitrate_val = self.audio_info.get('bitrate')
        if sup_bit and bitrate_val:
            cmd.extend(['-b:a', f"{int(int(bitrate_val)/1000)}k"])
        if self.audio_info.get('sample_rate'):
            cmd.extend(['-ar', self.audio_info['sample_rate']])
            
        cmd.extend(['-filter:a', filter_str, output_path])
        return cmd

    def _formatar_delay_html(self, offset_A: float) -> str:
        """Gera o HTML colorido do delay com base no tema e na severidade."""
        delay_ms = abs(offset_A) * 1000
        if delay_ms <= 40: cor_delay = "#2ecc71"
        elif delay_ms <= 60: cor_delay = "#f1c40f"
        elif delay_ms <= 100: cor_delay = "#e67e22"
        else: cor_delay = "#e74c3c"
        
        delay_formatado = f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}"
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        
        if is_dark:
            return f"<span style='color: {cor_delay}; font-weight: bold;'>{delay_formatado}</span>"
        else:
            fundo_chip = _misturar_cor(cor_delay)
            return (f"<span style='color: {cor_delay}; font-weight: bold; "
                    f"background-color: {fundo_chip}; border-radius: 4px;'>&nbsp;{delay_formatado}&nbsp;</span>")

    def _encontrar_fps_match(self, speed_factor: float):
        """Encontra a conversão de FPS padrão mais próxima do fator detectado."""
        fps_ratios = {
            "24_25": {"ratio": Fraction(25, 24), "expr": "25/24", "tr_key": "fps_24_25"},
            "ntsc_25": {"ratio": Fraction(25, 1) / Fraction(24000, 1001), "expr": "25/(24000/1001)", "tr_key": "fps_ntsc_25"},
            "ntsc_24": {"ratio": Fraction(24, 1) / Fraction(24000, 1001), "expr": "24/(24000/1001)", "tr_key": "fps_ntsc_24"},
            "25_24": {"ratio": Fraction(24, 25), "expr": "24/25", "tr_key": "fps_25_24"},
            "25_ntsc": {"ratio": Fraction(24000, 1001) / Fraction(25, 1), "expr": "(24000/1001)/25", "tr_key": "fps_25_ntsc"},
            "24_ntsc": {"ratio": Fraction(24000, 1001) / Fraction(24, 1), "expr": "(24000/1001)/24", "tr_key": "fps_24_ntsc"},
        }
        best_match, best_expr, min_error = None, None, float('inf')
        for name, data in fps_ratios.items():
            error = abs(speed_factor - float(data["ratio"]))
            if error < min_error:
                min_error = error
                best_match = tr(data["tr_key"])
                best_expr = data["expr"]
        return best_match, best_expr, min_error

    # ====================== VALIDAÇÃO E UI BÁSICA ======================
    def _setup_ffmpeg_menu_action(self) -> None:
        menu = self.view.btn_config.menu()
        if menu is None: return
        self.action_ffmpeg = QAction(tr("menu_ffmpeg"), self.view)
        self.action_ffmpeg.triggered.connect(self.open_ffmpeg_installer)
        menu.addAction(self.action_ffmpeg)

    def _is_ffmpeg_missing(self, e: object) -> bool:
        msg = str(e).lower()
        return any(p in msg for p in ("winerror 2", "winerror 3", "não pode encontrar o caminho",
                                       "o sistema não pode encontrar o arquivo", "the system cannot find",
                                       "no such file or directory", "não encontrados no sistema"))

    def _eh_cancelamento(self, msg: str) -> bool:
        return any(p in msg.lower() for p in ("cancelad", "canceled", "cancelled"))

    def _estilizar_msgbox(self, msg: QMessageBox, nivel: str) -> None:
        msg.setIconPixmap(_criar_icone_mensagem(nivel))
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        dlg_bg, dlg_fg = ("#2d2d30", "#e0e0e0") if is_dark else ("#f0f0f0", "#333333")
        msg.setStyleSheet(f"QMessageBox {{ background-color: {dlg_bg}; color: {dlg_fg}; }} QMessageBox QLabel {{ color: {dlg_fg}; background: transparent; }}")

    def _mostrar_mensagem(self, nivel: str, titulo: str, texto: str) -> None:
        msg = QMessageBox(self.view)
        msg.setWindowTitle(titulo); msg.setText(texto)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._estilizar_msgbox(msg, nivel); msg.exec()

    def _mostrar_sucesso_com_pasta(self, titulo: str, texto: str, file_path: str) -> None:
        msg = QMessageBox(self.view)
        msg.setWindowTitle(titulo); msg.setText(texto)
        btn_pasta = msg.addButton(tr("btn_abrir_pasta"), QMessageBox.ButtonRole.AcceptRole)
        btn_ok = msg.addButton("OK", QMessageBox.ButtonRole.RejectRole)
        msg.setDefaultButton(btn_ok); self._estilizar_msgbox(msg, "sucesso"); msg.exec()
        if msg.clickedButton() == btn_pasta:
            try: subprocess.Popen(f'explorer /select,"{os.path.normpath(file_path)}"')
            except Exception as e: self.view.log_message(f"[ERRO] Falha ao abrir pasta: {e}")

    def _check_and_get_output_path(self, desired_path: str):
        if not os.path.exists(desired_path): return desired_path
        msg = QMessageBox(self.view)
        msg.setWindowTitle(tr("dlg_arquivo_existe_titulo"))
        msg.setText(tr("dlg_arquivo_existe_msg").format(arquivo=os.path.basename(desired_path)))
        btn_sobrescrever = msg.addButton(tr("btn_sobrescrever"), QMessageBox.ButtonRole.AcceptRole)
        btn_novo = msg.addButton(tr("btn_gerar_novo"), QMessageBox.ButtonRole.AcceptRole)
        btn_cancelar = msg.addButton(tr("btn_cancelar"), QMessageBox.ButtonRole.RejectRole)
        self._estilizar_msgbox(msg, "aviso"); msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == btn_cancelar or clicked is None: return None
        if clicked == btn_sobrescrever: return desired_path
        
        base, ext = os.path.splitext(desired_path)
        contador, novo_path = 1, f"{base} (1){ext}"
        while os.path.exists(novo_path):
            contador += 1; novo_path = f"{base} ({contador}){ext}"
        return novo_path

    def _validar_arquivo_midia(self, filepath: str, tipo_guia: str) -> bool:
        data_hora = datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        if not os.path.exists(filepath):
            self._registrar_rejeicao(filepath, tipo_guia, data_hora, "Arquivo não encontrado.")
            return False

        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        cmd = ['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', filepath]
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=15, creationflags=creationflags)
            if result.returncode != 0:
                erro_tecnico = result.stderr.strip() or "Erro desconhecido do FFprobe."
                self.view.log_message(f"[{data_hora}] [{tipo_guia}] Falha ao analisar arquivo: {filepath} | Erro FFprobe: {erro_tecnico}")
                self._mostrar_erro_arquivo_incompativel()
                return False
                
            data = json.loads(result.stdout)
            codecs_imagem = {'mjpeg', 'png', 'bmp', 'tiff', 'gif', 'webp', 'ppm', 'pgm', 'pbm', 'pam', 'j2k', 'j2kp', 'jpeg2000', 'jpegls'}
            
            tem_audio = any(s.get('codec_type') == 'audio' for s in data.get('streams', []))
            tem_video = any(s.get('codec_type') == 'video' and s.get('codec_name', '').lower() not in codecs_imagem for s in data.get('streams', []))
            
            if not tem_audio and not tem_video:
                self._registrar_rejeicao(filepath, tipo_guia, data_hora, "Nenhum stream de áudio ou vídeo válido encontrado.")
                return False
            return True
        except Exception as e:
            motivo = "Tempo limite excedido." if isinstance(e, subprocess.TimeoutExpired) else f"Erro inesperado: {str(e)}"
            self.view.log_message(f"[{data_hora}] [{tipo_guia}] Falha ao analisar arquivo: {filepath} | Erro FFprobe: {motivo}")
            self._mostrar_erro_arquivo_incompativel()
            return False

    def _registrar_rejeicao(self, filepath, tipo_guia, data_hora, motivo):
        self.view.log_message(f"[{data_hora}] [{tipo_guia}] Arquivo incompatível: {filepath} | Motivo: {motivo}")
        self._mostrar_erro_arquivo_incompativel()

    def _mostrar_erro_arquivo_incompativel(self):
        self._mostrar_mensagem("erro", "Arquivo incompatível", "O arquivo selecionado não é um arquivo de vídeo ou áudio compatível com o programa.\n\nSelecione um arquivo de vídeo ou áudio válido e tente novamente.")

    # ====================== INSTALAÇÃO DO FFmpeg ======================
    def open_ffmpeg_installer(self) -> None:
        self.ffmpeg_dialog = FFmpegInstallerDialog(self.view)
        self.ffmpeg_dialog.btn_cancel.clicked.connect(self._cancel_ffmpeg_job)
        self.ffmpeg_dialog.set_working(); self.ffmpeg_dialog.show()
        self.ffmpeg_dialog.append_log(tr("ffmpeg_verificando"))
        self._start_ffmpeg_verify()

    def _start_ffmpeg_verify(self) -> None:
        self.ffmpeg_verifier = FFmpegVerifier()
        self.ffmpeg_verifier.finished.connect(self._on_ffmpeg_verified)
        self.ffmpeg_verifier.start()

    def _on_ffmpeg_verified(self, info: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg: return
        if info["ok"]:
            dlg.set_progress(tr("ffmpeg_pronto"), 100)
            dlg.append_log(tr("log_caminho").format(path=info['path']))
            dlg.append_log(tr("log_versao").format(version=info['version']))
            dlg.append_log(tr("log_rb_disponivel")); dlg.set_finished()
            self.view.log_message(tr("log_ffmpeg_verificado").format(path=info['path'])); return

        dlg.append_log(tr("log_ffmpeg_encontrado_sem_rb") if info["installed"] else tr("log_ffmpeg_nao_encontrado_x"))
        pergunta = tr("ffmpeg_pergunta_sem_rb") if info["installed"] else tr("ffmpeg_pergunta_nao_encontrado")
        
        msg = QMessageBox(dlg); msg.setWindowTitle(tr("popup_instalar_ffmpeg")); msg.setText(pergunta)
        self._estilizar_msgbox(msg, "info")
        btn_install = msg.addButton(tr("btn_instalar_agora"), QMessageBox.ButtonRole.AcceptRole)
        btn_pick = msg.addButton(tr("btn_escolher_pasta"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(tr("btn_mais_tarde"), QMessageBox.ButtonRole.RejectRole); msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == btn_install:
            dlg.set_working()
            self.ffmpeg_installer = FFmpegInstaller(use_fallback=True)
            self.ffmpeg_installer.progress.connect(dlg.set_progress)
            self.ffmpeg_installer.finished.connect(self._on_ffmpeg_installed)
            self.ffmpeg_installer.error.connect(self._on_ffmpeg_error)
            self.ffmpeg_installer.start()
        elif clicked == btn_pick: self._escolher_pasta_ffmpeg(dlg)
        else:
            dlg.append_log(tr("ffmpeg_instalacao_recusada"))
            dlg.set_progress(tr("verificacao_concluida"), 100); dlg.set_finished()

    def _escolher_pasta_ffmpeg(self, dlg) -> None:
        from PySide6.QtWidgets import QFileDialog
        pasta = QFileDialog.getExistingDirectory(dlg, tr("dlg_selecionar_pasta_ffmpeg"))
        if not pasta: return
        
        base = Path(pasta)
        exe = next((cand for cand in (base / "ffmpeg.exe", base / "bin" / "ffmpeg.exe") if cand.exists()), None)
        if not exe: exe = next(base.rglob("ffmpeg.exe"), None)
        
        if not exe:
            self._mostrar_mensagem("aviso", tr("popup_ffmpeg_nao_encontrado"), tr("ffmpeg_nenhum_exe_pasta").format(pasta=pasta)); return
            
        bin_dir = str(exe.parent)
        ft.salvar_caminho_ffmpeg(bin_dir); ft.add_to_user_path(bin_dir)
        info = ft.verify()
        
        linhas = []
        if info["ok"]:
            linhas.extend([tr("log_caminho").format(path=info['path']), tr("log_versao").format(version=info['version']),
                           tr("log_rb_disponivel"), tr("log_rb_salvo_path")])
            self.view.log_message(tr("log_ffmpeg_configurado").format(path=info['path']))
        else:
            linhas.extend([tr("log_ffmpeg_encontrado_em").format(bin=bin_dir), tr("log_rb_nao_disponivel")])
            
        if dlg:
            for l in linhas: dlg.append_log(l)
            dlg.set_progress(tr("popup_ffmpeg_configurado"), 100); dlg.set_finished()
        else:
            self._mostrar_mensagem("sucesso", tr("popup_ffmpeg_configurado"), "\n".join(linhas))

    def _on_ffmpeg_installed(self, result: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if not dlg: return
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
        if not dlg: return
        dlg.append_log(f"✖ {message}")
        if self._eh_cancelamento(message): dlg.progress_lbl.setText(tr("cancelado"))
        else:
            dlg.set_progress(tr("falha_operacao"), 100)
            self.view.log_message(tr("log_ffmpeg_erro").format(erro=message))
        dlg.set_finished()

    def _cancel_ffmpeg_job(self) -> None:
        installer = getattr(self, "ffmpeg_installer", None)
        if installer and installer.isRunning(): installer.cancel()
        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg:
            dlg.append_log(tr("cancelado_fechar"))
            dlg.progress_lbl.setText(tr("cancelado")); dlg.set_finished()

    # ====================== INJEÇÃO DE BOTÕES NA UI ======================
    def setup_cli_button(self):
        self.btn_cmd = QPushButton(tr("btn_cmd"), self.view)
        self.btn_cmd.setObjectName("btnCmd"); self.btn_cmd.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cmd.hide(); self.btn_cmd.clicked.connect(self.show_cli_window)
        
        if hasattr(self.view, 'progress_lbl'):
            parent = self.view.progress_lbl.parentWidget()
            if parent and parent.layout():
                layout = parent.layout()
                for i in range(layout.count()):
                    item = layout.itemAt(i)
                    if item and item.widget() == self.view.progress_lbl:
                        hbox = QHBoxLayout(); hbox.setContentsMargins(0, 0, 0, 0)
                        layout.takeAt(i); hbox.addWidget(self.view.progress_lbl)
                        hbox.addStretch(); hbox.addWidget(self.btn_cmd)
                        layout.insertLayout(i, hbox); break

    def setup_mux_button(self):
        self.btn_mux = QPushButton(tr("btn_gerar_mkv"), self.view)
        self.btn_mux.setObjectName("btnMux"); self.btn_mux.setMinimumHeight(45)
        self.btn_mux.setStyleSheet("background-color: #6d28d9; color: white; font-weight: bold; font-size: 15px;")
        self.btn_mux.hide(); self.btn_mux.clicked.connect(self.start_mux)
        
        parent = self.view.btn_convert.parentWidget()
        if parent is not None and parent.layout() is not None:
            outer = parent.layout()
            for i in range(outer.count()):
                item = outer.itemAt(i)
                if item is not None and item.layout() is not None and item.layout().indexOf(self.view.btn_convert) >= 0:
                    item.layout().insertWidget(2, self.btn_mux); break
            else: outer.addWidget(self.btn_mux)

    # ====================== FLUXO DE ARQUIVOS (CARDS) ======================
    def connect_signals(self):
        self.view.card_guide.file_dropped.connect(self.handle_guide_dropped)
        self.view.card_dubbed.file_dropped.connect(self.handle_dubbed_dropped)
        self.view.btn_analyze.clicked.connect(self.start_analysis)
        self.view.btn_convert.clicked.connect(self.start_conversion)
        self.view.btn_cancel.clicked.connect(self.cancel_operation)
        self.view.idioma_changed.connect(self.retraduzir_relatorio)

    def _atualizar_card_guia(self) -> None:
        info = getattr(self, "_guide_info", None)
        if not info: return
        self.view.card_guide.update_info(
            f"📁 <b>{info['nome']}</b><br>{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_fps').format(v=info['fps'] or 'N/A')}<br>{tr('card_faixa_selecionada').format(v=info['faixa'])}")

    def _atualizar_card_dublado(self) -> None:
        info = getattr(self, "_dubbed_info", None)
        if not info: return
        self.view.card_dubbed.update_info(
            f"📁 <b>{info['nome']}</b><br>{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_codec').format(v=info['codec'])}<br>{tr('card_canais').format(v=info['canais'])}")

    def handle_guide_dropped(self, path: str):
        if not self._validar_arquivo_midia(path, "GUIA"): return
        try:
            dur, fps, data, streams = get_file_info(path)
            self._resetar_estado_processo()
            self.guide_path, self.guide_dur, self.guide_fps = path, dur, fps or 0.0
            self.guide_has_video = any(s.get('codec_type') == 'video' for s in data.get('streams', []))
            
            selected_idx = 0
            if len(streams) > 1:
                dialog = TrackSelectionDialog(streams, self.view)
                if dialog.exec(): selected_idx = dialog.get_selected_index()
            self.guide_audio_idx = selected_idx
            
            self._guide_info = {"nome": os.path.basename(path), "dur": dur, "fps": fps, "faixa": selected_idx}
            self._atualizar_card_guia(); self.check_ready()
        except Exception as e:
            if self._is_ffmpeg_missing(e): self.open_ffmpeg_installer()
            else: self._mostrar_mensagem("erro", tr("popup_erro"), tr("falha_ler_guia").format(erro=e))

    def handle_dubbed_dropped(self, path: str):
        if not self._validar_arquivo_midia(path, "DUBLAGEM"): return
        try:
            dur, fps, data, streams = get_file_info(path)
            self._resetar_estado_processo()
            self.dubbed_path, self.dubbed_dur = path, dur
            
            selected_idx = 0
            if len(streams) > 1:
                dialog = TrackSelectionDialog(streams, self.view)
                if dialog.exec(): selected_idx = dialog.get_selected_index()
                    
            stream = next((s for s in streams if s.get('index') == selected_idx), streams[0])
            self.audio_info = {'index': selected_idx, 'codec_name': stream.get('codec_name', 'ac3'),
                               'sample_rate': stream.get('sample_rate', '48000'), 'channels': stream.get('channels', 2),
                               'bitrate': stream.get('bit_rate') or data.get('format', {}).get('bit_rate')}
            
            self._dubbed_info = {"nome": os.path.basename(path), "dur": dur,
                                 "codec": self.audio_info['codec_name'].upper(), "canais": self.audio_info['channels']}
            self._atualizar_card_dublado(); self.check_ready()
        except Exception as e:
            if self._is_ffmpeg_missing(e): self.open_ffmpeg_installer()
            else: self._mostrar_mensagem("erro", tr("popup_erro"), tr("falha_ler_dublado").format(erro=e))

    def check_ready(self):
        self.view.btn_analyze.show(); self.view.btn_convert.hide()
        if hasattr(self, 'btn_cmd'): self.btn_cmd.hide()
        if hasattr(self, 'btn_mux'): self.btn_mux.hide()
        
        if self.guide_path and self.dubbed_path:
            self.view.btn_analyze.setEnabled(True)
            self.view.btn_analyze.setStyleSheet("background-color: #007acc; color: white; font-weight: bold; font-size: 15px;")
            self.view.result_label.setText(tr("status_pronto"))

    # ====================== ANÁLISE E RELATÓRIO ======================
    def start_analysis(self):
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        self.view.btn_analyze.setEnabled(False)
        if hasattr(self, 'btn_mux'): self.btn_mux.hide()
        
        self.view.btn_cancel.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;")
        self.view.btn_cancel.show()
        
        self.worker = SyncWorker(self.guide_path, self.dubbed_path, self.guide_dur, self.dubbed_dur)
        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.analysis_finished)
        self.worker.error.connect(self.operation_error)
        self.worker.start()

    def update_progress(self, text: str, percent: float):
        self.view.progress_lbl.setText(text)
        self.view.progress_bar.setValue(int(percent))

    def _montar_relatorio(self, result: dict):
        speed_factor, diff_percent, offset_A = result['speed_factor'], result['diff_percent'], result['offset']
        audio_dur, video_dur, video_fps = self.dubbed_dur, self.guide_dur, self.guide_fps
        slope = 1.0 - speed_factor
        diferenca_bruta = audio_dur - video_dur
        effective_video_dur = video_dur - max(0, offset_A)
        projected_diff_seconds = effective_video_dur * (speed_factor - 1)
        
        lines = [
            tr("rel_dur_audio").format(t=format_time(audio_dur)),
            tr("rel_dur_video").format(t=format_time(video_dur))
        ]
        if video_fps: lines.append(tr("rel_fps_real").format(fps=f"{video_fps:.3f}"))
        lines.append(tr("rel_dif_total").format(d=f"{diferenca_bruta:.3f}"))
        
        if offset_A > 0.5: lines.append(tr("rel_inicio_video").format(v=f"{offset_A:.2f}"))
        elif offset_A < -0.5: lines.append(tr("rel_inicio_audio").format(v=f"{abs(offset_A):.2f}"))
        else: lines.append(tr("rel_inicio_ok").format(v=f"{offset_A:.3f}"))
        
        lines.extend([tr("rel_regressao").format(v=f"{slope:.6f}"), tr("rel_regressao_nota"), "-" * 70,
                      tr("rel_dif_pct").format(v=f"{abs(diff_percent):.3f}"), tr("rel_fator").format(v=f"{speed_factor:.5f}")])
        
        if projected_diff_seconds > 0.05: lines.append(tr("rel_status_longo"))
        elif projected_diff_seconds < -0.05: lines.append(tr("rel_status_curto"))
        
        # Uso do Helper para encontrar o match de FPS
        best_match, best_expr, min_error = self._encontrar_fps_match(speed_factor)
            
        is_fps_change_needed = True
        if abs(diff_percent) <= 0.05:
            lines.append(tr("rel_diag_ok"))
            # Uso do Helper para formatar o delay
            lines.extend([tr("rel_delay").format(delay=self._formatar_delay_html(offset_A)), tr("rel_sem_fps")])
            is_fps_change_needed = False
            self.analysis_result['tempo_str'] = "1.0"
        else:
            if min_error < 0.005:
                lines.append(tr("rel_diag_padrao").format(m=best_match))
                self.analysis_result['tempo_str'] = best_expr
            else:
                lines.extend([tr("rel_diag_atipica1").format(v=f"{abs(diff_percent):.2f}"), tr("rel_diag_atipica2"),
                              tr("rel_forcar").format(v=f"{speed_factor:.6f}")])
                self.analysis_result['tempo_str'] = f"{speed_factor:.6f}"
                
        texto_final = "\n".join(lines)
        nome_arquivo = os.path.basename(self.guide_path) if self.guide_path else "Desconhecido"
        log_linhas = [tr("rel_log_analisado").format(f=nome_arquivo)]
        
        if not is_fps_change_needed:
            log_linhas.extend([tr("rel_log_diag_ok"), tr("rel_log_delay").format(v=f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}")])
        else:
            if min_error < 0.005: log_linhas.append(tr("rel_log_diag_padrao").format(m=best_match))
            else: log_linhas.extend([tr("rel_log_diag_atipica").format(v=f"{abs(diff_percent):.2f}"),
                                     tr("rel_log_fator").format(v=f"{speed_factor:.6f}")])
                
        log_linhas.append("-" * 50)
        return texto_final, "\n".join(log_linhas), is_fps_change_needed

    def analysis_finished(self, result: dict):
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "analysis"
        status_msg = tr("status_concluido_tempo").format(tempo=self._last_completed_time)
        self.analysis_result = result
        
        texto_final, bloco_log, is_fps_change_needed = self._montar_relatorio(result)
        self.save_analysis_log(texto_final)
        self.view.result_label.setText(f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>")
        
        if hasattr(self.view, 'log_diagnostic_top'): self.view.log_diagnostic_top(bloco_log)
        else: self.view.log_message(bloco_log)
            
        self.view.progress_lbl.setText(status_msg); self.view.progress_bar.setValue(100)
        self.view.btn_analyze.hide(); self.view.btn_cancel.hide()
        
        if is_fps_change_needed:
            self.view.btn_convert.show(); self.view.btn_convert.setEnabled(True)
            self.view.btn_convert.setStyleSheet("background-color: #2b8a3e; color: white; font-weight: bold; font-size: 15px;")
            if hasattr(self, 'btn_cmd'): self.btn_cmd.show()
        else:
            self.view.btn_convert.hide()
            if hasattr(self, 'btn_cmd'): self.btn_cmd.hide()
                
        if hasattr(self, 'btn_mux'):
            if getattr(self, 'guide_has_video', False): self.btn_mux.show(); self.btn_mux.setEnabled(True)
            else: self.btn_mux.hide()

    def save_analysis_log(self, text: str):
        log_file = "SyncLog.log"
        folder_path = os.path.dirname(self.guide_path) if self.guide_path else "Desconhecido"
        clean_text = html.unescape(re.sub(r'<[^>]+>', '', text.replace("&nbsp;", "").replace("🔍 ", "").replace("✅ ", "").replace("🎯 ", "").replace("⚠️ ", "")))
        new_entry = f"{folder_path}\n{'='*70}\n{clean_text}\n\n"
        
        existing_content = ""
        if os.path.exists(log_file):
            try:
                with open(log_file, 'r', encoding='utf-8') as f: existing_content = f.read()
            except Exception: pass
        try:
            with open(log_file, 'w', encoding='utf-8') as f: f.write(new_entry + existing_content)
        except Exception as e:
            self.view.log_message(tr("log_synclog_erro").format(erro=e))

    # ====================== CONVERSÃO E MKV ======================
    def start_conversion(self):
        dialog = FilterSelectionDialog(self.view)
        if not dialog.exec(): return
        selected_filter = dialog.get_selected_filter()
        
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        tempo_str = self.analysis_result.get('tempo_str', f"{self.analysis_result.get('speed_factor', 1.0):.6f}")
        eff_dur = self.analysis_result.get('effective_video_dur', self.guide_dur - max(0, self.analysis_result['offset']))
        
        dir_name = os.path.dirname(self.dubbed_path)
        base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))
        enc, ext, sup_bit = self._get_audio_encoding_info()
        out_name = self._check_and_get_output_path(os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}"))
        if not out_name: return
        
        try:
            filtro = 'atempo' if selected_filter == "atempo" else 'rubberband'
            filter_str = f'atempo={tempo_str}' if filtro == 'atempo' else f'rubberband=tempo={tempo_str}'
            
            cmd = self._build_audio_ffmpeg_cmd(self.dubbed_path, out_name, filter_str)
            
            self.view.btn_convert.setEnabled(False); self.view.btn_analyze.hide()
            if hasattr(self, 'btn_mux'): self.btn_mux.hide()
            
            self.view.btn_cancel.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;")
            self.view.btn_cancel.show()
            self.view.log_message(tr("log_exportacao").format(arquivo=os.path.basename(out_name)))
            
            self.ff_worker = FFmpegWorker(cmd, eff_dur, out_name, tr("mkv_corrigindo"))
            self.ff_worker.progress.connect(self.update_progress)
            self.ff_worker.finished.connect(self.conversion_finished)
            self.ff_worker.error.connect(self.operation_error)
            self.ff_worker.start()
        except Exception as e:
            self.operation_error(str(e))

    def conversion_finished(self, output_path: str):
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "conversion"
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))
        self.reset_ui(status_text=tr("status_concluido_tempo").format(tempo=self._last_completed_time))
        self.view.progress_bar.setValue(100)
        self._mostrar_sucesso_com_pasta(tr("popup_sucesso"), tr("proc_concluido_salvo").format(caminho=output_path), output_path)

    def _precisa_correcao_velocidade(self) -> bool:
        return abs(self.analysis_result.get('diff_percent', 0.0)) > 0.05

    def start_mux(self):
        if not self.guide_path or not self.dubbed_path or not self.analysis_result: return
        if not getattr(self, 'guide_has_video', False):
            self._mostrar_mensagem("aviso", tr("popup_aviso"), tr("mkv_erro_sem_video")); return
            
        self._resetar_estado_processo()
        dir_name = os.path.dirname(self.guide_path)
        base = os.path.splitext(os.path.basename(self.guide_path))[0]
        out_name = self._check_and_get_output_path(os.path.join(dir_name, f"{base}_sync.mkv"))
        if not out_name: return
        
        self._mkv_out_name = out_name
        if not self._precisa_correcao_velocidade():
            self.btn_mux.setEnabled(False); self.view.btn_convert.hide(); self.view.btn_analyze.hide()
            self._process_start_time = time.time()
            self._start_mux_with_audio(self.dubbed_path, self.analysis_result.get('offset', 0.0))
        else:
            self._gerar_audio_corrigido_temp()

    def _gerar_audio_corrigido_temp(self):
        dialog = FilterSelectionDialog(self.view)
        if not dialog.exec():
            self.reset_ui(status_text=tr("op_cancelada_usuario")); return
            
        self.btn_mux.setEnabled(False); self.view.btn_convert.hide(); self.view.btn_analyze.hide()
        self._resetar_estado_processo()
        self._process_start_time = time.time()
        selected_filter = dialog.get_selected_filter()
        
        try:
            tempo_str = self.analysis_result.get('tempo_str', f"{self.analysis_result.get('speed_factor', 1.0):.6f}")
            enc, ext, sup_bit = self._get_audio_encoding_info()
            self._corrected_audio = os.path.join(tempfile.gettempdir(), f"DublaSync_mux_corrigido{ext}")
            
            filtro = 'atempo' if selected_filter == "atempo" else 'rubberband'
            filter_str = f'atempo={tempo_str}' if filtro == 'atempo' else f'rubberband=tempo={tempo_str}'
            
            cmd = self._build_audio_ffmpeg_cmd(self.dubbed_path, self._corrected_audio, filter_str)
            
            self.view.log_message(tr("mkv_corrigindo"))
            self.view.btn_cancel.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;")
            self.view.btn_cancel.show()
            
            self.ff_worker = FFmpegWorker(cmd, self.dubbed_dur, self._corrected_audio, tr("mkv_corrigindo"))
            self.ff_worker.progress.connect(self.update_progress)
            self.ff_worker.finished.connect(self._on_audio_corrigido_pronto)
            self.ff_worker.error.connect(self.operation_error)
            self.ff_worker.start()
        except Exception as e:
            self.operation_error(str(e))

    def _on_audio_corrigido_pronto(self, corrected_path: str):
        self.view.log_message(tr("mkv_analise2"))
        try: dur_corrigido, _, _, _ = get_file_info(corrected_path)
        except Exception as e: self.operation_error(str(e)); return
            
        self._worker2 = SyncWorker(self.guide_path, corrected_path, self.guide_dur, dur_corrigido)
        self._worker2.progress.connect(self.update_progress)
        self._worker2.finished.connect(self._on_segunda_analise)
        self._worker2.error.connect(self.operation_error)
        self._worker2.start()

    def _on_segunda_analise(self, result2: dict):
        texto_final, bloco_log, _ = self._montar_relatorio(result2)
        self.save_analysis_log(texto_final)
        self.view.result_label.setText(f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>")
        if hasattr(self.view, 'log_diagnostic_top'): self.view.log_diagnostic_top(bloco_log)
        else: self.view.log_message(bloco_log)
        self._start_mux_with_audio(self._corrected_audio, result2['offset'])

    def _start_mux_with_audio(self, audio_source: str, offset: float):
        delay = round(offset, 3)
        out_name = getattr(self, '_mkv_out_name', None) or os.path.join(os.path.dirname(self.guide_path), f"{os.path.splitext(os.path.basename(self.guide_path))[0]}_sync.mkv")
        
        cmd = ['ffmpeg', '-y', '-v', 'warning', '-progress', 'pipe:1', '-nostats']
        if delay >= 0: cmd += ['-i', self.guide_path, '-itsoffset', f'{delay:.3f}', '-i', audio_source]
        else: cmd += ['-itsoffset', f'{abs(delay):.3f}', '-i', self.guide_path, '-i', audio_source]
        
        cmd += ['-map', '0:v:0', '-map', '1:a:0', '-map', '0:a?', '-map', '0:s?', '-map', '0:t?',
                '-map_chapters', '0', '-map_metadata', '0', '-c', 'copy',
                '-metadata:s:a:0', 'title=Dublado (DublaSync)', '-metadata:s:a:0', 'language=por',
                '-disposition:a:0', 'default', out_name]
        
        self.view.log_message(tr("mkv_muxando"))
        self.view.btn_cancel.setStyleSheet("background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;")
        self.view.btn_cancel.show()
        
        self.ff_worker = FFmpegWorker(cmd, self.guide_dur, out_name, tr("mkv_muxando"))
        self.ff_worker.progress.connect(self.update_progress)
        self.ff_worker.finished.connect(self.mux_finished)
        self.ff_worker.error.connect(self.operation_error)
        self.ff_worker.start()

    def mux_finished(self, output_path: str):
        elapsed = time.time() - getattr(self, '_process_start_time', time.time())
        self._last_completed_time = format_elapsed_time(elapsed)
        self._last_completed_type = "mux"
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))
        self._limpar_audio_temp()
        self.reset_ui(status_text=tr("status_concluido_tempo").format(tempo=self._last_completed_time))
        self.view.progress_bar.setValue(100)
        self._mostrar_sucesso_com_pasta(tr("popup_sucesso"), tr("mkv_sucesso").format(caminho=output_path), output_path)

    def _limpar_audio_temp(self) -> None:
        path = getattr(self, '_corrected_audio', None)
        if path and os.path.exists(path):
            try: os.remove(path)
            except Exception: pass
        self._corrected_audio = None

    # ====================== CLI (LINHA DE COMANDO) ======================
    def get_mux_command(self, audio_source: str, offset: float) -> str:
        delay = round(offset, 3)
        out_name = os.path.join(os.path.dirname(self.guide_path), f"{os.path.splitext(os.path.basename(self.guide_path))[0]}_sync.mkv")
        head = f'ffmpeg -y -v warning -progress pipe:1 -nostats -i "{self.guide_path}" -itsoffset {delay:.3f} -i "{audio_source}"' if delay >= 0 else f'ffmpeg -y -v warning -progress pipe:1 -nostats -itsoffset {abs(delay):.3f} -i "{self.guide_path}" -i "{audio_source}"'
        return f'{head} -map 0:v:0 -map 1:a:0 -map 0:a? -map 0:s? -map 0:t? -map_chapters 0 -map_metadata 0 -c copy -metadata:s:a:0 title="Dublado (DublaSync)" -metadata:s:a:0 language=por -disposition:a:0 default "{out_name}"'

    def get_cli_command(self, filter_type: str = "rubberband"):
        try:
            tempo_str = self.analysis_result.get('tempo_str', f"{self.analysis_result.get('speed_factor', 1.0):.6f}")
            dir_name = os.path.dirname(self.dubbed_path)
            base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))
            enc, ext, sup_bit = self._get_audio_encoding_info()
            out_name = os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}")
            
            cli = f"[1] {tr('cli_correcao_avancada_atempo' if filter_type == 'atempo' else 'cli_correcao_avancada')}\n"
            cli += f'ffmpeg -y -v warning -progress pipe:1 -nostats -i "{self.dubbed_path}" -map 0:a:{self.audio_info.get("index", 0)} -vn -sn -dn -c:a {enc}'
            
            bitrate_val = self.audio_info.get('bitrate')
            if sup_bit and bitrate_val: cli += f' -b:a {f"{int(int(bitrate_val)/1000)}k"}'
            if self.audio_info.get('sample_rate'): cli += f' -ar {self.audio_info["sample_rate"]}'
            
            cli += f' -filter:a "{filter_type}=tempo={tempo_str}" "{out_name}"' if filter_type == "rubberband" else f' -filter:a "atempo={tempo_str}" "{out_name}"'
            
            if self.analysis_result and getattr(self, 'guide_has_video', False):
                cli += "\n\n" + (tr("mkv_cli_nota") if self._precisa_correcao_velocidade() else f"{tr('cli_mux_mkv')}\n{self.get_mux_command(self.dubbed_path, self.analysis_result.get('offset', 0.0))}")
            return cli
        except Exception as e:
            return tr("cli_erro").format(erro=e)

    def show_cli_window(self):
        is_dark = getattr(self.view.result_label, 'is_dark', True)
        te_bg, te_fg, te_border = ("#1e1e1e", "#d4d4d4", "#333333") if is_dark else ("#ffffff", "#333333", "#cccccc")
        
        dialog = QDialog(self.view); dialog.setWindowTitle(tr("dlg_cli_titulo")); dialog.resize(850, 240)
        layout = QVBoxLayout(dialog)
        
        radio_layout = QHBoxLayout()
        radio_rb = QRadioButton(tr("cli_rb_btn")); radio_rb.setChecked(True)
        radio_atempo = QRadioButton(tr("cli_atempo_btn"))
        radio_layout.addWidget(radio_rb); radio_layout.addWidget(radio_atempo); radio_layout.addStretch()
        layout.addLayout(radio_layout)
        
        text_edit = QTextEdit(); text_edit.setReadOnly(True)
        text_edit.setStyleSheet(f"background-color: {te_bg}; color: {te_fg}; font-family: Consolas, monospace; font-size: 13px; border: 1px solid {te_border};")
        
        def update_text(): text_edit.setPlainText(self.get_cli_command("atempo" if radio_atempo.isChecked() else "rubberband"))
        radio_rb.toggled.connect(update_text); radio_atempo.toggled.connect(update_text); update_text()
        layout.addWidget(text_edit)
        
        btn_layout = QHBoxLayout()
        btn_close = QPushButton(tr("btn_fechar")); btn_close.setFixedWidth(100); btn_close.clicked.connect(dialog.accept)
        btn_layout.addStretch(); btn_layout.addWidget(btn_close); layout.addLayout(btn_layout)
        dialog.exec()

    # ====================== RETRADUÇÃO E RESET ======================
    def retraduzir_relatorio(self, _codigo: str = ""):
        if hasattr(self, 'action_ffmpeg'): self.action_ffmpeg.setText(tr("menu_ffmpeg"))
        if hasattr(self, 'btn_cmd'): self.btn_cmd.setText(tr("btn_cmd"))
        if hasattr(self, 'btn_mux'): self.btn_mux.setText(tr("btn_gerar_mkv"))
        
        if getattr(self, '_last_completed_type', None) and getattr(self, '_last_completed_time', None):
            self.view.progress_lbl.setText(tr("status_concluido_tempo").format(tempo=self._last_completed_time))
        else:
            texto_atual = self.view.progress_lbl.text()
            if texto_atual in ("Análise Concluída!", "Analysis Complete!", "¡Análisis Completado!"): self.view.progress_lbl.setText(tr("prog_analise_concluida"))
            elif texto_atual in ("Conversão Finalizada com Sucesso!", "Conversion Completed Successfully!", "¡Conversión Finalizada con Éxito!"): self.view.progress_lbl.setText(tr("status_conversao_ok"))
                
        self._atualizar_card_guia(); self._atualizar_card_dublado()
        
        if not self.analysis_result: return
        texto_final, _, _ = self._montar_relatorio(self.analysis_result)
        self.view.result_label.setText(f"<html><body style='margin: 0;'>{texto_final.replace(chr(10), '<br>')}</body></html>")

    def cancel_operation(self):
        self._resetar_estado_processo()
        for attr in ['worker', '_worker2', 'ff_worker']:
            w = getattr(self, attr, None)
            if w and w.isRunning(): w.cancel()
        self._limpar_audio_temp(); self.reset_ui()

    def operation_error(self, err: str):
        self._resetar_estado_processo()
        self._limpar_audio_temp()
        
        if self._eh_cancelamento(err):
            self.view.log_message(tr("log_aviso").format(erro=err))
            self._mostrar_mensagem("aviso", tr("popup_aviso"), err)
            status = tr("cancelado")
        else:
            self.view.log_message(tr("log_erro").format(erro=err))
            if self._is_ffmpeg_missing(err): self.open_ffmpeg_installer()
            else: self._mostrar_mensagem("erro", tr("popup_erro"), err)
            status = tr("falha_operacao")
        self.reset_ui(status_text=status)

    def reset_ui(self, status_text: str = None):
        self.view.btn_cancel.hide(); self.view.btn_convert.hide()
        if hasattr(self, 'btn_cmd'): self.btn_cmd.hide()
        if hasattr(self, 'btn_mux'):
            self.btn_mux.setEnabled(True)
            if self.analysis_result and getattr(self, 'guide_has_video', False): self.btn_mux.show()
            else: self.btn_mux.hide()
                
        self.view.btn_analyze.show()
        self.view.btn_analyze.setEnabled(bool(self.guide_path and self.dubbed_path))
        
        if status_text: self.view.progress_lbl.setText(status_text)
        else:
            self.view.progress_bar.setValue(0)
            self.view.progress_lbl.setText(tr("status_nova_operacao"))
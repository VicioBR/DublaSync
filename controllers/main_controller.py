import os
import re
import html
from datetime import datetime
from fractions import Fraction
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QTimer, QPoint
from PySide6.QtGui import QAction, QColor, QPen, QPainter, QPixmap, QPolygon
from PySide6.QtWidgets import (
    QMessageBox,
    QPushButton,
    QHBoxLayout,
    QDialog,
    QVBoxLayout,
    QTextEdit
)

from ui.main_window import MainWindow
from ui.components import TrackSelectionDialog, FFmpegInstallerDialog
from utils.core_logic import (
    get_file_info,
    format_time,
    check_dependencies,
    check_rubberband_available,
    select_audio_output,
    get_ffmpeg_audio_encoders,
    validate_media_file
)
from utils import ffmpeg_tools as ft
from utils.translations import tr
from workers.tasks import SyncWorker, FFmpegWorker
from workers.installer_worker import FFmpegVerifier, FFmpegInstaller


def _misturar_cor(cor_hex: str, fundo_hex: str = "#f0f0f0", alpha: float = 0.25) -> str:
    cr, cg, cb = (int(cor_hex[i:i + 2], 16) for i in (1, 3, 5))
    fr, fg, fb = (int(fundo_hex[i:i + 2], 16) for i in (1, 3, 5))
    r = round(cr * alpha + fr * (1 - alpha))
    g = round(cg * alpha + fg * (1 - alpha))
    b = round(cb * alpha + fb * (1 - alpha))
    return f"#{r:02x}{g:02x}{b:02x}"


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
        amarelo = QColor(cores["aviso"])
        escuro = QColor("#2b2b2b")
        margem = int(tamanho * 0.16)

        p.setPen(QPen(
            amarelo,
            tamanho * 0.16,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin
        ))
        p.setBrush(amarelo)

        pontos = [
            QPoint(tamanho // 2, margem),
            QPoint(tamanho - margem, tamanho - margem),
            QPoint(margem, tamanho - margem)
        ]
        p.drawPolygon(QPolygon(pontos))

        p.setPen(QPen(
            escuro,
            max(2, tamanho // 9),
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap
        ))
        p.drawLine(tamanho // 2, int(tamanho * 0.34), tamanho // 2, int(tamanho * 0.62))

        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(escuro)
        p.drawEllipse(tamanho // 2 - 2, int(tamanho * 0.68), 4, 4)
    else:
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(cores.get(tipo, "#007acc")))
        p.drawEllipse(0, 0, tamanho, tamanho)

        branco = QColor("#ffffff")
        traco = QPen(branco, max(2, tamanho // 10))
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
            p.drawLine(tamanho // 2, int(tamanho * 0.42), tamanho // 2, int(tamanho * 0.76))

    p.end()
    return pix


class MainController(QObject):
    def __init__(self, view: MainWindow):
        super().__init__()
        self.view = view

        self.guide_path = None
        self.dubbed_path = None
        self.guide_dur = 0
        self.dubbed_dur = 0
        self.guide_fps = 0.0
        self.guide_audio_idx = 0
        self.audio_info = {}
        self.analysis_result = {}

        ft.refresh_path()

        try:
            check_dependencies()
            if not check_rubberband_available():
                self.view.log_message(tr("log_rubberband_nao_encontrado"))
        except Exception:
            self.view.log_message(tr("log_ffmpeg_nao_encontrado"))

        self.setup_cli_button()
        self.connect_signals()
        self._setup_ffmpeg_menu_action()

    def _setup_ffmpeg_menu_action(self) -> None:
        menu = self.view.btn_config.menu()
        if menu is None:
            return

        self.action_ffmpeg = QAction(tr("menu_ffmpeg"), self.view)
        self.action_ffmpeg.triggered.connect(self.open_ffmpeg_installer)
        menu.addAction(self.action_ffmpeg)

    def _is_ffmpeg_missing(self, e: object) -> bool:
        msg = str(e).lower()
        padroes = (
            "winerror 2",
            "winerror 3",
            "ffmpeg",
            "ffprobe",
            "não pode encontrar o caminho",
            "o sistema não pode encontrar o arquivo",
            "the system cannot find",
            "no such file or directory",
            "não encontrados no sistema"
        )
        return any(p in msg for p in padroes)

    def _eh_cancelamento(self, msg: str) -> bool:
        """Detecta mensagem de cancelamento em qualquer idioma (pt/en/es)."""
        m = msg.lower()
        return any(p in m for p in ("cancelad", "canceled", "cancelled"))

    def _estilizar_msgbox(self, msg: QMessageBox, nivel: str) -> None:
        msg.setIconPixmap(_criar_icone_mensagem(nivel))

        is_dark = getattr(self.view.result_label, "is_dark", True)
        dlg_bg, dlg_fg = ("#2d2d30", "#e0e0e0") if is_dark else ("#f0f0f0", "#333333")

        msg.setStyleSheet(f"""
            QMessageBox {{ background-color: {dlg_bg}; color: {dlg_fg}; }}
            QMessageBox QLabel {{ color: {dlg_fg}; background: transparent; }}
        """)

    def _mostrar_mensagem(self, nivel: str, titulo: str, texto: str) -> None:
        msg = QMessageBox(self.view)
        msg.setWindowTitle(titulo)
        msg.setText(texto)
        msg.setStandardButtons(QMessageBox.StandardButton.Ok)
        self._estilizar_msgbox(msg, nivel)
        msg.exec()

    def _validar_arquivo_media(self, path: str, tipo_card: str) -> bool:
        """Valida o arquivo e registra no log se for incompatível."""
        try:
            is_valid, motivo, erro_tecnico = validate_media_file(path)
        except FileNotFoundError:
            # Se for erro de FFmpeg/FFprobe ausente, deixa o fluxo normal tratar.
            raise
        except Exception as e:
            # Qualquer outro erro inesperado na validação é tratado como arquivo inválido.
            agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            log_msg = f"[{agora}] [{tipo_card}] Erro inesperado ao validar arquivo: {path} | Erro: {str(e)}"
            self.view.log_message(log_msg)
            self._mostrar_mensagem("aviso", tr("popup_arquivo_incompativel"), tr("txt_arquivo_incompativel"))
            return False

        if not is_valid:
            agora = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

            if erro_tecnico and erro_tecnico not in ("No valid audio/video streams found", "FileNotFoundError"):
                log_msg = f"[{agora}] [{tipo_card}] Falha ao analisar arquivo: {path} | Erro FFprobe: {erro_tecnico}"
            else:
                log_msg = f"[{agora}] [{tipo_card}] Arquivo incompatível: {path} | Motivo: {motivo}"

            self.view.log_message(log_msg)
            self._mostrar_mensagem("aviso", tr("popup_arquivo_incompativel"), tr("txt_arquivo_incompativel"))
            return False

        return True

    def _avisar_ffmpeg_ausente(self) -> None:
        self.view.log_message(tr("log_ffmpeg_nao_encontrado"))

        msg = QMessageBox(self.view)
        msg.setWindowTitle(tr("popup_ffmpeg_nao_encontrado"))
        msg.setText(tr("ffmpeg_txt_nao_encontrado"))
        msg.setInformativeText(tr("ffmpeg_o_que_fazer"))
        self._estilizar_msgbox(msg, "aviso")

        btn_install = msg.addButton(tr("btn_instalar_agora"), QMessageBox.ButtonRole.AcceptRole)
        btn_pick = msg.addButton(tr("btn_escolher_pasta"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(tr("btn_mais_tarde"), QMessageBox.ButtonRole.RejectRole)

        msg.exec()

        clicked = msg.clickedButton()
        if clicked == btn_install:
            QTimer.singleShot(0, self.open_ffmpeg_installer)
        elif clicked == btn_pick:
            QTimer.singleShot(0, lambda: self._escolher_pasta_ffmpeg(None))

    def open_ffmpeg_installer(self) -> None:
        self.ffmpeg_dialog = FFmpegInstallerDialog(self.view)
        self.ffmpeg_dialog.btn_cancel.clicked.connect(self._cancel_ffmpeg_job)
        self.ffmpeg_dialog.set_working()
        self.ffmpeg_dialog.show()
        self.ffmpeg_dialog.append_log(tr("ffmpeg_verificando"))
        self._start_ffmpeg_verify()

    def _start_ffmpeg_verify(self) -> None:
        self.ffmpeg_verifier = FFmpegVerifier()
        self.ffmpeg_verifier.finished.connect(self._on_ffmpeg_verified)
        self.ffmpeg_verifier.start()

    def _on_ffmpeg_verified(self, info: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg is None:
            return

        if info["ok"]:
            dlg.set_progress(tr("ffmpeg_pronto"), 100)
            dlg.append_log(tr("log_caminho").format(path=info["path"]))
            dlg.append_log(tr("log_versao").format(version=info["version"]))
            dlg.append_log(tr("log_rb_disponivel"))
            dlg.set_finished()
            self.view.log_message(tr("log_ffmpeg_verificado").format(path=info["path"]))
            return

        if info["installed"]:
            dlg.append_log(tr("log_ffmpeg_encontrado_sem_rb"))
            pergunta = tr("ffmpeg_pergunta_sem_rb")
        else:
            dlg.append_log(tr("log_ffmpeg_nao_encontrado_x"))
            pergunta = tr("ffmpeg_pergunta_nao_encontrado")

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

        base = Path(pasta)
        exe = None

        for cand in (base / "ffmpeg.exe", base / "bin" / "ffmpeg.exe"):
            if cand.exists():
                exe = cand
                break

        if exe is None:
            for cand in base.rglob("ffmpeg.exe"):
                exe = cand
                break

        if exe is None:
            self._mostrar_mensagem(
                "aviso",
                tr("popup_ffmpeg_nao_encontrado"),
                tr("ffmpeg_nenhum_exe_pasta").format(pasta=pasta)
            )
            return

        bin_dir = str(exe.parent)
        ft.salvar_caminho_ffmpeg(bin_dir)
        ft.add_to_user_path(bin_dir)

        info = ft.verify()
        linhas = []

        if info["ok"]:
            linhas.append(tr("log_caminho").format(path=info["path"]))
            linhas.append(tr("log_versao").format(version=info["version"]))
            linhas.append(tr("log_rb_disponivel"))
            linhas.append(tr("log_rb_salvo_path"))
            self.view.log_message(tr("log_ffmpeg_configurado").format(path=info["path"]))
        else:
            linhas.append(tr("log_ffmpeg_encontrado_em").format(bin=bin_dir))
            linhas.append(tr("log_rb_nao_disponivel"))

        if dlg is not None:
            for l in linhas:
                dlg.append_log(l)
            dlg.set_progress(tr("popup_ffmpeg_configurado"), 100)
            dlg.set_finished()
        else:
            self._mostrar_mensagem("sucesso", tr("popup_ffmpeg_configurado"), "\n".join(linhas))

    def _on_ffmpeg_installed(self, result: dict) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg is None:
            return

        if result.get("ok"):
            dlg.set_progress(tr("ffmpeg_instalado_verificado"), 100)
            dlg.append_log(tr("log_caminho").format(path=result["path"]))
            dlg.append_log(tr("log_versao").format(version=result["version"]))
            dlg.append_log(tr("log_rb_disponivel"))
            self.view.log_message(tr("log_ffmpeg_instalado").format(path=result["path"]))
        else:
            dlg.set_progress(tr("ffmpeg_instalado_pendencias"), 100)
            dlg.append_log(tr("log_rb_nao_disponivel"))
            self.view.log_message(tr("log_ffmpeg_sem_rb"))

        dlg.set_finished()

    def _on_ffmpeg_error(self, message: str) -> None:
        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg is None:
            return

        dlg.append_log(f"✖ {message}")

        if self._eh_cancelamento(message):
            dlg.progress_lbl.setText(tr("cancelado"))
        else:
            dlg.set_progress(tr("falha_operacao"), 100)
            self.view.log_message(tr("log_ffmpeg_erro").format(erro=message))

        dlg.set_finished()

    def _cancel_ffmpeg_job(self) -> None:
        installer = getattr(self, "ffmpeg_installer", None)
        if installer is not None and installer.isRunning():
            installer.cancel()

        dlg = getattr(self, "ffmpeg_dialog", None)
        if dlg is not None:
            dlg.append_log(tr("cancelado_fechar"))
            dlg.progress_lbl.setText(tr("cancelado"))
            dlg.set_finished()

    def setup_cli_button(self):
        self.btn_cmd = QPushButton(tr("btn_cmd"), self.view)
        self.btn_cmd.setObjectName("btnCmd")
        self.btn_cmd.setCursor(Qt.CursorShape.PointingHandCursor)

        is_dark = getattr(self.view.result_label, "is_dark", True)
        cor_hover = "#ffffff" if is_dark else "#007acc"

        self.btn_cmd.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: #aaaaaa; text-decoration: underline; border: none; font-size: 12px; padding: 0; }}
            QPushButton:hover {{ color: {cor_hover}; }}
        """)

        self.btn_cmd.hide()
        self.btn_cmd.clicked.connect(self.show_cli_window)

        if hasattr(self.view, "progress_lbl"):
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

    def get_cli_command(self):
        try:
            if not self.dubbed_path:
                return tr("cli_erro").format(erro="Arquivo dublado não carregado.")

            tempo_str = self.analysis_result.get(
                "tempo_str",
                f"{self.analysis_result.get('speed_factor', 1.0):.6f}"
            )

            dir_name = os.path.dirname(self.dubbed_path)
            base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))

            encoders = get_ffmpeg_audio_encoders()

            bitrate = None
            bitrate_val = self.audio_info.get("bitrate")
            if bitrate_val:
                try:
                    bitrate = f"{int(int(bitrate_val) // 1000)}k"
                except Exception:
                    bitrate = None

            enc, ext, sup_bit = select_audio_output(
                self.audio_info.get("codec_name", "ac3"),
                bitrate,
                encoders
            )

            out_name = os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}")

            # Agora este índice já é o índice relativo da faixa de áudio para o FFmpeg.
            audio_idx = int(self.audio_info.get("index", 0))
            sample_rate = self.audio_info.get("sample_rate", "48000")

            cli = "[1] " + tr("cli_correcao_avancada") + "\n"
            cli += (
                f'ffmpeg -y -v warning -progress pipe:1 -nostats '
                f'-i "{self.dubbed_path}" '
                f'-map 0:a:{audio_idx} -vn -sn -dn -c:a {enc}'
            )

            if sup_bit and bitrate:
                cli += f" -b:a {bitrate}"

            if sample_rate and str(sample_rate) != "0":
                cli += f" -ar {sample_rate}"

            cli += f' -filter:a "rubberband=tempo={tempo_str}" "{out_name}"'

            return cli
        except Exception as e:
            return tr("cli_erro").format(erro=e)

    def show_cli_window(self):
        is_dark = getattr(self.view.result_label, "is_dark", True)
        te_bg, te_fg, te_border = ("#1e1e1e", "#d4d4d4", "#333333") if is_dark else ("#ffffff", "#333333", "#cccccc")

        dialog = QDialog(self.view)
        dialog.setWindowTitle(tr("dlg_cli_titulo"))
        dialog.resize(850, 200)

        layout = QVBoxLayout(dialog)

        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setStyleSheet(
            f"background-color: {te_bg}; color: {te_fg}; "
            f"font-family: Consolas, monospace; font-size: 13px; border: 1px solid {te_border};"
        )
        text_edit.setPlainText(self.get_cli_command())

        layout.addWidget(text_edit)

        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        btn_close = QPushButton(tr("btn_fechar"))
        btn_close.setFixedWidth(100)
        btn_close.clicked.connect(dialog.accept)

        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

        dialog.exec()

    def connect_signals(self):
        self.view.card_guide.file_dropped.connect(self.handle_guide_dropped)
        self.view.card_dubbed.file_dropped.connect(self.handle_dubbed_dropped)
        self.view.btn_analyze.clicked.connect(self.start_analysis)
        self.view.btn_convert.clicked.connect(self.start_conversion)
        self.view.btn_cancel.clicked.connect(self.cancel_operation)
        self.view.idioma_changed.connect(self.retraduzir_relatorio)

    # ---------- Cards traduzidos (e retraduzidos ao trocar idioma) ----------
    def _atualizar_card_guia(self) -> None:
        info = getattr(self, "_guide_info", None)
        if not info:
            return

        info_html = (
            f"📁 <b>{info['nome']}</b><br>"
            f"{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_fps').format(v=info['fps'] or 'N/A')}<br>"
            f"{tr('card_faixa_selecionada').format(v=info['faixa'])}"
        )

        self.view.card_guide.update_info(info_html)

    def _atualizar_card_dublado(self) -> None:
        info = getattr(self, "_dubbed_info", None)
        if not info:
            return

        info_html = (
            f"📁 <b>{info['nome']}</b><br>"
            f"{tr('card_duracao').format(v=format_time(info['dur']))}<br>"
            f"{tr('card_codec').format(v=info['codec'])}<br>"
            f"{tr('card_canais').format(v=info['canais'])}"
        )

        self.view.card_dubbed.update_info(info_html)

    def handle_guide_dropped(self, path: str):
        try:
            if not self._validar_arquivo_media(path, "GUIA"):
                return

            dur, fps, data, streams = get_file_info(path)

            self.guide_path = path
            self.guide_dur = dur
            self.guide_fps = fps or 0.0

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

    def handle_dubbed_dropped(self, path: str):
        try:
            if not self._validar_arquivo_media(path, "DUBLAGEM"):
                return

            dur, fps, data, streams = get_file_info(path)

            self.dubbed_path = path
            self.dubbed_dur = dur

            selected_idx = 0
            if len(streams) > 1:
                dialog = TrackSelectionDialog(streams, self.view)
                if dialog.exec():
                    selected_idx = dialog.get_selected_index()

            stream = next(
                (s for s in streams if s.get("index") == selected_idx),
                streams[0] if streams else {}
            )

            # ------------------------------------------------------------------
            # CORREÇÃO IMPORTANTE:
            #
            # O FFprobe retorna o índice global da stream.
            # Exemplo:
            #   0 = vídeo
            #   1 = primeiro áudio
            #   2 = segundo áudio
            #
            # Mas o FFmpeg, em "-map 0:a:X", espera o índice relativo apenas
            # entre as faixas de áudio:
            #   0 = primeiro áudio
            #   1 = segundo áudio
            #
            # Por isso, agora calculamos a posição da faixa selecionada dentro
            # da lista "streams", que já contém somente faixas de áudio.
            # ------------------------------------------------------------------
            try:
                audio_map_idx = streams.index(stream)
            except ValueError:
                audio_map_idx = 0

            self.audio_info = {
                # Índice correto para usar em -map 0:a:X
                "index": audio_map_idx,

                # Mantém informações originais da faixa selecionada
                "global_index": stream.get("index", selected_idx),
                "codec_name": stream.get("codec_name", "ac3"),
                "sample_rate": stream.get("sample_rate", "48000"),
                "channels": stream.get("channels", 2),
                "bitrate": stream.get("bit_rate") or data.get("format", {}).get("bit_rate")
            }

            self._dubbed_info = {
                "nome": os.path.basename(path),
                "dur": dur,
                "codec": str(self.audio_info.get("codec_name", "???")).upper(),
                "canais": self.audio_info.get("channels", 0)
            }

            self._atualizar_card_dublado()
            self.check_ready()
        except Exception as e:
            if self._is_ffmpeg_missing(e):
                self.open_ffmpeg_installer()
            else:
                self._mostrar_mensagem("erro", tr("popup_erro"), tr("falha_ler_dublado").format(erro=e))

    def check_ready(self):
        self.view.btn_analyze.show()
        self.view.btn_convert.hide()

        if hasattr(self, "btn_cmd"):
            self.btn_cmd.hide()

        if self.guide_path and self.dubbed_path:
            self.view.btn_analyze.setEnabled(True)
            self.view.btn_analyze.setStyleSheet(
                "background-color: #007acc; color: white; font-weight: bold; font-size: 15px;"
            )
            self.view.result_label.setText(tr("status_pronto"))

    def save_analysis_log(self, text: str):
        log_file = "SyncLog.log"
        folder_path = os.path.dirname(self.guide_path) if self.guide_path else "Desconhecido"

        clean_text = text.replace("🔍 ", "").replace("✅ ", "").replace("🎯 ", "").replace("⚠️ ", "")
        clean_text = re.sub(r"<[^>]+>", "", clean_text)
        clean_text = clean_text.replace("&nbsp;", "")
        clean_text = html.unescape(clean_text)

        header = f"{folder_path}\n{'=' * 70}\n"
        new_entry = header + clean_text + "\n\n"

        existing_content = ""
        if os.path.exists(log_file):
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    existing_content = f.read()
            except Exception:
                pass

        try:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(new_entry + existing_content)
        except Exception as e:
            self.view.log_message(tr("log_synclog_erro").format(erro=e))

    def start_analysis(self):
        self.view.btn_analyze.setEnabled(False)
        self.view.btn_cancel.setStyleSheet(
            "background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;"
        )
        self.view.btn_cancel.show()

        self.worker = SyncWorker(
            self.guide_path,
            self.dubbed_path,
            self.guide_dur,
            self.dubbed_dur
        )

        self.worker.progress.connect(self.update_progress)
        self.worker.finished.connect(self.analysis_finished)
        self.worker.error.connect(self.operation_error)
        self.worker.start()

    def update_progress(self, text: str, percent: float):
        self.view.progress_lbl.setText(text)
        self.view.progress_bar.setValue(int(percent))

    def _montar_relatorio(self, result: dict):
        speed_factor = result["speed_factor"]
        diff_percent = result["diff_percent"]
        offset_A = result["offset"]

        audio_dur = self.dubbed_dur
        video_dur = self.guide_dur
        video_fps = self.guide_fps

        slope = 1.0 - speed_factor
        diferenca_bruta = audio_dur - video_dur
        effective_video_dur = video_dur - max(0, offset_A)
        projected_diff_seconds = effective_video_dur * (speed_factor - 1)

        lines = []

        lines.append(tr("rel_dur_audio").format(t=format_time(audio_dur)))
        lines.append(tr("rel_dur_video").format(t=format_time(video_dur)))

        if video_fps:
            lines.append(tr("rel_fps_real").format(fps=f"{video_fps:.3f}"))

        lines.append(tr("rel_dif_total").format(d=f"{diferenca_bruta:.3f}"))

        if offset_A > 0.5:
            lines.append(tr("rel_inicio_video").format(v=f"{offset_A:.2f}"))
        elif offset_A < -0.5:
            lines.append(tr("rel_inicio_audio").format(v=f"{abs(offset_A):.2f}"))
        else:
            lines.append(tr("rel_inicio_ok").format(v=f"{offset_A:.3f}"))

        lines.append(tr("rel_regressao").format(v=f"{slope:.6f}"))
        lines.append(tr("rel_regressao_nota"))
        lines.append("-" * 70)
        lines.append(tr("rel_dif_pct").format(v=f"{abs(diff_percent):.3f}"))
        lines.append(tr("rel_fator").format(v=f"{speed_factor:.5f}"))

        if projected_diff_seconds > 0.05:
            lines.append(tr("rel_status_longo"))
        elif projected_diff_seconds < -0.05:
            lines.append(tr("rel_status_curto"))

        fps_ratios = {
            "24_25": {
                "ratio": Fraction(25, 24),
                "expr": "25/24",
                "tr_key": "fps_24_25"
            },
            "ntsc_25": {
                "ratio": Fraction(25, 1) / Fraction(24000, 1001),
                "expr": "25/(24000/1001)",
                "tr_key": "fps_ntsc_25"
            },
            "ntsc_24": {
                "ratio": Fraction(24, 1) / Fraction(24000, 1001),
                "expr": "24/(24000/1001)",
                "tr_key": "fps_ntsc_24"
            },
            "25_24": {
                "ratio": Fraction(24, 25),
                "expr": "24/25",
                "tr_key": "fps_25_24"
            },
            "25_ntsc": {
                "ratio": Fraction(24000, 1001) / Fraction(25, 1),
                "expr": "(24000/1001)/25",
                "tr_key": "fps_25_ntsc"
            },
            "24_ntsc": {
                "ratio": Fraction(24000, 1001) / Fraction(24, 1),
                "expr": "(24000/1001)/24",
                "tr_key": "fps_24_ntsc"
            },
        }

        best_match = None
        best_expr = None
        min_error = float("inf")

        for name, data in fps_ratios.items():
            error = abs(speed_factor - float(data["ratio"]))
            if error < min_error:
                min_error = error
                best_match = tr(data["tr_key"])
                best_expr = data["expr"]

        is_fps_change_needed = True

        if abs(diff_percent) <= 0.05:
            lines.append(tr("rel_diag_ok"))

            delay_ms = abs(offset_A) * 1000
            if delay_ms <= 40:
                cor_delay = "#2ecc71"
            elif delay_ms <= 60:
                cor_delay = "#f1c40f"
            elif delay_ms <= 100:
                cor_delay = "#e67e22"
            else:
                cor_delay = "#e74c3c"

            delay_formatado = f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}"

            is_dark = getattr(self.view.result_label, "is_dark", True)
            if is_dark:
                delay_html = f"<span style='color: {cor_delay}; font-weight: bold;'>{delay_formatado}</span>"
            else:
                fundo_chip = _misturar_cor(cor_delay)
                delay_html = (
                    f"<span style='color: {cor_delay}; font-weight: bold; "
                    f"background-color: {fundo_chip}; border-radius: 4px;'>"
                    f"&nbsp;{delay_formatado}&nbsp;</span>"
                )

            lines.append(tr("rel_delay").format(delay=delay_html))
            lines.append(tr("rel_sem_fps"))

            is_fps_change_needed = False
            self.analysis_result["tempo_str"] = "1.0"
        else:
            if min_error < 0.005:
                lines.append(tr("rel_diag_padrao").format(m=best_match))
                self.analysis_result["tempo_str"] = best_expr
            else:
                lines.append(tr("rel_diag_atipica1").format(v=f"{abs(diff_percent):.2f}"))
                lines.append(tr("rel_diag_atipica2"))

                tempo_exato = f"{speed_factor:.6f}"
                lines.append(tr("rel_forcar").format(v=tempo_exato))
                self.analysis_result["tempo_str"] = tempo_exato

        texto_final = "\n".join(lines)

        nome_arquivo = os.path.basename(self.guide_path) if self.guide_path else "Desconhecido"
        log_linhas = [tr("rel_log_analisado").format(f=nome_arquivo)]

        if not is_fps_change_needed:
            log_linhas.append(tr("rel_log_diag_ok"))
            delay_str = f"+{offset_A:.3f}" if offset_A >= 0 else f"{offset_A:.3f}"
            log_linhas.append(tr("rel_log_delay").format(v=delay_str))
        else:
            if min_error < 0.005:
                log_linhas.append(tr("rel_log_diag_padrao").format(m=best_match))
            else:
                log_linhas.append(tr("rel_log_diag_atipica").format(v=f"{abs(diff_percent):.2f}"))
                log_linhas.append(tr("rel_log_fator").format(v=f"{speed_factor:.6f}"))

        log_linhas.append("-" * 50)
        bloco_log = "\n".join(log_linhas)

        return texto_final, bloco_log, is_fps_change_needed

    def analysis_finished(self, result: dict):
        self.analysis_result = result

        texto_final, bloco_log, is_fps_change_needed = self._montar_relatorio(result)

        self.save_analysis_log(texto_final)

        texto_html = (
            f"<html><body style='margin: 0;'>"
            f"{texto_final.replace(chr(10), '<br>')}"
            f"</body></html>"
        )

        if hasattr(self.view, "log_diagnostic_top"):
            self.view.log_diagnostic_top(bloco_log)
        elif hasattr(self.view, "log_message"):
            self.view.log_message(bloco_log)

        self.view.result_label.setText(texto_html)

        self.view.btn_analyze.hide()
        self.view.btn_cancel.hide()

        if is_fps_change_needed:
            self.view.btn_convert.show()
            self.view.btn_convert.setEnabled(True)
            self.view.btn_convert.setStyleSheet(
                "background-color: #2b8a3e; color: white; font-weight: bold; font-size: 15px;"
            )

            if hasattr(self, "btn_cmd"):
                self.btn_cmd.show()
        else:
            self.view.btn_convert.hide()

            if hasattr(self, "btn_cmd"):
                self.btn_cmd.hide()

    def retraduzir_relatorio(self, _codigo: str = ""):
        if hasattr(self, "action_ffmpeg"):
            self.action_ffmpeg.setText(tr("menu_ffmpeg"))

        # --- Botão "Mostrar linha de comando." acompanha o idioma ---
        if hasattr(self, "btn_cmd"):
            self.btn_cmd.setText(tr("btn_cmd"))

        # --- Label de progresso: retraduz os estados fixos ---
        analise_concluida = (
            "Análise Concluída!",
            "Analysis Complete!",
            "¡Análisis Completado!"
        )
        conversao_ok = (
            "Conversão Finalizada com Sucesso!",
            "Conversion Completed Successfully!",
            "¡Conversión Finalizada con Éxito!"
        )

        texto_atual = self.view.progress_lbl.text()
        if texto_atual in analise_concluida:
            self.view.progress_lbl.setText(tr("prog_analise_concluida"))
        elif texto_atual in conversao_ok:
            self.view.progress_lbl.setText(tr("status_conversao_ok"))

        # --- Cards com informações dos arquivos ---
        self._atualizar_card_guia()
        self._atualizar_card_dublado()

        # --- Relatório da análise ---
        if not self.analysis_result or "speed_factor" not in self.analysis_result:
            return

        texto_final, bloco_log, is_fps = self._montar_relatorio(self.analysis_result)

        texto_html = (
            f"<html><body style='margin: 0;'>"
            f"{texto_final.replace(chr(10), '<br>')}"
            f"</body></html>"
        )

        self.view.result_label.setText(texto_html)

    def start_conversion(self):
        try:
            tempo_str = self.analysis_result.get(
                "tempo_str",
                f"{self.analysis_result.get('speed_factor', 1.0):.6f}"
            )

            offset = self.analysis_result.get("offset", 0.0)
            eff_dur = self.analysis_result.get(
                "effective_video_dur",
                self.guide_dur - max(0, offset)
            )

            dir_name = os.path.dirname(self.dubbed_path)
            base_name, _ = os.path.splitext(os.path.basename(self.dubbed_path))

            encoders = get_ffmpeg_audio_encoders()

            bitrate = None
            bitrate_val = self.audio_info.get("bitrate")
            if bitrate_val:
                try:
                    bitrate = f"{int(int(bitrate_val) // 1000)}k"
                except Exception:
                    bitrate = None

            enc, ext, sup_bit = select_audio_output(
                self.audio_info.get("codec_name", "ac3"),
                bitrate,
                encoders
            )

            out_name = os.path.join(dir_name, f"{base_name}_fps-corrigido{ext}")

            # Agora usa o índice relativo correto da faixa de áudio.
            audio_idx = int(self.audio_info.get("index", 0))

            cmd = [
                "ffmpeg",
                "-y",
                "-v", "warning",
                "-progress", "pipe:1",
                "-nostats",
                "-i", self.dubbed_path,
                "-map", f"0:a:{audio_idx}",
                "-vn",
                "-sn",
                "-dn",
                "-c:a", enc
            ]

            if sup_bit and bitrate:
                cmd.extend(["-b:a", bitrate])

            sample_rate = self.audio_info.get("sample_rate")
            if sample_rate and str(sample_rate) != "0":
                cmd.extend(["-ar", str(sample_rate)])

            cmd.extend(["-filter:a", f"rubberband=tempo={tempo_str}", out_name])

            self.view.btn_convert.setEnabled(False)
            self.view.btn_cancel.setStyleSheet(
                "background-color: #d32f2f; color: white; font-weight: bold; font-size: 15px;"
            )
            self.view.btn_cancel.show()

            self.view.log_message(tr("log_exportacao").format(arquivo=os.path.basename(out_name)))

            self.ff_worker = FFmpegWorker(cmd, eff_dur, out_name)
            self.ff_worker.progress.connect(self.update_progress)
            self.ff_worker.finished.connect(self.conversion_finished)
            self.ff_worker.error.connect(self.operation_error)
            self.ff_worker.start()
        except Exception as e:
            self.operation_error(str(e))

    def conversion_finished(self, output_path: str):
        self.view.progress_lbl.setText(tr("status_conversao_ok"))
        self.view.progress_bar.setValue(100)
        self.view.log_message(tr("log_sucesso").format(arquivo=os.path.basename(output_path)))

        self._mostrar_mensagem(
            "sucesso",
            tr("popup_sucesso"),
            tr("proc_concluido_salvo").format(caminho=output_path)
        )

        self.reset_ui()

    def cancel_operation(self):
        if hasattr(self, "worker") and self.worker.isRunning():
            self.worker.cancel()

        if hasattr(self, "ff_worker") and self.ff_worker.isRunning():
            self.ff_worker.cancel()

        self.reset_ui()

    def operation_error(self, err: str):
        if self._eh_cancelamento(err):
            self.view.log_message(tr("log_aviso").format(erro=err))
            self._mostrar_mensagem("aviso", tr("popup_aviso"), err)
        else:
            self.view.log_message(tr("log_erro").format(erro=err))

            if self._is_ffmpeg_missing(err):
                self.open_ffmpeg_installer()
            else:
                self._mostrar_mensagem("erro", tr("popup_erro"), err)

        self.reset_ui()

    def reset_ui(self):
        self.view.btn_cancel.hide()
        self.view.btn_convert.hide()

        if hasattr(self, "btn_cmd"):
            self.btn_cmd.hide()

        self.view.btn_analyze.show()

        if self.guide_path and self.dubbed_path:
            self.view.btn_analyze.setEnabled(True)
        else:
            self.view.btn_analyze.setEnabled(False)

        self.view.progress_bar.setValue(0)
        self.view.progress_lbl.setText(tr("status_nova_operacao"))
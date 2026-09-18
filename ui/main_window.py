import os
import html
import datetime
import tempfile
import re
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QPushButton, QProgressBar, QLabel,
                               QTextEdit, QTextBrowser, QSplitter, QSizePolicy,
                               QMenu, QApplication, QToolTip, QTabBar, QStyle,
                               QStyleOptionTab, QStylePainter)
from PySide6.QtCore import Qt, QPoint, QSettings, Signal, QObject, QEvent, QUrl
from PySide6.QtGui import (QAction, QActionGroup, QPalette, QColor, QImage,
                           QPainter, QPen, QPolygon, QCursor, QDesktopServices)
from ui.components import DragDropCard
from ui.styles import (
    obter_estilo_botoes_global,
    obter_estilo_abas,
    obter_estilo_btn_analyze,
    obter_estilo_btn_convert,
    obter_estilo_btn_cancel,
    obter_estilo_btn_mux,
    obter_estilo_btn_segmented,
    obter_estilo_btn_sync_audio,
)
from utils.translations import tr, set_idioma, get_idioma, carregar_idioma_salvo, IDIOMAS

CHAVE_PIX = "552b40d1-d2ab-49bb-8485-96d5b8931dfc"

# O Log de Eventos e o console são saídas técnicas: não exibem emojis ou
# pictogramas, mesmo quando uma mensagem traduzida os contém.
_LOG_ICON_PATTERN = re.compile(
    r"[\U0001F000-\U0001FAFF\U0001FC00-\U0001FFFD"
    r"\u2300-\u23FF\u2600-\u27BF\u2B00-\u2BFF\uFE0E\uFE0F\u200D\u20E3]"
)


def limpar_icones_do_log(message: object) -> str:
    """Remove emojis e seus modificadores de mensagens destinadas ao log."""
    cleaned = _LOG_ICON_PATTERN.sub("", str(message or ""))
    cleaned = re.sub(r"(?m)^[ \t]+", "", cleaned)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


class HandCursorFilter(QObject):
    """Garante cursor interativo de mãozinha (PointingHandCursor) em todos os botões."""
    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Polish):
            if isinstance(watched, QPushButton):
                watched.setCursor(Qt.CursorShape.PointingHandCursor)
        return super().eventFilter(watched, event)


class MenuActionTabBar(QTabBar):
    """Exibe uma aba de ação como selecionada sem alterar o conteúdo aberto."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._menu_action_index = -1
        self._menu_action_active = False

    def set_menu_action_index(self, index: int):
        self._menu_action_index = index
        self.updateGeometry()
        self.update()

    def set_menu_action_active(self, active: bool):
        if self._menu_action_active != active:
            self._menu_action_active = active
            self.update()

    def paintEvent(self, event):
        painter = QStylePainter(self)
        current_index = self.currentIndex()
        for index in range(self.count()):
            selected = index == current_index
            if self._menu_action_active:
                if index == current_index:
                    selected = False
                elif index == self._menu_action_index:
                    selected = True
            self._draw_tab(painter, index, selected)
        painter.end()

    def tabSizeHint(self, index: int):
        size = super().tabSizeHint(index)
        if index == self._menu_action_index:
            size.setWidth(size.width() + 12)
        return size

    def _draw_tab(self, painter: QStylePainter, index: int, selected: bool):
        option = QStyleOptionTab()
        self.initStyleOption(option, index)
        if selected:
            option.state |= QStyle.StateFlag.State_Selected
        else:
            option.state &= ~QStyle.StateFlag.State_Selected
        painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)



def _criar_seta_scrollbar(direcao: str, cor: str, nome_arquivo: str) -> str:
    caminho = os.path.join(tempfile.gettempdir(), nome_arquivo).replace(os.sep, '/')
    if os.path.exists(caminho):
        return caminho
    img = QImage(10, 10, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(cor))
    painter.setPen(Qt.PenStyle.NoPen)
    pontos = {
        "up": [QPoint(5, 1), QPoint(9, 8), QPoint(1, 8)],
        "down": [QPoint(5, 9), QPoint(1, 2), QPoint(9, 2)],
        "left": [QPoint(1, 5), QPoint(8, 1), QPoint(8, 9)],
        "right": [QPoint(9, 5), QPoint(2, 1), QPoint(2, 9)],
    }[direcao]
    painter.drawPolygon(QPolygon(pontos))
    painter.end()
    img.save(caminho, "PNG")
    return caminho


def _criar_icone_check(cor: str, nome_arquivo: str, cor_borda: str = None) -> str:
    caminho = os.path.join(tempfile.gettempdir(), nome_arquivo).replace(os.sep, '/')
    if os.path.exists(caminho):
        return caminho
    img = QImage(14, 14, QImage.Format_ARGB32)
    img.fill(QColor(0, 0, 0, 0))
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pontos = QPolygon([QPoint(2, 7), QPoint(5, 11), QPoint(12, 3)])
    if cor_borda:
        painter.setPen(QPen(QColor(cor_borda), 4.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(pontos)
        painter.setPen(QPen(QColor(cor), 2.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(pontos)
    else:
        painter.setPen(QPen(QColor(cor), 2.0, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawPolyline(pontos)
    painter.end()
    img.save(caminho, "PNG")
    return caminho


class ResultLabel(QLabel):
    MENSAGENS_MARCA_DAGUA = (
        "Aguardando arquivos...", "Waiting for files...", "Esperando archivos...",
        "Arquivos carregados. Pronto para análise.", "Files loaded. Ready for analysis.",
        "Archivos cargados. Listo para análisis.",
    )

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.is_dark = True
        self._aplicar_estilo(text)

    def set_theme(self, is_dark: bool):
        self.is_dark = is_dark
        self._aplicar_estilo(self.text())

    def setText(self, text: str):
        self._aplicar_estilo(text)
        super().setText(text)

    def _aplicar_estilo(self, text: str):
        # Mantém o painel central visível contra o fundo da tela, no mesmo
        # tom dos cartões de arquivo.
        bg_color = "#222223" if self.is_dark else "#fffdf8"
        text_color_normal = "#e7d9b8" if self.is_dark else "#443b32"
        text_color_watermark = "#71654d" if self.is_dark else "#d4c6b3"
        if text in self.MENSAGENS_MARCA_DAGUA:
            self.setAlignment(Qt.AlignCenter)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
            self.setStyleSheet(f"""
                background-color: {bg_color}; border-radius: 8px; padding: 15px;
                font-size: 26px; font-weight: bold; font-family: Consolas, monospace;
                color: {text_color_watermark};
            """)
        else:
            self.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            self.setTextInteractionFlags(
                Qt.TextInteractionFlag.TextSelectableByMouse |
                Qt.TextInteractionFlag.TextSelectableByKeyboard
            )
            self.setStyleSheet(f"""
                background-color: {bg_color}; border-radius: 8px; padding: 15px;
                font-size: 14px; font-family: Consolas, monospace; color: {text_color_normal};
            """)


class OperationStatusLabel(QLabel):
    """Rótulo de progresso com cor consistente para todas as etapas."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(parent)
        self.is_dark = True
        self.setText(text)

    def set_theme(self, is_dark: bool) -> None:
        self.is_dark = is_dark
        self._aplicar_estilo(self.text())

    def setText(self, text: str) -> None:
        super().setText(text)
        self._aplicar_estilo(text)

    def _aplicar_estilo(self, text: str) -> None:
        color = "#beb18f" if self.is_dark else "#625d56"
        self.setStyleSheet(f"color: {color};")


class MainWindow(QMainWindow):
    idioma_changed = Signal(str)
    event_log_changed = Signal(str)
    LOG_SEPARATOR = "###############################################"

    def __init__(self):
        super().__init__()
        carregar_idioma_salvo()
        self.setWindowTitle("DublaSync")
        self.resize(900, 750)
        self.setMinimumSize(700, 400)
        self.is_dark = True

        # Cursor interativo para todos os botões da aplicação
        self._cursor_filter = HandCursorFilter()
        app_inst = QApplication.instance()
        if app_inst:
            app_inst.installEventFilter(self._cursor_filter)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        header = QHBoxLayout()
        self.title_label = QLabel("DublaSync")
        self.title_label.setStyleSheet("font-size: 24px; font-weight: bold; color: #007acc;")
        self.version_label = QLabel(tr("version_lbl"))
        self.version_label.setStyleSheet("color: #666; font-size: 12px;")
        self.btn_config = QPushButton(tr("btn_config"), self)
        self.btn_config.hide()
        header.addWidget(self.title_label)
        header.addWidget(self.version_label)
        header.addStretch()
        main_layout.addLayout(header)

        self._setup_theme_menu()

        self.tabs = QTabWidget()
        self.tabs.setTabBar(MenuActionTabBar(self.tabs))
        self.tabs.tabBar().setCursor(Qt.CursorShape.PointingHandCursor)
        self.tab_sync, self.tab_lipsync, self.tab_log = QWidget(), QWidget(), QWidget()
        self.tab_config, self.tab_about = QWidget(), QWidget()
        self.tabs.addTab(self.tab_sync, tr("tab_sync"))
        self.tabs.addTab(self.tab_lipsync, tr("tab_lipsync"))
        self.tabs.addTab(self.tab_log, tr("tab_log"))
        self._config_tab_index = self.tabs.addTab(self.tab_config, tr("btn_config"))
        self.tabs.addTab(self.tab_about, tr("tab_about"))
        self.tabs.tabBar().set_menu_action_index(self._config_tab_index)
        self._last_content_tab_index = self.tabs.currentIndex()
        self._config_menu_aberto = False
        self.tabs.currentChanged.connect(self._manter_configuracoes_como_menu)
        self.tabs.tabBar().installEventFilter(self)
        main_layout.addWidget(self.tabs)

        self._setup_sync_tab()
        self._setup_lipsync_tab()
        self._setup_log_tab()
        self._setup_about_tab()

    def _setup_theme_menu(self):
        menu = QMenu(self)
        self.menu_tema = menu.addMenu(tr("menu_tema"))
        self.action_claro = QAction(tr("menu_tema_claro"), self, checkable=True)
        self.action_escuro = QAction(tr("menu_tema_escuro"), self, checkable=True)
        self._grupo_tema = QActionGroup(self, exclusive=True)
        self._grupo_tema.addAction(self.action_claro)
        self._grupo_tema.addAction(self.action_escuro)
        self.action_claro.triggered.connect(self.aplicar_tema_claro)
        self.action_escuro.triggered.connect(self.aplicar_tema_escuro)
        self.menu_tema.addAction(self.action_claro)
        self.menu_tema.addAction(self.action_escuro)

        self.menu_idioma = menu.addMenu(tr("menu_idioma"))
        self._grupo_idioma = QActionGroup(self, exclusive=True)
        self._actions_idioma = {}
        for codigo, nome in IDIOMAS.items():
            action = QAction(nome, self, checkable=True)
            self._grupo_idioma.addAction(action)
            action.triggered.connect(lambda checked, c=codigo: self.aplicar_idioma(c))
            self.menu_idioma.addAction(action)
            self._actions_idioma[codigo] = action

        self.action_analise_segmentada = QAction(
            tr("menu_analise_segmentada"), self, checkable=True
        )
        valor_salvo = QSettings("Vicio", "DublaSync").value(
            "analise_segmentada_automatica", True
        )
        habilitada = str(valor_salvo).strip().lower() not in {"false", "0", "no"}
        self.action_analise_segmentada.setChecked(habilitada)
        self.action_analise_segmentada.toggled.connect(
            lambda ativa: QSettings("Vicio", "DublaSync").setValue(
                "analise_segmentada_automatica", ativa
            )
        )
        menu.addSeparator()
        menu.addAction(self.action_analise_segmentada)

        tema_salvo = QSettings("Vicio", "DublaSync").value("tema", "escuro")
        self.action_escuro.setChecked(tema_salvo != "claro")
        self.action_claro.setChecked(tema_salvo == "claro")

        idioma_atual = get_idioma()
        for c, a in self._actions_idioma.items():
            a.setChecked(c == idioma_atual)

        self.menu_config = menu
        self.btn_config.setMenu(menu)
        self.menu_config.aboutToHide.connect(self._restaurar_aba_apos_configuracoes)

    def analise_segmentada_automatica_habilitada(self) -> bool:
        return self.action_analise_segmentada.isChecked()

    def eventFilter(self, watched, event):
        if watched is self.tabs.tabBar():
            if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease):
                tab_index = watched.tabAt(event.position().toPoint())
                if tab_index == self._config_tab_index:
                    if event.type() == QEvent.Type.MouseButtonPress:
                        self._config_menu_aberto = True
                        self.tabs.tabBar().set_menu_action_active(True)
                        menu_pos = watched.mapToGlobal(watched.tabRect(tab_index).bottomLeft())
                        self.menu_config.popup(menu_pos)
                    return True
        return super().eventFilter(watched, event)

    def _restaurar_aba_apos_configuracoes(self):
        if not self._config_menu_aberto:
            return
        self._config_menu_aberto = False
        self.tabs.tabBar().set_menu_action_active(False)

    def _manter_configuracoes_como_menu(self, tab_index: int):
        if tab_index == self._config_tab_index:
            self.tabs.setCurrentIndex(self._last_content_tab_index)
        else:
            self._last_content_tab_index = tab_index

    def aplicar_idioma(self, codigo: str):
        set_idioma(codigo)
        for c, a in self._actions_idioma.items():
            a.setChecked(c == codigo)
        self.retraduzir()
        self.idioma_changed.emit(codigo)

    def retraduzir(self):
        self.btn_config.setText(tr("btn_config"))
        self.version_label.setText(tr("version_lbl"))
        for i, key in enumerate(["tab_sync", "tab_lipsync", "tab_log"]):
            self.tabs.setTabText(i, tr(key))
        self.tabs.setTabText(self._config_tab_index, tr("btn_config"))
        self.tabs.setTabText(self.tabs.indexOf(self.tab_about), tr("tab_about"))
        self.menu_tema.setTitle(tr("menu_tema"))
        self.menu_idioma.setTitle(tr("menu_idioma"))
        self.action_analise_segmentada.setText(tr("menu_analise_segmentada"))
        self.action_claro.setText(tr("menu_tema_claro"))
        self.action_escuro.setText(tr("menu_tema_escuro"))
        self.btn_analyze.setText(tr("btn_analisar"))
        self.btn_analyze.setToolTip(tr("tooltip_analisar"))
        self.btn_convert.setText(tr("btn_corrigir"))
        self.btn_convert.setToolTip(tr("tooltip_corrigir"))
        self.btn_cancel.setText(tr("btn_cancelar"))
        self.card_guide.set_texts(tr("card_guia"), tr("card_hint"))
        self.card_dubbed.set_texts(tr("card_dublado"), tr("card_hint"))
        aguardando = ("Aguardando arquivos...", "Waiting for files...", "Esperando archivos...")
        prontos = ("Arquivos carregados. Pronto para análise.", "Files loaded. Ready for analysis.",
                   "Archivos cargados. Listo para análisis.")
        if self.result_label.text() in aguardando:
            self.result_label.setText(tr("status_aguardando"))
        elif self.result_label.text() in prontos:
            self.result_label.setText(tr("status_pronto"))
        is_dark = getattr(self.result_label, 'is_dark', True)
        self.atualizar_estilos_css(is_dark)

    def _aplicar_paleta(self, is_dark: bool):
        app = QApplication.instance()
        palette = QPalette()
        if is_dark:
            palette.setColor(QPalette.Window, QColor(30, 30, 30))
            palette.setColor(QPalette.WindowText, QColor("#e7d9b8"))
            palette.setColor(QPalette.Base, QColor(37, 37, 38))
            palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
            palette.setColor(QPalette.ToolTipBase, QColor("#2d2d30"))
            palette.setColor(QPalette.ToolTipText, QColor("#e7d9b8"))
            palette.setColor(QPalette.Text, QColor("#e7d9b8"))
            palette.setColor(QPalette.Button, QColor(45, 45, 45))
            palette.setColor(QPalette.ButtonText, QColor("#e7d9b8"))
        else:
            palette.setColor(QPalette.Window, QColor("#f7f3ea"))
            palette.setColor(QPalette.WindowText, QColor("#443b32"))
            palette.setColor(QPalette.Base, QColor("#fffdf8"))
            palette.setColor(QPalette.AlternateBase, QColor("#f1e9db"))
            palette.setColor(QPalette.ToolTipBase, QColor("#fffdf8"))
            palette.setColor(QPalette.ToolTipText, QColor("#443b32"))
            palette.setColor(QPalette.Text, QColor("#443b32"))
            palette.setColor(QPalette.Button, QColor("#f1e9db"))
            palette.setColor(QPalette.ButtonText, QColor("#443b32"))
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor("#c9a968") if is_dark else QColor(0, 122, 204))
        palette.setColor(QPalette.Highlight, QColor("#c9a968") if is_dark else QColor(0, 122, 204))
        palette.setColor(QPalette.HighlightedText, QColor("#1e1e1e") if is_dark else Qt.white)
        app.setPalette(palette)
        QSettings("Vicio", "DublaSync").setValue("tema", "escuro" if is_dark else "claro")

    def aplicar_tema_escuro(self):
        self.is_dark = True
        self.action_escuro.setChecked(True)
        self._aplicar_paleta(is_dark=True)
        self.atualizar_estilos_css(is_dark=True)

    def aplicar_tema_claro(self):
        self.is_dark = False
        self.action_claro.setChecked(True)
        self._aplicar_paleta(is_dark=False)
        self.atualizar_estilos_css(is_dark=False)

    def _gerar_html_lipsync(self, text_color: str, table_header: str, table_border: str, accent_color: str) -> str:
        td_style = f"border: 1px solid {table_border};"
        return (
            f"<div style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px; color: {text_color}; padding: 15px;'>"
            f"<p style='margin-top: 0;'>{tr('lipsync_intro')}</p>"
            f"<h3 style='color: {accent_color}; margin-top: 20px;'>{tr('lipsync_h1')}</h3>"
            f"<p>{tr('lipsync_p1')}</p><ul><li>{tr('lipsync_li1')}</li><li>{tr('lipsync_li2')}</li></ul>"
            f"<p>{tr('lipsync_p2')}</p>"
            f"<table width='650' cellspacing='0' cellpadding='8' style='margin: 15px 0 25px 0; border-collapse: collapse;'>"
            f"<tr style='background-color: {table_header}; text-align: left; font-weight: bold;'>"
            f"<td width='180' style='{td_style}'>{tr('col_diferenca')}</td><td style='{td_style}'>{tr('col_percepcao')}</td></tr>"
            f"{''.join(f'<tr><td style=\"{td_style}\">{d}</td><td style=\"{td_style}\">{tr(p)}</td></tr>' for d, p in [('🟢 0–20 ms','perc_1'),('🟢 20–45 ms','perc_2'),('🟡 45–90 ms','perc_3'),('🟠 90–125 ms','perc_4'),('🔴 125–185 ms','perc_5'),('🔴 Acima de 185 ms','perc_6')])}"
            f"</table>"
            f"<h3 style='color: {accent_color};'>{tr('lipsync_h2')}</h3><p>{tr('lipsync_p3')}</p>"
            f"<table width='650' cellspacing='0' cellpadding='8' style='margin-top: 15px; border-collapse: collapse;'>"
            f"<tr style='background-color: {table_header}; text-align: left; font-weight: bold;'>"
            f"<td width='180' style='{td_style}'>{tr('col_diferenca')}</td><td style='{td_style}'>{tr('col_qualidade')}</td></tr>"
            f"{''.join(f'<tr><td style=\"{td_style}\">{d}</td><td style=\"{td_style}\">{tr(q)}</td></tr>' for d, q in [('🟢 0–20 ms','qual_1'),('🟢 20–40 ms','qual_2'),('🟡 40–60 ms','qual_3'),('🟠 60–100 ms','qual_4'),('🔴 Acima de 100 ms','qual_5')])}"
            f"</table></div>"
        )

    def _gerar_html_sobre(self, text_color: str, accent_color: str, accent_text_color: str) -> str:
        ui_dir = os.path.dirname(os.path.abspath(__file__))
        urso_img = os.path.join(ui_dir, "urso.png").replace(os.sep, '/')
        qr_img = os.path.join(ui_dir, "qrcode_pix.png").replace(os.sep, '/')
        return (
            f"<div style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px; color: {text_color};'>"
            f"<p style='margin-top: 0;'>{tr('sobre_p1')}</p><p>{tr('sobre_p2')}</p><p>{tr('sobre_p3')}</p><p>{tr('sobre_p4')}</p>"
            f"<h3 style='color: {accent_color}; margin-top: 0;'>{tr('sobre_mendigagem')}</h3>"
            f"<table cellspacing='0' cellpadding='4' border='0'><tr>"
            f"<td width='150' valign='middle'><img src='{urso_img}' width='140'></td>"
            f"<td valign='middle'><p style='margin: 0;'>{tr('sobre_doacao_p1')}</p><p style='margin-top: 10px;'>{tr('sobre_doacao_p2')}</p></td></tr>"
            f"<tr><td colspan='2' align='center'><img src='{qr_img}' width='170'></td></tr>"
            f"<tr><td colspan='2' align='center'><a href='copiar_pix' style='color: {accent_text_color}; background-color: {accent_color}; text-decoration: none; font-weight: bold;'>{tr('sobre_btn_pix')}</a></td></tr>"
            f"<tr><td colspan='2' align='center'><p>{tr('sobre_obrigado')}</p></td></tr></table><br>"
            f"<p>{tr('sobre_dev')}<br>{tr('sobre_canal')} <a href='https://www.youtube.com/@TutoriaisOnline/videos' style='color: {accent_color}; text-decoration: none; font-weight: bold;'>@TutoriaisOnline</a></p></div>"
        )

    # ====================== CONSOLE (TERMINAL) ======================
    def _aplicar_estilo_console(self):
        # Estilo SIMPLES (sem regras de QScrollBar) para evitar "Could not parse stylesheet".
        # A barra de rolagem do console é estilizada pelo stylesheet global da aplicação.
        is_dark = getattr(self, "console_is_dark", True)
        bg = "#222223" if is_dark else "#fffdf8"
        tc = "#e7d9b8" if is_dark else "#443b32"
        self.console_text.setStyleSheet(
            f"background-color: {bg}; color: {tc}; border-radius: 8px; padding: 10px; "
            f"font-family: Consolas, monospace; font-size: 13px;"
        )

    def console_start(self):
        self.console_is_dark = getattr(self.result_label, "is_dark", True)
        self._aplicar_estilo_console()
        self.console_text.clear()
        self.result_label.hide()
        self.console_text.show()

    def console_end(self):
        self.console_text.hide()
        self.result_label.show()

    def console_line(self, kind: str, text: str):
        is_dark = getattr(self, "console_is_dark", True)
        base = "#e7d9b8" if is_dark else "#443b32"
        ts_cor = "#9f947d" if is_dark else "#8a7a68"
        cores = {
            "step": "#c9a968" if is_dark else "#007acc",
            "progress": base,
            "ok": "#2ecc71" if is_dark else "#27ae60",
            "warn": "#f1c40f" if is_dark else "#b8860b",
            "error": "#e74c3c" if is_dark else "#c0392b",
        }
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        esc = html.escape(limpar_icones_do_log(text))
        linha = (f"<span style='color: {ts_cor};'>[{ts}]</span> "
                 f"<span style='color: {cores.get(kind, base)};'>{esc}</span>")
        self.console_text.append(linha)
        sb = self.console_text.verticalScrollBar()
        sb.setValue(sb.maximum())

    def atualizar_estilos_css(self, is_dark: bool):
        self.is_dark = is_dark
        bg_color = "#1e1e1e" if is_dark else "#f7f3ea"
        text_color = "#e7d9b8" if is_dark else "#443b32"
        btn_border = "#665a43" if is_dark else "#e5d9c7"
        table_header = "#333333" if is_dark else "#f1e9db"
        table_border = "#665a43" if is_dark else "#e5d9c7"
        accent_color = "#c9a968" if is_dark else "#007acc"
        accent_text_color = "#1e1e1e" if is_dark else "#ffffff"

        self.title_label.setStyleSheet(
            f"font-size: 24px; font-weight: bold; color: {accent_color};"
        )
        self.version_label.setStyleSheet(
            f"color: {'#beb18f' if is_dark else '#666'}; font-size: 12px;"
        )

        menu_bg = "#2d2d30" if is_dark else "#f7f3e8"
        img_check = _criar_icone_check("#e7d9b8" if is_dark else "#ffffff", f"ds_check_v2_{'escuro' if is_dark else 'claro'}.png", cor_borda=None if is_dark else "#007acc")
        css_menu = f"""
            QMenu {{ background-color: {menu_bg}; color: {text_color}; border: 1px solid {btn_border}; padding: 4px; }}
            QMenu::item {{ padding: 6px 28px 6px 8px; border-radius: 4px; color: {text_color}; }}
            QMenu::item:selected {{ background-color: {accent_color}; color: {accent_text_color}; }}
            QMenu::separator {{ height: 1px; background: {btn_border}; margin: 4px 8px; }}
            QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}
            QMenu::indicator:checked {{ image: url("{img_check}"); }}
        """
        self.menu_config.setStyleSheet(css_menu)
        for submenu in self.menu_config.findChildren(QMenu):
            submenu.setStyleSheet(css_menu)

        # Estilos SIMPLES dos widgets de texto (sem regras de QScrollBar aqui)
        self.log_text.setStyleSheet(f"font-family: Consolas; font-size: 13px; background-color: {bg_color}; color: {text_color};")
        if hasattr(self, "btn_open_synclog"):
            disabled_text = "#666666" if is_dark else "#a39483"
            self.btn_open_synclog.setStyleSheet(f"""
                QPushButton {{ color: {text_color}; }}
                QPushButton:hover {{ color: {text_color}; }}
                QPushButton:pressed {{ color: {text_color}; }}
                QPushButton:disabled {{ color: {disabled_text}; }}
            """)
        if hasattr(self, "synclog_status_label"):
            footer_color = "#beb18f" if is_dark else "#786b5c"
            self.synclog_status_label.setStyleSheet(
                f"font-size: 11px; color: {footer_color}; padding: 2px 0;"
            )

        if hasattr(self, 'result_label') and hasattr(self.result_label, 'set_theme'):
            self.result_label.set_theme(is_dark)

        if hasattr(self, 'card_guide') and hasattr(self.card_guide, 'set_theme'):
            self.card_guide.set_theme(is_dark)
            self.card_dubbed.set_theme(is_dark)

        self.lipsync_text.setStyleSheet(f"background-color: {bg_color};")
        self.lipsync_text.setHtml(self._gerar_html_lipsync(text_color, table_header, table_border, accent_color))
        self.about_text.setStyleSheet(f"background-color: {bg_color};")
        self.about_text.setHtml(self._gerar_html_sobre(text_color, accent_color, accent_text_color))

        sufixo = "escuro" if is_dark else "claro"
        cor_seta = "#beb18f" if is_dark else "#786b5c"
        setas = {d: _criar_seta_scrollbar(d, cor_seta, f"ds_seta_{d}_{sufixo}.png") for d in ["up", "down", "left", "right"]}
        sb_track = "#252526" if is_dark else "#f7f3ea"
        sb_handle = "#5a5a5a" if is_dark else "#cdbda9"
        sb_hover = "#787878" if is_dark else "#b9a486"
        sb_button = "#2d2d30" if is_dark else "#f1e9db"

        # ── ESTILO DA BARRA DE ROLAGEM (SOMENTE GLOBAL) ──
        # As setas têm posição FIXA (subcontrol-position + subcontrol-origin: margin),
        # então a alça central nunca as sobrepõe. Aplicado apenas no stylesheet da
        # aplicação, que vale para todos os scrollbars (inclusive os dos widgets de texto).
        scroll_css = f"""
            QScrollBar:vertical {{ background: {sb_track}; border: 1px solid {btn_border}; width: 14px; margin: 14px 0; }}
            QScrollBar::handle:vertical {{ background: {sb_handle}; min-height: 25px; }}
            QScrollBar::handle:vertical:hover {{ background: {sb_hover}; }}
            QScrollBar::sub-line:vertical {{ background: {sb_button}; border: 1px solid {btn_border}; height: 14px; subcontrol-position: top; subcontrol-origin: margin; }}
            QScrollBar::add-line:vertical {{ background: {sb_button}; border: 1px solid {btn_border}; height: 14px; subcontrol-position: bottom; subcontrol-origin: margin; }}
            QScrollBar::up-arrow:vertical {{ image: url("{setas['up']}"); }}
            QScrollBar::down-arrow:vertical {{ image: url("{setas['down']}"); }}
            QScrollBar:horizontal {{ background: {sb_track}; border: 1px solid {btn_border}; height: 14px; margin: 0 14px; }}
            QScrollBar::handle:horizontal {{ background: {sb_handle}; min-width: 25px; }}
            QScrollBar::handle:horizontal:hover {{ background: {sb_hover}; }}
            QScrollBar::sub-line:horizontal {{ background: {sb_button}; border: 1px solid {btn_border}; width: 14px; subcontrol-position: left; subcontrol-origin: margin; }}
            QScrollBar::add-line:horizontal {{ background: {sb_button}; border: 1px solid {btn_border}; width: 14px; subcontrol-position: right; subcontrol-origin: margin; }}
            QScrollBar::left-arrow:horizontal {{ image: url("{setas['left']}"); }}
            QScrollBar::right-arrow:horizontal {{ image: url("{setas['right']}"); }}
        """

        if hasattr(self, "console_text"):
            self.console_is_dark = is_dark
            self._aplicar_estilo_console()

        btn_global_css = obter_estilo_botoes_global(is_dark)
        tabs_css = obter_estilo_abas(is_dark)
        tooltip_css = "" if is_dark else f"""
            QToolTip {{
                background-color: {menu_bg};
                color: {text_color};
                border: 1px solid {btn_border};
                padding: 1px 2px;
            }}
        """

        app_inst = QApplication.instance()
        if app_inst:
            app_inst.setStyleSheet(scroll_css + css_menu + btn_global_css + tabs_css + tooltip_css)

        self.progress_lbl.set_theme(is_dark)

        # Atualiza botões de ação se já estiverem instanciados
        if hasattr(self, "btn_analyze"):
            self.btn_analyze.setStyleSheet(obter_estilo_btn_analyze(is_dark, enabled=self.btn_analyze.isEnabled()))
        if hasattr(self, "btn_convert") and self.btn_convert.isVisible():
            self.btn_convert.setStyleSheet(obter_estilo_btn_convert(is_dark))
        if hasattr(self, "btn_cancel") and self.btn_cancel.isVisible():
            self.btn_cancel.setStyleSheet(obter_estilo_btn_cancel(is_dark))

        # Atualiza botões do controlador se ativos
        ctrl = getattr(self, "controller", None)
        if ctrl:
            if hasattr(ctrl, "btn_mux") and ctrl.btn_mux.isVisible():
                ctrl.btn_mux.setStyleSheet(obter_estilo_btn_mux(is_dark))
            if hasattr(ctrl, "btn_sync_audio") and ctrl.btn_sync_audio.isVisible():
                ctrl.btn_sync_audio.setStyleSheet(obter_estilo_btn_sync_audio(is_dark))
            if hasattr(ctrl, "btn_segmented") and ctrl.btn_segmented.isVisible():
                ctrl.btn_segmented.setStyleSheet(obter_estilo_btn_segmented(is_dark))

    def _handle_about_link(self, link):
        if link.toString() == "copiar_pix":
            QApplication.clipboard().setText(CHAVE_PIX)
            QToolTip.showText(QCursor.pos(), tr("sobre_tooltip_pix"))
        elif link.scheme() in ("http", "https", "ftp"):
            QDesktopServices.openUrl(link)

    def _setup_sync_tab(self):
        layout = QVBoxLayout(self.tab_sync)
        splitter = QSplitter(Qt.Vertical)

        top_widget = QWidget()
        cards_layout = QHBoxLayout(top_widget)
        self.card_guide = DragDropCard(tr("card_guia"), "🎥")
        self.card_dubbed = DragDropCard(tr("card_dublado"), "🎙️")
        cards_layout.addWidget(self.card_guide)
        cards_layout.addWidget(self.card_dubbed)

        bottom_widget = QWidget()
        bottom_layout = QVBoxLayout(bottom_widget)

        self.result_label = ResultLabel(tr("status_aguardando"))
        self.result_label.setWordWrap(True)

        # ── CONSOLE (TERMINAL) — ocupa o lugar do relatório durante os processos ──
        self.console_text = QTextEdit()
        self.console_text.setReadOnly(True)
        self.console_text.setVisible(False)
        self.console_is_dark = True
        self.console_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.console_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.progress_lbl = OperationStatusLabel(tr("status_operacao"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(25)

        btn_layout = QHBoxLayout()
        self.btn_analyze = QPushButton(tr("btn_analisar"))
        self.btn_analyze.setMinimumHeight(45)
        self.btn_analyze.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_analyze.setToolTip(tr("tooltip_analisar"))
        self.btn_analyze.setEnabled(False)
        self.btn_analyze.setStyleSheet(obter_estilo_btn_analyze(self.is_dark, enabled=False))
        self.btn_convert = QPushButton(tr("btn_corrigir"))
        self.btn_convert.setMinimumHeight(45)
        self.btn_convert.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_convert.setToolTip(tr("tooltip_corrigir"))
        self.btn_convert.setEnabled(False)
        self.btn_convert.hide()
        self.btn_cancel = QPushButton(tr("btn_cancelar"))
        self.btn_cancel.setMinimumHeight(45)
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.hide()
        btn_layout.addWidget(self.btn_analyze)
        btn_layout.addWidget(self.btn_convert)
        btn_layout.addWidget(self.btn_cancel)

        bottom_layout.addWidget(self.result_label)
        bottom_layout.addWidget(self.console_text)
        bottom_layout.addWidget(self.progress_lbl)
        bottom_layout.addWidget(self.progress_bar)
        bottom_layout.addLayout(btn_layout)

        splitter.addWidget(top_widget)
        splitter.addWidget(bottom_widget)
        splitter.setSizes([150, 600])
        layout.addWidget(splitter)

    def _setup_lipsync_tab(self):
        layout = QVBoxLayout(self.tab_lipsync)
        self.lipsync_text = QTextEdit()
        self.lipsync_text.setReadOnly(True)
        self.lipsync_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.lipsync_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        layout.addWidget(self.lipsync_text)

    def _setup_log_tab(self):
        layout = QVBoxLayout(self.tab_log)
        self.btn_open_synclog = QPushButton(tr("btn_abrir_historico_salvo"))
        self.btn_open_synclog.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_synclog.setToolTip(tr("tooltip_abrir_historico_salvo"))
        self.btn_open_synclog.setEnabled(False)
        self.btn_open_synclog.clicked.connect(self._open_synclog)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.log_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._log_groups = []
        self._active_log_group_key = None
        layout.addWidget(self.log_text)

        self.synclog_status_label = QLabel(tr("synclog_status_sem_historico"))
        self.synclog_status_label.setWordWrap(True)
        log_footer = QHBoxLayout()
        log_footer.setContentsMargins(0, 0, 0, 0)
        log_footer.addWidget(self.synclog_status_label)
        log_footer.addStretch()
        log_footer.addWidget(self.btn_open_synclog)
        layout.addLayout(log_footer)

    def set_synclog_status(self, path: str, has_history: bool) -> None:
        """Atualiza o rodapé e habilita a abertura apenas para um histórico salvo."""
        self._synclog_path = os.path.abspath(path) if path else ""
        available = bool(has_history and self._synclog_path and os.path.isfile(self._synclog_path))
        self.btn_open_synclog.setEnabled(available)
        self.synclog_status_label.setText(
            tr("synclog_status_salvo") if available else tr("synclog_status_sem_historico")
        )

    def _open_synclog(self) -> None:
        path = getattr(self, "_synclog_path", "")
        if path and os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _setup_about_tab(self):
        layout = QVBoxLayout(self.tab_about)
        self.about_text = QTextBrowser()
        self.about_text.setReadOnly(True)
        self.about_text.setOpenLinks(False)
        self.about_text.setOpenExternalLinks(False)
        self.about_text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.about_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.about_text.anchorClicked.connect(self._handle_about_link)
        layout.addWidget(self.about_text)

    def log_message(self, message: str):
        group = self._get_active_log_group()
        group["messages"].append(self._new_log_entry(message))
        self._render_event_log()

    def prepend_log(self, message: str):
        self.log_diagnostic_top(message)

    def log_diagnostic_top(self, message: str):
        group = self._get_active_log_group()
        group["messages"].insert(0, self._new_log_entry(message))
        self._render_event_log()

    def start_log_group(self, file_path: str) -> None:
        """Move o bloco do arquivo atual para o topo do Log de Eventos."""
        key = os.path.normcase(os.path.abspath(file_path)) if file_path else ""
        for index, group in enumerate(self._log_groups):
            if group["key"] == key:
                self._log_groups.insert(0, self._log_groups.pop(index))
                break
        else:
            self._log_groups.insert(
                0,
                {
                    "key": key,
                    "source_path": file_path,
                    "output_path": None,
                    "started_at": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
                    "messages": [],
                    "technical_messages": [],
                },
            )
        self._active_log_group_key = key
        self._render_event_log()

    def set_active_log_output(self, output_path: str) -> None:
        """Registra no cabeçalho o arquivo gerado pela operação atual."""
        group = self._get_active_log_group()
        group["output_path"] = output_path
        self._render_event_log()

    def log_technical_message(self, message: str) -> None:
        """Acrescenta detalhes reproduzíveis sem poluir as etapas principais."""
        group = self._get_active_log_group()
        group.setdefault("technical_messages", []).append(self._new_log_entry(message))
        self._render_event_log()

    def replace_active_log_content(self, content: str) -> None:
        """Atualiza todo o conteúdo do bloco do arquivo atualmente ativo."""
        group = self._get_active_log_group()
        group["messages"] = [self._new_log_entry(content)] if content.strip() else []
        self._render_event_log()

    def replace_active_log_fragment(self, old: str, new: str) -> bool:
        """Substitui um diagnóstico já exibido sem alterar os demais blocos."""
        group = self._get_active_log_group()
        for entry in group["messages"]:
            if isinstance(entry, dict) and old in entry["text"]:
                entry["text"] = entry["text"].replace(old, new, 1)
                self._render_event_log()
                return True
        return False

    def set_event_log_content(self, content: str) -> None:
        """Carrega o conteúdo persistido quando a janela é inicializada."""
        normalized = limpar_icones_do_log(content)
        self._log_groups = (
            [{"key": "__persisted__", "messages": [{"text": normalized, "timestamp": None}]}]
            if normalized else []
        )
        self._active_log_group_key = None
        self._render_event_log()

    def _get_active_log_group(self) -> dict:
        for group in self._log_groups:
            if group["key"] == self._active_log_group_key:
                return group
        group = {"key": "__general__", "messages": [], "technical_messages": []}
        self._log_groups.append(group)
        self._active_log_group_key = group["key"]
        return group

    def _render_event_log(self) -> None:
        blocks = [self._format_log_group(group) for group in self._log_groups]
        content = f"\n\n{self.LOG_SEPARATOR}\n\n".join(
            block for block in blocks if block
        )
        self.log_text.setPlainText(content)
        scrollbar = self.log_text.verticalScrollBar()
        if scrollbar:
            scrollbar.setValue(0)
        self.event_log_changed.emit(content)

    def _format_log_group(self, group: dict) -> str:
        messages = "\n".join(
            self._format_log_entry(message)
            for message in group["messages"]
            if message
        ).strip()
        source_path = group.get("source_path")
        if not source_path:
            return messages

        lines = [
            f"Arquivo: {os.path.basename(source_path)}",
            f"Origem: {os.path.basename(source_path)}",
            f"Iniciado em: {group.get('started_at', '')}",
        ]
        output_path = group.get("output_path")
        if output_path:
            lines.append(f"Saída: {os.path.basename(output_path)}")
        if messages:
            lines.extend(["", messages])

        technical_messages = group.get("technical_messages", [])
        if technical_messages:
            technical_time = group.get("started_at", "")[-8:]
            lines.extend([
                "",
                "[DETALHES TÉCNICOS]",
                f"[{technical_time}] Origem completa: {source_path}",
            ])
            if output_path:
                lines.append(f"[{technical_time}] Saída completa: {output_path}")
            lines.extend(self._format_log_entry(message) for message in technical_messages)
        return "\n".join(lines).strip()

    @staticmethod
    def _new_log_entry(message: str) -> dict:
        return {
            "text": limpar_icones_do_log(message),
            "timestamp": datetime.datetime.now().strftime("%H:%M:%S"),
        }

    @staticmethod
    def _format_log_entry(entry: object) -> str:
        if isinstance(entry, str):
            return entry
        timestamp = entry.get("timestamp")
        text = entry.get("text", "")
        if not timestamp:
            return text
        return "\n".join(
            f"[{timestamp}] {line}" if line else ""
            for line in text.splitlines()
        )

import os
import tempfile
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QTabWidget, QPushButton, QProgressBar, QLabel,
                               QTextEdit, QTextBrowser, QSplitter, QSizePolicy,
                               QMenu, QApplication, QToolTip)
from PySide6.QtCore import Qt, QPoint, QSettings, Signal
from PySide6.QtGui import (QAction, QActionGroup, QPalette, QColor, QImage,
                           QPainter, QPen, QPolygon, QCursor, QDesktopServices)
from ui.components import DragDropCard
from utils.translations import tr, set_idioma, get_idioma, carregar_idioma_salvo, IDIOMAS

CHAVE_PIX = "552b40d1-d2ab-49bb-8485-96d5b8931dfc"


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
        bg_color = "#1e1e1e" if self.is_dark else "#f0f0f0"
        text_color_normal = "#d4d4d4" if self.is_dark else "#333333"
        text_color_watermark = "#2e2e2e" if self.is_dark else "#cccccc"
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


class MainWindow(QMainWindow):
    idioma_changed = Signal(str)

    def __init__(self):
        super().__init__()
        carregar_idioma_salvo()
        self.setWindowTitle("DublaSync")
        self.resize(900, 750)
        self.setMinimumSize(700, 400)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        header = QHBoxLayout()
        title = QLabel("DublaSync")
        title.setStyleSheet("font-size: 24px; font-weight: bold; color: #007acc;")
        self.version_label = QLabel(tr("version_lbl"))
        self.version_label.setStyleSheet("color: #666; font-size: 12px;")
        self.btn_config = QPushButton(tr("btn_config"))
        self.btn_config.setCursor(Qt.CursorShape.PointingHandCursor)
        header.addWidget(title)
        header.addWidget(self.version_label)
        header.addStretch()
        header.addWidget(self.btn_config)
        main_layout.addLayout(header)
        self._setup_theme_menu()
        self.tabs = QTabWidget()
        self.tab_sync, self.tab_lipsync, self.tab_log, self.tab_about = QWidget(), QWidget(), QWidget(), QWidget()
        self.tabs.addTab(self.tab_sync, tr("tab_sync"))
        self.tabs.addTab(self.tab_lipsync, tr("tab_lipsync"))
        self.tabs.addTab(self.tab_log, tr("tab_log"))
        self.tabs.addTab(self.tab_about, tr("tab_about"))
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
        tema_salvo = QSettings("Vicio", "DublaSync").value("tema", "escuro")
        self.action_escuro.setChecked(tema_salvo != "claro")
        self.action_claro.setChecked(tema_salvo == "claro")
        idioma_atual = get_idioma()
        for c, a in self._actions_idioma.items():
            a.setChecked(c == idioma_atual)
        self.btn_config.setMenu(menu)

    def aplicar_idioma(self, codigo: str):
        set_idioma(codigo)
        for c, a in self._actions_idioma.items():
            a.setChecked(c == codigo)
        self.retraduzir()
        self.idioma_changed.emit(codigo)

    def retraduzir(self):
        self.btn_config.setText(tr("btn_config"))
        self.version_label.setText(tr("version_lbl"))
        for i, key in enumerate(["tab_sync", "tab_lipsync", "tab_log", "tab_about"]):
            self.tabs.setTabText(i, tr(key))
        self.menu_tema.setTitle(tr("menu_tema"))
        self.menu_idioma.setTitle(tr("menu_idioma"))
        self.action_claro.setText(tr("menu_tema_claro"))
        self.action_escuro.setText(tr("menu_tema_escuro"))
        self.btn_analyze.setText(tr("btn_analisar"))
        self.btn_convert.setText(tr("btn_corrigir"))
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
            palette.setColor(QPalette.WindowText, Qt.white)
            palette.setColor(QPalette.Base, QColor(37, 37, 38))
            palette.setColor(QPalette.AlternateBase, QColor(45, 45, 45))
            palette.setColor(QPalette.ToolTipBase, Qt.white)
            palette.setColor(QPalette.ToolTipText, Qt.white)
            palette.setColor(QPalette.Text, Qt.white)
            palette.setColor(QPalette.Button, QColor(45, 45, 45))
            palette.setColor(QPalette.ButtonText, Qt.white)
        else:
            palette.setColor(QPalette.Window, QColor(240, 240, 240))
            palette.setColor(QPalette.WindowText, Qt.black)
            palette.setColor(QPalette.Base, QColor(255, 255, 255))
            palette.setColor(QPalette.AlternateBase, QColor(230, 230, 230))
            palette.setColor(QPalette.ToolTipBase, Qt.black)
            palette.setColor(QPalette.ToolTipText, Qt.black)
            palette.setColor(QPalette.Text, Qt.black)
            palette.setColor(QPalette.Button, QColor(220, 220, 220))
            palette.setColor(QPalette.ButtonText, Qt.black)
        palette.setColor(QPalette.BrightText, Qt.red)
        palette.setColor(QPalette.Link, QColor(0, 122, 204))
        palette.setColor(QPalette.Highlight, QColor(0, 122, 204))
        palette.setColor(QPalette.HighlightedText, Qt.white)
        app.setPalette(palette)
        QSettings("Vicio", "DublaSync").setValue("tema", "escuro" if is_dark else "claro")

    def aplicar_tema_escuro(self):
        self.action_escuro.setChecked(True)
        self._aplicar_paleta(is_dark=True)
        self.atualizar_estilos_css(is_dark=True)

    def aplicar_tema_claro(self):
        self.action_claro.setChecked(True)
        self._aplicar_paleta(is_dark=False)
        self.atualizar_estilos_css(is_dark=False)

    def _gerar_html_lipsync(self, text_color: str, table_header: str, table_border: str) -> str:
        td_style = f"border: 1px solid {table_border};"
        return (
            f"<div style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px; color: {text_color}; padding: 15px;'>"
            f"<p style='margin-top: 0;'>{tr('lipsync_intro')}</p>"
            f"<h3 style='color: #007acc; margin-top: 20px;'>{tr('lipsync_h1')}</h3>"
            f"<p>{tr('lipsync_p1')}</p><ul><li>{tr('lipsync_li1')}</li><li>{tr('lipsync_li2')}</li></ul>"
            f"<p>{tr('lipsync_p2')}</p>"
            f"<table width='650' cellspacing='0' cellpadding='8' style='margin: 15px 0 25px 0; border-collapse: collapse;'>"
            f"<tr style='background-color: {table_header}; text-align: left; font-weight: bold;'>"
            f"<td width='180' style='{td_style}'>{tr('col_diferenca')}</td><td style='{td_style}'>{tr('col_percepcao')}</td></tr>"
            f"{''.join(f'<tr><td style=\"{td_style}\">{d}</td><td style=\"{td_style}\">{tr(p)}</td></tr>' for d, p in [('🟢 0–20 ms','perc_1'),('🟢 20–45 ms','perc_2'),('🟡 45–90 ms','perc_3'),('🟠 90–125 ms','perc_4'),('🔴 125–185 ms','perc_5'),('🔴 Acima de 185 ms','perc_6')])}"
            f"</table>"
            f"<h3 style='color: #007acc;'>{tr('lipsync_h2')}</h3><p>{tr('lipsync_p3')}</p>"
            f"<table width='650' cellspacing='0' cellpadding='8' style='margin-top: 15px; border-collapse: collapse;'>"
            f"<tr style='background-color: {table_header}; text-align: left; font-weight: bold;'>"
            f"<td width='180' style='{td_style}'>{tr('col_diferenca')}</td><td style='{td_style}'>{tr('col_qualidade')}</td></tr>"
            f"{''.join(f'<tr><td style=\"{td_style}\">{d}</td><td style=\"{td_style}\">{tr(q)}</td></tr>' for d, q in [('🟢 0–20 ms','qual_1'),('🟢 20–40 ms','qual_2'),('🟡 40–60 ms','qual_3'),('🟠 60–100 ms','qual_4'),('🔴 Acima de 100 ms','qual_5')])}"
            f"</table></div>"
        )

    def _gerar_html_sobre(self, text_color: str) -> str:
        ui_dir = os.path.dirname(os.path.abspath(__file__))
        urso_img = os.path.join(ui_dir, "urso.png").replace(os.sep, '/')
        qr_img = os.path.join(ui_dir, "qrcode_pix.png").replace(os.sep, '/')
        return (
            f"<div style='font-family: Segoe UI, Arial, sans-serif; font-size: 14px; color: {text_color};'>"
            f"<p style='margin-top: 0;'>{tr('sobre_p1')}</p><p>{tr('sobre_p2')}</p><p>{tr('sobre_p3')}</p><p>{tr('sobre_p4')}</p>"
            f"<h3 style='color: #007acc; margin-top: 0;'>{tr('sobre_mendigagem')}</h3>"
            f"<table cellspacing='0' cellpadding='4' border='0'><tr>"
            f"<td width='150' valign='middle'><img src='{urso_img}' width='140'></td>"
            f"<td valign='middle'><p style='margin: 0;'>{tr('sobre_doacao_p1')}</p><p style='margin-top: 10px;'>{tr('sobre_doacao_p2')}</p></td></tr>"
            f"<tr><td colspan='2' align='center'><img src='{qr_img}' width='170'></td></tr>"
            f"<tr><td colspan='2' align='center'><a href='copiar_pix' style='color: #ffffff; background-color: #007acc; text-decoration: none; font-weight: bold;'>{tr('sobre_btn_pix')}</a></td></tr>"
            f"<tr><td colspan='2' align='center'><p>{tr('sobre_obrigado')}</p></td></tr></table><br>"
            f"<p>{tr('sobre_dev')}<br>{tr('sobre_canal')} <a href='https://www.youtube.com/@TutoriaisOnline/videos' style='color: #007acc; text-decoration: none; font-weight: bold;'>@TutoriaisOnline</a></p></div>"
        )

    def atualizar_estilos_css(self, is_dark: bool):
        bg_color = "#1e1e1e" if is_dark else "#f0f0f0"
        text_color = "#d4d4d4" if is_dark else "#333333"
        btn_bg = "#333333" if is_dark else "#e0e0e0"
        btn_border = "#444444" if is_dark else "#cccccc"
        table_header = "#333333" if is_dark else "#dddddd"
        table_border = "#555555" if is_dark else "#cccccc"
        self.btn_config.setStyleSheet(f"""
            QPushButton {{ background-color: {btn_bg}; color: {text_color}; border: 1px solid {btn_border}; border-radius: 4px; padding: 6px 12px; font-size: 13px; }}
            QPushButton:hover {{ background-color: #007acc; color: #ffffff; }}
            QPushButton::menu-indicator {{ image: none; }}
        """)
        menu_bg = "#2d2d30" if is_dark else "#f0f0f0"
        img_check = _criar_icone_check("#ffffff", f"ds_check_{'escuro' if is_dark else 'claro'}.png", cor_borda=None if is_dark else "#007acc")
        css_menu = f"""
            QMenu {{ background-color: {menu_bg}; color: {text_color}; border: 1px solid {btn_border}; padding: 4px; }}
            QMenu::item {{ padding: 6px 28px 6px 8px; border-radius: 4px; color: {text_color}; }}
            QMenu::item:selected {{ background-color: #007acc; color: #ffffff; }}
            QMenu::separator {{ height: 1px; background: {btn_border}; margin: 4px 8px; }}
            QMenu::indicator {{ width: 14px; height: 14px; margin-left: 4px; }}
            QMenu::indicator:checked {{ image: url("{img_check}"); }}
        """
        menu_config = self.btn_config.menu()
        menu_config.setStyleSheet(css_menu)
        for submenu in menu_config.findChildren(QMenu):
            submenu.setStyleSheet(css_menu)
        self.log_text.setStyleSheet(f"font-family: Consolas; font-size: 13px; background-color: {bg_color}; color: {text_color};")
        if hasattr(self.result_label, 'set_theme'):
            self.result_label.set_theme(is_dark)
        if hasattr(self.card_guide, 'set_theme'):
            self.card_guide.set_theme(is_dark)
            self.card_dubbed.set_theme(is_dark)
        self.lipsync_text.setStyleSheet(f"background-color: {bg_color};")
        self.lipsync_text.setHtml(self._gerar_html_lipsync(text_color, table_header, table_border))
        self.about_text.setStyleSheet(f"background-color: {bg_color};")
        self.about_text.setHtml(self._gerar_html_sobre(text_color))
        sufixo = "escuro" if is_dark else "claro"
        cor_seta = "#aaaaaa" if is_dark else "#666666"
        setas = {d: _criar_seta_scrollbar(d, cor_seta, f"ds_seta_{d}_{sufixo}.png") for d in ["up", "down", "left", "right"]}
        sb_track = "#252526" if is_dark else "#f0f0f0"
        sb_handle = "#5a5a5a" if is_dark else "#c2c2c2"
        sb_hover = "#787878" if is_dark else "#a0a0a0"
        sb_button = "#2d2d30" if is_dark else "#f0f0f0"
        # ── CORREÇÃO VISUAL: os botões de seta (sub-line/add-line) agora ficam ancorados
        #    na área de margem reservada (subcontrol-origin: margin), impedindo que o
        #    handle os sobreponha ao chegar nos extremos da barra de rolagem. ──
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
        QApplication.instance().setStyleSheet(scroll_css + css_menu)
        self.progress_lbl.setStyleSheet("" if is_dark else "color: #aaaaaa;")
        btn_cmd = self.findChild(QPushButton, "btnCmd")
        if btn_cmd:
            cor_hover = "#ffffff" if is_dark else "#007acc"
            btn_cmd.setStyleSheet(f"""
                QPushButton {{ background-color: transparent; color: #aaaaaa; text-decoration: underline; border: none; font-size: 12px; padding: 0; }}
                QPushButton:hover {{ color: {cor_hover}; }}
            """)

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
        self.progress_lbl = QLabel(tr("status_operacao"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(25)
        btn_layout = QHBoxLayout()
        self.btn_analyze = QPushButton(tr("btn_analisar"))
        self.btn_analyze.setMinimumHeight(45)
        self.btn_analyze.setEnabled(False)
        self.btn_convert = QPushButton(tr("btn_corrigir"))
        self.btn_convert.setMinimumHeight(45)
        self.btn_convert.setEnabled(False)
        self.btn_convert.hide()
        self.btn_cancel = QPushButton(tr("btn_cancelar"))
        self.btn_cancel.setMinimumHeight(45)
        self.btn_cancel.hide()
        btn_layout.addWidget(self.btn_analyze)
        btn_layout.addWidget(self.btn_convert)
        btn_layout.addWidget(self.btn_cancel)
        bottom_layout.addWidget(self.result_label)
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
        layout.addWidget(self.lipsync_text)

    def _setup_log_tab(self):
        layout = QVBoxLayout(self.tab_log)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        layout.addWidget(self.log_text)

    def _setup_about_tab(self):
        layout = QVBoxLayout(self.tab_about)
        self.about_text = QTextBrowser()
        self.about_text.setReadOnly(True)
        self.about_text.setOpenLinks(False)
        self.about_text.setOpenExternalLinks(False)
        self.about_text.anchorClicked.connect(self._handle_about_link)
        layout.addWidget(self.about_text)

    def log_message(self, message: str):
        self.log_text.append(message)

    def log_diagnostic_top(self, message: str):
        current_text = self.log_text.toPlainText()
        new_text = f"{message}\n\n{current_text}" if current_text else message
        self.log_text.setPlainText(new_text)
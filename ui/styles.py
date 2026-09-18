"""
Módulo de estilos CSS (QSS) para a interface do DublaSync.
Fornece estilos visuais de botões, abas e elementos interativos com
efeitos de hover destacados e de alta percepção para os temas Escuro e Claro.
"""

def obter_estilo_botoes_global(is_dark: bool) -> str:
    btn_bg = "#2d2d30" if is_dark else "#f1e9db"
    btn_text = "#e7d9b8" if is_dark else "#443b32"
    btn_border = "#665a43" if is_dark else "#e0d3c0"

    hover_bg = "#5a482a" if is_dark else "#007acc"
    hover_border = "#c9a968" if is_dark else "#005999"
    hover_text = "#e7d9b8" if is_dark else "#ffffff"

    pressed_bg = "#40341f" if is_dark else "#004c80"
    pressed_border = "#8f743f" if is_dark else "#007acc"

    disabled_bg = "#222224" if is_dark else "#eee5d8"
    disabled_text = "#666666" if is_dark else "#a39483"
    disabled_border = "#333333" if is_dark else "#e1d5c5"

    # Tom neutro com contraste suficiente nos dois fundos, sem recorrer ao azul.
    link_text = "#beb18f" if is_dark else "#786b5c"
    link_hover = "#e7d9b8" if is_dark else "#443b32"
    link_pressed = "#a89772" if is_dark else "#665848"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: {btn_text};
            border: 1px solid {btn_border};
            border-radius: 5px;
            padding: 6px 14px;
            font-size: 13px;
        }}
        QPushButton:hover {{
            background-color: {hover_bg};
            border: 1px solid {hover_border};
            color: {hover_text};
        }}
        QPushButton:pressed {{
            background-color: {pressed_bg};
            border: 1px solid {pressed_border};
            color: {hover_text};
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 1px solid {disabled_border};
        }}
        /* Botões tipo link plano */
        QPushButton#btnCmd, QPushButton#btnCli {{
            background-color: transparent;
            border: none;
            color: {link_text};
            text-decoration: underline;
            padding: 1px 2px;
        }}
        QPushButton#btnCmd:hover, QPushButton#btnCli:hover {{
            background-color: transparent;
            border: none;
            color: {link_hover};
        }}
        QPushButton#btnCmd:pressed, QPushButton#btnCli:pressed {{
            background-color: transparent;
            border: none;
            color: {link_pressed};
        }}
    """


def obter_estilo_abas(is_dark: bool) -> str:
    tab_pane_bg = "#1e1e1e" if is_dark else "#f7f3ea"
    tab_border = "#665a43" if is_dark else "#e5d9c7"
    tab_bg = "#252526" if is_dark else "#f1e9db"
    tab_text = "#beb18f" if is_dark else "#786b5c"

    tab_hover_bg = "#3b3427" if is_dark else "#faf4e9"
    tab_hover_text = "#e7d9b8" if is_dark else "#005999"
    tab_accent = "#c9a968" if is_dark else "#007acc"

    return f"""
        QTabWidget::pane {{
            border: 1px solid {tab_border};
            background-color: {tab_pane_bg};
            top: -1px;
        }}
        QTabBar::tab {{
            background-color: {tab_bg};
            color: {tab_text};
            border: 1px solid {tab_border};
            border-bottom: none;
            border-top-left-radius: 5px;
            border-top-right-radius: 5px;
            padding: 8px 18px;
            margin-right: 2px;
            font-size: 13px;
        }}
        QTabBar::tab:hover {{
            background-color: {tab_hover_bg};
            color: {tab_hover_text};
            border-color: {tab_accent};
        }}
        QTabBar::tab:selected {{
            background-color: {tab_pane_bg};
            color: {tab_accent};
            font-weight: bold;
            border-color: {tab_border};
            border-top: 2px solid {tab_accent};
        }}
    """


def obter_estilo_btn_analyze(is_dark: bool, enabled: bool = True) -> str:
    disabled_bg = "#2b2b2d" if is_dark else "#eee5d8"
    disabled_text = "#666666" if is_dark else "#a39483"
    disabled_border = "#383838" if is_dark else "#e1d5c5"

    if not enabled:
        return f"""
            QPushButton {{
                background-color: {disabled_bg};
                color: {disabled_text};
                border: 2px solid {disabled_border};
                border-radius: 6px;
                font-weight: bold;
                font-size: 15px;
            }}
            QPushButton:hover {{
                background-color: {disabled_bg};
                color: {disabled_text};
                border: 2px solid {disabled_border};
            }}
        """

    btn_bg = "#80652e" if is_dark else "#007acc"
    btn_hover_bg = "#98793a" if is_dark else "#0098ff"
    btn_hover_border = "#c9a968" if is_dark else "#38bdf8"
    btn_pressed_bg = "#604a22" if is_dark else "#005999"
    btn_pressed_border = "#8f743f" if is_dark else "#004c80"
    btn_text = "#f3e7c8" if is_dark else "#ffffff"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: {btn_text};
            font-weight: bold;
            font-size: 15px;
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: {btn_text};
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: {btn_text};
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 2px solid {disabled_border};
        }}
    """


def obter_estilo_btn_convert(is_dark: bool, font_size: int | None = 15) -> str:
    disabled_bg = "#2b2b2d" if is_dark else "#eee5d8"
    disabled_text = "#666666" if is_dark else "#a39483"
    font_size_rule = f"font-size: {font_size}px;" if font_size is not None else ""
    btn_bg = "#45664d" if is_dark else "#2b8a3e"
    btn_hover_bg = "#54795c" if is_dark else "#37b24d"
    btn_hover_border = "#9ebc93" if is_dark else "#69db7c"
    btn_pressed_bg = "#344d3b" if is_dark else "#237032"
    btn_pressed_border = "#45664d" if is_dark else "#2b8a3e"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: #ffffff;
            font-weight: bold;
            {font_size_rule}
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 2px solid transparent;
        }}
    """


def obter_estilo_btn_cancel(is_dark: bool = True, font_size: int | None = 15) -> str:
    font_size_rule = f"font-size: {font_size}px;" if font_size is not None else ""
    btn_bg = "#783d35" if is_dark else "#d32f2f"
    btn_hover_bg = "#914a40" if is_dark else "#ef5350"
    btn_hover_border = "#d98776" if is_dark else "#ff8a80"
    btn_pressed_bg = "#5c2f29" if is_dark else "#c62828"
    btn_pressed_border = "#783d35" if is_dark else "#d32f2f"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: #ffffff;
            font-weight: bold;
            {font_size_rule}
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: #ffffff;
        }}
    """


def obter_estilo_btn_mux(is_dark: bool) -> str:
    disabled_bg = "#2b2b2d" if is_dark else "#eee5d8"
    disabled_text = "#666666" if is_dark else "#a39483"
    btn_bg = "#62503a" if is_dark else "#6d28d9"
    btn_hover_bg = "#786247" if is_dark else "#8b5cf6"
    btn_hover_border = "#c9a968" if is_dark else "#c4b5fd"
    btn_pressed_bg = "#493b2b" if is_dark else "#5b21b6"
    btn_pressed_border = "#62503a" if is_dark else "#6d28d9"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: #ffffff;
            font-weight: bold;
            font-size: 15px;
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 2px solid transparent;
        }}
    """


def obter_estilo_btn_segmented(is_dark: bool) -> str:
    disabled_bg = "#2b2b2d" if is_dark else "#eee5d8"
    disabled_text = "#666666" if is_dark else "#a39483"
    btn_bg = "#585e61" if is_dark else "#0d9488"
    btn_hover_bg = "#6a7175" if is_dark else "#14b8a6"
    btn_hover_border = "#b9c0c4" if is_dark else "#5eead4"
    btn_pressed_bg = "#454a4d" if is_dark else "#0f766e"
    btn_pressed_border = "#585e61" if is_dark else "#0d9488"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: #ffffff;
            font-weight: bold;
            font-size: 15px;
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 2px solid transparent;
        }}
    """


def obter_estilo_btn_sync_audio(is_dark: bool) -> str:
    disabled_bg = "#2b2b2d" if is_dark else "#d88ba8"
    disabled_text = "#666666" if is_dark else "#ffffff"
    btn_bg = "#76552d" if is_dark else "#ea3680"
    btn_hover_bg = "#906837" if is_dark else "#f43f5e"
    btn_hover_border = "#d5b66f" if is_dark else "#fda4af"
    btn_pressed_bg = "#5a3f21" if is_dark else "#be123c"
    btn_pressed_border = "#76552d" if is_dark else "#ea3680"

    return f"""
        QPushButton {{
            background-color: {btn_bg};
            color: #ffffff;
            font-weight: bold;
            font-size: 15px;
            border: 2px solid transparent;
            border-radius: 6px;
        }}
        QPushButton:hover {{
            background-color: {btn_hover_bg};
            border: 2px solid {btn_hover_border};
            color: #ffffff;
        }}
        QPushButton:pressed {{
            background-color: {btn_pressed_bg};
            border: 2px solid {btn_pressed_border};
            color: #ffffff;
        }}
        QPushButton:disabled {{
            background-color: {disabled_bg};
            color: {disabled_text};
            border: 2px solid transparent;
        }}
    """

import sys
import os
import ctypes
import datetime
import re
import threading
import traceback
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSettings
from ui.main_window import MainWindow
from controllers.main_controller import MainController


SYNCLOG_FILE = "SyncLog.log"
LOG_SEPARATOR = "###############################################"
EMOJI_PATTERN = re.compile(
    "[\U0001F000-\U0001FAFF\U00002700-\U000027BF\U0000FE0F\U0000200D]"
)
_crash_log_lock = threading.Lock()


def _registrar_falha_inesperada(exc_type, exc_value, exc_traceback) -> None:
    """Registra exceções não tratadas antes que o processo seja encerrado."""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    trace = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)).strip()
    entry = EMOJI_PATTERN.sub("", (
        f"[CRASH] Falha inesperada em {timestamp}\n"
        f"Tipo: {exc_type.__name__}\n"
        f"Mensagem: {exc_value}\n\n"
        f"Traceback:\n{trace}"
    ))
    try:
        with _crash_log_lock:
            try:
                with open(SYNCLOG_FILE, "r", encoding="utf-8") as file:
                    previous_content = file.read().strip()
            except FileNotFoundError:
                previous_content = ""
            content = (
                f"{entry}\n\n{LOG_SEPARATOR}\n\n{previous_content}"
                if previous_content else entry
            )
            with open(SYNCLOG_FILE, "w", encoding="utf-8") as file:
                file.write(content)
    except OSError:
        pass


def _instalar_registro_global_de_falhas() -> None:
    """Captura erros da interface e de threads Python no SyncLog.log."""
    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def handle_exception(exc_type, exc_value, exc_traceback):
        if not issubclass(exc_type, KeyboardInterrupt):
            _registrar_falha_inesperada(exc_type, exc_value, exc_traceback)
        original_sys_hook(exc_type, exc_value, exc_traceback)

    def handle_thread_exception(args):
        if not issubclass(args.exc_type, KeyboardInterrupt):
            _registrar_falha_inesperada(
                args.exc_type,
                args.exc_value,
                args.exc_traceback,
            )
        original_thread_hook(args)

    sys.excepthook = handle_exception
    threading.excepthook = handle_thread_exception

def resource_path(relative_path):
    """ Retorna o caminho absoluto para o recurso, funcionando no dev e no PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def main():
    _instalar_registro_global_de_falhas()
    if os.name == 'nt':
        # Cria um ID único para o seu aplicativo no Windows
        myappid = 'vicio.audiosync.cdd.1.0'
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    # Buscando o arquivo .png
    icon_path = resource_path("icone.png")
    app_icon = QIcon(icon_path)
    if app_icon.isNull():
        QMessageBox.warning(
            None,
            "Erro de Ícone",
            f"O ícone da barra superior não pôde ser carregado.\nVerifique se este caminho está correto:\n{icon_path}"
        )
    app.setWindowIcon(app_icon)
    window = MainWindow()
    window.setWindowIcon(app_icon)
    # Aplica o tema salvo pelo usuário (padrão: escuro na primeira execução)
    tema_salvo = QSettings("Vicio", "DublaSync").value("tema", "escuro")
    if tema_salvo == "claro":
        window.aplicar_tema_claro()
    else:
        window.aplicar_tema_escuro()
    controller = MainController(window)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

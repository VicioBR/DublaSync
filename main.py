import sys
import os
import ctypes
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtGui import QIcon
from PySide6.QtCore import QSettings
from ui.main_window import MainWindow
from controllers.main_controller import MainController

def resource_path(relative_path):
    """ Retorna o caminho absoluto para o recurso, funcionando no dev e no PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def main():
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
import os
import subprocess
import sys


def build_executable():
    print("Iniciando compilação do PyInstaller...")

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",  # Se quiser gerar um único .exe, troque por "--onefile"
        "--windowed",
        "--name", "DublaSync",
        "--icon", "icone.ico",
        "--add-data", f"icone.png{os.pathsep}.",
        "--add-data", f"ui{os.pathsep}ui",
        "--add-data", f"utils{os.pathsep}utils",
        "--add-data", f"workers{os.pathsep}workers",
        "--add-data", f"controllers{os.pathsep}controllers",
        "main.py"
    ]

    try:
        subprocess.run(cmd, check=True)
        print("\n✅ Build finalizado com sucesso!")
        print("📁 O programa completo está na pasta: 'dist/DublaSync/'")
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erro durante o build: {e}")


if __name__ == "__main__":
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)
    build_executable()
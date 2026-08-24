"""Ferramentas de detecção, verificação e instalação do FFmpeg (Windows)."""
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Callable, Dict, Optional
from urllib.request import Request, urlopen

try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

# ID do pacote no Winget (build completa e ESTÁVEL do gyan.dev)
WINGET_ID: str = "Gyan.FFmpeg"
# Build de fallback ESTÁVEL oficial (Gyan.dev Release Full - inclui librubberband)
FALLBACK_URL: str = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-full.7z"
USER_AGENT: str = "DublaSync/1.0"


def _creationflags() -> int:
    """Evita a abertura de janela de console no Windows."""
    return subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def salvar_caminho_ffmpeg(bin_dir: str) -> None:
    """Grava a pasta do FFmpeg nas configurações do app (lembrada na próxima vez)."""
    try:
        from PySide6.QtCore import QSettings
        QSettings("Vicio", "DublaSync").setValue("ffmpeg_bin", str(bin_dir))
    except Exception:
        pass


def caminho_salvo_ffmpeg() -> Optional[str]:
    """Lê a pasta do FFmpeg salva anteriormente (ou None)."""
    try:
        from PySide6.QtCore import QSettings
        v = QSettings("Vicio", "DublaSync").value("ffmpeg_bin", "")
        return str(v) if v else None
    except Exception:
        return None


def winget_disponivel() -> bool:
    """Verifica se o Winget existe no sistema."""
    return shutil.which("winget") is not None


def _candidate_bins() -> list:
    """Lista pastas candidatas fora do PATH (salva, WinGet, pasta local e comuns)."""
    bins = []
    roots = []
    salvo = caminho_salvo_ffmpeg()
    if salvo:
        roots.append(Path(salvo))
    local = os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog64 = os.environ.get("ProgramW6432", prog)
    user = os.environ.get("USERPROFILE", "")
    if local:
        roots.append(Path(local) / "DublaSync" / "ffmpeg")
        roots.append(Path(local) / "Microsoft" / "WinGet" / "Packages")
    roots.append(Path(prog) / "ffmpeg")
    if Path(prog64) not in [Path(r) for r in roots]:
        roots.append(Path(prog64) / "ffmpeg")
    roots.append(Path(r"C:\ffmpeg"))
    if user:
        roots.append(Path(user) / "ffmpeg")
    for root in roots:
        if not root.exists():
            continue
        if (root / "bin" / "ffmpeg.exe").exists():
            bins.append(root / "bin")
            continue
        try:
            for exe in root.rglob("ffmpeg.exe"):
                bins.append(exe.parent)
                break
        except Exception:
            pass
    return bins


def find_ffmpeg_exe() -> Optional[str]:
    """Retorna o caminho do ffmpeg (PATH, caminho salvo ou pastas conhecidas)."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    for bin_dir in _candidate_bins():
        exe = bin_dir / "ffmpeg.exe"
        if exe.exists():
            return str(exe)
    return None


def refresh_path() -> Optional[str]:
    """Adiciona ao PATH do processo a pasta bin do FFmpeg encontrada fora do PATH."""
    if shutil.which("ffmpeg"):
        return None
    for bin_dir in _candidate_bins():
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        return str(bin_dir)
    return None


def add_to_user_path(bin_dir: str) -> bool:
    """Adiciona a pasta ao PATH do USUÁRIO do Windows de forma PERMANENTE."""
    if os.name != 'nt':
        return False
    try:
        import winreg
        import ctypes
        bin_dir = str(bin_dir)
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                            winreg.KEY_READ | winreg.KEY_WRITE) as key:
            try:
                value, _ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                value = ""
            parts = [p for p in str(value).split(os.pathsep) if p.strip()]
            if bin_dir.lower().rstrip("\\") not in (p.lower().rstrip("\\") for p in parts):
                parts.append(bin_dir)
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ,
                                  os.pathsep.join(parts))
        result = ctypes.c_ulong()
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result)
        )
        atual = [p.lower().rstrip("\\") for p in os.environ.get("PATH", "").split(os.pathsep)]
        if bin_dir.lower().rstrip("\\") not in atual:
            os.environ["PATH"] = bin_dir + os.pathsep + os.environ.get("PATH", "")
        return True
    except Exception:
        return False


def get_ffmpeg_version(exe: str = "ffmpeg") -> Optional[str]:
    """Retorna a versão do FFmpeg (primeira linha do 'ffmpeg -version')."""
    try:
        result = subprocess.run([exe, "-version"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15,
                                creationflags=_creationflags())
        first = (result.stdout or "").splitlines()[0].strip()
        match = re.search(r"ffmpeg version\s+([^\s]+)", first)
        return match.group(1) if match else (first or None)
    except Exception:
        return None


def has_rubberband(exe: str = "ffmpeg") -> bool:
    """Verifica se o filtro 'rubberband' está disponível."""
    try:
        result = subprocess.run([exe, "-hide_banner", "-filters"], capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=15, creationflags=_creationflags())
        return bool(re.search(r"\brubberband\b", result.stdout))
    except Exception:
        return False


def verify() -> Dict[str, object]:
    """Verificação completa: instalação, caminho, versão e suporte ao rubberband."""
    exe = find_ffmpeg_exe()
    if not exe:
        return {"installed": False, "ok": False, "path": None,
                "version": None, "rubberband": False}
    version = get_ffmpeg_version(exe)
    rubberband = has_rubberband(exe)
    return {"installed": True, "ok": bool(version) and rubberband,
            "path": exe, "version": version, "rubberband": rubberband}


def download_file(url: str, dest: Path,
                  progress_cb: Optional[Callable[[int], None]] = None,
                  cancel_cb: Optional[Callable[[], bool]] = None) -> None:
    """Baixa um arquivo com callback de progresso (0-99) e suporte a cancelamento."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response, open(dest, "wb") as out:
        total = int(response.headers.get("Content-Length", 0))
        done = 0
        while True:
            if cancel_cb and cancel_cb():
                raise InterruptedError("Download cancelado pelo usuário.")
            chunk = response.read(262144)
            if not chunk:
                break
            out.write(chunk)
            done += len(chunk)
            if progress_cb and total > 0:
                progress_cb(min(99, int(done * 100 / total)))


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extrai um ZIP ou 7Z para a pasta de destino."""
    nome = str(archive_path).lower()
    if nome.endswith(".7z"):
        if not HAS_PY7ZR:
            raise RuntimeError("Biblioteca 'py7zr' não encontrada. Rode 'pip install py7zr'.")
        with py7zr.SevenZipFile(archive_path, mode='r') as z:
            z.extractall(path=dest_dir)
    elif nome.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as z:
            z.extractall(dest_dir)
    else:
        raise ValueError("Formato de arquivo não suportado.")
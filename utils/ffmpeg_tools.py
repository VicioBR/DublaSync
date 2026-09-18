"""Ferramentas de detecção, verificação e instalação do FFmpeg (Windows)."""
import os
import re
import shutil
import subprocess
import zipfile
import hashlib
import hmac
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
# Build de fallback ESTÁVEL oficial. A variante Essentials inclui librubberband
# e é disponibilizada em ZIP, formato extraído nativamente pelo Python. O
# pacote Full é apenas .7z e usa BCJ2, filtro incompatível com o py7zr.
FALLBACK_URL: str = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
# O fornecedor publica o SHA-256 ao lado de cada arquivo de build. Validá-lo
# evita extrair um download corrompido ou incompleto antes de usar o FFmpeg.
FALLBACK_SHA256_URL: str = f"{FALLBACK_URL}.sha256"
USER_AGENT: str = "DublaSync/1.0"


def _creationflags() -> int:
    """Retorna flags para evitar uma janela de console no Windows."""
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def run_ffmpeg_subprocess(cmd: list, timeout: int = 60, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    """Wrapper centralizado para chamadas do FFmpeg/FFprobe.
    Aplica automaticamente flags para ocultar janela no Windows, encoding UTF-8 e tratamento de erros."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        creationflags=_creationflags(),
        check=check
    )

def salvar_caminho_ffmpeg(bin_dir: str) -> None:
    """Grava a pasta do FFmpeg nas configurações do app (lembrada na próxima vez)."""
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("Vicio", "DublaSync")
        settings.setValue("ffmpeg_bin", str(bin_dir))
        settings.remove("ffmpeg_requer_configuracao")
        settings.sync()
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


def remover_caminho_ffmpeg() -> None:
    """Esquece o caminho salvo e exige uma nova configuração no DublaSync."""
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("Vicio", "DublaSync")
        settings.remove("ffmpeg_bin")
        settings.setValue("ffmpeg_requer_configuracao", True)
        settings.sync()
    except Exception:
        pass


def ffmpeg_requer_configuracao() -> bool:
    """Indica que o usuário redefiniu a configuração do FFmpeg no DublaSync."""
    try:
        from PySide6.QtCore import QSettings
        valor = QSettings("Vicio", "DublaSync").value(
            "ffmpeg_requer_configuracao", False
        )
        return str(valor).strip().lower() in {"true", "1", "yes", "sim"}
    except Exception:
        return False


def marcar_ffmpeg_configurado() -> None:
    """Registra que uma nova configuração do FFmpeg foi concluída."""
    try:
        from PySide6.QtCore import QSettings
        settings = QSettings("Vicio", "DublaSync")
        settings.remove("ffmpeg_requer_configuracao")
        settings.sync()
    except Exception:
        pass

def winget_disponivel() -> bool:
    """Verifica se o Winget existe no sistema."""
    return shutil.which("winget") is not None

def _ffmpeg_in_bin(bin_dir: str | Path) -> Optional[str]:
    """Retorna o executável diretamente de uma pasta ``bin`` conhecida."""
    try:
        base = Path(bin_dir)
        for filename in ("ffmpeg.exe", "ffmpeg"):
            candidate = base / filename
            if candidate.is_file():
                return str(candidate)
    except OSError:
        return None
    return None


def find_ffmpeg_bin(directory: str | Path) -> Optional[str]:
    """Localiza uma pasta ``bin`` que contenha um executável FFmpeg.

    A busca é limitada à pasta indicada, o que permite validar exatamente a
    instalação que acabou de ser escolhida ou extraída, sem reutilizar uma
    configuração antiga salva pelo aplicativo.
    """
    try:
        root = Path(directory)
        if not root.is_dir():
            return None
        for candidate in (root, root / "bin"):
            if _ffmpeg_in_bin(candidate):
                return str(candidate)
        for executable in root.rglob("ffmpeg.exe"):
            if executable.is_file():
                return str(executable.parent)
    except OSError:
        return None
    return None


def find_ffmpeg_exe_in(directory: str | Path) -> Optional[str]:
    """Retorna o executável FFmpeg encontrado dentro de ``directory``."""
    bin_dir = find_ffmpeg_bin(directory)
    return _ffmpeg_in_bin(bin_dir) if bin_dir else None


def _candidate_bins(include_saved: bool = True) -> list:
    """Lista pastas candidatas fora do PATH (salva, WinGet, pasta local e comuns)."""
    bins = []
    roots = []
    salvo = caminho_salvo_ffmpeg()
    if include_saved and salvo:
        roots.append(Path(salvo))

    local = os.environ.get("LOCALAPPDATA", "")
    prog = os.environ.get("ProgramFiles", r"C:\Program Files")
    prog64 = os.environ.get("ProgramW6432", prog)
    user = os.environ.get("USERPROFILE", "")

    if local:
        roots.append(Path(local) / "Microsoft" / "WinGet" / "Packages")
        roots.append(Path(local) / "DublaSync" / "ffmpeg")
    roots.append(Path(prog) / "ffmpeg")
    if Path(prog64) not in roots:
        roots.append(Path(prog64) / "ffmpeg")
    roots.append(Path(r"C:\ffmpeg"))
    if user:
        roots.append(Path(user) / "ffmpeg")

    for root in roots:
        bin_dir = find_ffmpeg_bin(root)
        if bin_dir:
            bins.append(Path(bin_dir))
    return bins

def _prioritize_process_path(bin_dir: str | Path) -> str:
    """Move uma pasta válida para o início do PATH apenas desta execução."""
    normalized = str(Path(bin_dir))
    current = os.environ.get("PATH", "")
    key = normalized.lower().rstrip("\\/")
    entries = [entry for entry in current.split(os.pathsep) if entry.strip()]
    entries = [entry for entry in entries if entry.lower().rstrip("\\/") != key]
    os.environ["PATH"] = os.pathsep.join([normalized, *entries])
    return normalized


def find_ffmpeg_exe() -> Optional[str]:
    """Retorna o FFmpeg configurado pelo usuário antes de consultar o PATH."""
    saved = caminho_salvo_ffmpeg()
    if saved:
        configured = _ffmpeg_in_bin(saved)
        if configured:
            return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    for bin_dir in _candidate_bins():
        exe = _ffmpeg_in_bin(bin_dir)
        if exe:
            return exe
    return None

def refresh_path(prefer_candidate: bool = False) -> Optional[str]:
    """Prioriza no PATH a configuração salva ou uma instalação recém-localizada.

    ``prefer_candidate`` é usado após instalar o FFmpeg: mesmo que exista uma
    versão antiga no PATH, uma instalação encontrada nas pastas conhecidas
    passa a ser verificada nesta sessão.
    """
    # Após uma instalação, não deixe uma rota salva antiga mascarar o novo
    # executável. O chamador valida e persiste o candidato somente se ele for
    # de fato compatível com o DublaSync.
    if prefer_candidate:
        for bin_dir in _candidate_bins(include_saved=False):
            if _ffmpeg_in_bin(bin_dir):
                return _prioritize_process_path(bin_dir)

    saved = caminho_salvo_ffmpeg()
    if saved and _ffmpeg_in_bin(saved):
        return _prioritize_process_path(saved)
    if not prefer_candidate and shutil.which("ffmpeg"):
        return None
    for bin_dir in _candidate_bins():
        if _ffmpeg_in_bin(bin_dir):
            return _prioritize_process_path(bin_dir)
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
                winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, os.pathsep.join(parts))
            
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
        result = run_ffmpeg_subprocess([exe, "-version"])
        first = (result.stdout or "").splitlines()[0].strip()
        match = re.search(r"ffmpeg version\s+([^\s]+)", first)
        return match.group(1) if match else (first or None)
    except Exception:
        return None

def has_rubberband(exe: str = "ffmpeg") -> bool:
    """Verifica se o filtro 'rubberband' está disponível."""
    try:
        result = run_ffmpeg_subprocess([exe, "-hide_banner", "-filters"])
        return bool(re.search(r"\brubberband\b", result.stdout))
    except Exception:
        return False


def has_ffprobe(ffmpeg_exe: str) -> bool:
    """Confirma que o FFprobe correspondente está disponível e executável."""
    executable = Path(ffmpeg_exe)
    probe_name = "ffprobe.exe" if executable.suffix.lower() == ".exe" else "ffprobe"
    ffprobe_exe = executable.with_name(probe_name)
    if not ffprobe_exe.is_file():
        return False
    try:
        result = run_ffmpeg_subprocess([str(ffprobe_exe), "-version"])
        first_line = (result.stdout or "").splitlines()[0].lower()
        return "ffprobe version" in first_line
    except Exception:
        return False


def verify(executable_path: Optional[str] = None) -> Dict[str, object]:
    """Verifica um FFmpeg específico ou a instalação atualmente configurada."""
    exe = executable_path or find_ffmpeg_exe()
    if executable_path and not Path(executable_path).is_file():
        exe = None
    if not exe:
        return {
            "installed": False, "ok": False, "path": None, "version": None,
            "rubberband": False, "ffprobe": False,
        }
    version = get_ffmpeg_version(exe)
    rubberband = has_rubberband(exe)
    ffprobe = has_ffprobe(exe)
    return {"installed": True, "ok": bool(version) and rubberband and ffprobe,
            "path": exe, "version": version, "rubberband": rubberband,
            "ffprobe": ffprobe}

def obter_checksum_sha256(url: str) -> str:
    """Lê e normaliza o SHA-256 publicado pelo fornecedor do arquivo."""
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")
    match = re.search(r"\b([a-fA-F0-9]{64})\b", text)
    if not match:
        raise RuntimeError("O fornecedor não publicou um SHA-256 válido para o arquivo.")
    return match.group(1).lower()


def verificar_checksum_sha256(path: Path, esperado: str) -> None:
    """Confirma a integridade de um arquivo antes de extraí-lo."""
    esperado_normalizado = (esperado or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", esperado_normalizado):
        raise ValueError("SHA-256 esperado inválido.")
    digest = hashlib.sha256()
    with open(path, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    if not hmac.compare_digest(digest.hexdigest(), esperado_normalizado):
        raise RuntimeError("A verificação de integridade do download do FFmpeg falhou.")


def download_file(url: str, dest: Path,
                  progress_cb: Optional[Callable[[int], None]] = None,
                  cancel_cb: Optional[Callable[[], bool]] = None,
                  expected_sha256: Optional[str] = None) -> None:
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
    if expected_sha256:
        verificar_checksum_sha256(dest, expected_sha256)

def _validate_archive_members(member_names, dest_dir: Path) -> None:
    """Impede arquivos de arquivo compactado de escaparem da pasta destino."""
    destination = dest_dir.resolve()
    for name in member_names:
        target = (dest_dir / name).resolve()
        try:
            target.relative_to(destination)
        except ValueError as exc:
            raise RuntimeError("Arquivo compactado contém um caminho inseguro.") from exc


def extract_archive(archive_path: Path, dest_dir: Path) -> None:
    """Extrai um ZIP ou 7Z para a pasta de destino."""
    nome = str(archive_path).lower()
    if nome.endswith(".7z"):
        if not HAS_PY7ZR:
            raise RuntimeError("Biblioteca 'py7zr' não encontrada. Rode 'pip install py7zr'.")
        with py7zr.SevenZipFile(archive_path, mode='r') as z:
            _validate_archive_members(z.getnames(), dest_dir)
            z.extractall(path=dest_dir)
    elif nome.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as z:
            _validate_archive_members((item.filename for item in z.infolist()), dest_dir)
            z.extractall(dest_dir)
    else:
        raise ValueError("Formato de arquivo não suportado.")

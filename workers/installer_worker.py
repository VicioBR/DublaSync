"""Threads de verificação e instalação do FFmpeg — mantêm a interface responsiva."""
import os
import subprocess
import tempfile
from pathlib import Path
from PySide6.QtCore import QThread, Signal
from utils import ffmpeg_tools as ft
from utils.translations import tr


class FFmpegVerifier(QThread):
    """Verifica FFmpeg (presença, versão, caminho e rubberband) em segundo plano."""
    finished = Signal(dict)

    def run(self) -> None:
        self.finished.emit(ft.verify())


class FFmpegInstaller(QThread):
    """Instala o FFmpeg (Winget e/ou build de fallback ESTÁVEL) em segundo plano."""
    progress = Signal(str, int)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, use_fallback: bool = True):
        super().__init__()
        self.use_fallback = use_fallback
        self._cancelled = False
        self.process = None

    def cancel(self) -> None:
        self._cancelled = True
        if self.process:
            self.process.terminate()

    def run(self) -> None:
        try:
            if ft.winget_disponivel():
                self._install_winget()
            else:
                self.progress.emit(tr("iw_winget_nao_encontrado"), 5)
            if self._cancelled:
                self.error.emit(tr("instalacao_cancelada"))
                return

            # O código de saída do Winget não garante que o executável que o
            # app usará contém rubberband. Validamos o candidato recém-instalado.
            result = self._verify_new_candidate()
            if not result["ok"] and self.use_fallback:
                fallback_bin = self._install_fallback()
                if self._cancelled:
                    self.error.emit(tr("instalacao_cancelada"))
                    return
                result = ft.verify(ft.find_ffmpeg_exe_in(fallback_bin))
            if self._cancelled:
                self.error.emit(tr("instalacao_cancelada"))
                return
            if not result["ok"]:
                raise RuntimeError(
                    "Nenhuma instalação compatível do FFmpeg com o filtro rubberband foi encontrada."
                )

            self.progress.emit(tr("iw_atualizando_path"), 95)
            bin_dir = str(Path(result["path"]).parent)
            ft.salvar_caminho_ffmpeg(bin_dir)
            ft.refresh_path()
            if ft.add_to_user_path(bin_dir):
                self.progress.emit(tr("iw_path_atualizado"), 97)
            self.progress.emit(tr("verificacao_concluida"), 100)
            self.finished.emit(result)
        except InterruptedError:
            self.error.emit(tr("instalacao_cancelada"))
        except PermissionError:
            self.error.emit(tr("iw_sem_permissao"))
        except Exception as e:
            if "URL" in type(e).__name__:
                self.error.emit(tr("iw_falha_rede").format(erro=e))
            else:
                self.error.emit(tr("iw_falha_instalacao").format(erro=e))

    @staticmethod
    def _verify_new_candidate() -> dict:
        """Valida um candidato novo sem deixar uma rota salva antiga vencê-lo."""
        bin_dir = ft.refresh_path(prefer_candidate=True)
        executable = ft.find_ffmpeg_exe_in(bin_dir) if bin_dir else None
        return ft.verify(executable) if executable else ft.verify()

    def _install_winget(self) -> bool:
        self.progress.emit(tr("iw_instalando_winget").format(id=ft.WINGET_ID), 10)
        cmd = ["winget", "install", "--id", ft.WINGET_ID, "-e",
               "--accept-source-agreements", "--accept-package-agreements",
               "--disable-interactivity", "--silent"]
        self.process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, encoding="utf-8", errors="replace",
            creationflags=ft._creationflags()
        )
        for line in self.process.stdout:
            if self._cancelled:
                break
            line = line.strip()
            if line:
                self.progress.emit(line, 40)
        self.process.wait()
        code = self.process.returncode
        self.process = None
        if self._cancelled:
            return False
        if code == 0:
            self.progress.emit(tr("iw_winget_concluiu"), 60)
            return True
        self.progress.emit(tr("iw_winget_codigo").format(code=code), 50)
        return False

    def _install_fallback(self) -> str:
        self.progress.emit(tr("iw_baixando_build"), 55)
        base = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
        dest_dir = base / "DublaSync" / "ffmpeg"
        dest_dir.mkdir(parents=True, exist_ok=True)
        archive_name = ft.FALLBACK_URL.split("/")[-1]
        archive_path = dest_dir / archive_name
        expected_sha256 = ft.obter_checksum_sha256(ft.FALLBACK_SHA256_URL)
        ft.download_file(
            ft.FALLBACK_URL, archive_path,
            progress_cb=lambda p: self.progress.emit(tr("iw_baixando"), 55 + int(p * 0.35)),
            cancel_cb=lambda: self._cancelled,
            expected_sha256=expected_sha256,
        )
        self.progress.emit(tr("iw_extraindo"), 92)
        # Nunca misture uma extração nova com releases anteriores: além de
        # evitar arquivos residuais, isto assegura que validaremos o build
        # baixado nesta execução, e não um executável antigo na mesma pasta.
        extract_dir = Path(tempfile.mkdtemp(prefix="release-", dir=dest_dir))
        ft.extract_archive(archive_path, extract_dir)
        try:
            archive_path.unlink(missing_ok=True)
        except Exception:
            pass
        bin_dir = ft.find_ffmpeg_bin(extract_dir)
        if not bin_dir:
            raise RuntimeError("A instalação de fallback não contém ffmpeg.exe.")
        return bin_dir

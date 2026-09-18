"""Integração isolada e multiplataforma com o executável ``mkvmerge``.

Este módulo concentra configuração, validação, identificação de faixas e
muxação MKV. Ele não altera nem depende da lógica de sincronização do
DublaSync: recebe apenas os arquivos e o offset final já calculado.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
import re
import subprocess
from typing import Optional
from uuid import uuid4

from PySide6.QtCore import QSettings, QThread, Signal


SETTINGS_KEY = "mkvmerge_path"


@dataclass(frozen=True)
class MkvmergeValidation:
    """Estado normalizado da configuração do MKVToolNix."""

    path: Optional[str]
    valid: bool
    version: Optional[str] = None
    error: str = ""


@dataclass(frozen=True)
class MkvmergeResult:
    """Resultado normalizado de uma tentativa de muxação com mkvmerge."""

    success: bool
    output_path: str
    cancelled: bool = False
    returncode: Optional[int] = None
    message: str = ""
    details: str = ""
    command: tuple[str, ...] = field(default_factory=tuple)


def _creationflags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _output_text(result: subprocess.CompletedProcess) -> str:
    return "\n".join(part for part in (result.stdout, result.stderr) if part).strip()


def _technical_tail(text: str, limit: int = 3000) -> str:
    text = (text or "").strip()
    return text[-limit:] if len(text) > limit else text


def caminho_configurado_mkvmerge() -> Optional[str]:
    """Retorna o caminho salvo pelo usuário, sem assumir localização padrão."""

    try:
        value = QSettings("Vicio", "DublaSync").value(SETTINGS_KEY, "")
        return str(value).strip() or None
    except Exception:
        return None


def salvar_caminho_mkvmerge(executable_path: str) -> None:
    """Persiste somente o executável validado de mkvmerge."""

    QSettings("Vicio", "DublaSync").setValue(SETTINGS_KEY, str(executable_path))


def localizar_mkvmerge(directory: Optional[str]) -> Optional[str]:
    """Localiza mkvmerge na pasta escolhida pelo usuário, sem caminhos fixos.

    Instalações tradicionais costumam conter o executável na própria pasta
    selecionada, enquanto distribuições portáteis podem concentrá-lo em
    ``bin``. A validação posterior confirma que o candidato é executável.
    """

    if not directory:
        return None
    base = Path(str(directory)).expanduser()
    if not base.is_dir():
        return None
    for folder in (base, base / "bin"):
        for filename in ("mkvmerge.exe", "mkvmerge"):
            candidate = folder / filename
            if candidate.is_file():
                return str(candidate)
    return None


def validar_mkvmerge(executable_path: Optional[str], timeout: int = 10) -> MkvmergeValidation:
    """Valida o executável executando ``mkvmerge --version`` de forma segura."""

    if not executable_path:
        return MkvmergeValidation(None, False, error="MKVToolNix não configurado.")

    path = Path(str(executable_path)).expanduser()
    if not path.is_file():
        return MkvmergeValidation(str(path), False, error="O executável informado não existe.")

    try:
        result = subprocess.run(
            [str(path), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
            creationflags=_creationflags(),
        )
    except subprocess.TimeoutExpired:
        return MkvmergeValidation(str(path), False, error="Tempo limite excedido ao validar o executável.")
    except OSError as exc:
        return MkvmergeValidation(str(path), False, error=f"Não foi possível executar o arquivo: {exc}")

    output = _output_text(result)
    if result.returncode != 0:
        detail = _technical_tail(output) or f"Código de saída {result.returncode}."
        return MkvmergeValidation(str(path), False, error=detail)
    if "mkvmerge" not in output.lower():
        return MkvmergeValidation(
            str(path),
            False,
            error="O arquivo executado não foi identificado como mkvmerge.",
        )

    first_line = next((line.strip() for line in output.splitlines() if line.strip()), "mkvmerge")
    match = re.search(r"mkvmerge(?:\s+v(?:ersion)?\s*|\s+version\s+)(.+)", first_line, re.IGNORECASE)
    version = match.group(1).strip() if match else first_line
    return MkvmergeValidation(str(path), True, version=version)


def validar_mkvmerge_configurado() -> MkvmergeValidation:
    """Revalida o caminho salvo antes de iniciar cada muxação MKV."""

    return validar_mkvmerge(caminho_configurado_mkvmerge())


def _identificar_faixas(executable_path: str, media_path: str) -> list[dict]:
    """Obtém IDs reais de faixas para aplicar opções por arquivo corretamente."""

    try:
        result = subprocess.run(
            [executable_path, "-J", media_path],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            creationflags=_creationflags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Tempo limite ao identificar '{Path(media_path).name}'.") from exc
    except OSError as exc:
        raise RuntimeError(f"Não foi possível executar mkvmerge: {exc}") from exc

    if result.returncode != 0:
        detail = _technical_tail(_output_text(result)) or f"Código {result.returncode}."
        raise RuntimeError(f"Não foi possível identificar '{Path(media_path).name}': {detail}")
    try:
        payload = json.loads(result.stdout)
        tracks = payload.get("tracks", [])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Resposta inválida do mkvmerge ao identificar '{Path(media_path).name}'.") from exc
    if not isinstance(tracks, list):
        raise RuntimeError(f"Nenhuma faixa foi identificada em '{Path(media_path).name}'.")
    return tracks


def _first_track_id(tracks: list[dict], track_type: str, path: str) -> int:
    for track in tracks:
        if track.get("type") == track_type and isinstance(track.get("id"), int):
            return track["id"]
    raise RuntimeError(f"Nenhuma faixa de {track_type} encontrada em '{Path(path).name}'.")


def _audio_track_id(tracks: list[dict], audio_index: int, path: str) -> int:
    """Retorna o ID do mkvmerge para a enésima faixa de áudio do arquivo."""

    audio_tracks = [
        track for track in tracks
        if track.get("type") == "audio" and isinstance(track.get("id"), int)
    ]
    try:
        index = int(audio_index)
    except (TypeError, ValueError):
        index = 0
    if 0 <= index < len(audio_tracks):
        return audio_tracks[index]["id"]
    raise RuntimeError(
        f"A faixa de áudio selecionada ({index + 1}) não existe em '{Path(path).name}'."
    )


def _working_output_path(output_path: str) -> Path:
    """Cria um nome temporário no mesmo volume para evitar arquivo final parcial."""

    final_path = Path(output_path)
    return final_path.with_name(f".{final_path.stem}.mkvtoolnix-{uuid4().hex}.mkv")


def construir_comando_mux(
    executable_path: str,
    guide_path: str,
    audio_path: str,
    working_output_path: str,
    offset_seconds: float,
    dubbed_audio_index: int = 0,
) -> list[str]:
    """Monta a muxação equivalente à regra MKV existente do DublaSync.

    A fonte dublada entra primeiro para que sua faixa de áudio seja a primeira
    faixa de áudio de saída, enquanto a ordenação padrão do mkvmerge mantém o
    vídeo do guia antes das faixas de áudio. Do guia são mantidos vídeo,
    áudios originais, legendas, anexos, capítulos e metadados.
    """

    guide_tracks = _identificar_faixas(executable_path, guide_path)
    audio_tracks = _identificar_faixas(executable_path, audio_path)
    guide_video_id = _first_track_id(guide_tracks, "video", guide_path)
    dubbed_audio_id = _audio_track_id(audio_tracks, dubbed_audio_index, audio_path)
    guide_audio_ids = [
        track["id"]
        for track in guide_tracks
        if track.get("type") == "audio" and isinstance(track.get("id"), int)
    ]
    delay_ms = int(round(round(float(offset_seconds), 3) * 1000))

    # Opções antes de cada arquivo afetam exclusivamente aquele arquivo.
    command = [
        executable_path,
        "--gui-mode",
        "--output", working_output_path,
        "--no-video",
        "--no-subtitles",
        "--no-buttons",
        "--no-attachments",
        "--no-chapters",
        "--no-global-tags",
        "--no-track-tags",
        "--audio-tracks", str(dubbed_audio_id),
        "--sync", f"{dubbed_audio_id}:{delay_ms}",
        "--track-name", f"{dubbed_audio_id}:DublaSync",
        "--language", f"{dubbed_audio_id}:por",
        "--default-track-flag", f"{dubbed_audio_id}:1",
        audio_path,
        "--video-tracks", str(guide_video_id),
    ]
    for track_id in guide_audio_ids:
        command.extend(["--default-track-flag", f"{track_id}:0"])
    command.append(guide_path)
    return command


class MkvmergeWorker(QThread):
    """Executa o mkvmerge fora da thread da interface e emite resultado único."""

    progress = Signal(str, float)
    log = Signal(str)
    completed = Signal(object)

    def __init__(
        self,
        executable_path: str,
        guide_path: str,
        audio_path: str,
        output_path: str,
        offset_seconds: float,
        stage_text: str,
        dubbed_audio_index: int = 0,
    ):
        super().__init__()
        self.executable_path = executable_path
        self.guide_path = guide_path
        self.audio_path = audio_path
        self.output_path = output_path
        self.offset_seconds = offset_seconds
        self.stage_text = stage_text
        self.dubbed_audio_index = dubbed_audio_index
        self._cancelled = False
        self.process: Optional[subprocess.Popen] = None

    def cancel(self) -> None:
        self._cancelled = True
        if self.process is not None:
            self.process.terminate()

    def _emit_result(
        self,
        *,
        success: bool,
        working_output: Optional[Path],
        returncode: Optional[int] = None,
        message: str = "",
        details: str = "",
        command: Optional[list[str]] = None,
    ) -> None:
        if working_output and working_output.exists() and not success:
            try:
                working_output.unlink()
            except OSError:
                pass
        self.completed.emit(
            MkvmergeResult(
                success=success,
                output_path=self.output_path,
                cancelled=self._cancelled,
                returncode=returncode,
                message=message,
                details=_technical_tail(details),
                command=tuple(command or ()),
            )
        )

    def run(self) -> None:
        working_output: Optional[Path] = None
        command: list[str] = []
        output_lines: list[str] = []
        try:
            self.log.emit(f"[MKVTOOLNIX] Preparando muxação com: {self.executable_path}")
            working_output = _working_output_path(self.output_path)
            command = construir_comando_mux(
                self.executable_path,
                self.guide_path,
                self.audio_path,
                str(working_output),
                self.offset_seconds,
                self.dubbed_audio_index,
            )
            self.log.emit("[MKVTOOLNIX] Executando mkvmerge.")
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_creationflags(),
            )
            assert self.process.stdout is not None
            for raw_line in self.process.stdout:
                if self._cancelled:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                match = re.search(r"^#GUI#progress\s+(\d+(?:\.\d+)?)%$", line)
                if match:
                    self.progress.emit(self.stage_text, max(0.0, min(100.0, float(match.group(1)))))
                else:
                    output_lines.append(line)
            self.process.wait()
            returncode = self.process.returncode
            self.process = None

            if self._cancelled:
                self._emit_result(
                    success=False,
                    working_output=working_output,
                    returncode=returncode,
                    message="Operação cancelada pelo usuário.",
                    details="\n".join(output_lines),
                    command=command,
                )
                return
            if returncode != 0:
                details = "\n".join(output_lines)
                self.log.emit(f"[MKVTOOLNIX] mkvmerge finalizou com código {returncode}.")
                if details:
                    self.log.emit(f"[MKVTOOLNIX] Detalhes técnicos:\n{_technical_tail(details)}")
                self._emit_result(
                    success=False,
                    working_output=working_output,
                    returncode=returncode,
                    message=f"mkvmerge finalizou com código {returncode}.",
                    details=details,
                    command=command,
                )
                return
            if not working_output.exists() or working_output.stat().st_size == 0:
                self._emit_result(
                    success=False,
                    working_output=working_output,
                    returncode=returncode,
                    message="mkvmerge não produziu um arquivo MKV válido.",
                    details="\n".join(output_lines),
                    command=command,
                )
                return

            os.replace(working_output, self.output_path)
            self.progress.emit(self.stage_text, 100.0)
            self._emit_result(
                success=True,
                working_output=None,
                returncode=returncode,
                command=command,
            )
        except Exception as exc:
            self.process = None
            self.log.emit(f"[MKVTOOLNIX] Falha inesperada: {exc}")
            self._emit_result(
                success=False,
                working_output=working_output,
                message=str(exc),
                details="\n".join(output_lines),
                command=command,
            )

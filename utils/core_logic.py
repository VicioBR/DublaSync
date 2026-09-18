import subprocess
import json
import os
import tempfile
import re
from typing import Optional, Tuple, Dict, Any, List, Set
import numpy as np
from scipy import signal
from scipy.io import wavfile
from utils.ffmpeg_tools import run_ffmpeg_subprocess


def parse_fps_value(fps_str):
    """Converte a fração do FFprobe em número decimal (Float)"""
    if not fps_str or fps_str == "0/0":
        return 0.0
    try:
        num, den = map(int, str(fps_str).split('/'))
        return num / den if den != 0 else 0.0
    except Exception:
        return 0.0


def get_real_video_fps(stream_video):
    """Extrai o FPS real, ignorando taxas inválidas como 90000 Hz"""
    r_fps = parse_fps_value(stream_video.get('r_frame_rate', ''))
    avg_fps = parse_fps_value(stream_video.get('avg_frame_rate', ''))
    if r_fps > 0 and r_fps != 90000:
        return r_fps
    if avg_fps > 0 and avg_fps != 90000:
        return avg_fps
    try:
        nb_frames = float(stream_video.get('nb_frames', 0))
        duration = float(stream_video.get('duration', 0))
        if nb_frames > 0 and duration > 0:
            return nb_frames / duration
    except Exception:
        pass
    return 0.0


def check_dependencies() -> None:
    """Verifica se FFmpeg e FFprobe estão instalados."""
    if not os.name == 'nt':
        import shutil
        if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
            raise RuntimeError("FFmpeg ou FFprobe não encontrados no sistema.")


def get_ffmpeg_audio_encoders() -> Set[str]:
    """Obtém lista de encoders de áudio disponíveis no FFmpeg."""
    result = run_ffmpeg_subprocess(['ffmpeg', '-hide_banner', '-encoders'])
    encoders = set()
    for line in result.stdout.splitlines():
        match = re.match(r'^\s*[A-Z.]{6}\s+(\S+)', line)
        if match and line.lstrip().startswith('A'):
            encoders.add(match.group(1))
    return encoders


def check_rubberband_available() -> bool:
    """Verifica se o filtro rubberband está disponível."""
    result = run_ffmpeg_subprocess(['ffmpeg', '-hide_banner', '-filters'])
    return bool(re.search(r'\brubberband\b', result.stdout))


def get_file_info(filepath: str) -> Tuple[Optional[float], Optional[float], Dict[str, Any], List[Dict]]:
    """Extrai informações do arquivo via FFprobe."""
    if not os.path.exists(filepath):
        raise FileNotFoundError("Arquivo não encontrado.")
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', filepath]
    result = run_ffmpeg_subprocess(cmd)
    data = json.loads(result.stdout)
    duration = float(data.get('format', {}).get('duration', 0))
    fps = None
    audio_streams = []
    for stream in data.get('streams', []):
        if stream.get('codec_type') == 'video':
            if fps is None or fps == 0.0:
                candidate_fps = get_real_video_fps(stream)
                if candidate_fps > 0:
                    fps = candidate_fps
        elif stream.get('codec_type') == 'audio':
            audio_streams.append(stream)
    return duration, fps, data, audio_streams


def format_time(seconds: float) -> str:
    """Formata segundos para HH:MM:SS.mmm"""
    total_ms = max(0, round(seconds * 1000))
    h, remaining_ms = divmod(total_ms, 3_600_000)
    m, remaining_ms = divmod(remaining_ms, 60_000)
    s, ms = divmod(remaining_ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def format_elapsed_time(seconds: float) -> str:
    """Formata segundos decorridos para HH:MM:SS"""
    total_sec = max(0, int(round(seconds)))
    h, rem = divmod(total_sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def extract_audio_chunk(filepath: str, outpath: str, start_time: float = 0.0,
                        duration: float = 360.0, audio_idx: int = 0) -> None:
    """Extrai trecho de áudio em PCM (mono, 8 kHz).

    CORREÇÃO: agora captura a saída do FFmpeg e valida que o WAV resultante
    não ficou vazio. Se algo falhar, lança RuntimeError com mensagem clara,
    em vez de deixar o erro explodir depois como IndexError do NumPy.
    """
    cmd = [
        'ffmpeg', '-y', '-v', 'error', '-ss', f'{start_time:.3f}', '-i', filepath,
        '-map', f'0:a:{audio_idx}',
        '-t', f'{duration:.3f}', '-ac', '1', '-ar', '8000', '-c:a', 'pcm_s16le', outpath
    ]
    try:
        run_ffmpeg_subprocess(cmd, capture=True, timeout=120)
    except subprocess.CalledProcessError as e:
        stderr = ((e.stderr or '') + (e.stdout or '')).strip()
        raise RuntimeError(
            f"Falha ao extrair áudio de '{os.path.basename(filepath)}' "
            f"(FFmpeg código {e.returncode}): {stderr[-500:]}"
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Tempo limite excedido ao extrair áudio de '{os.path.basename(filepath)}'."
        ) from e
    if not os.path.exists(outpath) or os.path.getsize(outpath) <= 44:
        raise RuntimeError(
            f"Não foi possível extrair áudio de '{os.path.basename(filepath)}': "
            f"o trecho em {start_time:.1f}s não retornou amostras "
            f"(arquivo muito curto ou codec não decodificável)."
        )


def ajustar_corte_para_silencio(
    filepath: str,
    audio_duration: float,
    audio_idx: int,
    cut: float,
    radius: float = 4.0,
    max_move: float = 4.0,
    edge_guard: float = 0.05,
) -> float:
    """Move um corte ao centro do silêncio próximo, sem ultrapassar limites seguros.

    A mesma política é usada pela análise segmentada e pela aplicação final,
    para que o ponto apresentado ao usuário seja o ponto usado na correção.
    """
    try:
        cut = float(cut)
        audio_duration = float(audio_duration)
    except (TypeError, ValueError):
        return cut
    if cut <= edge_guard or cut >= audio_duration - edge_guard:
        return cut
    radius = max(0.5, min(float(radius), 10.0))
    max_move = max(0.0, float(max_move))
    start = max(0.0, cut - radius)
    end = min(audio_duration, cut + radius)
    duration = end - start
    if duration < 0.5:
        return cut
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            wav_path = os.path.join(temp_dir, "snap.wav")
            extract_audio_chunk(filepath, wav_path, start, duration, audio_idx)
            sample_rate, data = wavfile.read(wav_path)
            if sample_rate <= 0 or data.size == 0:
                return cut
            if data.ndim > 1:
                data = np.mean(data, axis=1)
            audio = data.astype(np.float32) / 32768.0
            window = int(max(1, sample_rate * 0.10))
            hop = int(max(1, sample_rate * 0.05))
            if len(audio) < window:
                return cut
            rms_values = [
                float(np.sqrt(np.mean(audio[index:index + window] ** 2)))
                for index in range(0, len(audio) - window + 1, hop)
            ]
            if not rms_values:
                return cut
            rms = np.array(rms_values, dtype=np.float32)
            threshold = max(0.0015, min(0.004, float(np.median(rms)) * 0.45))
            if float(np.min(rms)) > threshold:
                return cut
            candidates = np.where(rms <= threshold)[0]
            if candidates.size == 0:
                return cut
            hop_seconds = hop / float(sample_rate)
            target_index = int(round((cut - start) / hop_seconds))
            target_index = max(0, min(rms.size - 1, target_index))
            closest = int(candidates[np.argmin(np.abs(candidates - target_index))])
            left = closest
            while left > 0 and rms[left - 1] <= threshold:
                left -= 1
            right = closest
            while right < rms.size - 1 and rms[right + 1] <= threshold:
                right += 1
            center = (left + right) // 2
            snapped = start + center * hop_seconds + window / (2.0 * sample_rate)
            snapped = max(start, min(end, snapped))
            return float(snapped) if abs(snapped - cut) <= max_move else cut
    except Exception:
        return cut


# Cache do teste de suporte ao encoder experimental dca (DTS)
_dca_experimental_cache = None


def is_dca_experimental_supported() -> bool:
    """Testa rapidamente se o encoder dca (DTS) funciona com a flag experimental -strict -2.
    O encoder nativo de DTS do FFmpeg é marcado como experimental. Este teste gera um
    trecho mínimo de áudio silencioso e tenta codificá-lo com dca + -strict -2.
    O resultado é armazenado em cache para executar o teste apenas uma vez por sessão.
    """
    global _dca_experimental_cache
    if _dca_experimental_cache is not None:
        return _dca_experimental_cache
    try:
        cmd = ['ffmpeg', '-hide_banner', '-y', '-f', 'lavfi', '-i',
               'anullsrc=r=48000:cl=stereo', '-t', '0.1',
               '-c:a', 'dca', '-strict', '-2', '-f', 'null', '-']
        result = run_ffmpeg_subprocess(cmd, timeout=15, check=False)
        _dca_experimental_cache = (result.returncode == 0)
    except Exception:
        _dca_experimental_cache = False
    return _dca_experimental_cache


# Mínimo de amostras para uma correlação confiável (0,5 s @ 8 kHz)
MIN_AMOSTRAS_CORRELACAO = 4000


def calculate_sync_offset_detail(ref_path: str, target_path: str, start_ref: float = 0.0,
                                 start_target: float = 0.0,
                                 duration_ref: float = 360.0, duration_target: float = 360.0,
                                 ref_audio_idx: int = 0, target_audio_idx: int = 0
                                 ) -> Tuple[float, float, float]:
    """Correlação cruzada com métricas de qualidade.
    Retorna (offset_segundos, peak_ratio, energia_rms)."""
    with tempfile.TemporaryDirectory() as temp_dir:
        ref_wav = os.path.join(temp_dir, "ref.wav")
        target_wav = os.path.join(temp_dir, "target.wav")
        extract_audio_chunk(ref_path, ref_wav, start_ref, duration_ref, ref_audio_idx)
        extract_audio_chunk(target_path, target_wav, start_target, duration_target, target_audio_idx)
        sr1, audio1 = wavfile.read(ref_wav)
        sr2, audio2 = wavfile.read(target_wav)
        if audio1.ndim > 1:
            audio1 = audio1.mean(axis=1)
        if audio2.ndim > 1:
            audio2 = audio2.mean(axis=1)
        # CORREÇÃO: guarda de segurança contra áudio vazio/insuficiente.
        # Evita o "index 0 is out of bounds for axis 0 with size 0".
        if audio1.size < MIN_AMOSTRAS_CORRELACAO or audio2.size < MIN_AMOSTRAS_CORRELACAO:
            faltando = []
            if audio1.size < MIN_AMOSTRAS_CORRELACAO:
                faltando.append(f"'{os.path.basename(ref_path)}' (início {start_ref:.1f}s)")
            if audio2.size < MIN_AMOSTRAS_CORRELACAO:
                faltando.append(f"'{os.path.basename(target_path)}' (início {start_target:.1f}s)")
            raise RuntimeError(
                "Extração de áudio vazia/insuficiente para correlação: "
                + "; ".join(faltando)
                + ". Verifique se o trecho existe e se o codec é decodificável pelo FFmpeg."
            )
        audio1 = audio1.astype(np.float32) / 32768.0
        audio2 = audio2.astype(np.float32) / 32768.0
        correlation = signal.correlate(audio1, audio2, mode='full', method='fft')
        lags = signal.correlation_lags(audio1.size, audio2.size, mode='full')
        peak_idx = int(np.argmax(correlation))
        lag = lags[peak_idx]
        offset = lag / sr1
        true_offset = offset + start_ref - start_target
        corr_abs = np.abs(correlation)
        peak = float(corr_abs[peak_idx])
        med = float(np.median(corr_abs)) + 1e-9
        peak_ratio = peak / med
        energy = float(np.sqrt(np.mean(audio1 ** 2)))
        return true_offset, peak_ratio, energy


def select_audio_output(codec_name: str, bitrate: str, available_encoders: Set[str]) -> Tuple[str, str, bool]:
    """Seleciona o encoder de saída com base na disponibilidade."""
    preferred = {
        'aac': ('aac', '.m4a', True), 'mp3': ('libmp3lame', '.mp3', True),
        'ac3': ('ac3', '.ac3', True), 'eac3': ('eac3', '.eac3', True),
        'dts': ('dca', '.dts', True), 'flac': ('flac', '.flac', False),
    }
    encoder, extension, supports_bitrate = preferred.get(codec_name, ('flac', '.flac', False))
    # Fallback: o encoder dca (DTS) é experimental no FFmpeg. Se ele não funcionar
    # mesmo com a flag -strict -2, converte para FLAC como alternativa segura.
    if encoder == 'dca' and not is_dca_experimental_supported():
        encoder, extension, supports_bitrate = ('flac', '.flac', False)
    if encoder in available_encoders:
        return encoder, extension, supports_bitrate
    for fallback in [('flac', '.flac', False), ('ac3', '.ac3', True), ('pcm_s16le', '.wav', False)]:
        if fallback[0] in available_encoders:
            return fallback
    raise RuntimeError("Nenhum encoder compatível encontrado.")

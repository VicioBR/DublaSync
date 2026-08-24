import subprocess
import json
import os
import tempfile
import re
from fractions import Fraction
from typing import Optional, Tuple, Dict, Any, List, Set
import numpy as np
from scipy import signal, stats
from scipy.io import wavfile

def parse_fps_value(fps_str):
    """ Converte a fração do FFprobe em número decimal (Float) """
    if not fps_str or fps_str == "0/0":
        return 0.0
    try:
        num, den = map(int, str(fps_str).split('/'))
        return num / den if den != 0 else 0.0
    except Exception:
        return 0.0

def get_real_video_fps(stream_video):
    """ Extrai o FPS real, ignorando taxas inválidas como 90000 Hz """
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
    else:
        pass

def get_ffmpeg_audio_encoders() -> Set[str]:
    """Obtém lista de encoders de áudio disponíveis no FFmpeg sem abrir console."""
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-encoders'], capture_output=True,
        text=True, encoding='utf-8', errors='replace', check=True, timeout=15,
        creationflags=creationflags
    )
    encoders = set()
    for line in result.stdout.splitlines():
        match = re.match(r'^\s*[A-Z.]{6}\s+(\S+)', line)
        if match and line.lstrip().startswith('A'):
            encoders.add(match.group(1))
    return encoders

def check_rubberband_available() -> bool:
    """Verifica se o filtro rubberband está disponível sem abrir console."""
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(
        ['ffmpeg', '-hide_banner', '-filters'], capture_output=True,
        text=True, encoding='utf-8', errors='replace', check=True, timeout=15,
        creationflags=creationflags
    )
    return bool(re.search(r'\brubberband\b', result.stdout))

def get_file_info(filepath: str) -> Tuple[Optional[float], Optional[float], Dict[str, Any], List[Dict]]:
    """Extrai informações do arquivo via FFprobe sem abrir console."""
    if not os.path.exists(filepath):
        raise FileNotFoundError("Arquivo não encontrado.")
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', filepath]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding='utf-8', timeout=15, creationflags=creationflags)
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

def extract_audio_chunk(filepath: str, outpath: str, start_time: float = 0.0, duration: float = 360.0) -> None:
    """Extrai trecho de áudio em PCM sem abrir console."""
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    cmd = [
        'ffmpeg', '-y', '-v', 'error', '-ss', f'{start_time:.3f}', '-i', filepath,
        '-t', f'{duration:.3f}', '-ac', '1', '-ar', '8000', '-c:a', 'pcm_s16le', outpath
    ]
    subprocess.run(cmd, check=True, creationflags=creationflags)

def calculate_sync_offset(ref_path: str, target_path: str, start_ref: float = 0.0, start_target: float = 0.0,
                          duration_ref: float = 360.0, duration_target: float = 360.0) -> float:
    """Realiza a Correlação Cruzada via SciPy e retorna o offset exato."""
    with tempfile.TemporaryDirectory() as temp_dir:
        ref_wav = os.path.join(temp_dir, "ref.wav")
        target_wav = os.path.join(temp_dir, "target.wav")
        extract_audio_chunk(ref_path, ref_wav, start_ref, duration_ref)
        extract_audio_chunk(target_path, target_wav, start_target, duration_target)
        sr1, audio1 = wavfile.read(ref_wav)
        sr2, audio2 = wavfile.read(target_wav)
        audio1 = audio1.astype(np.float32)
        audio2 = audio2.astype(np.float32)
        correlation = signal.correlate(audio1, audio2, mode='full', method='fft')
        lags = signal.correlation_lags(audio1.size, audio2.size, mode='full')
        lag = lags[np.argmax(correlation)]
        offset = lag / sr1
        true_offset = offset + start_ref - start_target
        return true_offset

def select_audio_output(codec_name: str, bitrate: str, available_encoders: Set[str]) -> Tuple[str, str, bool]:
    """Seleciona o encoder de saída com base na disponibilidade."""
    preferred = {
        'aac': ('aac', '.m4a', True), 'mp3': ('libmp3lame', '.mp3', True),
        'ac3': ('ac3', '.ac3', True), 'eac3': ('eac3', '.eac3', True),
        'dts': ('dca', '.dts', True), 'flac': ('flac', '.flac', False),
    }
    encoder, extension, supports_bitrate = preferred.get(codec_name, ('flac', '.flac', False))
    if encoder in available_encoders:
        return encoder, extension, supports_bitrate
    for fallback in [('flac', '.flac', False), ('ac3', '.ac3', True), ('pcm_s16le', '.wav', False)]:
        if fallback[0] in available_encoders:
            return fallback
    raise RuntimeError("Nenhum encoder compatível encontrado.")

def validate_media_file(filepath: str) -> Tuple[bool, str, str]:
    """
    Valida se o arquivo contém streams de áudio ou vídeo usando ffprobe.
    Não depende de extensão.
    Retorna: (is_valid, motivo_rejeicao, erro_tecnico)
    """
    if not os.path.exists(filepath):
        return False, "arquivo não encontrado.", "FileNotFoundError"
    
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    cmd = [
        'ffprobe', '-v', 'error', '-print_format', 'json', 
        '-show_streams', filepath
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=True, 
            encoding='utf-8', timeout=15, creationflags=creationflags
        )
        data = json.loads(result.stdout)
        streams = data.get('streams', [])
        
        has_valid_stream = False
        for stream in streams:
            codec_type = stream.get('codec_type')
            if codec_type in ('video', 'audio'):
                has_valid_stream = True
                break
        
        if not has_valid_stream:
            return False, "nenhum stream de áudio ou vídeo válido encontrado.", "No valid audio/video streams found"
            
        return True, "", ""
        
    except FileNotFoundError:
        # Se o ffprobe não existir no sistema, repassa a exceção para o controller 
        # tratar como FFmpeg ausente e abrir o instalador.
        raise
    except subprocess.CalledProcessError as e:
        # ffprobe retorna código de erro para arquivos corrompidos/inválidos
        error_msg = e.stderr.strip() if e.stderr else "Unknown error"
        return False, "arquivo inválido ou corrompido.", error_msg
    except json.JSONDecodeError as e:
        return False, "falha ao analisar a saída do FFprobe.", str(e)
    except Exception as e:
        return False, "falha ao analisar o arquivo.", str(e)
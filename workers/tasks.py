from PySide6.QtCore import QThread, Signal
import time
import subprocess
import os
from scipy import stats
from fractions import Fraction
from utils.core_logic import calculate_sync_offset
from utils.translations import tr


class SyncWorker(QThread):
    progress = Signal(str, int)
    finished = Signal(dict)
    error = Signal(str)

    def __init__(self, video_path: str, audio_path: str, video_dur: float, audio_dur: float):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.video_dur = video_dur
        self.audio_dur = audio_dur
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            anchor_dur = min(self.video_dur, self.audio_dur)
            pontos_pct = [0.0, 0.20, 0.40, 0.60, 0.80]
            offsets, x_times = [], []
            self.progress.emit(tr("prog_ponto0"), 10)
            offset_0 = calculate_sync_offset(self.video_path, self.audio_path, 0.0, 0.0, 360, 360)
            if self._cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
                return
            offsets.append(offset_0)
            x_times.append(0.0)
            for i, pct in enumerate(pontos_pct[1:]):
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                pct_int = int(pct * 100)
                self.progress.emit(tr("prog_ponto").format(pct=pct_int), 20 + (i * 15))
                start_ref = anchor_dur * pct
                start_target = max(0.0, start_ref - offset_0 - 150)
                off = calculate_sync_offset(self.video_path, self.audio_path, start_ref, start_target, 240, 540)
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                offsets.append(off)
                x_times.append(start_ref)
            self.progress.emit(tr("prog_regressao"), 90)
            res = stats.theilslopes(offsets, x_times)
            slope, offset_A = res[0], res[1]
            speed_factor = 1.0 - slope
            self.progress.emit(tr("prog_analise_concluida"), 100)
            self.finished.emit({
                "offset": offset_A,
                "speed_factor": speed_factor,
                "diff_percent": (speed_factor - 1) * 100,
                "effective_video_dur": self.video_dur - max(0, offset_A)
            })
        except Exception as e:
            if self._cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
            else:
                self.error.emit(str(e))


class FFmpegWorker(QThread):
    progress = Signal(str, float)
    finished = Signal(str)
    error = Signal(str)

    def __init__(self, cmd: list, target_duration: float, output_path: str, stage_text: str = ""):
        super().__init__()
        self.cmd = cmd
        self.target_duration = target_duration
        self.output_path = output_path
        self.stage_text = stage_text
        self._is_cancelled = False
        self.process = None

    def cancel(self):
        self._is_cancelled = True
        if self.process:
            self.process.terminate()

    def run(self):
        try:
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            self.process = subprocess.Popen(
                self.cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                universal_newlines=True, encoding='utf-8', errors='replace',
                creationflags=creationflags
            )
            start_time = time.time()
            for line in self.process.stdout:
                if self._is_cancelled:
                    break
                if 'out_time_us=' in line:
                    us_str = line.strip().split('=')[1]
                    if us_str.lstrip('-').isdigit():
                        percent = max(0.0, min(100.0, ((int(us_str) / 1000000.0) / max(self.target_duration, 0.001)) * 100))
                        elapsed = time.time() - start_time
                        remaining = (elapsed / (percent / 100) - elapsed) if percent > 0 else 0
                        rem_m, rem_s = divmod(int(remaining), 60)
                        rem_h, rem_m = divmod(rem_m, 60)
                        
                        faltam_texto = tr("prog_faltam").format(tempo=f"{rem_h:02d}:{rem_m:02d}:{rem_s:02d}")
                        if self.stage_text:
                            msg = f"{self.stage_text}    {faltam_texto}"
                        else:
                            msg = faltam_texto
                            
                        self.progress.emit(msg, percent)
            self.process.wait()
            if self._is_cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
                if os.path.exists(self.output_path):
                    try:
                        os.remove(self.output_path)
                    except Exception:
                        pass
            elif self.process.returncode != 0:
                if self.process.returncode in (-28, 4294967268):
                    self.error.emit(tr("falha_espaco"))
                else:
                    self.error.emit(tr("falha_ffmpeg_codigo").format(code=self.process.returncode))
                if os.path.exists(self.output_path):
                    try:
                        os.remove(self.output_path)
                    except Exception:
                        pass
            else:
                self.finished.emit(self.output_path)
        except Exception as e:
            self.error.emit(str(e))
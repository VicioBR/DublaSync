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

    def __init__(self, video_path: str, audio_path: str, video_dur: float, audio_dur: float, 
                 video_audio_idx: int = 0, target_audio_idx: int = 0):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.video_dur = video_dur
        self.audio_dur = audio_dur
        self.video_audio_idx = video_audio_idx
        self.target_audio_idx = target_audio_idx
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            anchor_dur = min(self.video_dur, self.audio_dur)

            # ── JANELA SEGURA ──
            # Descarta ~3 min iniciais (vinhetas/recap) e 10% finais (créditos).
            inicio = min(180.0, anchor_dur * 0.10)
            fim = anchor_dur * 0.90
            if fim - inicio < 600.0:          # arquivo curto: sem corte
                inicio, fim = 0.0, anchor_dur

            # ── GRADE FIXA: 9 cortes dentro da janela ──
            # Âncora com chunks grandes (busca larga); demais pontos com chunks leves.
            REF_ANCORA, ALVO_ANCORA = 240.0, 540.0   # âncora: margem ±150 s
            REF_PONTO,  ALVO_PONTO  = 120.0, 300.0   # pontos: margem ±90 s
            grade_inicio = inicio
            grade_fim = (fim - REF_ANCORA) if (fim - REF_ANCORA) > inicio else fim
            n_pontos = 9
            pontos = [grade_inicio + (grade_fim - grade_inicio) * i / (n_pontos - 1)
                      for i in range(n_pontos)]

            offsets, x_times = [], []

            # Âncora: primeira medição da janela, chunks grandes, busca larga
            self.progress.emit(tr("prog_ponto").format(pct=int(pontos[0] / anchor_dur * 100)), 10)
            offset_0 = calculate_sync_offset(
                self.video_path, self.audio_path, pontos[0], max(0.0, pontos[0] - 150.0),
                REF_ANCORA, ALVO_ANCORA,
                self.video_audio_idx, self.target_audio_idx
            )
            if self._cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
                return

            offsets.append(offset_0)
            x_times.append(pontos[0])

            # Demais pontos: chunks reduzidos (mais rápido), janela centrada pelo offset_0
            for i, t_ref in enumerate(pontos[1:], start=1):
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return

                self.progress.emit(
                    tr("prog_ponto").format(pct=int(t_ref / anchor_dur * 100)),
                    10 + int(80 * i / (n_pontos - 1))
                )

                start_target = max(0.0, t_ref - offset_0 - 90.0)
                off = calculate_sync_offset(
                    self.video_path, self.audio_path, t_ref, start_target, REF_PONTO, ALVO_PONTO,
                    self.video_audio_idx, self.target_audio_idx
                )
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return

                offsets.append(off)
                x_times.append(t_ref)

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
                    try: os.remove(self.output_path)
                    except Exception: pass

            elif self.process.returncode != 0:
                if self.process.returncode in (-28, 4294967268):
                    self.error.emit(tr("falha_espaco"))
                else:
                    self.error.emit(tr("falha_ffmpeg_codigo").format(code=self.process.returncode))

                if os.path.exists(self.output_path):
                    try: os.remove(self.output_path)
                    except Exception: pass
            else:
                self.finished.emit(self.output_path)

        except Exception as e:
            self.error.emit(str(e))
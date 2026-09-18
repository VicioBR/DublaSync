from PySide6.QtCore import QThread, Signal
import time
import subprocess
import os
import tempfile
import numpy as np
from scipy import stats
from utils.core_logic import calculate_sync_offset_detail, ajustar_corte_para_silencio
from utils.translations import tr, get_idioma


# Textos locais dos workers (pt/en/es) para mensagens novas sem chave no translations.py
_WORKER_TEXTS = {
    "pt": {
        "curto": "Arquivo muito curto para análise de sincronia (duração mínima: 40s).",
        "pontos_insuficientes": "Não foi possível encontrar trechos de áudio confiáveis o suficiente para medir a velocidade.",
    },
    "en": {
        "curto": "File too short for sync analysis (minimum duration: 40s).",
        "pontos_insuficientes": "Could not find enough reliable audio sections to measure the speed.",
    },
    "es": {
        "curto": "Archivo demasiado corto para el análisis de sincronía (duración mínima: 40s).",
        "pontos_insuficientes": "No fue posible encontrar suficientes fragmentos de audio fiables para medir la velocidad.",
    },
}


def _trw(key: str) -> str:
    try:
        lang = get_idioma()
    except Exception:
        lang = "pt"
    lang = lang if lang in _WORKER_TEXTS else "pt"
    return _WORKER_TEXTS.get(lang, _WORKER_TEXTS["pt"]).get(key, _WORKER_TEXTS["pt"][key])


class SyncWorker(QThread):
    progress = Signal(str, int)
    finished = Signal(dict)
    error = Signal(str)

    # CORREÇÃO: margem mínima de áudio restante no último ponto da grade.
    # Impede que o "-ss" caia além do fim do arquivo e extraia 0 amostras.
    MIN_TRECHO = 20.0
    MIN_ANCHOR_DUR = 40.0
    CONFIDENCE_INLIER_TOLERANCE = 0.5
    MIN_PEAK_RATIO = 2.0
    MIN_ENERGY = 0.004
    MIN_VALID_ANCHORS = 5
    ANCHOR_RETRY_SHIFTS = (0.0, -45.0, 45.0)

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

    def _is_reliable_measurement(self, peak_ratio: float, energy: float) -> bool:
        """Aceita somente trechos com áudio audível e pico de correlação claro."""
        return peak_ratio >= self.MIN_PEAK_RATIO and energy >= self.MIN_ENERGY

    def _measure_reliable_anchor(self, time_ref: float, expected_offset: float,
                                 slope_estimate: float, ref_duration: float,
                                 target_duration: float, anchor_duration: float):
        """Mede uma âncora e, se o trecho for ruim, tenta vizinhos próximos."""
        max_time = max(0.0, anchor_duration - self.MIN_TRECHO)
        attempted_times = set()
        for attempt, shift in enumerate(self.ANCHOR_RETRY_SHIFTS):
            sample_time = min(max(0.0, time_ref + shift), max_time)
            time_key = round(sample_time, 3)
            if time_key in attempted_times:
                continue
            attempted_times.add(time_key)

            if expected_offset is None:
                start_target = max(0.0, sample_time - 150.0)
            else:
                expected_at_sample = expected_offset + slope_estimate * (sample_time - time_ref)
                start_target = max(0.0, sample_time - expected_at_sample - 90.0)
            start_target = min(
                start_target,
                max(0.0, self.audio_dur - self.MIN_TRECHO),
            )

            try:
                offset, peak_ratio, energy = calculate_sync_offset_detail(
                    self.video_path, self.audio_path, sample_time, start_target,
                    ref_duration, target_duration,
                    self.video_audio_idx, self.target_audio_idx,
                )
            except Exception:
                continue

            if self._is_reliable_measurement(peak_ratio, energy):
                return sample_time, offset, peak_ratio, energy, attempt
        return None

    def run(self):
        try:
            anchor_dur = min(self.video_dur, self.audio_dur)
            # CORREÇÃO: duração mínima para uma análise confiável (mensagem clara).
            if anchor_dur < self.MIN_ANCHOR_DUR:
                self.error.emit(_trw("curto"))
                return
            inicio = min(180.0, anchor_dur * 0.10)
            fim = anchor_dur * 0.90
            if fim - inicio < 600.0:
                inicio, fim = 0.0, anchor_dur
            REF_ANCORA, ALVO_ANCORA = 240.0, 540.0
            REF_PONTO, ALVO_PONTO = 120.0, 300.0
            grade_inicio = inicio
            grade_fim = (fim - REF_ANCORA) if (fim - REF_ANCORA) > inicio else fim
            # CORREÇÃO: o último ponto sempre deixa >= MIN_TRECHO de áudio restante.
            grade_fim = min(grade_fim, max(0.0, anchor_dur - self.MIN_TRECHO))
            if grade_fim <= grade_inicio:
                grade_inicio = 0.0
                grade_fim = max(0.0, anchor_dur - self.MIN_TRECHO)
            if grade_fim <= grade_inicio:
                self.error.emit(_trw("curto"))
                return
            n_pontos = 9
            pontos = [grade_inicio + (grade_fim - grade_inicio) * i / (n_pontos - 1)
                      for i in range(n_pontos)]
            offsets, x_times = [], []
            retried_anchor_count = 0
            for i, t_ref in enumerate(pontos):
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                self.progress.emit(tr("prog_ponto").format(pct=int(t_ref / anchor_dur * 100)),
                                   10 + int(80 * i / (n_pontos - 1)))
                if len(offsets) >= 2:
                    res_temp = stats.theilslopes(offsets, x_times)
                    slope_est = res_temp[0]
                    # Limit slope to sensible FPS conversion bounds (e.g. max 5%)
                    slope_est = max(-0.06, min(0.06, slope_est))
                    expected_offset = offsets[0] + slope_est * (t_ref - x_times[0])
                elif offsets:
                    slope_est = 0.0
                    expected_offset = offsets[0]
                else:
                    slope_est = 0.0
                    expected_offset = None

                measurement = self._measure_reliable_anchor(
                    t_ref,
                    expected_offset,
                    slope_est,
                    REF_ANCORA if expected_offset is None else REF_PONTO,
                    ALVO_ANCORA if expected_offset is None else ALVO_PONTO,
                    anchor_dur,
                )
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                if measurement is None:
                    continue
                sample_time, offset, _peak_ratio, _energy, attempt = measurement
                offsets.append(offset)
                x_times.append(sample_time)
                if attempt > 0:
                    retried_anchor_count += 1

            if len(offsets) < self.MIN_VALID_ANCHORS:
                self.error.emit(_trw("pontos_insuficientes"))
                return
            self.progress.emit(tr("prog_regressao"), 90)
            res = stats.theilslopes(offsets, x_times)
            slope, offset_A, low_slope, high_slope = res
            speed_factor = 1.0 - slope
            speed_factor_ci_low = float(min(1.0 - high_slope, 1.0 - low_slope))
            speed_factor_ci_high = float(max(1.0 - high_slope, 1.0 - low_slope))

            # A diferença de FPS faz os offsets crescerem/caírem ao longo do
            # vídeo. Para validar a confiança, medimos apenas o quanto cada
            # ponto se afasta dessa tendência, e não os offsets brutos.
            residuals = np.asarray([
                offset - (slope * time_point + offset_A)
                for offset, time_point in zip(offsets, x_times)
            ], dtype=float)
            residual_center = float(np.median(residuals))
            centered_residuals = residuals - residual_center
            spread = float(
                np.percentile(centered_residuals, 90)
                - np.percentile(centered_residuals, 10)
            )
            residual_mad = float(np.median(np.abs(centered_residuals)))
            inlier_count = int(np.count_nonzero(
                np.abs(centered_residuals) <= self.CONFIDENCE_INLIER_TOLERANCE
            ))
            self.progress.emit(tr("prog_analise_concluida"), 100)
            self.finished.emit({
                "offset": offset_A,
                "speed_factor": speed_factor,
                "diff_percent": (speed_factor - 1) * 100,
                "effective_video_dur": self.video_dur - max(0, offset_A),
                "spread": spread,
                "residual_mad": residual_mad,
                "anchor_count": len(offsets),
                "inlier_count": inlier_count,
                "requested_anchor_count": n_pontos,
                "retried_anchor_count": retried_anchor_count,
                "speed_factor_ci_low": speed_factor_ci_low,
                "speed_factor_ci_high": speed_factor_ci_high,
            })
        except Exception as e:
            if self._cancelled:
                self.error.emit(tr("op_cancelada_usuario"))
            else:
                self.error.emit(str(e))


class SegmentedSyncWorker(QThread):
    """Motor de análise segmentada por NÍVEIS CONFIRMADOS.
    Detecta quebras de sincronia no meio do vídeo sem contaminar a medição.
    Melhorias adicionadas:
    1. Quando há um intervalo grande entre o último ponto bom do nível antigo
       e o primeiro ponto bom do nível novo, o motor tenta refinar a posição
       real da transição.
    2. Depois de refinada a transição, o ponto de corte no áudio dublado tenta
       ser ajustado para o silêncio mais próximo, evitando cortar fala.
    3. Se o refinamento ou o snap falharem, o comportamento antigo é usado
       como fallback.
    """
    progress = Signal(str, int)
    finished = Signal(dict)
    error = Signal(str)
    MIN_LISTA = 0.100
    MAX_CORRECAO = 15.0
    MAX_CORR_N = 8
    SEARCH_NARROW = 5.0
    SEARCH_WIDE = 60.0
    REF_LEN = 30.0
    LEVEL_TOL = 0.150
    RATIO_JOIN = 2.5
    RATIO_WIDE = 6.0
    ENERGY_MIN = 0.010
    INLIER_MS = 0.300
    # Refinamento da transição
    REFINE_GAP_MIN = 45.0
    REFINE_REF_LEN = 10.0
    REFINE_SEARCH = 4.0
    REFINE_TOL = 0.350
    REFINE_RATIO = 2.0
    REFINE_ENERGY = 0.004
    REFINE_MAX_ITER = 14
    # CORREÇÃO: margem mínima de áudio restante nos pontos de medição.
    MIN_TRECHO = 20.0

    def __init__(self, video_path, audio_path, video_dur, audio_dur,
                 video_audio_idx=0, target_audio_idx=0, initial_offset=None, quick=False):
        super().__init__()
        self.video_path = video_path
        self.audio_path = audio_path
        self.video_dur = video_dur
        self.audio_dur = audio_dur
        self.video_audio_idx = video_audio_idx
        self.target_audio_idx = target_audio_idx
        self.initial_offset = initial_offset
        self.quick = quick
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _measure(self, t, center, search):
        st = max(0.0, t - center - search)
        return calculate_sync_offset_detail(
            self.video_path, self.audio_path, t, st,
            self.REF_LEN, self.REF_LEN + 2 * search,
            self.video_audio_idx, self.target_audio_idx)

    def _measure_short(self, t, center):
        st = max(0.0, t - center - self.REFINE_SEARCH)
        return calculate_sync_offset_detail(
            self.video_path, self.audio_path, t, st,
            self.REFINE_REF_LEN, self.REFINE_REF_LEN + 2 * self.REFINE_SEARCH,
            self.video_audio_idx, self.target_audio_idx)

    def _refine_valid(self, medicao, nivel):
        off, ratio, energy = medicao
        if ratio < self.REFINE_RATIO:
            return False
        if energy < self.REFINE_ENERGY:
            return False
        return abs(off - nivel) <= self.REFINE_TOL

    def _refine_score(self, medicao, nivel):
        off, ratio, energy = medicao
        erro = abs(off - nivel) + 0.05
        return (ratio + energy * 10.0) / erro

    def _classify_refine_point(self, t, old_level, new_level):
        try:
            old_res = self._measure_short(t, old_level)
            new_res = self._measure_short(t, new_level)
        except Exception:
            return None
        old_ok = self._refine_valid(old_res, old_level)
        new_ok = self._refine_valid(new_res, new_level)
        if old_ok and new_ok:
            score_old = self._refine_score(old_res, old_level)
            score_new = self._refine_score(new_res, new_level)
            return "new" if score_new >= score_old else "old"
        if new_ok:
            return "new"
        if old_ok:
            return "old"
        return None

    def _refine_transition_time(self, left_t, right_t, old_level, new_level, fallback_time):
        """Tenta descobrir onde a transição realmente ocorre dentro do intervalo
        entre o último ponto bom do nível antigo e o primeiro ponto bom do nível novo."""
        if self.quick:
            return fallback_time
        if right_t - left_t <= self.REFINE_GAP_MIN:
            return fallback_time
        if abs(new_level - old_level) < 0.250:
            return fallback_time
        lo = float(left_t)
        hi = float(right_t)
        moves = 0
        for _ in range(self.REFINE_MAX_ITER):
            if self._cancelled:
                return fallback_time
            if hi - lo <= 2.0:
                break
            mid = (lo + hi) / 2.0
            cls = self._classify_refine_point(mid, old_level, new_level)
            if cls == "new":
                hi = mid
                moves += 1
            elif cls == "old":
                lo = mid
                moves += 1
            else:
                if moves >= 3:
                    return mid
                # Se o ponto ficou ambíguo, tenta pequenas sondagens ao redor.
                found = False
                for delta in (2.0, -2.0, 4.0, -4.0, 6.0, -6.0):
                    tt = mid + delta
                    if tt <= lo or tt >= hi:
                        continue
                    cls2 = self._classify_refine_point(tt, old_level, new_level)
                    if cls2 == "new":
                        hi = tt
                        moves += 1
                        found = True
                        break
                    elif cls2 == "old":
                        lo = tt
                        moves += 1
                        found = True
                        break
                if not found:
                    if moves:
                        return (lo + hi) / 2.0
                    return fallback_time
        return (lo + hi) / 2.0

    def _snap_cut_to_silence(self, cut):
        """Procura um trecho de silêncio perto do ponto de corte no áudio dublado.
        Se encontrar, move o corte para esse trecho. Caso contrário, mantém o corte."""
        if self.quick:
            return cut
        return ajustar_corte_para_silencio(
            self.audio_path,
            self.audio_dur,
            self.target_audio_idx,
            cut,
        )

    def run(self):
        try:
            anchor_dur = min(self.video_dur, self.audio_dur)
            if anchor_dur < 240.0:
                self.finished.emit({"ok": False, "reason": "curto", "anchors": []})
                return
            inicio = min(180.0, anchor_dur * 0.10)
            fim = anchor_dur * 0.90
            if fim - inicio < 600.0:
                inicio, fim = 0.0, anchor_dur
            max_anc = 16 if self.quick else 40
            passo_min = 120.0 if self.quick else 60.0
            # CORREÇÃO: margem de segurança para o último ponto nunca cair
            # além do fim do arquivo (evita extração vazia).
            limite = max(inicio + 10.0, min(fim, anchor_dur - 10.0) - self.REF_LEN)
            janela = max(1.0, limite - inicio)
            passo = max(passo_min, janela / max_anc)
            n = min(max_anc, max(8, int(janela // passo) + 1))
            pontos = [inicio + (limite - inicio) * i / (n - 1) for i in range(n)] if n > 1 else [inicio]
            levels = []
            assigned = []
            measured = []
            cur = -1
            pending = None
            for i, t in enumerate(pontos):
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                self.progress.emit(tr("seg_progress"), int(5 + 90 * (i + 1) / len(pontos)))
                off_show = None
                if cur < 0:
                    st0 = max(0.0, t - 150.0)
                    off, ratio, energy = calculate_sync_offset_detail(
                        self.video_path, self.audio_path, t, st0, 120.0, 420.0,
                        self.video_audio_idx, self.target_audio_idx)
                    off_show = off
                    if ratio >= self.RATIO_JOIN and energy >= self.ENERGY_MIN:
                        levels.append({"value": off, "count": 1, "confirmed": False})
                        cur = 0
                        assigned.append(0)
                    else:
                        assigned.append(-1)
                    measured.append({"t": t, "offset": off})
                    continue
                off, ratio, energy = self._measure(t, levels[cur]["value"], self.SEARCH_NARROW)
                off_show = off
                if ratio >= self.RATIO_JOIN and energy >= self.ENERGY_MIN \
                        and abs(off - levels[cur]["value"]) <= self.LEVEL_TOL:
                    levels[cur]["count"] += 1
                    if levels[cur]["count"] >= 2:
                        levels[cur]["confirmed"] = True
                    assigned.append(cur)
                    pending = None
                    measured.append({"t": t, "offset": off})
                    continue
                hit = None
                for L in range(len(levels)):
                    if L == cur or not levels[L]["confirmed"]:
                        continue
                    offL, rL, eL = self._measure(t, levels[L]["value"], self.SEARCH_NARROW)
                    if rL >= self.RATIO_JOIN and eL >= self.ENERGY_MIN \
                            and abs(offL - levels[L]["value"]) <= self.LEVEL_TOL:
                        hit = L
                        off = offL
                        off_show = off
                        break
                if hit is not None:
                    levels[hit]["count"] += 1
                    assigned.append(hit)
                    cur = hit
                    pending = None
                    measured.append({"t": t, "offset": off})
                    continue
                if pending is not None:
                    offp, rp, ep = self._measure(t, pending["value"], self.SEARCH_NARROW)
                    if rp >= self.RATIO_JOIN and ep >= self.ENERGY_MIN \
                            and abs(offp - pending["value"]) <= self.LEVEL_TOL:
                        levels.append({"value": (pending["value"] + offp) / 2.0, "count": 2, "confirmed": True})
                        newL = len(levels) - 1
                        assigned[pending["idx"]] = newL
                        measured[pending["idx"]]["offset"] = pending["value"]
                        assigned.append(newL)
                        cur = newL
                        off_show = offp
                        pending = None
                        measured.append({"t": t, "offset": offp})
                        continue
                    pending = None
                offw, rw, ew = self._measure(t, levels[cur]["value"], self.SEARCH_WIDE)
                off_show = offw
                if rw >= self.RATIO_WIDE and ew >= self.ENERGY_MIN:
                    pending = {"value": offw, "idx": i}
                assigned.append(-1)
                measured.append({"t": t, "offset": off_show})
            for L in range(len(levels)):
                vals = [measured[i]["offset"] for i in range(len(assigned)) if assigned[i] == L]
                if vals:
                    levels[L]["value"] = float(np.median(vals))
            raw_transitions = []
            prevL, prevT = None, None
            for i in range(len(assigned)):
                L = assigned[i]
                if L < 0:
                    continue
                if prevL is not None and L != prevL:
                    jump = levels[L]["value"] - levels[prevL]["value"]
                    if abs(jump) >= self.MIN_LISTA:
                        raw_transitions.append({
                            "time": (prevT + measured[i]["t"]) / 2.0,
                            "jump": float(jump),
                            "left_t": float(prevT),
                            "right_t": float(measured[i]["t"]),
                            "old_level": float(levels[prevL]["value"]),
                            "new_level": float(levels[L]["value"]),
                        })
                prevL, prevT = L, measured[i]["t"]
            transitions_refined = []
            for idx, raw in enumerate(raw_transitions):
                if self._cancelled:
                    self.error.emit(tr("op_cancelada_usuario"))
                    return
                if not self.quick and raw_transitions:
                    self.progress.emit(tr("seg_progress"), int(95 + 3 * (idx + 1) / len(raw_transitions)))
                t_refined = self._refine_transition_time(
                    raw["left_t"],
                    raw["right_t"],
                    raw["old_level"],
                    raw["new_level"],
                    raw["time"]
                )
                transitions_refined.append({
                    "time": float(t_refined),
                    "jump": raw["jump"],
                    "left_t": raw["left_t"],
                    "right_t": raw["right_t"],
                })
            first_level = next((assigned[i] for i in range(len(assigned)) if assigned[i] >= 0), None)
            first_offset = levels[first_level]["value"] if first_level is not None else 0.0
            cuts, cuts_ok = [], True
            cum, s_prev = first_offset, 0.0
            for trn in transitions_refined:
                off_after = cum + trn["jump"]
                base = cum if trn["jump"] > 0 else off_after
                s_original = trn["time"] - base
                s_candidate = self._snap_cut_to_silence(s_original) if not self.quick else s_original
                time_candidate = s_candidate + base
                # Se o snap jogar a transição para fora do intervalo refinado, volta ao original.
                if time_candidate < trn["left_t"] - 1.0 or time_candidate > trn["right_t"] + 1.0:
                    s_candidate = s_original
                # Se o snap criar corte inválido, volta ao original.
                if s_candidate <= s_prev + 0.5 or s_candidate >= self.audio_dur - 0.5:
                    s_candidate = s_original
                # Se mesmo o original for inválido, a correção não pode ser aplicada.
                if s_candidate <= s_prev + 0.5 or s_candidate >= self.audio_dur - 0.5:
                    cuts_ok = False
                    break
                trn["time"] = float(s_candidate + base)
                cuts.append(float(s_candidate))
                cum = off_after
                s_prev = s_candidate
            transitions = [{"time": trn["time"], "jump": trn["jump"]} for trn in transitions_refined]
            assigned_idx = [i for i in range(len(assigned)) if assigned[i] >= 0]
            coverage = len(assigned_idx) / max(1, len(assigned))
            residuals = [abs(measured[i]["offset"] - levels[assigned[i]]["value"]) * 1000.0 for i in assigned_idx]
            res_ms = float(np.median(residuals)) if residuals else 999.0
            res_q = max(0.0, 1.0 - (res_ms / 1000.0) / 0.150)
            score = round(100 * (0.5 * coverage + 0.5 * res_q), 1)
            if score >= 80 and len(assigned_idx) >= 8:
                confidence = tr("conf_alta")
            elif score >= 60 and len(assigned_idx) >= 6:
                confidence = tr("conf_media")
            else:
                confidence = tr("conf_baixa")
            largest = max([abs(t["jump"]) for t in transitions], default=0.0)
            vals_levels = [lv["value"] for lv in levels]
            amplitude = (max(vals_levels) - min(vals_levels)) if vals_levels else 0.0
            relation = "curtos" if largest < 5 else ("medios" if largest < 20 else "longos")
            anchors = []
            for i in range(len(assigned)):
                a = {"t": measured[i]["t"], "offset": measured[i]["offset"], "good": assigned[i] >= 0}
                if assigned[i] >= 0:
                    a["inlier"] = bool(abs(measured[i]["offset"] - levels[assigned[i]]["value"]) <= self.INLIER_MS)
                anchors.append(a)
            goods = [a for a in anchors if a["good"]]
            xs = [a["t"] for a in goods]
            ys = [a["offset"] for a in goods]
            if len(goods) >= 2:
                res = stats.theilslopes(ys, xs)
                slope, intercept = float(res[0]), float(res[1])
            else:
                slope, intercept = 0.0, first_offset
            self.progress.emit(tr("prog_analise_concluida"), 100)
            self.finished.emit({
                "ok": True,
                "anchors": anchors,
                "total": len(anchors),
                "good": len(goods),
                "found": len(transitions) > 0,
                "scale": 1.0 - slope,
                "slope": slope,
                "intercept": intercept,
                "residual_ms": res_ms,
                "inliers": sum(1 for a in anchors if a.get("inlier")),
                "score": score,
                "confidence": confidence,
                "coverage": coverage,
                "relation": relation,
                "largest_jump": largest,
                "amplitude": amplitude,
                "transitions": transitions,
                "cuts": cuts,
                "cuts_ok": cuts_ok,
                "first_offset": first_offset,
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
                self.cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                encoding='utf-8',
                errors='replace',
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
                        msg = f"{self.stage_text}    {faltam_texto}" if self.stage_text else faltam_texto
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

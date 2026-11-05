"""Extração do replay a partir do buffer de segmentos, a partir do INSTANTE DO ACIONAMENTO (t0).

Regras de segurança do conteúdo:
 * só gera o replay se houver cobertura CONTÍNUA de [t0-min_pre, t0+min_post]; senão → Unrecoverable (nunca um replay "errado");
 * aguarda o pós-roll ser gravado (NotReady) sem gastar tentativa;
 * saída é validada com ffprobe (vídeo íntegro, duração plausível) antes de ser aceita.
Em modo `copy` o corte começa no início do segmento que contém o pré-roll (keyframe): o clipe tem >= pre segundos de passado.
"""
import os
import subprocess
import tempfile
import time
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from .adapters import NotReady, Unrecoverable, probe_clip, utc_epoch
from .capture import BufferIndex, SegmentRecorder
from .config import ClipCfg, CameraCfg, redact_text
from .render import Rendered


class BufferClipRenderer:
    needs_buffer = True

    def __init__(self, indexes: dict[str, BufferIndex], recorders: dict[str, SegmentRecorder], clips: dict[str, ClipCfg],
                 cams: dict[str, CameraCfg], ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe", slot=lambda: nullcontext(),
                 nice: int = 10, est_bytes: int = 4_000_000, clock=time.time, grace_s: float = 2.0, encoder_args=lambda: None):
        self.indexes, self.recorders, self.clips, self.cams = indexes, recorders, clips, cams
        self.ffmpeg, self.ffprobe, self.slot, self.nice, self.clock, self.grace = ffmpeg, ffprobe, slot, nice, clock, grace_s
        self.size_bytes = est_bytes
        self.encoder_args = encoder_args

    def ready_at(self, court_key: str, at_epoch: float, post: float) -> float | None:
        return at_epoch + post + self.cams[court_key].segment_s + self.grace

    def _recording(self, court_key: str) -> bool:
        r = self.recorders.get(court_key)
        return bool(r and r.is_alive() and r.state in ("streaming", "connecting"))

    def plan(self, court_key: str, t0: float, pre: float, post: float) -> tuple[float, float, list]:
        """Decide (início, fim, segmentos) ou levanta NotReady/Unrecoverable. Sem efeitos colaterais."""
        clip, cam, idx = self.clips[court_key], self.cams[court_key], self.indexes[court_key]
        now = self.clock()
        a, b = t0 - pre, t0 + post
        spans = idx.spans(idx.scan(open_last=not self._recording(court_key)))
        # só vale esperar se algo ainda pode gravar o trecho (gravador vivo ou buffer externo em atualização)
        can_wait = self._recording(court_key) or court_key not in self.recorders
        deadline = b + cam.segment_s + self.grace if can_wait else 0.0
        runs = [r for r in idx.runs(spans, clip.gap_tolerance_s) if r[0] <= t0 + 0.5 and r[1] >= t0 - 0.5]
        if not runs:
            if now < deadline:
                raise NotReady(deadline)
            raise Unrecoverable("no_coverage_at_t0", "nenhum vídeo gravado no instante do acionamento")
        ra, rb, run = runs[0]
        if ra > t0 - clip.min_pre_s + 0.5:
            older = bool(spans) and spans[0].start > ra - 0.1 and ra == spans[0].start
            raise Unrecoverable("segments_expired" if older else "buffer_gap", f"cobertura começa {t0 - ra:.1f}s antes do acionamento (mínimo {clip.min_pre_s:g}s)")
        if rb < b - 0.5 and now < deadline:
            raise NotReady(deadline)                                    # pós-roll ainda sendo gravado
        if rb < t0 + clip.min_post_s - 0.5:
            raise Unrecoverable("buffer_gap", f"cobertura termina {rb - t0:.1f}s após o acionamento (mínimo {clip.min_post_s:g}s)")
        ss, to = max(a, ra), min(b, rb)
        chosen = [sp for sp in run if sp.end > ss and sp.start < to]
        if not chosen:
            raise Unrecoverable("buffer_gap", "sem segmentos na janela")
        return ss, to, chosen

    def render(self, court_key: str, dest: Path, pre: int, post: int, at: datetime | None = None) -> Rendered:
        if at is None:
            raise Unrecoverable("no_trigger_time", "acionamento sem horário")
        t0 = utc_epoch(at)
        ss, to, chosen = self.plan(court_key, t0, pre, post)
        clip = self.clips[court_key]
        first_start = chosen[0].start
        with tempfile.TemporaryDirectory(prefix="clip-", dir=dest.parent) as td:
            lst = Path(td) / "list.txt"
            lst.write_text("".join(f"file '{sp.seg.path}'\n" for sp in chosen))
            cmd = [self.ffmpeg, "-y", "-v", "error", "-nostdin", "-f", "concat", "-safe", "0", "-i", str(lst), "-map", "0:v:0", "-map", "0:a:0?"]
            if clip.mode == "copy":
                cmd += ["-t", f"{to - first_start:.3f}", "-c:v", "copy"]                  # começa no keyframe do 1º segmento
            else:
                enc = self.encoder_args() or ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"]
                cmd += ["-ss", f"{max(ss - first_start, 0):.3f}", "-t", f"{to - ss:.3f}", *enc]
            cmd += ["-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-avoid_negative_ts", "make_zero", str(dest)]
            with self.slot():
                try:
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                                       preexec_fn=(lambda: os.nice(self.nice)) if self.nice else None)
                except subprocess.TimeoutExpired as e:
                    raise TimeoutError("corte excedeu o tempo") from e
            if p.returncode != 0:
                dest.unlink(missing_ok=True)
                raise RuntimeError("falha no corte: " + redact_text((p.stderr or "")[-160:]).strip())
        out = probe_clip(dest, self.ffprobe)
        need = (clip.min_pre_s + clip.min_post_s) * 0.6
        if out.duration < need:
            dest.unlink(missing_ok=True)
            raise Unrecoverable("short_output", f"saída com {out.duration:.1f}s (< {need:.1f}s esperados)")
        return out

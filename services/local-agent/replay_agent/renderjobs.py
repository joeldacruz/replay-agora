"""Renderização LOCAL de prévias patrocinadas e exportações de áudio (original|music|mix|mute) no computador da arena.

Fluxo: reivindica job (lease) → baixa SÓ logos/música da central (nunca o vídeo) → FFmpeg sobre o MP4 local →
verifica a saída → guarda em `renders/` (na arena) → informa bytes+SHA-256. A central serve o resultado pelo relay.
Roda com prioridade baixa (nice) para não competir com a captura/geração de replays.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .client import CentralClient, CentralRejected, CentralUnavailable
from .resources import ResourceGovernor, looks_like_hw_failure
from .ffplan import ExportPlan, Overlay, PlanError, SourceMeta, build_command
from .store import LocalStore

_LOCAL = re.compile(r"^[A-Za-z0-9_.:\-]{8,120}$")
_JOB = re.compile(r"^[0-9a-f]{32}$")
_ASSET = re.compile(r"^(logo[0-3]|music)$")


class RenderError(Exception):
    def __init__(self, msg: str, retriable: bool = False):
        super().__init__(msg)
        self.retriable = retriable


def probe(path: Path, ffprobe: str = "ffprobe") -> dict:
    r = subprocess.run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RenderError("origem ilegível")
    return json.loads(r.stdout)


def source_meta(path: Path, ffprobe: str = "ffprobe") -> SourceMeta:
    d = probe(path, ffprobe)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    if not v:
        raise RenderError("origem sem vídeo")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return SourceMeta(float(d["format"]["duration"]), int(v["width"]), int(v["height"]), v["codec_name"], a is not None, a["codec_name"] if a else None)


def verify_output(path: Path, mode: str, expected: float, ffprobe: str = "ffprobe") -> None:
    d = probe(path, ffprobe)
    kinds = [s["codec_type"] for s in d["streams"]]
    if "video" not in kinds:
        raise RenderError("saída sem vídeo")
    if mode == "mute" and "audio" in kinds:
        raise RenderError("saída 'mute' contém áudio")
    if mode != "mute" and "audio" not in kinds:
        raise RenderError("saída sem faixa de áudio")
    if abs(float(d["format"]["duration"]) - expected) > 1.0:
        raise RenderError("duração da saída difere do original")


class RenderWorker:
    def __init__(self, client: CentralClient, store: LocalStore, renders_dir: Path, ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe",
                 timeout_s: int = 600, nice: int = 10, allowed_source_roots: list[Path] | None = None,
                 governor: ResourceGovernor | None = None, frame_provider=None):
        self.frame_provider = frame_provider      # callable(court_key, orientation) -> FramePlan | None (cache local; nunca a central)
        self.client, self.store, self.dir = client, store, Path(renders_dir)
        self.ffmpeg, self.ffprobe, self.timeout_s, self.nice = ffmpeg, ffprobe, timeout_s, nice
        self.roots = [Path(r).resolve() for r in (allowed_source_roots or [])]
        self.dir.mkdir(parents=True, exist_ok=True)
        self.done = self.failed = 0
        self.governor = governor

    def _source(self, local_id: str) -> Path:
        row = self.store.get(local_id) if _LOCAL.match(local_id) else None
        if not row or row["deleted"] or not Path(row["path"]).is_file():
            raise RenderError("replay não encontrado nesta arena")
        p = Path(row["path"]).resolve()
        if self.roots and not any(r in p.parents for r in self.roots):
            raise RenderError("origem fora das pastas permitidas")
        return p

    def _fetch_asset(self, job_id: str, name: str, sha: str, tmp: Path) -> Path:
        if not _ASSET.match(name):
            raise RenderError("recurso inválido")
        data = self.client.request_raw("GET", f"/render/{job_id}/asset/{name}").content
        if hashlib.sha256(data).hexdigest() != sha:
            raise RenderError("recurso corrompido em trânsito", retriable=True)
        p = tmp / name
        p.write_bytes(data)
        return p

    def run_one(self) -> bool:
        spec = self.client.call("GET", "/render/next")
        if not spec:
            return False
        job_id = spec["jobId"]
        if not _JOB.match(job_id):
            return True
        tmp = Path(tempfile.mkdtemp(prefix="render-", dir=self.dir))
        try:
            out = self._render(spec, tmp)
            h = hashlib.sha256()
            with open(out, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
            final = self.dir / f"{job_id}.mp4"
            os.replace(out, final)
            self.store.add_render(job_id, spec["localId"], str(final), final.stat().st_size, h.hexdigest(), spec["purpose"])
            self.client.call("POST", f"/render/{job_id}/complete", {"bytes": final.stat().st_size, "sha256": h.hexdigest()})
            self.done += 1
        except RenderError as e:
            self.failed += 1
            self._report_fail(job_id, str(e), e.retriable)
        except (PlanError, subprocess.TimeoutExpired, OSError) as e:
            self.failed += 1
            self._report_fail(job_id, f"{type(e).__name__}", True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        return True

    def _report_fail(self, job_id: str, msg: str, retriable: bool) -> None:
        try:
            self.client.call("POST", f"/render/{job_id}/fail", {"error": msg[:300], "retriable": retriable})
        except (CentralUnavailable, CentralRejected):
            pass  # a concessão vence sozinha e a central reatribui

    def _render(self, spec: dict, tmp: Path) -> Path:
        src = self._source(spec["localId"])
        meta = source_meta(src, self.ffprobe)
        overlays = []
        for o in spec["overlays"]:
            p = self._fetch_asset(spec["jobId"], o["asset"], o["sha256"], tmp)
            overlays.append(Overlay(str(p), o["logoW"], o["logoH"], o["position"], o["sizePct"], o["opacity"], o["durationS"]))
        music = self._fetch_asset(spec["jobId"], spec["music"]["asset"], spec["music"]["sha256"], tmp) if spec.get("music") else None
        out = tmp / "out.mp4"
        frame = None
        if self.frame_provider is not None:
            row = self.store.get(spec["localId"])
            try:
                frame = self.frame_provider(row["court_key"], spec.get("frameOrientation") or "horizontal")
            except Exception as e:  # noqa: BLE001 - FrameUnavailable: configurada mas ausente => recusa (nunca entrega sem a moldura configurada)
                raise RenderError(str(e) or "moldura indisponível") from e
        plan = ExportPlan(str(src), str(out), meta, spec["audioMode"], str(music) if music else None,
                          spec["originalVolume"], spec["musicVolume"], overlays, frame)
        meta_dur = meta.duration
        from contextlib import nullcontext
        with (self.governor.slot() if self.governor else nullcontext()):        # limite de FFmpeg e pausa se a captura atrasar
            hw = self.governor.hw_profile() if self.governor else None
            rc, err = self._run(plan, spec, meta_dur, hw)
            if rc != 0 and hw is not None and looks_like_hw_failure(err):       # recuo automático: GPU falhou => CPU
                self.governor.report_hw_failure(err.strip().splitlines()[-1] if err.strip() else "falha de hardware")
                Path(plan.out).unlink(missing_ok=True)
                rc, err = self._run(plan, spec, meta_dur, None)
        if rc != 0:
            raise RenderError("tempo limite excedido" if rc < 0 else f"falha no FFmpeg: {err.strip()[-120:]}", retriable=rc < 0)
        verify_output(out, spec["audioMode"], meta.duration, self.ffprobe)
        return out

    def _run(self, plan: ExportPlan, spec: dict, duration: float, hw) -> tuple[int, str]:
        try:
            cmd = build_command(plan, self.ffmpeg, hw)
        except PlanError as e:
            raise RenderError(str(e)) from e
        nice = self.governor.render_nice if self.governor else self.nice
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                preexec_fn=(lambda: os.nice(nice)) if nice else None)
        timer = threading.Timer(self.timeout_s, proc.kill)
        timer.start()
        last = 0.0
        try:
            for line in proc.stdout:  # type: ignore[union-attr]
                m = re.match(r"out_time_(?:us|ms)=(\d+)", line)
                if m and time.monotonic() - last > 2.0:
                    last = time.monotonic()
                    pr = max(1, min(99, int(int(m.group(1)) / 1e6 / duration * 100)))
                    try:
                        self.client.call("POST", f"/render/{spec['jobId']}/progress", {"progress": pr})  # renova a concessão
                    except (CentralUnavailable, CentralRejected):
                        pass
            rc = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
        return rc, (proc.stderr.read() if proc.stderr else "")[-400:]

    def run_forever(self, stop: threading.Event, idle_s: float = 3.0) -> None:  # pragma: no cover - laço de produção
        fails = 0
        while not stop.is_set():
            try:
                if not self.run_one():
                    stop.wait(idle_s)
                fails = 0
            except (CentralUnavailable, CentralRejected):
                fails += 1
                stop.wait(min(60, 1.0 * 2 ** min(fails, 6)))

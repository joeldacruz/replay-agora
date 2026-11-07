"""Montagem dos componentes por quadra a partir da configuração: captura · acionamento · renderização · (entrega no link/relay)."""
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import ExternalCommandRenderer, LogTailTrigger
from .capture import BufferIndex, SegmentRecorder
from .clipper import BufferClipRenderer
from .config import AgentCfg
from .engine import Engine
from .render import SimRenderer
from .resources import ResourceGovernor
from .store import LocalStore
from .triggers import SerialTrigger, TriggerRouter


class CourtDispatchRenderer:
    """Escolhe o renderizador de cada quadra (buffer interno ou comando externo/legado) com a mesma interface."""
    needs_buffer = True
    size_bytes = 4_000_000

    def __init__(self, per_court: dict):
        self.per_court = per_court

    def ready_at(self, court_key, at_epoch, post):
        return self.per_court[court_key].ready_at(court_key, at_epoch, post)

    def render(self, court_key, dest, pre, post, at=None):
        return self.per_court[court_key].render(court_key, dest, pre, post, at)


class StructuralChange(Exception):
    """Quadras adicionadas/removidas: exige reconstruir o motor (momento controlado)."""


@dataclass
class Stack:
    cfg: AgentCfg
    store: LocalStore
    recorders: dict[str, SegmentRecorder] = field(default_factory=dict)
    indexes: dict[str, BufferIndex] = field(default_factory=dict)
    engine: Engine | None = None
    router: TriggerRouter | None = None
    triggers: list = field(default_factory=list)
    renderer: object = None
    governor: ResourceGovernor | None = None
    buf: object = None
    clock: object = None
    started: bool = False

    # ---------------- reconfiguração por quadra (configuração remota) ----------------
    def plan(self, new: AgentCfg) -> dict:
        old = {c.key: c for c in self.cfg.courts}
        nw = {c.key: c for c in new.courts}
        return {"added": sorted(set(nw) - set(old)), "removed": sorted(set(old) - set(nw)),
                "camera": sorted(k for k in set(old) & set(nw) if old[k].camera != nw[k].camera),
                "clip": sorted(k for k in set(old) & set(nw) if old[k].clip != nw[k].clip),
                "trigger": sorted(k for k in set(old) & set(nw) if old[k].trigger != nw[k].trigger),
                "serial": self.cfg.serial != new.serial,
                "resources": self.cfg.resources != new.resources,
                "structural": bool(set(nw) ^ set(old))}

    def _make_recorder(self, c, cfg: AgentCfg) -> SegmentRecorder:
        return SegmentRecorder(c.key, c.camera, Path(cfg.data_dir) / "buffer" / c.key, cfg.ffmpeg_bin, cfg.ffprobe_bin,
                               pinned=(lambda k=c.key: self.store.pinned_windows(k)), tolerance=c.clip.gap_tolerance_s,
                               **({"clock": self.clock} if self.clock else {}))

    def reconfigure(self, new: AgentCfg) -> list[dict]:
        """Aplica a nova configuração SEM reiniciar o que não mudou. Fase 1 prepara tudo (falha aqui => nada foi alterado);
        fase 2 troca quadra a quadra e, se algo falhar, restaura o que já tinha sido trocado. Retorna itens por quadra/controlador."""
        p = self.plan(new)
        if p["structural"]:
            raise StructuralChange("quadras adicionadas ou removidas")
        old_cfg = self.cfg
        newc = {c.key: c for c in new.courts}
        oldc = {c.key: c for c in old_cfg.courts}
        prepared: dict[str, SegmentRecorder] = {}
        for k in p["camera"]:
            if newc[k].camera.mode == "managed" and oldc[k].clip.renderer != "external_command":
                prepared[k] = self._make_recorder(newc[k], new)                         # fase 1: construir (não inicia)
        items: list[dict] = []
        swapped: list[str] = []
        old_triggers = list(self.triggers)
        try:
            for k, rec in prepared.items():                                           # fase 2: troca por quadra
                old = self.recorders.get(k)
                if old:
                    old.stop()
                self.recorders[k], self.indexes[k] = rec, rec.index
                if self.started:
                    rec.start()
                swapped.append(k)
                items.append({"kind": "camera", "key": k, "ok": True, "message": "captura reiniciada só nesta quadra"})
            for k in sorted(set(p["camera"]) | set(p["clip"]) | set(p["trigger"])):
                c = newc[k]
                self.engine.configure_court(k, cooldown_s=c.trigger.cooldown_s, pre=c.clip.pre_s, post=c.clip.post_s)
                if self.buf is not None:
                    self.buf.clips[k], self.buf.cams[k] = c.clip, c.camera
                if k not in prepared:
                    items.append({"kind": "court", "key": k, "ok": True, "message": "parâmetros aplicados sem reiniciar a captura"})
            self.router.buttons.clear()
            self.router.buttons.update(new.button_map())
            self.router.debounce.clear()
            self.router.debounce.update({c.key: c.trigger.debounce_s for c in new.courts})
            if p["serial"]:
                old_active = [s for s in old_cfg.serial if s.mode in ("serial", "log_tail")]
                keep = {s.name: t for s, t in zip(old_active, old_triggers) if s in new.serial}
                for s, t in zip(old_active, old_triggers):
                    if s not in new.serial:
                        t.stop()
                trig = []
                for s in [x for x in new.serial if x.mode in ("serial", "log_tail")]:
                    t = keep.get(s.name) or self._make_trigger(s)
                    if t is not None and s.name not in keep and self.started:
                        t.start(self.router.handle)
                    if t is not None:
                        trig.append(t)
                    items.append({"kind": "controller", "key": s.name, "ok": True, "message": "mantido" if s.name in keep else "controlador (re)iniciado"})
                self.triggers = trig
            if p["resources"] and self.governor is not None:
                r = new.resources
                self.governor.apply(r.max_ffmpeg, r.hwaccel, r.render_nice)
            self.cfg = new
            return items
        except Exception:
            self.cfg = old_cfg                                                        # restaura o que foi trocado
            for k in swapped:
                try:
                    cur = self.recorders.get(k)
                    if cur:
                        cur.stop()
                    rec = self._make_recorder(oldc[k], old_cfg)
                    self.recorders[k], self.indexes[k] = rec, rec.index
                    if self.started:
                        rec.start()
                    if self.buf is not None:
                        self.buf.clips[k], self.buf.cams[k] = oldc[k].clip, oldc[k].camera
                    self.engine.configure_court(k, cooldown_s=oldc[k].trigger.cooldown_s, pre=oldc[k].clip.pre_s, post=oldc[k].clip.post_s)
                except Exception:  # noqa: BLE001 - melhor esforço; o relatório de falha informa
                    pass
            self.router.buttons.clear()
            self.router.buttons.update(old_cfg.button_map())
            self.router.debounce.update({c.key: c.trigger.debounce_s for c in old_cfg.courts})
            raise

    @staticmethod
    def _make_trigger(s):
        if s.mode == "serial":
            return SerialTrigger(s.name, s.port or "", s.baud)
        if s.mode == "log_tail":
            return LogTailTrigger(s.name, s.regex, s.log_file, s.journal_unit)
        return None

    def start(self) -> None:
        self.started = True
        for r in self.recorders.values():
            r.start()
        self.router.recover_pending()                                    # acionamentos registrados e ainda sem job
        for t in self.triggers:
            t.start(self.router.handle)

    def stop(self) -> None:
        for t in self.triggers:
            t.stop()
        self.engine.stop()
        for r in self.recorders.values():
            r.stop()


def build_stack(cfg: AgentCfg, store: LocalStore | None = None, governor: ResourceGovernor | None = None, clock=None) -> Stack:
    data = Path(cfg.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    store = store or LocalStore(str(data / "local.db"))
    st = Stack(cfg, store, clock=clock)
    res = cfg.resources
    st.governor = governor or ResourceGovernor(res.max_ffmpeg, res.hwaccel, res.vaapi_device, res.render_nice, st.recorders,
                                               res.pause_render_when_capture_lags, cfg.ffmpeg_bin)
    per_court: dict = {}
    clips, cams = {}, {}
    from contextlib import nullcontext
    for c in cfg.courts:
        clips[c.key], cams[c.key] = c.clip, c.camera
        if c.clip.renderer == "external_command":
            per_court[c.key] = ExternalCommandRenderer(c.clip.command or [], ffprobe=cfg.ffprobe_bin, nice=cfg.resources.render_nice)
            continue
        if c.camera.mode == "managed":
            rec = SegmentRecorder(c.key, c.camera, data / "buffer" / c.key, cfg.ffmpeg_bin, cfg.ffprobe_bin,
                                  pinned=(lambda k=c.key: store.pinned_windows(k)), tolerance=c.clip.gap_tolerance_s,
                                  **({"clock": clock} if clock else {}))
            st.recorders[c.key], st.indexes[c.key] = rec, rec.index
        else:   # buffer de outro processo: somente leitura
            st.indexes[c.key] = BufferIndex(data / "buffer" / c.key, c.camera.segment_s, glob=c.camera.external_glob,
                                            time_source=c.camera.external_time, ffprobe=cfg.ffprobe_bin)
    managed = [k for k in st.indexes]
    buf = BufferClipRenderer(st.indexes, st.recorders, {k: clips[k] for k in managed}, {k: cams[k] for k in managed},
                             ffmpeg=cfg.ffmpeg_bin, ffprobe=cfg.ffprobe_bin, governor=st.governor, nice=cfg.resources.render_nice)
    for k in managed:
        per_court[k] = buf
    st.buf = buf
    st.renderer = CourtDispatchRenderer(per_court)
    st.engine = Engine(cfg.arena_tag, data / "replays", [c.key for c in cfg.courts], store, st.renderer, dedup_s=3.0)
    for c in cfg.courts:
        st.engine.configure_court(c.key, cooldown_s=c.trigger.cooldown_s, pre=c.clip.pre_s, post=c.clip.post_s)
    st.router = TriggerRouter(st.engine, store, cfg.button_map(), {c.key: c.trigger.debounce_s for c in cfg.courts})
    for s in cfg.serial:
        t = Stack._make_trigger(s)
        if t is not None:
            st.triggers.append(t)
    return st

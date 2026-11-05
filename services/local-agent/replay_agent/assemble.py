"""Montagem dos componentes por quadra a partir da configuração: captura · acionamento · renderização · (entrega no link/relay)."""
from dataclasses import dataclass, field
from pathlib import Path

from .adapters import ExternalCommandRenderer, LogTailTrigger
from .capture import BufferIndex, SegmentRecorder
from .clipper import BufferClipRenderer
from .config import AgentCfg
from .engine import Engine
from .render import SimRenderer
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

    def start(self) -> None:
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


def build_stack(cfg: AgentCfg, store: LocalStore | None = None, slot=None, encoder_args=None, clock=None) -> Stack:
    data = Path(cfg.data_dir)
    data.mkdir(parents=True, exist_ok=True)
    store = store or LocalStore(str(data / "local.db"))
    st = Stack(cfg, store)
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
                             ffmpeg=cfg.ffmpeg_bin, ffprobe=cfg.ffprobe_bin, slot=slot or (lambda: nullcontext()),
                             nice=cfg.resources.render_nice, **({"encoder_args": encoder_args} if encoder_args else {}))
    for k in managed:
        per_court[k] = buf
    st.renderer = CourtDispatchRenderer(per_court)
    st.engine = Engine(cfg.arena_tag, data / "replays", [c.key for c in cfg.courts], store, st.renderer, dedup_s=3.0)
    for c in cfg.courts:
        st.engine.configure_court(c.key, cooldown_s=c.trigger.cooldown_s, pre=c.clip.pre_s, post=c.clip.post_s)
    st.router = TriggerRouter(st.engine, store, cfg.button_map(), {c.key: c.trigger.debounce_s for c in cfg.courts})
    for s in cfg.serial:
        if s.mode == "serial":
            st.triggers.append(SerialTrigger(s.name, s.port or "", s.baud))
        elif s.mode == "log_tail":
            st.triggers.append(LogTailTrigger(s.name, s.regex, s.log_file, s.journal_unit))
    return st

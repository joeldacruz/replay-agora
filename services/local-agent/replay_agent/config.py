"""Configuração do agente em JSON (arquivo 0600; contém URLs RTSP com credencial — NUNCA versionar nem registrar em log).

Cada quadra tem configuração INDEPENDENTE de câmera, botoeira, janela do replay e armazenamento. Separação de responsabilidades:
captura (camera/buffer) · acionamento (trigger) · renderização (clip/render) · entrega (relay) — cada uma tem o seu bloco.
"""
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{1,60}$")
MSG_RE = re.compile(r"^[A-Za-z0-9_\-]{3,40}$")
HWACCEL = ("none", "auto", "vaapi", "qsv")


class ConfigError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def redact_url(url: str) -> str:
    """rtsp://user:senha@host/x -> rtsp://***@host/x (para qualquer log, painel ou mensagem de erro)."""
    try:
        p = urlsplit(url)
        if p.username or p.password:
            host = p.hostname or ""
            if p.port:
                host += f":{p.port}"
            return urlunsplit((p.scheme, f"***@{host}", p.path, "", ""))
        return urlunsplit((p.scheme, p.netloc, p.path, "", "")) if p.query else url
    except ValueError:
        return "<url inválida>"


def redact_text(text: str) -> str:
    return re.sub(r"(rtsps?|rtmp|http)://[^\s'\"@/]+@", r"\1://***@", text)


@dataclass
class CameraCfg:
    url: str = ""                      # rtsp://… | file:/caminho.mp4 (laço em tempo real, para testes) | lavfi:testsrc2 (sintético)
    enabled: bool = True
    transport: str = "tcp"             # tcp é o mais robusto contra perda de pacotes
    segment_s: float = 2.0             # o GOP da câmera deve ser <= segment_s (cortes só ocorrem em keyframes)
    buffer_s: float = 120.0            # duração do buffer circular desta câmera
    stall_s: float = 10.0              # sem progresso por N s => mata e reconecta
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0
    rtsp_timeout_s: float = 10.0
    mode: str = "managed"              # managed: o agente grava/rotaciona | external_dir: lê (somente leitura) segmentos de outro processo
    external_glob: str | None = None   # ex.: /srv/replayagora/quadra01/buffer/*.ts (somente em external_dir)
    external_time: str = "name_epoch"  # name_epoch | mtime (mtime = instante de FECHAMENTO do segmento)


@dataclass
class TriggerCfg:
    buttons: list[str] = field(default_factory=list)   # mensagens seriais desta quadra (várias botoeiras por quadra)
    debounce_s: float = 0.3            # ignora repique da MESMA botoeira
    cooldown_s: float = 3.0            # intervalo mínimo entre replays aceitos da quadra


@dataclass
class ClipCfg:
    pre_s: float = 15.0
    post_s: float = 5.0
    min_pre_s: float = 5.0             # sem cobertura contínua de pelo menos [t0-min_pre, t0+min_post] => irrecuperável
    min_post_s: float = 2.0
    gap_tolerance_s: float = 2.5
    mode: str = "copy"                 # copy (barato; corta no keyframe) | encode (exato; custa CPU/GPU)
    renderer: str = "internal"         # internal | external_command (adaptador para replay.py legado)
    command: list[str] | None = None   # modelo com {court} {t0} {t0_iso} {pre} {post} {out}


@dataclass
class CourtCfg:
    key: str
    label: str = ""
    camera: CameraCfg = field(default_factory=CameraCfg)
    trigger: TriggerCfg = field(default_factory=TriggerCfg)
    clip: ClipCfg = field(default_factory=ClipCfg)


@dataclass
class SerialCfg:
    name: str
    mode: str = "serial"               # serial | log_tail (modo sombra: lê o log do leitor.py legado sem tocar na porta) | none
    port: str | None = None            # /dev/ttyUSB0 ou /dev/replayagora/botoeira (regra udev)
    baud: int = 9600
    log_file: str | None = None
    journal_unit: str | None = None    # ex.: replayagora-leitor.service
    regex: str = r"(REPLAY_[A-Za-z0-9_\-]+)"


@dataclass
class ResourceCfg:
    max_ffmpeg: int = 1                # processos FFmpeg de renderização/corte simultâneos (captura NÃO conta)
    hwaccel: str = "none"              # none | auto | vaapi | qsv
    vaapi_device: str = "/dev/dri/renderD128"
    render_nice: int = 10              # prioridade baixa para render/corte; a captura roda em prioridade normal
    pause_render_when_capture_lags: bool = True


@dataclass
class CentralCfg:
    base_url: str = ""
    node_id: str = ""
    credentials_file: str = "/etc/replayagora/credentials.json"
    ca_file: str | None = None
    client_cert: str | None = None
    client_key: str | None = None


@dataclass
class AgentCfg:
    arena_tag: str
    data_dir: Path
    courts: list[CourtCfg]
    serial: list[SerialCfg] = field(default_factory=list)
    central: CentralCfg = field(default_factory=CentralCfg)
    resources: ResourceCfg = field(default_factory=ResourceCfg)
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    def button_map(self) -> dict[str, str]:
        return {b: c.key for c in self.courts for b in c.trigger.buttons}

    def court(self, key: str) -> CourtCfg:
        return next(c for c in self.courts if c.key == key)

    def public_summary(self) -> dict:
        """Versão SEM segredos (para diagnóstico)."""
        return {"arenaTag": self.arena_tag, "courts": [{"key": c.key, "label": c.label, "camera": redact_url(c.camera.url),
                                                         "buttons": c.trigger.buttons, "preS": c.clip.pre_s, "postS": c.clip.post_s} for c in self.courts]}


def _mk(cls, data: dict, where: str, errs: list[str]):
    known = set(cls.__dataclass_fields__)
    for k in data:
        if k not in known:
            errs.append(f"{where}: campo desconhecido '{k}'")
    return cls(**{k: v for k, v in data.items() if k in known})


def parse(data: dict) -> AgentCfg:
    errs: list[str] = []
    if not isinstance(data, dict):
        raise ConfigError(["raiz deve ser um objeto"])
    tag = data.get("arena_tag", "")
    if not KEY_RE.match(str(tag)):
        errs.append("arena_tag inválida")
    courts: list[CourtCfg] = []
    for i, c in enumerate(data.get("courts", [])):
        w = f"courts[{i}]"
        key = str(c.get("key", ""))
        if not KEY_RE.match(key):
            errs.append(f"{w}.key inválida")
            continue
        cc = CourtCfg(key=key, label=str(c.get("label", key)),
                      camera=_mk(CameraCfg, c.get("camera", {}), w + ".camera", errs),
                      trigger=_mk(TriggerCfg, c.get("trigger", {}), w + ".trigger", errs),
                      clip=_mk(ClipCfg, c.get("clip", {}), w + ".clip", errs))
        cam, trg, clip = cc.camera, cc.trigger, cc.clip
        if cam.enabled and cam.mode == "managed" and not re.match(r"^(rtsps?://|file:|lavfi:)", cam.url or ""):
            errs.append(f"{w}.camera.url deve começar com rtsp://, file: ou lavfi:")
        if cam.mode not in ("managed", "external_dir") or (cam.mode == "external_dir" and not cam.external_glob):
            errs.append(f"{w}.camera.mode inválido (external_dir exige external_glob)")
        if cam.transport not in ("tcp", "udp"):
            errs.append(f"{w}.camera.transport deve ser tcp ou udp")
        if cam.external_time not in ("name_epoch", "mtime"):
            errs.append(f"{w}.camera.external_time inválido")
        if not (0.5 <= cam.segment_s <= 10) or cam.buffer_s < clip.pre_s + clip.post_s + 2 * cam.segment_s:
            errs.append(f"{w}.camera: segment_s em [0.5,10] e buffer_s >= pre+post+2*segment_s")
        if not (0 <= clip.min_pre_s <= clip.pre_s and 0 <= clip.min_post_s <= clip.post_s and clip.pre_s > 0 and clip.post_s >= 0):
            errs.append(f"{w}.clip: janelas inválidas (min_* <= valor)")
        if clip.mode not in ("copy", "encode") or clip.renderer not in ("internal", "external_command"):
            errs.append(f"{w}.clip.mode/renderer inválido")
        if clip.renderer == "external_command" and (not clip.command or not any("{out}" in a for a in clip.command)):
            errs.append(f"{w}.clip.command precisa conter {{out}}")
        if not (0 <= trg.debounce_s <= 5 and 0 <= trg.cooldown_s <= 120):
            errs.append(f"{w}.trigger: debounce_s em [0,5] e cooldown_s em [0,120]")
        for b in trg.buttons:
            if not MSG_RE.match(str(b)):
                errs.append(f"{w}.trigger.buttons: '{b}' inválido")
        courts.append(cc)
    if len({c.key for c in courts}) != len(courts):
        errs.append("courts: chaves repetidas")
    seen: dict[str, str] = {}
    for c in courts:
        for b in c.trigger.buttons:
            if b in seen and seen[b] != c.key:
                errs.append(f"botoeira '{b}' mapeada para duas quadras ({seen[b]} e {c.key})")
            seen[b] = c.key
    serial = [_mk(SerialCfg, s, f"serial[{i}]", errs) for i, s in enumerate(data.get("serial", []))]
    for s in serial:
        if s.mode == "serial" and not (s.port or "").startswith("/dev/"):
            errs.append(f"serial {s.name}: port deve estar em /dev/")
        if s.mode == "log_tail" and not (s.log_file or s.journal_unit):
            errs.append(f"serial {s.name}: log_tail exige log_file ou journal_unit")
        if s.mode not in ("serial", "log_tail", "none"):
            errs.append(f"serial {s.name}: mode inválido")
        try:
            re.compile(s.regex)
        except re.error:
            errs.append(f"serial {s.name}: regex inválida")
    res = _mk(ResourceCfg, data.get("resources", {}), "resources", errs)
    if not (1 <= res.max_ffmpeg <= 16) or res.hwaccel not in HWACCEL or not (0 <= res.render_nice <= 19):
        errs.append("resources: max_ffmpeg em [1,16], hwaccel em none|auto|vaapi|qsv, render_nice em [0,19]")
    central = _mk(CentralCfg, data.get("central", {}), "central", errs)
    if "data_dir" not in data:
        errs.append("data_dir obrigatório")
    if errs:
        raise ConfigError(errs)
    return AgentCfg(arena_tag=tag, data_dir=Path(data["data_dir"]), courts=courts, serial=serial, central=central, resources=res,
                    ffmpeg_bin=data.get("ffmpeg_bin", "ffmpeg"), ffprobe_bin=data.get("ffprobe_bin", "ffprobe"))


def load_config(path: str | Path) -> AgentCfg:
    p = Path(path)
    if p.stat().st_mode & 0o077:
        raise ConfigError([f"{p} deve ter permissão 0600 (contém credenciais de câmera)"])
    try:
        return parse(json.loads(p.read_text()))
    except json.JSONDecodeError as e:
        raise ConfigError([f"JSON inválido: {e}"]) from e

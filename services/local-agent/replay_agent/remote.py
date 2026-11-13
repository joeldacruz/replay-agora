"""Configuração remota no agente: recebe do Portal (via API central), valida, prepara, aplica com rollback e confirma a versão ATIVA.

Garantias:
  * o agente SEMPRE inicia a conexão (funciona atrás de NAT); nada de shell remoto — só tarefas de lista fechada (ping, scan,
    teste de câmera, teste de controlador, simulação de acionamento, ações de serviço autorizadas);
  * senha de câmera vai para o cofre cifrado local; nunca para arquivo de configuração, log ou diagnóstico;
  * molduras são baixadas, validadas por SHA-256 e mantidas em cache local (o render não depende da central);
  * a configuração anterior que funcionava é preservada: falha => reversão automática + relatório;
  * mudança que só afeta uma quadra reinicia só aquela quadra; mudança estrutural exige permissão explícita do Portal.
"""
import copy
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from urllib.parse import quote

from . import __version__, camtest
from .assemble import StructuralChange
from .client import CentralRejected, CentralUnavailable
from .config import AgentCfg, ConfigError, parse, redact_text
from .devices import PORT_ID_RE, Devices
from .ffplan import FramePlan
from .vault import CameraVault, VaultError, _atomic_write

log = logging.getLogger("replay_agent.remote")
SCHEMA = 1
AUTHORIZED_SERVICES = {"agent": "Agente local", "trigger_listener": "Listener de botoeiras", "render_queue": "Fila de exportação/processamento de replays",
                       "link_sync": "Sincronização com a API central", "storage_manager": "Gerenciamento de armazenamento", "monitor": "Monitoramento"}


class ApplyError(Exception):
    def __init__(self, msg: str, items: list[dict] | None = None, rolled_back: bool = False):
        super().__init__(msg)
        self.items, self.rolled_back = items or [], rolled_back


class FrameUnavailable(Exception):
    pass


class RingLog(logging.Handler):
    """Últimas linhas de log do agente (já com segredos redigidos) para consulta filtrada pelo Portal."""

    def __init__(self, size: int = 400):
        super().__init__(level=logging.INFO)
        self.lines: deque = deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append({"t": record.created, "level": record.levelname, "logger": record.name, "msg": redact_text(record.getMessage())[:300]})
        except Exception:  # noqa: BLE001
            pass

    def tail(self, n: int = 80, level: str | None = None, contains: str | None = None) -> list[dict]:
        rows = [r for r in self.lines if (not level or r["level"] == level) and (not contains or contains.lower() in r["msg"].lower())]
        return rows[-n:]


RING = RingLog()


def install_ring() -> None:
    lg = logging.getLogger("replay_agent")
    if RING not in lg.handlers:
        lg.addHandler(RING)
        if lg.level in (logging.NOTSET, logging.WARNING):
            lg.setLevel(logging.INFO)


def rtsp_url(cam: dict, secret: dict | None) -> str:
    user = (secret or {}).get("username") if secret else cam.get("username")
    pw = (secret or {}).get("password") if secret else None
    cred = f"{quote(user or '', safe='')}:{quote(pw or '', safe='')}@" if user else ""
    return f"rtsp://{cred}{cam['host']}:{int(cam.get('port') or 554)}{cam.get('path') or ''}"


def serial_path(port_id: str) -> str | None:
    if not PORT_ID_RE.match(port_id or ""):
        return None
    return f"/dev/serial/by-id/{port_id[6:]}" if port_id.startswith("by-id:") else f"/dev/{port_id[4:]}"


def build_effective(base_raw: dict, snap: dict, vault: CameraVault, devices: Devices | None = None, url_builder=None, strict: bool = True) -> tuple[AgentCfg, list[dict]]:
    """Mescla a configuração LOCAL (que continua valendo para o que o Portal não gerencia) com o snapshot remoto.
    Quadras/controladores presentes no snapshot passam a ser geridos remotamente; o restante permanece intacto."""
    url_builder = url_builder or rtsp_url
    notes: list[dict] = []
    raw = copy.deepcopy(base_raw)
    keys = {c["key"] for c in snap["courts"]}
    ctl_by_court: dict[str, list[dict]] = {}
    serial = []
    for k in snap["controllers"]:
        if k["kind"] != "arduino_usb":
            raise ApplyError(f"O controlador '{k['name']}' é do tipo '{k['kind']}', ainda não suportado por este agente.")
        path = serial_path(k["portId"])
        if not path:
            raise ApplyError(f"O controlador '{k['name']}' tem porta inválida.")
        if strict and devices is not None and devices.serial.resolve(k["portId"]) is None:
            raise ApplyError(f"A porta do controlador '{k['name']}' não foi encontrada neste servidor. Conecte o dispositivo e use 'Detectar dispositivos'.")
        serial.append({"name": k["name"], "mode": "serial", "port": path, "baud": k["baud"], "reconnect": "discard_pending"})
        if k["reconnect"] != "discard_pending":
            notes.append({"kind": "controller", "key": k["name"], "ok": True, "message": "comportamento 'processar pendentes' ainda não suportado: aplicado 'descartar pendentes'"})
        for i in k["inputs"]:
            if i["enabled"] and i["courtKey"]:
                ctl_by_court.setdefault(i["courtKey"], []).append({"ref": i["ref"], "ctl": k})
    courts = [c for c in raw.get("courts", []) if c.get("key") not in keys]
    for c in snap["courts"]:
        cam, rp = c["camera"], c["replay"]
        enabled = bool(c["active"] and cam)
        url = ""
        if enabled:
            sec = None
            if cam["hasSecret"]:
                sec = vault.get(cam["id"], cam["secretVersion"])
                if sec is None:
                    raise ApplyError(f"Senha da câmera '{cam['name']}' (v{cam['secretVersion']}) não está disponível no cofre local.")
            url = url_builder(cam, sec)
        feeds = ctl_by_court.get(c["key"], [])
        courts.append({
            "key": c["key"], "label": c["label"],
            "camera": {"url": url, "enabled": enabled, "transport": (cam or {}).get("transport", "tcp"), "segment_s": rp["segmentS"], "buffer_s": rp["bufferS"],
                       "reconnect_min_s": rp["reconnectMinS"], "reconnect_max_s": rp["reconnectMaxS"], "mode": "managed"},
            "trigger": {"buttons": [f["ref"] for f in feeds], "debounce_s": max([f["ctl"]["debounceS"] for f in feeds] or [0.3]),
                        "cooldown_s": max([max(f["ctl"]["cooldownS"], f["ctl"]["minIntervalS"]) for f in feeds] or [3.0])},
            "clip": {"pre_s": rp["preS"], "post_s": rp["postS"], "min_pre_s": min(5.0, rp["preS"]), "min_post_s": min(2.0, rp["postS"]), "close_s": rp["closeCountdownS"]},
        })
    raw["courts"] = courts
    names = {s["name"] for s in serial}
    raw["serial"] = [s for s in raw.get("serial", []) if s.get("name") not in names] + serial
    try:
        return parse(raw), notes
    except ConfigError as e:
        raise ApplyError("Configuração rejeitada pelo agente: " + "; ".join(e.errors[:4])) from e


class RemoteManager:
    def __init__(self, host, client, data_dir: Path, devices: Devices | None = None, vault: CameraVault | None = None, url_builder=None,
                 ffprobe: str = "ffprobe", ffmpeg: str = "ffmpeg", runner=None, connect=None, clock=time.time):
        self.host, self.client, self.dir = host, client, Path(data_dir) / "remote"
        self.devices = devices or Devices()
        self.vault = vault or CameraVault(self.dir)
        self.url_builder, self.ffprobe, self.ffmpeg = url_builder, ffprobe, ffmpeg
        self.runner, self.connect, self.clock = runner, connect, clock
        self.frames_dir = self.dir / "frames"
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.dir / "state.json"
        self.result_path = self.dir / "pending-result.json"
        self.state = self._load_state()
        self.lock = threading.Lock()
        self.last_inventory = 0.0
        self.last_apply: dict | None = None
        self.service_events: dict[str, dict] = {}
        install_ring()

    # ------------------------------------------------------------ estado persistente
    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {"activeVersion": 0, "snapshot": None, "prevVersion": 0, "prevSnapshot": None}

    def _save_state(self) -> None:
        _atomic_write(self.state_path, json.dumps(self.state).encode())

    @property
    def active_version(self) -> int:
        return int(self.state.get("activeVersion") or 0)

    def effective_at_boot(self, base_raw: dict) -> AgentCfg | None:
        """Última configuração remota que funcionou (sobrevive a reinício e a queda de internet). Porta ausente não impede o boot."""
        snap = self.state.get("snapshot")
        if not snap:
            return None
        try:
            return build_effective(base_raw, snap, self.vault, self.devices, self.url_builder, strict=False)[0]
        except (ApplyError, VaultError) as e:
            log.error("configuração remota ativa não pôde ser reconstruída no boot: %s", e)
            return None

    # ------------------------------------------------------------ molduras (cache local verificado)
    def _frame_path(self, version_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", version_id):
            raise ApplyError("identificador de moldura inválido")
        return self.frames_dir / f"{version_id}.png"

    def _stage_frame(self, asset: dict) -> None:
        p = self._frame_path(asset["versionId"])
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() == asset["sha256"]:
            return
        r = self.client.request_raw("GET", asset["url"], timeout=60)
        data = r.content
        if hashlib.sha256(data).hexdigest() != asset["sha256"] or r.headers.get("x-sha256", asset["sha256"]) != asset["sha256"] or len(data) != asset["bytes"]:
            raise ApplyError("Arquivo de moldura corrompido em trânsito (SHA-256 não confere); a configuração anterior foi mantida.")
        tmp = p.with_suffix(".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def frame_for(self, court_key: str, orientation: str) -> FramePlan | None:
        """Moldura da quadra para o render. None = sem moldura configurada. Levanta FrameUnavailable se configurada mas ausente/corrompida."""
        snap = self.state.get("snapshot")
        if not snap:
            return None
        court = next((c for c in snap["courts"] if c["key"] == court_key), None)
        if not court:
            return None
        if orientation not in court["replay"]["formats"]:
            raise FrameUnavailable(f"o formato '{orientation}' não está habilitado para esta quadra")
        fr = court["frames"].get(orientation) if court["frames"]["enabled"] else None
        if not fr:
            return None
        p = self._frame_path(fr["versionId"])
        if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest() != fr["sha256"]:
            raise FrameUnavailable("moldura indisponível ou corrompida no servidor local")
        w, h = (int(x) for x in fr["out"].split("x"))
        return FramePlan(str(p), fr["width"], fr["height"], fr["window"], fr["layout"], w, h)

    def _prune_cache(self) -> None:
        keep = set()
        for snap in (self.state.get("snapshot"), self.state.get("prevSnapshot")):
            for c in (snap or {}).get("courts", []):
                for f in c["frames"].values():
                    if isinstance(f, dict):
                        keep.add(f["versionId"])
        for p in self.frames_dir.glob("*.png"):
            if p.stem not in keep:
                p.unlink(missing_ok=True)
        cams = set()
        for snap in (self.state.get("snapshot"), self.state.get("prevSnapshot")):
            for c in (snap or {}).get("courts", []):
                if c.get("camera"):
                    cams.add(c["camera"]["id"])
        try:
            self.vault.prune(cams)
        except VaultError:
            pass

    # ------------------------------------------------------------ aplicação
    def _confirm_pending_result(self) -> None:
        """Reenvia somente o RESULTADO persistido; nunca a ordem de aplicar."""
        if not self.result_path.exists():
            return
        pending = json.loads(self.result_path.read_text())
        version = int(pending["version"])
        self.client.call("POST", f"/remote/{version}/result", pending["report"])
        self.result_path.unlink()

    def poll(self) -> dict | None:
        """Confirma um resultado anterior antes de buscar novos comandos."""
        self._confirm_pending_result()
        rev = self.client.call("GET", "/remote/pending")
        if not rev:
            return None
        v = int(rev["version"])
        received = self.client.call("POST", f"/remote/{v}/received", {})
        if received.get("status") != "recebido":
            raise ApplyError("central recusou a entrega da revisão")
        progress = self.client.call("POST", f"/remote/{v}/progress", {})
        if progress.get("status") != "aplicando":
            raise ApplyError("central recusou o início da aplicação")
        report = self.apply(rev)
        # Resultado persistido ANTES do envio: perda de rede ou processo
        # após a aplicação não resulta em uma nova ativação de câmeras.
        _atomic_write(self.result_path, json.dumps({"version": v, "report": report}).encode())
        try:
            self._confirm_pending_result()
        except CentralRejected as e:
            log.warning("central recusou o resultado da v%s: %s", v, e.detail)
            # Falha fechada: manter o resultado local para auditoria e
            # evitar novo comando até reconciliar a divergência.
            raise
        self.last_apply = {"version": v, "status": report["status"], "at": self.clock()}
        self.last_inventory = 0.0
        return report

    def apply(self, rev: dict) -> dict:
        v, snap = rev["version"], rev["snapshot"]
        with self.lock:
            prev_active = self.active_version
            items: list[dict] = []
            if v == prev_active and self.state.get("snapshot") == snap:    # reentrega da mesma versão: idempotente
                return {"status": "aplicado", "activeVersion": v, "items": [{"kind": "agent", "key": "", "ok": True, "message": "versão já estava ativa"}], "rolledBack": False}
            try:
                if snap.get("schema") != SCHEMA:
                    raise ApplyError("versão de esquema de configuração incompatível com este agente")
                # 1) segredos -> cofre (por versão)
                for cid, sec in (rev.get("secrets") or {}).items():
                    if not sec.get("ok") or sec.get("password") is None:
                        raise ApplyError("A central não conseguiu entregar uma senha de câmera (cadastre-a novamente no Portal).")
                    self.vault.put(cid, sec.get("username") or "", sec["password"], int(sec["version"]))
                # 2) molduras -> cache verificado
                for asset in rev.get("assets") or []:
                    self._stage_frame(asset)
                # 3) configuração efetiva (valida tudo antes de tocar no motor)
                new_cfg, notes = build_effective(self.host.base_raw(), snap, self.vault, self.devices, self.url_builder, strict=True)
                items += notes
                plan = self.host.stack.plan(new_cfg)
                if plan["structural"] and not rev.get("allowRestart"):
                    raise ApplyError("A alteração adiciona/remove quadras e exige reinício controlado do motor. Marque 'permitir reinício' ao aplicar e escolha um momento sem jogos.")
                # 4) ativar (por quadra; reversão automática se falhar)
                try:
                    items += self.host.activate(new_cfg, allow_restart=bool(rev.get("allowRestart")))
                except StructuralChange:
                    items += self.host.rebuild(new_cfg)
                except Exception as e:  # noqa: BLE001
                    raise ApplyError("Falha ao ativar a configuração; a anterior foi restaurada: " + type(e).__name__, items, rolled_back=True) from e
                # 5) confirmar e persistir
                self.state = {"activeVersion": v, "snapshot": snap, "prevVersion": prev_active, "prevSnapshot": self.state.get("snapshot")}
                self._save_state()
                self._prune_cache()
                for c in snap["courts"]:
                    rp = c["replay"]
                    items.append({"kind": "retention", "key": c["key"], "ok": True,
                                  "message": f"retenção {rp['retentionDays']} d / limite {rp['storageLimitMb'] or '—'} MB registrados (exclusão automática segue desativada)"})
                log.info("configuração remota v%s aplicada", v)
                return {"status": "aplicado", "activeVersion": v, "items": items[:190], "rolledBack": False}
            except (ApplyError, CentralUnavailable, OSError, VaultError) as e:
                if isinstance(e, ApplyError):
                    items = (e.items or items) + [{"kind": "erro", "key": "", "ok": False, "message": redact_text(str(e))[:290]}]
                    rolled = e.rolled_back
                else:
                    items.append({"kind": "erro", "key": "", "ok": False, "message": f"{type(e).__name__}: não foi possível preparar a configuração"})
                    rolled = False
                log.warning("configuração remota v%s falhou: %s", v, redact_text(str(e))[:200])
                return {"status": "falha", "activeVersion": prev_active, "items": items[:190], "error": redact_text(str(e))[:390], "rolledBack": rolled}

    # ------------------------------------------------------------ tarefas
    def run_tasks(self, limit: int = 5) -> int:
        n = 0
        for _ in range(limit):
            t = self.client.call("GET", "/remote/tasks/next")
            if not t:
                break
            n += 1
            try:
                res = self.handle_task(t["kind"], t.get("params") or {})
                body = {"ok": bool(res.get("ok", True)), "result": res, "error": res.get("error")}
                if "snapshotJpegB64" in res:
                    body["snapshotJpegB64"] = res.pop("snapshotJpegB64")
            except Exception as e:  # noqa: BLE001
                body = {"ok": False, "result": {}, "error": f"{type(e).__name__} ao executar a tarefa"}
            self.client.call("POST", f"/remote/tasks/{t['id']}/result", body)
        return n

    def handle_task(self, kind: str, p: dict) -> dict:
        if kind == "ping":
            return {"ok": True, "serverTime": self.clock(), "version": __version__, "activeVersion": self.active_version}
        if kind == "scan_devices":
            return {"ok": True, **self.devices.scan(), "capabilities": self.capabilities()}
        if kind == "camera_test":
            kw = {k: v for k, v in (("runner", self.runner), ("connect", self.connect)) if v}
            res = camtest.run_camera_test(p, self.ffprobe, self.ffmpeg, **kw)
            snap = res.pop("snapshotJpegB64", None)
            if snap:
                res["snapshotJpegB64"] = snap
            return res
        if kind == "controller_test":
            return self._controller_test(p)
        if kind == "trigger_dry_run":
            return self._dry_run(p.get("courtKey", ""))
        if kind == "service_action":
            return self._service_action(p)
        return {"ok": False, "error": "tarefa não suportada"}

    def _find_trigger(self, controller_id: str):
        snap = self.state.get("snapshot") or {}
        ctl = next((k for k in snap.get("controllers", []) if k["id"] == controller_id), None)
        if not ctl:
            return None, None
        trig = next((t for t in self.host.stack.triggers if getattr(t, "name", None) == ctl["name"]), None)
        return ctl, trig

    def _controller_test(self, p: dict) -> dict:
        ctl, trig = self._find_trigger(p.get("controllerId", ""))
        if not ctl:
            return {"ok": False, "code": "not_applied", "message": "Este controlador ainda não foi aplicado ao servidor. Aplique a configuração antes de testar."}
        resolved = self.devices.serial.resolve(ctl["portId"]) is not None
        if not trig:
            return {"ok": False, "code": "no_listener", "message": "O listener deste controlador não está ativo.", "portPresent": resolved}
        d = trig.diagnostics()
        base = {"portPresent": resolved, "state": d["state"], "reconnects": d["reconnects"], "garbled": d["garbled"]}
        if p.get("action") != "monitor":
            ok = d["state"] == "connected"
            return {"ok": ok, "code": "ok" if ok else d["state"], "message": "Comunicação com o controlador OK." if ok else f"Controlador {d['state']}.", **base}
        t0 = self.clock()
        time.sleep(min(30, int(p.get("seconds") or 10)))
        rows = [r for r in self.host.stack.store.recent_triggers(50) if r["ts"] >= t0 and str(r["source"]).startswith(f"serial:{ctl['name']}")]
        return {"ok": True, "code": "monitor", "message": f"{len(rows)} evento(s) recebido(s) em {p.get('seconds', 10)} s.",
                "events": [{"input": r["button"], "court": r["court_key"], "outcome": r["outcome"], "at": r["ts"]} for r in rows], **base}

    def _dry_run(self, court_key: str) -> dict:
        """Simula o acionamento SEM criar job nem publicar vídeo: verifica quadra, captura, buffer e cooldown."""
        st = self.host.stack
        if court_key not in st.engine.workers:
            return {"ok": False, "code": "unknown_court", "message": "Quadra desconhecida neste servidor."}
        rec = st.recorders.get(court_key)
        idx = st.indexes.get(court_key)
        segs = idx.scan() if idx else []
        cover = round(sum(s.dur for s in segs), 1) if segs and hasattr(segs[0], "dur") else len(segs)
        state = rec.state if rec else "external"
        ok = state in ("streaming", "external", "connecting") and bool(segs)
        return {"ok": ok, "code": "ok" if ok else "not_ready", "dryRun": True, "publishes": False, "captureState": state, "bufferSegments": len(segs),
                "bufferCoverage": cover, "cooldownS": st.engine.policy[court_key]["cooldown_s"],
                "message": "Acionamento seria aceito (nenhum vídeo foi gerado)." if ok else "Quadra ainda sem captura/buffer suficiente."}

    def _service_action(self, p: dict) -> dict:
        svc, action, ck = p.get("service"), p.get("action"), p.get("courtKey")
        st = self.host.stack
        if action == "logs":
            return {"ok": True, "logs": RING.tail(80, contains=ck or None)}
        if action == "check":
            inv = {s["name"]: s for s in self.services()}
            row = inv.get(svc) or next((s for n, s in inv.items() if n.startswith(f"{svc}:")), None)
            return {"ok": bool(row and row["status"] == "running"), "service": svc, "detail": row or {}, "message": "Serviço íntegro." if row and row["status"] == "running" else "Serviço com problema."}
        if action in ("restart_component", "reconnect_device"):
            if svc == "capture" and ck in st.recorders:
                old = st.recorders[ck]
                c = next(x for x in st.cfg.courts if x.key == ck)
                old.stop()
                rec = st._make_recorder(c, st.cfg)
                st.recorders[ck], st.indexes[ck] = rec, rec.index
                rec.start()
                self.service_events[f"capture:{ck}"] = {"lastStart": self.clock()}
                return {"ok": True, "message": f"Captura da quadra {ck} reiniciada (outras quadras não foram afetadas)."}
            if svc == "trigger_listener":
                for t in st.triggers:
                    if hasattr(t, "stop") and hasattr(t, "start") and getattr(t, "port", None):
                        t.stop()
                        t.stop_flag.clear()
                        t.start(st.router.handle)
                self.service_events["trigger_listener"] = {"lastStart": self.clock()}
                return {"ok": True, "message": "Listener de botoeiras reconectado."}
            return {"ok": False, "unsupported": True, "message": "Reinício individual deste componente não é suportado nesta versão."}
        return {"ok": False, "error": "ação não permitida"}

    # ------------------------------------------------------------ inventário
    def capabilities(self) -> dict:
        ffv = None
        try:
            ffv = subprocess.run([self.ffmpeg, "-version"], capture_output=True, text=True, timeout=5).stdout.splitlines()[0].split(" ")[2]
        except (OSError, subprocess.SubprocessError, IndexError):
            pass
        from .resources import detect_hw
        try:
            hw = detect_hw(self.ffmpeg)
        except Exception:  # noqa: BLE001
            hw = {}
        return {"ffmpeg": ffv, "hw": {k: hw.get(k) for k in ("vaapi", "qsv")}, "agent": __version__}

    def services(self) -> list[dict]:
        st = self.host.stack
        now = self.clock()
        out = [{"name": "agent", "label": AUTHORIZED_SERVICES["agent"], "purpose": "Processo principal do servidor local", "status": "running",
                "lastStart": self.host.started_at, "lastFailure": None, "restarts": [], "error": None, "deps": []}]
        for k, r in st.recorders.items():
            alive = r.is_alive() and r.state in ("streaming", "connecting", "reconnecting")
            out.append({"name": f"capture:{k}", "label": f"Captura — quadra {k}", "purpose": "Gravação contínua do buffer (FFmpeg)", "status": "running" if r.state == "streaming" else ("degraded" if alive else "stopped"),
                        "state": r.state, "court": k, "lastStart": self.service_events.get(f"capture:{k}", {}).get("lastStart", self.host.started_at), "lastFailure": None,
                        "restarts": [], "error": getattr(r, "last_error", None) and redact_text(str(r.last_error))[:200], "deps": ["agent"]})
        trg = st.triggers
        out.append({"name": "trigger_listener", "label": AUTHORIZED_SERVICES["trigger_listener"], "purpose": "Recebe acionamentos de botoeiras/Arduino",
                    "status": "running" if (not trg or all(getattr(t, "state", "connected") in ("connected", "running") for t in trg)) else "degraded",
                    "lastStart": self.service_events.get("trigger_listener", {}).get("lastStart", self.host.started_at), "lastFailure": None, "restarts": [], "error": None, "deps": ["agent"]})
        link = self.host.link
        out.append({"name": "link_sync", "label": AUTHORIZED_SERVICES["link_sync"], "purpose": "Conexão com a API central (heartbeat e sincronização)",
                    "status": "running" if link and link.state == "online" else "degraded", "lastStart": self.host.started_at, "lastFailure": link.last_error if link else None,
                    "restarts": [], "error": None, "deps": ["agent"]})
        rw = self.host.render
        out.append({"name": "render_queue", "label": AUTHORIZED_SERVICES["render_queue"], "purpose": "Processa e exporta replays (molduras, patrocínios, áudio)",
                    "status": "running" if rw else "stopped", "lastStart": self.host.started_at, "lastFailure": None, "restarts": [],
                    "error": None, "deps": ["agent", "link_sync"], "stats": {"done": getattr(rw, "done", 0), "failed": getattr(rw, "failed", 0)}})
        out.append({"name": "storage_manager", "label": AUTHORIZED_SERVICES["storage_manager"], "purpose": "Acompanha uso de disco e limites", "status": "running",
                    "lastStart": self.host.started_at, "lastFailure": None, "restarts": [], "error": None, "deps": ["agent"]})
        out.append({"name": "monitor", "label": AUTHORIZED_SERVICES["monitor"], "purpose": "Coleta CPU, memória, disco e diagnósticos", "status": "running",
                    "lastStart": self.host.started_at, "lastFailure": None, "restarts": [], "error": None, "deps": ["agent"]})
        return out

    def controllers_state(self) -> list[dict]:
        out = []
        for t in self.host.stack.triggers:
            d = t.diagnostics() if hasattr(t, "diagnostics") else {}
            out.append({"name": getattr(t, "name", "?"), "kind": d.get("kind", "serial"), "state": d.get("state"), "lastLineAt": d.get("lastLineAt"),
                        "reconnects": d.get("reconnects"), "port": d.get("port")})
        return out

    def send_inventory(self, force: bool = False) -> None:
        if not force and self.clock() - self.last_inventory < 60:
            return
        self.last_inventory = self.clock()
        events = [{"input": r["button"], "at": r["ts"]} for r in self.host.stack.store.recent_triggers(10) if r["button"]]
        self.client.call("POST", "/remote/inventory", {"os": f"{platform.system()} {platform.release()}"[:120], "capabilities": self.capabilities(),
                                                       "devices": self.devices.scan(), "services": self.services(), "controllers": self.controllers_state(), "events": events})

    # ------------------------------------------------------------ chamado a cada heartbeat
    def tick(self, hint: dict | None) -> None:
        if not hint or not hint.get("linked"):
            return
        try:
            if hint.get("pendingVersion"):
                self.poll()
            if hint.get("tasks"):
                self.run_tasks()
            self.send_inventory()
        except (CentralUnavailable, CentralRejected) as e:
            log.info("configuração remota: aguardando a central (%s)", type(e).__name__)

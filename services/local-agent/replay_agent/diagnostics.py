"""Diagnóstico consolidado do agente (enviado no heartbeat e exibido no painel do gestor da arena). Sem segredos: URLs RTSP mascaradas."""
import time

from .config import AgentCfg
from .store import LocalStore

SEV = {"critical": 0, "warning": 1, "info": 2}


def _alert(code: str, severity: str, message: str, court: str | None = None) -> dict:
    return {"code": code, "severity": severity, "court": court, "message": message}


def court_alerts(key: str, cam: dict, trig_state: str, queue: dict, buf_target: float, window_s: float) -> list[dict]:
    out: list[dict] = []
    st = cam.get("state")
    if st == "down":
        out.append(_alert("camera_down", "critical", f"Câmera sem sinal ({cam.get('lastError') or 'sem detalhe'})", key))
    elif st in ("reconnecting", "connecting"):
        out.append(_alert("camera_reconnecting", "warning", "Câmera reconectando", key))
    elif st == "streaming":
        decl, fps = cam.get("declaredFps") or 0, cam.get("fps") or 0
        if decl and fps and fps < 0.7 * decl:
            out.append(_alert("fps_low", "warning", f"FPS recebido {fps:g} abaixo do declarado {decl:g}", key))
        if (cam.get("lossPct") or 0) > 5:
            out.append(_alert("frame_loss", "warning", f"Perda de quadros de {cam['lossPct']:g}%", key))
        b = cam.get("buffer") or {}
        if b.get("availableSeconds", 0) < window_s and b.get("segments", 0):
            out.append(_alert("buffer_short", "info", f"Buffer contínuo de {b['availableSeconds']:g}s (janela do replay: {window_s:g}s)", key))
    if cam.get("warning"):
        out.append(_alert("gop_large", "warning", cam["warning"], key))
    if trig_state in ("disconnected", "busy", "log_unavailable"):
        out.append(_alert("trigger_down", "critical", "Botoeira/porta serial desconectada" if trig_state != "busy" else "Porta serial ocupada por outro processo", key))
    if queue.get("unrecoverable", 0):
        out.append(_alert("unrecoverable", "warning", f"{queue['unrecoverable']} acionamento(s) sem vídeo recuperável", key))
    if (queue.get("oldestWaitS") or 0) > 90:
        out.append(_alert("queue_slow", "warning", f"Fila parada há {queue['oldestWaitS']:.0f}s", key))
    return out


def collect(cfg: AgentCfg, store: LocalStore, recorders: dict, indexes: dict, engine, triggers: list, router, governor, sampler=None,
            link=None, relay=None, render=None, version: str = "") -> dict:
    now = time.time()
    sources = [t.diagnostics() for t in triggers]
    states = [s.get("state") for s in sources]
    trig_state = ("connected" if "connected" in states or "running" in states else
                  next((x for x in states if x in ("busy", "disconnected", "log_unavailable")), "none" if not states else "stopped"))
    stats = {s["courtKey"]: s for s in engine.court_stats()}
    courts: dict[str, dict] = {}
    alerts: list[dict] = []
    for c in cfg.courts:
        rec = recorders.get(c.key)
        if rec:
            cam = rec.diagnostics()
        elif c.key in indexes:
            cam = {"state": "external", "url": "externo (somente leitura)", "buffer": {**indexes[c.key].stats(c.clip.gap_tolerance_s), "targetSeconds": None}}
        else:
            cam = {"state": "disabled" if not c.camera.enabled else "external_command", "buffer": None}
        counts = store.job_counts(c.key)
        queue = {"queued": counts.get("queued", 0), "processing": counts.get("processing", 0), "done": counts.get("done", 0),
                 "failed": counts.get("failed", 0), "unrecoverable": counts.get("unrecoverable", 0),
                 "oldestWaitS": store.oldest_wait_s(c.key), "retries": engine.workers[c.key].retries}
        last = next(iter(store.recent_triggers(1, c.key)), None)
        trigger = {"state": trig_state, "buttons": c.trigger.buttons, "debounceS": c.trigger.debounce_s, "cooldownS": c.trigger.cooldown_s,
                   "outcomes": store.trigger_outcomes(c.key),
                   "last": {"at": last["ts"], "ageS": round(now - last["ts"], 1), "source": last["source"], "button": last["button"],
                            "outcome": last["outcome"]} if last else None}
        w = stats.get(c.key, {})
        courts[c.key] = {"label": c.label or c.key, "camera": cam, "trigger": trigger, "queue": queue, "engine": {"state": w.get("state"), "lastError": engine.workers[c.key].last_error,
                         "avgRenderS": w.get("avgRenderS"), "p95RenderS": w.get("p95RenderS"), "storageUsedBytes": w.get("storageUsedBytes")},
                         "windowS": c.clip.pre_s + c.clip.post_s}
        alerts += court_alerts(c.key, cam, trig_state, queue, c.camera.buffer_s, c.clip.pre_s + c.clip.post_s)
    system = sampler.sample() if sampler else {}
    if system.get("cpuPct") and system["cpuPct"] > 90:
        alerts.append(_alert("cpu_high", "warning", f"CPU em {system['cpuPct']:g}%"))
    if system.get("memUsedPct") and system["memUsedPct"] > 90:
        alerts.append(_alert("mem_high", "warning", f"Memória em {system['memUsedPct']:g}%"))
    if system.get("diskTotalBytes"):
        free = 100 * system["diskFreeBytes"] / system["diskTotalBytes"]
        if free < 5:
            alerts.append(_alert("disk_critical", "critical", f"Disco com apenas {free:.1f}% livre"))
        elif free < 10:
            alerts.append(_alert("disk_low", "warning", f"Disco com {free:.1f}% livre"))
    res = governor.stats() if governor else None
    if res and res["hw"]["failed"]:
        alerts.append(_alert("hw_fallback", "info", f"Aceleração por hardware falhou; usando CPU ({res['hw']['reason']})"))
    if res and res["pausedNow"]:
        alerts.append(_alert("render_paused", "info", "Renderização em pausa para priorizar a captura"))
    lk = None
    if link is not None:
        lk = {"state": link.state, "lastError": link.last_error, "failStreak": link.fail_streak, "pending": store.count("pending"),
              "rejected": store.count("rejected"), "configVersion": link.config_version, "epoch": link.client.epoch, "rotations": link.rotations,
              "lastOkAt": getattr(link, "last_ok_at", None)}
        if link.state != "online":
            alerts.append(_alert("central_offline", "warning", "Sem comunicação com a API central; operando localmente"))
    alerts.sort(key=lambda a: SEV[a["severity"]])
    return {"generatedAt": now, "version": version, "courts": courts, "triggerSources": sources, "resources": res, "system": system,
            "link": lk, "alerts": alerts, "config": cfg.public_summary(),
            "relay": {"served": relay.served, "errors": relay.errors} if relay else None,
            "render": {"done": render.done, "failed": render.failed} if render else None}

"""Armazenamento, retenção e saúde do motor local — escopo da PRÓPRIA arena (contexto /arena/{arena_id}).
O limite global da arena (override do plano) só pode ser alterado pelo portal; aqui a arena define limiares,
limites por quadra (≤ limite da arena), retenção por quadra e proteção de vídeos."""
import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..db import get_db, utcnow
from ..deps import ArenaCtx, ArenaGate
from ..models import AgentCommand, Arena, ArenaAgent, Court, CourtRuntime, Video
from ..config import settings
from datetime import timedelta
from .serializers import iso
from .. import delivery

router = APIRouter(prefix="/api/arena/{arena_id}", tags=["arena-capacity"])
staff = ArenaGate("arena_admin", "arena_operator")
admin = ArenaGate("arena_admin")


def storage_overview(db: Session, arena: Arena) -> dict:
    lim = capacity.arena_limit_bytes(db, arena)
    used = capacity.arena_usage(db, arena.id)
    forecast = capacity.forecast_days_to_full([(r.ts, r.used_bytes) for r in capacity.samples(db, arena.id, None)], lim)
    rows = capacity.samples(db, arena.id, None)
    courts = []
    for ct in db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number):
        cl = capacity.court_limit_bytes(ct, lim)
        cu = capacity.court_usage(db, ct.id)
        courts.append({"courtId": ct.id, "label": ct.label, "usedBytes": cu, "limitBytes": cl, "retentionDays": ct.retention_days,
                       "level": capacity.usage_level(cu, cl, arena.storage_warn_pct, arena.storage_critical_pct),
                       "forecast": capacity.forecast_days_to_full([(r.ts, r.used_bytes) for r in capacity.samples(db, arena.id, ct.id)], cl)})
    return {"arenaId": arena.id, "usedBytes": used, "limitBytes": lim, "limitSource": "arena" if arena.storage_limit_mb else "plan",
            "warnPct": arena.storage_warn_pct, "criticalPct": arena.storage_critical_pct,
            "level": capacity.usage_level(used, lim, arena.storage_warn_pct, arena.storage_critical_pct),
            "forecast": forecast, "diskForecast": capacity.disk_forecast(rows),
            "protectedVideos": db.query(Video).filter(Video.arena_id == arena.id, Video.protected.is_(True), Video.deleted_at.is_(None)).count(),
            "autoDelete": False, "courts": courts}


def runtime_overview(db: Session, arena: Arena) -> dict:
    agents = db.query(ArenaAgent).filter_by(arena_id=arena.id).all()
    rts = {r.court_id: r for r in db.query(CourtRuntime).filter_by(arena_id=arena.id)}
    now = utcnow()
    return {
        "connection": capacity.arena_connection(agents, now),
        "agents": [{"id": a.id, "label": a.label, "active": a.active, "state": capacity.connection_state(a, now),
                    "hostname": a.hostname, "version": a.agent_version, "lastHeartbeatAt": iso(a.last_heartbeat_at),
                    "pendingSync": a.pending_sync, "configApplied": a.config_ack,
                    "system": json.loads(a.system_json) if a.system_json else None} for a in agents],
        "courts": [{"courtId": c.id, "label": c.label, "state": (rts[c.id].state if c.id in rts else "unknown"),
                    "queueDepth": rts[c.id].queue_depth if c.id in rts else None,
                    "completed": rts[c.id].completed_total if c.id in rts else None,
                    "failed": rts[c.id].failed_total if c.id in rts else None,
                    "avgRenderS": rts[c.id].avg_render_s if c.id in rts else None,
                    "p95RenderS": rts[c.id].p95_render_s if c.id in rts else None,
                    "updatedAt": iso(rts[c.id].updated_at) if c.id in rts else None}
                   for c in db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number)],
    }


@router.get("/storage")
def storage(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return storage_overview(db, c.arena)


@router.get("/runtime")
def runtime(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    return runtime_overview(db, c.arena)


class PolicyIn(BaseModel):
    warnPct: int = Field(80, ge=10, le=99)
    criticalPct: int = Field(95, ge=11, le=100)


@router.put("/storage-policy")
def set_policy(body: PolicyIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    if body.warnPct >= body.criticalPct:
        raise HTTPException(422, "warnPct deve ser menor que criticalPct")
    c.arena.storage_warn_pct, c.arena.storage_critical_pct = body.warnPct, body.criticalPct
    audit.record(db, c.user.id, c.arena.id, "storage.policy", "arena", c.arena.id, body.model_dump())
    db.commit()
    return storage_overview(db, c.arena)


class CourtPolicyIn(BaseModel):
    storageLimitMb: int | None = Field(None, ge=1)
    retentionDays: int = Field(30, ge=1, le=3650)


@router.put("/courts/{court_id}/storage-policy")
def set_court_policy(court_id: str, body: CourtPolicyIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    ct = db.get(Court, court_id)
    if not ct or ct.arena_id != c.arena.id:  # IDOR: quadra de outra arena é "inexistente"
        raise HTTPException(404, "quadra não encontrada")
    alim = capacity.arena_limit_bytes(db, c.arena)
    if body.storageLimitMb and alim and body.storageLimitMb * capacity.MB > alim:
        raise HTTPException(422, "limite da quadra não pode exceder o limite da arena")
    ct.storage_limit_mb, ct.retention_days = body.storageLimitMb, body.retentionDays
    audit.record(db, c.user.id, c.arena.id, "storage.court_policy", "court", ct.id, body.model_dump())
    db.commit()
    return storage_overview(db, c.arena)


class ProtectIn(BaseModel):
    protected: bool


@router.post("/videos/{video_id}/protection")
def protect(video_id: str, body: ProtectIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    v = db.get(Video, video_id)
    if not v or v.arena_id != c.arena.id or v.deleted_at is not None:
        raise HTTPException(404, "vídeo não encontrado")
    v.protected = body.protected
    audit.record(db, c.user.id, c.arena.id, "video.protect" if body.protected else "video.unprotect", "video", v.id, {"code": v.code})
    db.commit()
    return {"id": v.id, "protected": v.protected}


@router.get("/retention/plan")
def retention_plan(c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    """Dry-run. Não existe endpoint que remova arquivos reais nesta etapa."""
    return capacity.retention_plan(db, c.arena)


class ResourcesIn(BaseModel):
    maxFfmpeg: int = Field(1, ge=1, le=16)
    hwaccel: str = Field("none", pattern=r"^(none|auto|vaapi|qsv)$")
    renderNice: int = Field(10, ge=0, le=19)


def _res_out(a: Arena) -> dict:
    return {"maxFfmpeg": a.agent_max_ffmpeg, "hwaccel": a.agent_hwaccel, "renderNice": a.agent_render_nice,
            "note": "O agente aplica dentro dos limites do hardware local e recua para CPU se a aceleração não estiver disponível."}


@router.get("/agent-resources")
def get_resources(c: ArenaCtx = Depends(staff)):
    return _res_out(c.arena)


@router.put("/agent-resources")
def set_resources(body: ResourcesIn, c: ArenaCtx = Depends(admin), db: Session = Depends(get_db)):
    """Ajuste por arena de FFmpeg simultâneos, aceleração por hardware e prioridade. Só a PRÓPRIA arena (gestor)."""
    c.arena.agent_max_ffmpeg, c.arena.agent_hwaccel, c.arena.agent_render_nice = body.maxFfmpeg, body.hwaccel, body.renderNice
    audit.record(db, c.user.id, c.arena.id, "agent.resources", "arena", c.arena.id, body.model_dump())
    db.commit()
    return _res_out(c.arena)


# ---------------- painel de diagnóstico ----------------
def _loads(txt):
    try:
        return json.loads(txt) if txt else None
    except ValueError:
        return None


def _central_alerts(db: Session, arena: Arena, agents: list[ArenaAgent], conn: str, stale: bool, st: dict) -> list[dict]:
    out = []
    if conn in ("offline", "never_connected", "no_agent"):
        msg = {"offline": "Motor local sem comunicação com a central", "never_connected": "Motor local nunca se conectou",
               "no_agent": "Nenhum motor local cadastrado"}[conn]
        out.append({"code": "agent_" + conn, "severity": "critical", "court": None, "message": msg, "source": "central"})
    elif conn == "delayed":
        out.append({"code": "agent_delayed", "severity": "warning", "court": None, "message": "Heartbeat atrasado", "source": "central"})
    if st["level"] in ("warning", "critical", "full"):
        out.append({"code": "storage_" + st["level"], "severity": "critical" if st["level"] != "warning" else "warning", "court": None,
                    "message": f"Armazenamento {st['level']} ({st['usedBytes'] * 100 // st['limitBytes']}% do limite)" if st["limitBytes"] else "Armazenamento", "source": "central"})
    days = (st.get("forecast") or {}).get("daysToFull")
    if days is not None and days < 14:
        out.append({"code": "storage_forecast", "severity": "warning", "court": None, "message": f"Previsão de esgotamento em ~{days:g} dias", "source": "central"})
    cfg_ver = capacity.agent_config(db, arena)["configVersion"]
    for a in agents:
        if a.active and conn == "online" and a.config_ack and a.config_ack != cfg_ver:
            out.append({"code": "config_outdated", "severity": "info", "court": None, "message": "Motor local ainda não aplicou a última configuração", "source": "central"})
        if a.pending_sync and a.pending_sync > 50:
            out.append({"code": "sync_backlog", "severity": "warning", "court": None, "message": f"{a.pending_sync} replays aguardando sincronização", "source": "central"})
        if a.active and not a.sig_key:
            out.append({"code": "unsigned_agent", "severity": "info", "court": None, "message": "Credencial sem assinatura de requisições: rotacione", "source": "central"})
    return out


@router.get("/diagnostics")
def diagnostics(c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    """Visão única para o gestor/operador DA PRÓPRIA arena: quadras, câmeras, botoeiras, buffer, fila, armazenamento, sincronização."""
    arena = c.arena
    now = utcnow()
    agents = db.query(ArenaAgent).filter_by(arena_id=arena.id, active=True).all()
    conn = capacity.arena_connection(agents, now)
    st = storage_overview(db, arena)
    rts = {r.court_id: r for r in db.query(CourtRuntime).filter_by(arena_id=arena.id)}
    st_by_court = {x["courtId"]: x for x in st["courts"]}
    hb = settings.agent_heartbeat_s
    courts, newest = [], None
    for ct in db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number):
        rt = rts.get(ct.id)
        age = (now - rt.updated_at).total_seconds() if rt else None
        newest = rt.updated_at if rt and (newest is None or rt.updated_at > newest) else newest
        d = _loads(rt.diag_json) if rt else None
        courts.append({"courtId": ct.id, "label": ct.label, "number": ct.number, "status": ct.status, "localKey": ct.local_court_key,
                       "dataAgeS": round(age, 1) if age is not None else None, "stale": age is None or age > 2 * hb + 5,
                       "state": rt.state if rt else "unknown", "queueDepth": rt.queue_depth if rt else None, "completed": rt.completed_total if rt else None,
                       "failed": rt.failed_total if rt else None, "diag": d, "storage": st_by_court.get(ct.id)})
    ag = max(agents, key=lambda a: a.last_heartbeat_at or a.created_at, default=None)
    sysd = _loads(ag.system_json) if ag else None
    stale = ag is None or ag.last_heartbeat_at is None or (now - ag.last_heartbeat_at).total_seconds() > 2 * hb + 5
    alerts = _central_alerts(db, arena, agents, conn, stale, st)
    if not stale and sysd and sysd.get("diag"):
        alerts += [{**a, "source": "agent"} for a in (sysd["diag"].get("alerts") or [])]
    order = {"critical": 0, "warning": 1, "info": 2}
    alerts.sort(key=lambda a: order.get(a["severity"], 3))
    return {"generatedAt": iso(now), "connection": conn, "stale": stale,
            "agent": None if ag is None else {"id": ag.id, "label": ag.label, "hostname": ag.hostname, "version": ag.agent_version,
                                              "state": capacity.connection_state(ag, now), "lastHeartbeatAt": iso(ag.last_heartbeat_at),
                                              "pendingSync": ag.pending_sync, "configApplied": ag.config_ack,
                                              "credentialExpiresAt": iso(ag.token_expires_at), "signed": bool(ag.sig_key),
                                              "system": {k: v for k, v in (sysd or {}).items() if k != "diag"},
                                              "link": (sysd or {}).get("diag", {}).get("link") if sysd else None,
                                              "resources": (sysd or {}).get("diag", {}).get("resources") if sysd else None,
                                              "triggerSources": (sysd or {}).get("diag", {}).get("triggerSources") if sysd else None},
            "storage": {k: st[k] for k in ("usedBytes", "limitBytes", "level", "warnPct", "criticalPct", "forecast", "diskForecast", "protectedVideos")},
            "courts": courts, "alerts": alerts}


@router.post("/courts/{court_id}/test-trigger", status_code=202)
def test_trigger(court_id: str, c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    """Pede ao agente um acionamento de TESTE (origem 'manual:painel-…'). Passa pelo mesmo caminho da botoeira: debounce não se
    aplica, cooldown sim. O comando só é entregue quando o agente buscar (heartbeat); nada é chamado de fora para dentro."""
    ct = db.get(Court, court_id)
    if not ct or ct.arena_id != c.arena.id:
        raise HTTPException(404, "quadra não encontrada")
    if ct.status != "active":
        raise HTTPException(409, "quadra inativa")
    if not delivery.arena_reachable(db, c.arena.id):
        raise HTTPException(503, "motor local offline: não é possível testar agora")
    now = utcnow()
    if db.query(AgentCommand).filter(AgentCommand.court_id == ct.id, AgentCommand.status.in_(("pending", "delivered")),
                                     AgentCommand.expires_at > now).count():
        raise HTTPException(409, "já existe um teste em andamento nesta quadra")
    cmd = AgentCommand(arena_id=c.arena.id, court_id=ct.id, court_key=ct.local_court_key, created_by=c.user.id,
                       expires_at=now + timedelta(seconds=120))
    db.add(cmd)
    db.flush()
    audit.record(db, c.user.id, c.arena.id, "agent.test_trigger", "court", ct.id, {"command": cmd.id})
    db.commit()
    return {"commandId": cmd.id, "status": cmd.status, "expiresInS": 120}


@router.get("/commands/{cmd_id}")
def command_status(cmd_id: str, c: ArenaCtx = Depends(staff), db: Session = Depends(get_db)):
    cmd = db.get(AgentCommand, cmd_id)
    if not cmd or cmd.arena_id != c.arena.id:
        raise HTTPException(404, "comando não encontrado")
    status = "expired" if cmd.status in ("pending", "delivered") and cmd.expires_at < utcnow() else cmd.status
    return {"id": cmd.id, "kind": cmd.kind, "courtId": cmd.court_id, "status": status, "outcome": cmd.outcome, "detail": cmd.detail,
            "createdAt": iso(cmd.created_at), "finishedAt": iso(cmd.finished_at)}

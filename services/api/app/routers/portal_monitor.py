"""Portal Dev — painel geral e monitoramento (somente dados REAIS reportados pelos agentes; sem valores inventados)."""
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import capacity, remote_config as rc, storage
from ..db import get_db, utcnow
from ..deps import Auth, platform_auth
from ..models import (Arena, ArenaAgent, Camera, ConfigRevision, Controller, Court, CourtRuntime, CourtSetting, ExportJob, LocalServer,
                      ReplayEvent, StorageSample, Video)
from .serializers import iso

router = APIRouter(prefix="/api/platform", tags=["portal-dev-monitor"])
CAM_OK = {"streaming", "external", "external_command"}
CAM_BAD = {"down", "reconnecting", "stopped"}
LABEL = {"operacional": "Operacional", "atencao": "Atenção", "falha": "Falha", "desconectado": "Desconectado", "desconhecido": "Desconhecido"}


def classify(server_state: str, diag: dict | None) -> str:
    if server_state in ("offline", "revoked", "never_connected"):
        return "desconectado" if server_state != "never_connected" else "desconhecido"
    if not diag:
        return "desconhecido"
    return "operacional"


def camera_status(rt: CourtRuntime | None, srv_state: str) -> tuple[str, dict]:
    """Estado da câmera segundo o AGENTE. Cadastro sozinho nunca vira 'conectada'."""
    if srv_state in ("offline", "revoked"):
        return "desconectado", {}
    diag = json.loads(rt.diag_json) if rt and rt.diag_json else None
    cam = (diag or {}).get("camera") if diag else None
    if not cam:
        return "desconhecido", {}
    st = cam.get("state")
    base = {"state": st, "fps": cam.get("fps"), "declaredFps": cam.get("declaredFps"), "lossPct": cam.get("lossPct"), "reconnects": cam.get("reconnects"),
            "lastFrameAgeS": cam.get("lastFrameAgeS"), "buffer": cam.get("buffer"), "bitrateKbps": cam.get("bitrateKbps")}
    if st in CAM_OK:
        decl, fps = cam.get("declaredFps"), cam.get("fps")
        if decl and fps and fps < decl * 0.85:
            return "atencao", base
        return "operacional", base
    if st in CAM_BAD:
        return "falha", base
    return "atencao", base


def _server_diag(ag: ArenaAgent | None) -> dict:
    sysd = json.loads(ag.system_json) if ag and ag.system_json else {}
    return sysd


@router.get("/overview")
def overview(a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    now = utcnow()
    servers = db.query(LocalServer).all()
    states = {s.id: rc.record_state(db, s, now) for s in servers}
    online = sum(1 for v in states.values() if v in ("online", "delayed"))
    legacy = [g for g in db.query(ArenaAgent).all() if not db.query(LocalServer.id).filter_by(agent_id=g.id).first()]
    legacy_online = sum(1 for g in legacy if capacity.connection_state(g, now) in ("online", "delayed"))
    courts_total = db.query(func.count(Court.id)).scalar()
    cams_ok = cams_bad = cams_unknown = 0
    alerts: list[dict] = []
    free_total = total_total = 0
    srv_by_id = {s.id: s for s in servers}
    for ct, cs in db.query(Court, CourtSetting).join(CourtSetting, CourtSetting.court_id == Court.id).filter(CourtSetting.server_id.isnot(None)):
        st, _ = camera_status(db.get(CourtRuntime, ct.id), states.get(cs.server_id, "never_connected"))
        if db.query(Camera.id).filter_by(court_id=ct.id, active=True).first():
            if st == "operacional" or st == "atencao":
                cams_ok += 1
            elif st in ("falha", "desconectado"):
                cams_bad += 1
            else:
                cams_unknown += 1
    ctl_ok = ctl_total = svc_run = svc_total = 0
    for s in servers:
        ag = rc.server_agent(db, s)
        sysd = _server_diag(ag)
        if sysd.get("diskFreeBytes") is not None and states[s.id] in ("online", "delayed"):
            free_total += sysd["diskFreeBytes"]
            total_total += sysd.get("diskTotalBytes") or 0
        inv = json.loads(s.inventory_json) if s.inventory_json else {}
        for sv in inv.get("services", []):
            svc_total += 1
            svc_run += 1 if sv.get("status") == "running" else 0
        for c in inv.get("controllers", []):
            ctl_total += 1
            ctl_ok += 1 if c.get("state") == "connected" else 0
        for al in (sysd.get("diag") or {}).get("alerts", [])[:5]:
            alerts.append({"severity": al.get("severity"), "message": al.get("message"), "serverId": s.id, "server": s.name, "arenaId": s.arena_id,
                           "arena": db.get(Arena, s.arena_id).name, "court": al.get("court")})
        if states[s.id] == "offline":
            alerts.append({"severity": "critical", "message": "servidor sem comunicação", "serverId": s.id, "server": s.name, "arenaId": s.arena_id,
                           "arena": db.get(Arena, s.arena_id).name})
    for r in db.query(ConfigRevision).filter_by(status="falha").order_by(ConfigRevision.finished_at.desc()).limit(5):
        s = srv_by_id.get(r.server_id)
        if s:
            alerts.append({"severity": "warning", "message": f"configuração v{r.version} falhou: {r.error or 'sem detalhe'}", "serverId": s.id, "server": s.name,
                           "arenaId": s.arena_id, "arena": db.get(Arena, s.arena_id).name})
    ops = 0
    for ct in db.query(Court).filter_by(status="active"):
        cs = db.get(CourtSetting, ct.id)
        rt = db.get(CourtRuntime, ct.id)
        if cs and cs.server_id and states.get(cs.server_id) in ("online", "delayed") and rt and rt.state == "ok":
            ops += 1
    pending = [{"serverId": r.server_id, "server": srv_by_id[r.server_id].name if r.server_id in srv_by_id else "", "version": r.version, "status": r.status,
                "arenaId": r.arena_id} for r in db.query(ConfigRevision).filter(ConfigRevision.status.in_(rc.ACTIVE_STATES)).order_by(ConfigRevision.created_at.desc()).limit(20)]
    return {
        "environment": {"demo": os.environ.get("DEMO_MODE", "").lower() in ("1", "true", "yes"), "label": os.environ.get("DEMO_LABEL", "")},
        "arenas": {"total": db.query(func.count(Arena.id)).scalar(), "active": db.query(func.count(Arena.id)).filter(Arena.status == "active").scalar()},
        "agents": {"connected": online + legacy_online, "disconnected": (len(servers) - online) + (len(legacy) - legacy_online), "servers": len(servers)},
        "courts": {"total": courts_total, "operational": ops},
        "cameras": {"connected": cams_ok, "problems": cams_bad, "unknown": cams_unknown},
        "controllers": {"connected": ctl_ok, "total": ctl_total},
        "services": {"running": svc_run, "total": svc_total},
        "replays": {"processed": db.query(func.count(ExportJob.id)).filter(ExportJob.status == "ready").scalar() + db.query(func.count(Video.id)).filter(Video.deleted_at.is_(None)).scalar(),
                    "failed": db.query(func.count(ExportJob.id)).filter(ExportJob.status == "failed").scalar() + db.query(func.count(ReplayEvent.id)).filter(ReplayEvent.status == "failed").scalar()},
        "storage": {"freeBytes": free_total, "totalBytes": total_total},
        "alerts": alerts[:20], "pendingConfig": pending, "pendingConfigCount": len(pending),
    }


@router.get("/monitoring")
def monitoring(arenaId: str | None = None, serverId: str | None = None, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    q = db.query(LocalServer)
    if arenaId:
        q = q.filter(LocalServer.arena_id == arenaId)
    if serverId:
        q = q.filter(LocalServer.id == serverId)
    out = []
    for s in q.order_by(LocalServer.name):
        st = rc.record_state(db, s)
        ag = rc.server_agent(db, s)
        sysd = _server_diag(ag)
        sdiag = sysd.get("diag") or {}
        inv = json.loads(s.inventory_json) if s.inventory_json else {}
        courts = []
        for ct, cs in db.query(Court, CourtSetting).join(CourtSetting, CourtSetting.court_id == Court.id).filter(CourtSetting.server_id == s.id).order_by(Court.number):
            rt = db.get(CourtRuntime, ct.id)
            cam_state, cam = camera_status(rt, st)
            d = json.loads(rt.diag_json) if rt and rt.diag_json else {}
            trg = d.get("trigger") or {}
            courts.append({"courtId": ct.id, "label": ct.label, "status": ct.status, "camera": {"status": cam_state, **cam},
                           "trigger": {"state": trg.get("state"), "last": trg.get("last"), "buttons": trg.get("buttons")},
                           "queue": d.get("queue"), "runtimeState": rt.state if rt else None, "updatedAt": iso(rt.updated_at) if rt else None,
                           "alerts": d.get("alerts") or []})
        statuses = [c["camera"]["status"] for c in courts]
        overall = "desconectado" if st in ("offline", "revoked") else "desconhecido" if st == "never_connected" else (
            "falha" if "falha" in statuses else "atencao" if "atencao" in statuses or "desconhecido" in statuses else "operacional")
        out.append({"serverId": s.id, "server": s.name, "arenaId": s.arena_id, "arena": db.get(Arena, s.arena_id).name, "state": st, "status": overall,
                    "statusLabel": LABEL[overall], "lastHeartbeatAt": iso(ag.last_heartbeat_at) if ag and ag.last_heartbeat_at else None,
                    "version": ag.agent_version if ag else None, "appliedVersion": s.applied_version,
                    "resources": {"cpuPct": sysd.get("cpuPct"), "memUsedPct": sysd.get("memUsedPct"), "diskFreeBytes": sysd.get("diskFreeBytes"),
                                  "diskTotalBytes": sysd.get("diskTotalBytes"), "temperatureC": sysd.get("temperatureC"), "hw": sdiag.get("resources")},
                    "link": sdiag.get("link"), "render": sdiag.get("render"), "alerts": sdiag.get("alerts") or [],
                    "services": inv.get("services", []), "controllers": inv.get("controllers", []), "courts": courts})
    db.commit()
    return out


@router.get("/cameras/{cid}/snapshot")
def camera_snapshot(cid: str, a: Auth = Depends(platform_auth), db: Session = Depends(get_db)):
    """Última imagem de teste da câmera (JPEG pequeno enviado pelo agente). Acesso restrito ao Portal (global_admin + MFA)."""
    c = db.get(Camera, cid)
    if not c:
        raise HTTPException(404, "câmera não encontrada")
    p = storage.path_for(storage.make_key(c.arena_id, "camtest", f"{c.id}.jpg"))
    if not p.is_file():
        raise HTTPException(404, "ainda não há imagem de teste")
    return Response(p.read_bytes(), media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})

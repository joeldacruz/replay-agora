"""Contrato Central <-> motor local da arena (v1). SOMENTE METADADOS: nenhum endpoint aceita arquivo de vídeo.

Autenticação: token por arena (hash no banco) + vínculo ao primeiro `nodeId` registrado. Toda escrita é filtrada pela
arena do token. Escritas só são aceitas pelo nó `primary` e com epoch de fencing compatível (preparação para o
servidor reserva da API central; ver docs/ALTA-DISPONIBILIDADE.md).
"""
from datetime import datetime, timedelta, timezone

import hashlib
import json
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import audit, capacity
from ..config import settings
from ..db import get_db, utcnow
from ..models import AgentCommand, ArenaAgent, Arena, Court, CourtRuntime, ExportJob, MusicTrack, ReplayEvent, Video
from .. import policy, storage
from fastapi.responses import FileResponse
from ..agent_security import agent_auth_v1 as agent_auth, rotate as rotate_credentials, rotation_hint, issue_credentials
from .ingest import _code
from ..delivery import purge_spool, spool_file
from ..db import SessionLocal
from ..models import RelayRequest

NODE_RE = r"^[A-Za-z0-9_.\-]{4,64}$"
KEY_RE = r"^[A-Za-z0-9_\-]{1,60}$"
LOCAL_ID_RE = r"^[A-Za-z0-9_.:\-]{8,120}$"


router = APIRouter(prefix="/api/agent/v1", tags=["agent"])  # cabeçalhos X-Central-* são aplicados no middleware (main.py)


def writer(request: Request, ag: ArenaAgent = Depends(agent_auth)) -> ArenaAgent:
    """Fencing: reserva (standby) não grava; nó com epoch menor que o já visto pelo agente é um primário obsoleto."""
    if settings.node_role != "primary":
        raise HTTPException(503, "nó reserva: escrita de agentes indisponível", headers={"Retry-After": "5"})
    seen = request.headers.get("x-agent-epoch")
    if seen and seen.isdigit() and int(seen) > settings.central_epoch:
        raise HTTPException(409, "epoch obsoleto: este nó não é mais o primário")
    return ag


def bound(request: Request, ag: ArenaAgent = Depends(writer)) -> ArenaAgent:
    node = request.headers.get("x-agent-node")
    if not ag.node_id:
        raise HTTPException(409, "agente não registrado: chame POST /api/agent/v1/register")
    if node != ag.node_id:
        raise HTTPException(409, "credencial vinculada a outro computador")
    return ag


def _courts(db: Session, arena_id: str) -> dict[str, Court]:
    return {c.local_court_key: c for c in db.query(Court).filter_by(arena_id=arena_id)}  # sempre escopado à arena do token


def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


# ---------------- registro / configuração ----------------
class RegisterIn(BaseModel):
    nodeId: str = Field(pattern=NODE_RE)
    hostname: str = Field("", max_length=120)
    version: str = Field("", max_length=40)
    courts: list[str] = Field(default_factory=list, max_length=50)


@router.post("/register")
def register(body: RegisterIn, ag: ArenaAgent = Depends(writer), db: Session = Depends(get_db)):
    if ag.node_id and ag.node_id != body.nodeId:
        raise HTTPException(409, "credencial já vinculada a outro computador; crie uma nova credencial no App da Arena")
    first = ag.node_id is None
    ag.node_id, ag.hostname, ag.agent_version = body.nodeId, body.hostname or None, body.version or None
    ag.registered_at = ag.registered_at or utcnow()
    if ag.token_expires_at is None and settings.agent_token_ttl_days > 0:
        ag.token_expires_at = utcnow() + timedelta(days=settings.agent_token_ttl_days)
    arena = db.get(Arena, ag.arena_id)
    known = _courts(db, ag.arena_id)
    if first:
        audit.record(db, None, ag.arena_id, "agent.register", "agent", ag.id, {"node": body.nodeId, "host": body.hostname})
    db.commit()
    return {"agentId": ag.id, "arenaId": ag.arena_id, "epoch": settings.central_epoch, **rotation_hint(ag),
            "unknownCourts": sorted(k for k in body.courts if k not in known), "config": capacity.agent_config(db, arena)}


@router.get("/config")
def config(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    return capacity.agent_config(db, db.get(Arena, ag.arena_id))


# ---------------- heartbeat ----------------
class SystemIn(BaseModel):
    cpuPct: float | None = Field(None, ge=0, le=100)
    load1: float | None = Field(None, ge=0, le=10000)
    memUsedPct: float | None = Field(None, ge=0, le=100)
    diskFreeBytes: int | None = Field(None, ge=0)
    diskTotalBytes: int | None = Field(None, ge=0)


class CourtStat(BaseModel):
    courtKey: str = Field(pattern=KEY_RE)
    state: str = Field("ok", pattern=r"^(ok|degraded|storage_failure|camera_down)$")
    queueDepth: int = Field(0, ge=0, le=1_000_000)
    completed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    lastRenderS: float | None = Field(None, ge=0, le=86400)
    avgRenderS: float | None = Field(None, ge=0, le=86400)
    p95RenderS: float | None = Field(None, ge=0, le=86400)
    storageUsedBytes: int = Field(0, ge=0)


class HeartbeatIn(BaseModel):
    version: str = Field("", max_length=40)
    configVersion: str = Field("", max_length=40)
    pendingSync: int = Field(0, ge=0, le=10_000_000)
    system: SystemIn = Field(default_factory=SystemIn)
    courts: list[CourtStat] = Field(default_factory=list, max_length=50)
    diagnostics: dict | None = None      # diagnóstico completo (câmeras, buffer, botoeiras, fila, recursos, alertas); sem segredos


@router.post("/heartbeat")
def heartbeat(body: HeartbeatIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    now = utcnow()
    arena = db.get(Arena, ag.arena_id)
    ag.last_heartbeat_at, ag.agent_version, ag.pending_sync = now, body.version or ag.agent_version, body.pendingSync
    ag.config_ack = body.configVersion or None
    diag = body.diagnostics if body.diagnostics and len(json.dumps(body.diagnostics, default=str)) <= 120_000 else None
    sysd = body.system.model_dump()
    if diag:  # peças do nível do agente (as por quadra vão em court_runtime)
        sysd["diag"] = {k: diag.get(k) for k in ("resources", "link", "triggerSources", "alerts", "relay", "render", "version") if k in diag}
    ag.system_json = json.dumps(sysd, default=str)
    courts, unknown = _courts(db, ag.arena_id), []
    for cs in body.courts:
        ct = courts.get(cs.courtKey)
        if not ct:
            unknown.append(cs.courtKey)
            continue
        rt = db.get(CourtRuntime, ct.id) or CourtRuntime(court_id=ct.id, arena_id=ag.arena_id)
        rt.state, rt.queue_depth, rt.completed_total, rt.failed_total = cs.state, cs.queueDepth, cs.completed, cs.failed
        rt.last_render_s, rt.avg_render_s, rt.p95_render_s = cs.lastRenderS, cs.avgRenderS, cs.p95RenderS
        rt.storage_used_bytes, rt.updated_at = cs.storageUsedBytes, now
        if diag and isinstance(diag.get("courts"), dict) and cs.courtKey in diag["courts"]:
            rt.diag_json = json.dumps(diag["courts"][cs.courtKey], default=str)
        db.merge(rt)
        capacity.record_sample(db, ag.arena_id, ct.id, cs.storageUsedBytes, None, None, now)
    total = sum(c.storageUsedBytes for c in body.courts if c.courtKey in courts)
    capacity.record_sample(db, ag.arena_id, None, total, body.system.diskFreeBytes, body.system.diskTotalBytes, now)
    current = capacity.agent_config(db, arena)["configVersion"]
    cmds = _deliver_commands(db, ag, now)
    remote = _remote_hint(db, ag)
    db.commit()
    return {"ok": True, "serverTime": now.isoformat() + "Z", "epoch": settings.central_epoch, "commands": cmds, "remote": remote,
            "configVersion": current, "configChanged": current != body.configVersion, "unknownCourts": unknown,
            **rotation_hint(ag)}


def _remote_hint(db: Session, ag: ArenaAgent) -> dict:
    """Dica barata para o agente só buscar configuração/tarefas quando houver algo (configuração remota; ver agent_remote.py)."""
    from .. import remote_config as rc
    from ..models import AgentTask, ConfigRevision, LocalServer
    srv = db.query(LocalServer).filter_by(agent_id=ag.id).first()
    if not srv or srv.status == "revoked":
        return {"linked": False}
    rc.record_state(db, srv)
    pend = (db.query(ConfigRevision.version).filter(ConfigRevision.server_id == srv.id, ConfigRevision.status.in_(("pendente", "enviado", "recebido", "aplicando")))
            .order_by(ConfigRevision.version.desc()).first())
    tasks = db.query(AgentTask.id).filter_by(server_id=srv.id, status="pending").first() is not None
    return {"linked": True, "pendingVersion": pend[0] if pend else None, "tasks": tasks, "appliedVersion": srv.applied_version}


def _deliver_commands(db: Session, ag: ArenaAgent, now) -> list[dict]:
    """Entrega (uma única vez) os comandos pendentes DA PRÓPRIA arena; vencidos viram 'expired'."""
    db.query(AgentCommand).filter(AgentCommand.arena_id == ag.arena_id, AgentCommand.status.in_(("pending", "delivered")),
                                  AgentCommand.expires_at < now).update({"status": "expired", "finished_at": now}, synchronize_session=False)
    rows = (db.query(AgentCommand).filter(AgentCommand.arena_id == ag.arena_id, AgentCommand.status == "pending")
            .order_by(AgentCommand.created_at).limit(5).all())
    out = []
    for c in rows:
        if db.query(AgentCommand).filter(AgentCommand.id == c.id, AgentCommand.status == "pending").update(
                {"status": "delivered", "agent_id": ag.id}, synchronize_session=False) == 1:
            out.append({"id": c.id, "kind": c.kind, "courtKey": c.court_key, "by": f"painel-{c.created_by[:8]}"})
    return out


class CmdResult(BaseModel):
    outcome: str = Field(max_length=30)
    jobId: str | None = Field(None, max_length=120)
    detail: str | None = Field(None, max_length=200)


@router.post("/commands/{cmd_id}/result")
def command_result(cmd_id: str, body: CmdResult, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    c = db.get(AgentCommand, cmd_id)
    if not c or c.arena_id != ag.arena_id or c.agent_id != ag.id:  # comando de outra arena/agente => inexistente
        raise HTTPException(404, "comando não encontrado")
    if c.status != "delivered":
        raise HTTPException(409, "comando não está aguardando resultado")
    c.status = "done" if body.outcome == "accepted" else "failed"
    c.outcome, c.job_id, c.detail, c.finished_at = body.outcome, body.jobId, body.detail, utcnow()
    db.commit()
    return {"ok": True}


# ---------------- sincronização de metadados de replays ----------------
class ReplayIn(BaseModel):
    localId: str = Field(pattern=LOCAL_ID_RE)  # id estável gerado no computador da arena => chave de idempotência
    courtKey: str = Field(pattern=KEY_RE)
    capturedAt: datetime
    durationS: float = Field(0, ge=0, le=3600)
    bytes: int = Field(0, ge=0, le=50 * 1024**3)
    width: int = Field(0, ge=0, le=8192)
    height: int = Field(0, ge=0, le=8192)
    hasAudio: bool = False
    videoCodec: str | None = Field(None, max_length=30)
    protected: bool = False
    deletedLocally: bool = False
    fileAvailable: bool = True  # o agente confirma que o MP4 existe em disco
    title: str | None = Field(None, max_length=160)  # só na criação (ex.: replay de teste manual)


class SyncIn(BaseModel):
    replays: list[ReplayIn] = Field(max_length=1000)


def _apply(db: Session, ag: ArenaAgent, it: ReplayIn, ct: Court) -> dict:
    v = db.query(Video).filter_by(arena_id=ag.arena_id, local_id=it.localId).first()
    if v:
        if v.court_id != ct.id:
            return {"localId": it.localId, "status": "rejected", "reason": "replay pertence a outra quadra"}
        if v.bytes != it.bytes or abs(v.duration - it.durationS) > 0.01:
            v.bytes, v.duration = it.bytes, it.durationS
        v.media_state = "ok" if it.fileAvailable else "missing"
        if it.deletedLocally and v.deleted_at is None:
            v.deleted_at = utcnow()
            audit.record(db, None, ag.arena_id, "video.deleted_local", "video", v.id, {"agent": ag.id})
        return {"localId": it.localId, "status": "duplicate", "videoId": v.id, "code": v.code}
    ev = db.query(ReplayEvent).filter_by(arena_id=ag.arena_id, idempotency_key=it.localId).first()
    at = _utc(it.capturedAt)
    if ev and ev.court_id != ct.id:
        return {"localId": it.localId, "status": "rejected", "reason": "evento pertence a outra quadra"}
    if not ev:
        ev = ReplayEvent(arena_id=ag.arena_id, court_id=ct.id, captured_at=at, source="agent-sync",
                         idempotency_key=it.localId, status="completed")
        db.add(ev)
        db.flush()
    ev.status = "completed"
    v = Video(arena_id=ag.arena_id, court_id=ct.id, event_id=ev.id, code=_code(db), storage_key="", thumb_key=None,
              bytes=it.bytes, duration=it.durationS, width=it.width, height=it.height, video_codec=it.videoCodec,
              title=it.title or "", has_audio=it.hasAudio, visibility="private", processing_status="ready", captured_at=at,
              storage_location="agent", local_id=it.localId, protected=it.protected,
              media_state="ok" if it.fileAvailable else "missing",
              deleted_at=utcnow() if it.deletedLocally else None)
    db.add(v)
    db.flush()
    return {"localId": it.localId, "status": "created", "videoId": v.id, "code": v.code}


@router.post("/sync/replays")
def sync_replays(body: SyncIn, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    if len(body.replays) > settings.agent_sync_batch_max:
        raise HTTPException(413, f"lote acima de {settings.agent_sync_batch_max} itens")
    courts, results = _courts(db, ag.arena_id), []
    for it in body.replays:
        ct = courts.get(it.courtKey)
        if not ct or ct.status == "inactive":
            results.append({"localId": it.localId, "status": "rejected", "reason": "quadra desconhecida ou inativa nesta arena"})
            continue
        try:
            with db.begin_nested():  # savepoint: corrida entre dois envios do mesmo localId não derruba o lote
                results.append(_apply(db, ag, it, ct))
        except IntegrityError:
            r = db.query(Video).filter_by(arena_id=ag.arena_id, local_id=it.localId).first()
            if not r:
                results.append({"localId": it.localId, "status": "rejected", "reason": "conflito de integridade"})
            else:
                results.append({"localId": it.localId, "status": "duplicate", "videoId": r.id, "code": r.code})
    ag.last_used_at = utcnow()
    db.commit()
    arena = db.get(Arena, ag.arena_id)
    lim = capacity.arena_limit_bytes(db, arena)
    return {"results": results, "storageLevel": capacity.usage_level(capacity.arena_usage(db, arena.id), lim,
                                                                      arena.storage_warn_pct, arena.storage_critical_pct)}


@router.get("/sync/protection")
def protection(limit: int = Query(5000, ge=1, le=20000), ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Lista autoritativa de replays protegidos (a central é a fonte). O agente nunca remove o que está aqui."""
    rows = (db.query(Video.local_id).filter(Video.arena_id == ag.arena_id, Video.protected.is_(True),
                                            Video.deleted_at.is_(None), Video.local_id.isnot(None)).limit(limit).all())
    return {"localIds": [r[0] for r in rows]}


@router.get("/retention/plan")
def retention(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Somente candidatos (dry-run). Nesta etapa nenhum componente apaga arquivos automaticamente."""
    return capacity.retention_plan(db, db.get(Arena, ag.arena_id))


@router.post("/credentials/rotate")
def rotate(request: Request, ag: ArenaAgent = Depends(writer), db: Session = Depends(get_db)):
    """O agente troca suas próprias credenciais. Resposta contém token e chave NOVOS (única vez); a anterior vale só
    pela janela de carência, para sobreviver a uma resposta perdida. Nada disso vai para log/auditoria."""
    token, key = rotate_credentials(ag, bool(getattr(request.state, "via_prev", False)))
    audit.record(db, None, ag.arena_id, "agent.rotate", "agent", ag.id, {"by": "agent"})
    db.commit()
    return {"token": token, "signingKey": key, "expiresAt": ag.token_expires_at.isoformat() + "Z" if ag.token_expires_at else None,
            "previousValidUntil": ag.prev_valid_until.isoformat() + "Z" if ag.prev_valid_until else None}


# ---------------- relay: entrega de vídeo (o agente busca por conexão de SAÍDA) ----------------
@router.get("/relay/next")
def relay_next(wait: int = Query(20, ge=0, le=60), ag: ArenaAgent = Depends(bound)):
    """Long-poll: devolve o pedido de trecho mais antigo da PRÓPRIA arena (204 se nada surgir)."""
    deadline = time.monotonic() + min(wait, settings.relay_poll_max_s)
    first = True
    while first or time.monotonic() < deadline:
        first = False
        with SessionLocal() as s:
            now = utcnow()
            cand = (s.query(RelayRequest.id).filter(RelayRequest.arena_id == ag.arena_id, RelayRequest.status == "pending",
                                                    RelayRequest.expires_at > now)
                    .order_by(RelayRequest.created_at).limit(3).all())
            for (rid,) in cand:
                n = s.query(RelayRequest).filter(RelayRequest.id == rid, RelayRequest.status == "pending").update(
                    {"status": "claimed", "agent_id": ag.id})
                s.commit()
                if n == 1:  # reivindicação atômica
                    r = s.get(RelayRequest, rid)
                    return {"requestId": r.id, "kind": r.kind, "ref": r.ref, "start": r.start, "end": r.end}
            # heartbeat implícito: o agente está vivo e pronto para servir
            s.query(ArenaAgent).filter_by(id=ag.id).update({"last_used_at": now})
            s.commit()
        time.sleep(0.05)
    return Response(status_code=204)


def _claimed(db: Session, ag: ArenaAgent, rid: str) -> RelayRequest:
    r = db.get(RelayRequest, rid)
    if not r or r.arena_id != ag.arena_id or r.agent_id != ag.id:  # outro agente/arena => inexistente
        raise HTTPException(404, "pedido não encontrado")
    if r.status != "claimed":
        raise HTTPException(409, "pedido não está aguardando dados")
    return r


@router.post("/relay/{rid}/data")
async def relay_data(rid: str, request: Request, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Corpo binário = bytes do trecho. Validado contra o pedido; guardado em spool TEMPORÁRIO até a entrega."""
    r = _claimed(db, ag, rid)
    body = await request.body()
    want = r.end - r.start + 1
    total = request.headers.get("x-total-size", "")
    if not total.isdigit() or not (0 < len(body) <= want) or len(body) > settings.relay_window_bytes:
        r.status, r.error = "failed", "tamanho inválido"
        db.commit()
        raise HTTPException(422, "trecho inválido")
    if hashlib.sha256(body).hexdigest() != request.headers.get("x-chunk-sha256", ""):
        r.status, r.error = "failed", "integridade"
        db.commit()
        raise HTTPException(422, "hash do trecho não confere")
    path = spool_file(rid)
    tmp = path.with_suffix(".part")
    tmp.write_bytes(body)
    tmp.replace(path)
    r.status, r.total_size, r.sha256, r.done_at = "done", int(total), hashlib.sha256(body).hexdigest(), utcnow()
    db.commit()
    purge_spool()
    return {"ok": True}


class RelayErr(BaseModel):
    code: str = Field(pattern=r"^(not_found|io_error|bad_request)$")
    message: str = Field("", max_length=200)


@router.post("/relay/{rid}/error")
def relay_error(rid: str, body: RelayErr, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    r = _claimed(db, ag, rid)
    r.status, r.error, r.done_at = "failed", body.code, utcnow()
    db.commit()
    return {"ok": True}


# ---------------- renderização local: o agente reivindica jobs de prévia/exportação ----------------
def _job_for(db: Session, ag: ArenaAgent, job_id: str) -> ExportJob:
    j = db.get(ExportJob, job_id)
    if not j or j.arena_id != ag.arena_id or j.render_location != "agent":  # outra arena/central => inexistente
        raise HTTPException(404, "job não encontrado")
    return j


def _fail_job(j: ExportJob, msg: str) -> None:
    j.status, j.error, j.finished_at, j.lease_until = "failed", msg[:300], utcnow(), None


def _asset_table(db: Session, j: ExportJob) -> dict[str, tuple[str, str]]:
    """name -> (storage_key, mime). Só logos da MESMA arena listados no snapshot do job e a faixa licenciada dele."""
    out: dict[str, tuple[str, str]] = {}
    if j.apply_sponsors:
        for i, o in enumerate(json.loads(j.overlay_spec or "[]")):
            if o["asset_key"].startswith(f"arenas/{j.arena_id}/"):
                out[f"logo{i}"] = (o["asset_key"], "image/png")
    if j.audio_mode in ("music", "mix") and j.music_id:
        t = db.get(MusicTrack, j.music_id)
        if t and t.arena_id == j.arena_id:
            out["music"] = (t.asset_key, "application/octet-stream")
    return out


def _sha(key: str) -> str:
    p = storage.path_for(key)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


@router.get("/render/next")
def render_next(ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Concede (lease) o job mais antigo da PRÓPRIA arena. Jobs com concessão vencida são reatribuídos até o limite."""
    now = utcnow()
    base = db.query(ExportJob).filter(ExportJob.arena_id == ag.arena_id, ExportJob.render_location == "agent")
    for j in base.filter(ExportJob.status == "processing", ExportJob.lease_until < now, ExportJob.attempts >= settings.render_max_attempts).all():
        _fail_job(j, "tempo esgotado após várias tentativas")
    db.commit()
    cand = (base.filter(((ExportJob.status == "queued")) | ((ExportJob.status == "processing") & (ExportJob.lease_until < now)))
            .order_by(ExportJob.created_at).limit(5).all())
    for c in cand:
        n = (db.query(ExportJob).filter(ExportJob.id == c.id, ExportJob.status == c.status)
             .update({"status": "processing", "attempts": ExportJob.attempts + 1, "started_at": now, "heartbeat_at": now,
                      "lease_until": now + timedelta(seconds=settings.render_lease_s), "progress": 0, "error": None}))
        db.commit()
        if n != 1:
            continue
        j = db.get(ExportJob, c.id)
        v = db.get(Video, j.video_id)
        if not v or v.deleted_at or v.arena_id != ag.arena_id or not v.local_id:
            _fail_job(j, "replay indisponível")
            db.commit()
            continue
        if j.audio_mode in ("music", "mix"):  # revalida licença/arena NO MOMENTO da renderização
            t = db.get(MusicTrack, j.music_id) if j.music_id else None
            if not t or t.arena_id != j.arena_id or not policy.track_usable(t):
                _fail_job(j, "faixa musical sem licença válida no momento da exportação")
                db.commit()
                continue
        assets = _asset_table(db, j)
        overlays = []
        for i, o in enumerate(json.loads(j.overlay_spec or "[]") if j.apply_sponsors else []):
            if f"logo{i}" not in assets or not storage.path_for(assets[f"logo{i}"][0]).is_file():
                _fail_job(j, "logotipo de patrocinador ausente; exportação recusada")
                db.commit()
                overlays = None
                break
            overlays.append({"asset": f"logo{i}", "sha256": _sha(assets[f"logo{i}"][0]), "logoW": o["logo_w"], "logoH": o["logo_h"],
                             "position": o["position"], "sizePct": o["size_pct"], "opacity": o["opacity"], "durationS": o["duration_s"]})
        if overlays is None:
            continue
        music = {"asset": "music", "sha256": _sha(assets["music"][0])} if "music" in assets else None
        audit.record(db, None, j.arena_id, "render.claim", "export", j.id, {"purpose": j.purpose, "attempt": j.attempts})
        db.commit()
        return {"jobId": j.id, "purpose": j.purpose, "localId": v.local_id, "audioMode": j.audio_mode, "originalVolume": j.original_volume,
                "musicVolume": j.music_volume, "overlays": overlays, "music": music, "leaseS": settings.render_lease_s,
                "sponsorVersion": j.sponsor_template_version, "frameOrientation": j.frame_orientation}
    return Response(status_code=204)


@router.get("/render/{job_id}/asset/{name}")
def render_asset(job_id: str, name: str, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    """Logos e música (NÃO vídeo) necessários ao job que este agente reivindicou."""
    j = _job_for(db, ag, job_id)
    if j.status != "processing":
        raise HTTPException(409, "job não está em processamento")
    key = _asset_table(db, j).get(name)
    if not key or not storage.path_for(key[0]).is_file():
        raise HTTPException(404, "recurso não encontrado")
    r = FileResponse(storage.path_for(key[0]), media_type=key[1])
    r.headers["Cache-Control"] = "private, no-store"
    return r


class RenderProgress(BaseModel):
    progress: int = Field(ge=0, le=99)


@router.post("/render/{job_id}/progress")
def render_progress(job_id: str, body: RenderProgress, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    j = _job_for(db, ag, job_id)
    if j.status != "processing":
        raise HTTPException(409, "job não está em processamento")
    now = utcnow()
    j.progress, j.heartbeat_at, j.lease_until = body.progress, now, now + timedelta(seconds=settings.render_lease_s)  # renova a concessão
    db.commit()
    return {"ok": True}


class RenderDone(BaseModel):
    bytes: int = Field(gt=0, le=50 * 1024**3)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/render/{job_id}/complete")
def render_complete(job_id: str, body: RenderDone, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    j = _job_for(db, ag, job_id)
    if j.status != "processing":
        raise HTTPException(409, "job não está em processamento")
    j.status, j.progress, j.output_bytes, j.output_sha256 = "ready", 100, body.bytes, body.sha256
    j.finished_at, j.lease_until, j.output_key = utcnow(), None, None  # a saída NÃO está na central
    db.commit()
    return {"ok": True}


class RenderFail(BaseModel):
    error: str = Field(max_length=300)
    retriable: bool = False


@router.post("/render/{job_id}/fail")
def render_fail(job_id: str, body: RenderFail, ag: ArenaAgent = Depends(bound), db: Session = Depends(get_db)):
    j = _job_for(db, ag, job_id)
    if j.status != "processing":
        raise HTTPException(409, "job não está em processamento")
    if body.retriable and j.attempts < settings.render_max_attempts:
        j.status, j.lease_until, j.error = "queued", None, body.error
    else:
        _fail_job(j, body.error)
    db.commit()
    return {"ok": True}


@router.get("/status")
def status(ag: ArenaAgent = Depends(agent_auth)):
    return {"nodeId": settings.node_id, "role": settings.node_role, "epoch": settings.central_epoch,
            "heartbeatIntervalS": settings.agent_heartbeat_s}

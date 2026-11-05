"""Capacidade, armazenamento e retenção — regras puras/consultas, SEM efeito colateral em arquivos.

Nada aqui apaga vídeo: o plano de retenção só LISTA candidatos (dry-run). Vídeos `protected` nunca entram.
"""
import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import settings
from .db import utcnow
from .models import Arena, ArenaAgent, Court, Plan, StorageSample, Video

MB = 1024 * 1024


# ---------------- conexão do agente ----------------
def connection_state(agent: ArenaAgent, now: datetime | None = None) -> str:
    """online ≤ 2 intervalos; delayed ≤ 6; offline depois disso; revoked se desativado."""
    now = now or utcnow()
    if not agent.active:
        return "revoked"
    seen = agent.last_heartbeat_at
    if not seen:
        return "never_connected"
    age = (now - seen).total_seconds()
    hb = settings.agent_heartbeat_s
    return "online" if age <= 2 * hb else "delayed" if age <= 6 * hb else "offline"


def arena_connection(agents: list[ArenaAgent], now: datetime | None = None) -> str:
    states = [connection_state(a, now) for a in agents if a.active]
    for s in ("online", "delayed", "offline"):
        if s in states:
            return s
    return "never_connected" if states else "no_agent"


# ---------------- limites ----------------
def arena_limit_bytes(db: Session, arena: Arena) -> int | None:
    """Override definido pelo portal tem prioridade sobre o plano. None = sem limite configurado."""
    if arena.storage_limit_mb:
        return arena.storage_limit_mb * MB
    plan = db.get(Plan, arena.plan_id) if arena.plan_id else None
    return plan.max_storage_mb * MB if plan else None


def court_limit_bytes(court: Court, arena_limit: int | None) -> int | None:
    lim = court.storage_limit_mb * MB if court.storage_limit_mb else None
    if lim and arena_limit:
        return min(lim, arena_limit)
    return lim


def usage_level(used: int, limit: int | None, warn_pct: int = 80, critical_pct: int = 95) -> str:
    if not limit:
        return "unlimited"
    pct = used * 100 / limit
    return "full" if pct >= 100 else "critical" if pct >= critical_pct else "warning" if pct >= warn_pct else "ok"


def arena_usage(db: Session, arena_id: str) -> int:
    """Metadados contabilizam o MP4 onde quer que esteja (central legado ou computador da arena)."""
    return int(db.query(func.coalesce(func.sum(Video.bytes), 0)).filter(Video.arena_id == arena_id, Video.deleted_at.is_(None)).scalar())


def court_usage(db: Session, court_id: str) -> int:
    return int(db.query(func.coalesce(func.sum(Video.bytes), 0)).filter(Video.court_id == court_id, Video.deleted_at.is_(None)).scalar())


# ---------------- previsão de esgotamento ----------------
def forecast_days_to_full(points: list[tuple[datetime, int]], limit: int | None, min_points: int = 3,
                          min_span_s: float = 3600) -> dict:
    """Regressão linear (mínimos quadrados) de uso x tempo. Honesta: sem dados suficientes → `insufficient_data`."""
    if not limit:
        return {"status": "no_limit", "daysToFull": None, "bytesPerDay": None}
    pts = sorted(points)
    if len(pts) < min_points or (pts[-1][0] - pts[0][0]).total_seconds() < min_span_s:
        return {"status": "insufficient_data", "daysToFull": None, "bytesPerDay": None}
    t0 = pts[0][0]
    xs = [(t - t0).total_seconds() for t, _ in pts]
    ys = [float(u) for _, u in pts]
    n = len(pts)
    mx, my = sum(xs) / n, sum(ys) / n
    var = sum((x - mx) ** 2 for x in xs)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var else 0.0  # bytes/s
    per_day = slope * 86400
    used = pts[-1][1]
    if used >= limit:
        return {"status": "full", "daysToFull": 0.0, "bytesPerDay": round(per_day)}
    if slope <= 0:
        return {"status": "stable", "daysToFull": None, "bytesPerDay": round(per_day)}
    return {"status": "growing", "daysToFull": round((limit - used) / slope / 86400, 2), "bytesPerDay": round(per_day)}


def samples(db: Session, arena_id: str, court_id: str | None, days: int = 14) -> list[StorageSample]:
    q = db.query(StorageSample).filter(StorageSample.arena_id == arena_id, StorageSample.ts >= utcnow() - timedelta(days=days))
    q = q.filter(StorageSample.court_id.is_(None) if court_id is None else StorageSample.court_id == court_id)
    return q.order_by(StorageSample.ts).all()


def disk_forecast(rows: list[StorageSample]) -> dict:
    """Previsão do disco físico: usa espaço livre reportado como 'capacidade restante'."""
    pts = [(r.ts, (r.disk_total_bytes or 0) - (r.disk_free_bytes or 0)) for r in rows if r.disk_total_bytes]
    return forecast_days_to_full(pts, rows[-1].disk_total_bytes) if pts else {"status": "insufficient_data", "daysToFull": None, "bytesPerDay": None}


def record_sample(db: Session, arena_id: str, court_id: str | None, used: int, free: int | None, total: int | None,
                  now: datetime | None = None) -> bool:
    """Throttle por (arena, quadra) para não crescer sem limite com heartbeats frequentes."""
    now = now or utcnow()
    last = (db.query(func.max(StorageSample.ts))
            .filter(StorageSample.arena_id == arena_id,
                    StorageSample.court_id.is_(None) if court_id is None else StorageSample.court_id == court_id).scalar())
    if last and (now - last).total_seconds() < settings.storage_sample_min_interval_s:
        return False
    db.add(StorageSample(arena_id=arena_id, court_id=court_id, ts=now, used_bytes=used, disk_free_bytes=free, disk_total_bytes=total))
    return True


# ---------------- retenção (somente planejamento) ----------------
def retention_plan(db: Session, arena: Arena, now: datetime | None = None, limit: int = 500) -> dict:
    """Candidatos à remoção LOCAL pelo agente: vencidos pela retenção da quadra, mais os mais antigos se a arena
    estiver acima do limiar crítico. `protected` é inviolável. Este módulo não apaga nada e `deleteEnabled` é sempre falso."""
    now = now or utcnow()
    courts = {c.id: c for c in db.query(Court).filter_by(arena_id=arena.id)}
    base = db.query(Video).filter(Video.arena_id == arena.id, Video.deleted_at.is_(None))
    protected_skipped = base.filter(Video.protected.is_(True)).count()
    rows = base.filter(Video.protected.is_(False)).order_by(Video.captured_at.asc()).all()
    cands: list[dict] = []
    chosen: set[str] = set()
    for v in rows:
        ct = courts.get(v.court_id)
        if ct and v.captured_at < now - timedelta(days=ct.retention_days):
            cands.append({"videoId": v.id, "localId": v.local_id, "courtId": v.court_id, "bytes": v.bytes,
                          "capturedAt": v.captured_at.isoformat() + "Z", "reason": "expired"})
            chosen.add(v.id)
    used = arena_usage(db, arena.id)
    lim = arena_limit_bytes(db, arena)
    level = usage_level(used, lim, arena.storage_warn_pct, arena.storage_critical_pct)
    if lim and level in ("critical", "full"):
        target = lim * arena.storage_warn_pct / 100
        projected = used - sum(c["bytes"] for c in cands)
        for v in rows:
            if projected <= target:
                break
            if v.id in chosen:
                continue
            cands.append({"videoId": v.id, "localId": v.local_id, "courtId": v.court_id, "bytes": v.bytes,
                          "capturedAt": v.captured_at.isoformat() + "Z", "reason": "pressure"})
            projected -= v.bytes
    cands = cands[:limit]
    return {"deleteEnabled": False, "dryRun": True, "level": level, "candidates": cands,
            "bytesReclaimable": sum(c["bytes"] for c in cands), "protectedSkipped": protected_skipped}


# ---------------- configuração entregue ao agente ----------------
def agent_config(db: Session, arena: Arena) -> dict:
    alim = arena_limit_bytes(db, arena)
    courts = db.query(Court).filter_by(arena_id=arena.id).order_by(Court.number).all()
    cfg = {
        "arenaId": arena.id, "arenaStatus": arena.status, "timezone": arena.timezone,
        "storage": {"arenaLimitBytes": alim, "warnPct": arena.storage_warn_pct, "criticalPct": arena.storage_critical_pct,
                    "autoDelete": False},  # nesta etapa o agente NUNCA apaga automaticamente
        "courts": [{"courtKey": c.local_court_key, "status": c.status, "retentionDays": c.retention_days,
                    "storageLimitBytes": court_limit_bytes(c, alim)} for c in courts],
        "heartbeatIntervalS": settings.agent_heartbeat_s,
        "resources": {"maxFfmpeg": arena.agent_max_ffmpeg, "hwaccel": arena.agent_hwaccel, "renderNice": arena.agent_render_nice},
    }
    cfg["configVersion"] = hashlib.sha256(json.dumps(cfg, sort_keys=True).encode()).hexdigest()[:16]
    return cfg

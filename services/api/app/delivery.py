"""Entrega de vídeo hospedado no computador da arena, sem armazená-lo na central.

Fluxo: jogador → /media/{token assinado} → a API valida (assinatura, expiração, usuário, replay, arena, patrocínio) →
cria RelayRequest(trecho) → o AGENTE (conexão de saída, autenticada e assinada) busca o trecho no disco local e o envia
→ a API entrega ao jogador e APAGA o trecho do spool. Suporta Range/seek: cada trecho é uma janela limitada.
O agente nunca é exposto à internet.
"""
import hashlib
import re
import time
from datetime import timedelta
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal, utcnow
from .models import ArenaAgent, RelayRequest, Video

_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RelayError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ---------- disponibilidade ----------
def _last_seen(a: ArenaAgent):
    seen = [t for t in (a.last_used_at, a.last_heartbeat_at) if t]
    return max(seen) if seen else None


def arena_reachable(db: Session, arena_id: str, cache: dict | None = None) -> bool:
    """Arena acessível = algum agente ativo falou com a central (heartbeat ou long-poll) nos últimos N segundos."""
    if cache is not None and arena_id in cache:
        return cache[arena_id]
    cut = utcnow() - timedelta(seconds=settings.agent_reachable_s)
    ok = any((ls := _last_seen(a)) is not None and ls >= cut for a in db.query(ArenaAgent).filter_by(arena_id=arena_id, active=True))
    if cache is not None:
        cache[arena_id] = ok
    return ok


def availability(db: Session, v: Video, cache: dict | None = None) -> dict:
    """Nunca declara 'playable' um vídeo que não pode ser entregue agora."""
    if v.deleted_at is not None:
        return {"playable": False, "availability": "deleted", "reason": "Replay removido"}
    if v.processing_status != "ready":
        return {"playable": False, "availability": "processing", "reason": "Replay em processamento"}
    if v.storage_location != "agent":
        return {"playable": True, "availability": "available", "reason": None}
    if v.media_state == "missing":
        return {"playable": False, "availability": "missing", "reason": "Arquivo não encontrado na arena"}
    if not v.bytes:
        return {"playable": False, "availability": "processing", "reason": "Metadados do arquivo ainda não confirmados"}
    if not arena_reachable(db, v.arena_id, cache):
        return {"playable": False, "availability": "arena_offline", "reason": "A arena está temporariamente offline; tente novamente em instantes"}
    return {"playable": True, "availability": "available", "reason": None}


# ---------- spool temporário ----------
def spool_dir() -> Path:
    settings.relay_spool_dir.mkdir(parents=True, exist_ok=True)
    return settings.relay_spool_dir


def spool_file(rid: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", rid):
        raise ValueError("id inválido")
    return spool_dir() / f"{rid}.bin"


def purge_spool(max_age_s: int = 300) -> int:
    """Remove sobras (cliente desistiu, API reiniciou). Chamado de forma oportunista."""
    n, cut = 0, time.time() - max_age_s
    for p in spool_dir().glob("*"):
        try:
            if p.stat().st_mtime < cut:
                p.unlink()
                n += 1
        except OSError:
            pass
    return n


# ---------- busca de trecho ----------
def fetch_range(arena_id: str, kind: str, ref: str, start: int, end: int, timeout: float | None = None) -> tuple[bytes, int]:
    """Cria o pedido e aguarda o agente. Sessões curtas: enxerga o commit do agente, sem segurar transação."""
    timeout = timeout if timeout is not None else settings.relay_timeout_s
    with SessionLocal() as s:
        req = RelayRequest(arena_id=arena_id, kind=kind, ref=ref, start=start, end=end,
                           expires_at=utcnow() + timedelta(seconds=timeout))
        s.add(req)
        s.commit()
        rid = req.id
    deadline, delay = time.monotonic() + timeout, 0.005
    while True:
        with SessionLocal() as s:
            r = s.get(RelayRequest, rid)
            if r.status == "done":
                path = spool_file(rid)
                try:
                    data = path.read_bytes()
                finally:
                    path.unlink(missing_ok=True)  # entregue => apagado
                if hashlib.sha256(data).hexdigest() != r.sha256:
                    r.status, r.error = "failed", "integridade"
                    s.commit()
                    raise RelayError("integrity", "trecho corrompido em trânsito")
                total = r.total_size or 0
                r.status = "consumed"
                s.commit()
                return data, total
            if r.status == "failed":
                raise RelayError(r.error or "failed", r.error or "falha no agente")
            if time.monotonic() >= deadline:
                r.status = "expired"
                s.commit()
                raise RelayError("timeout", "a arena não respondeu a tempo")
        time.sleep(delay)
        delay = min(delay * 1.6, 0.1)


# ---------- Range ----------
def parse_range(header: str | None, total: int) -> tuple[int, int] | None | str:
    """None = sem Range (ou multi-range: ignorado, resposta 200 é válida). 'invalid' => 416."""
    if not header:
        return None
    m = _RANGE.match(header.strip())
    if not m or (m.group(1) == "" and m.group(2) == ""):
        return None if "," in header else "invalid"
    a, b = m.groups()
    if a == "":  # sufixo: últimos N bytes
        n = int(b)
        if n == 0:
            return "invalid"
        return max(total - n, 0), total - 1
    start = int(a)
    end = int(b) if b else total - 1
    if start >= total or end < start:
        return "invalid"
    return start, min(end, total - 1)


def safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name)[:80]


def remote_response(db: Session, v_arena_id: str, kind: str, ref: str, total: int, mime: str, filename: str, disp: str,
                    range_header: str | None, video: Video | None = None) -> StreamingResponse:
    """Resposta HTTP 200/206 com streaming por janelas. `inline` limita cada resposta a uma janela (seek barato);
    `attachment` entrega até o fim (download/retomada)."""
    if video is not None:
        av = availability(db, video)
        if not av["playable"]:
            code = 404 if av["availability"] in ("missing", "deleted") else 503
            raise HTTPException(code, av["reason"], headers={"Retry-After": "10"} if code == 503 else None)
    rng = parse_range(range_header, total)
    if rng == "invalid":
        raise HTTPException(416, "intervalo inválido", headers={"Content-Range": f"bytes */{total}"})
    start, end = (0, total - 1) if rng is None else rng
    status = 200 if rng is None else 206
    win = settings.relay_window_bytes
    if disp == "inline" and rng is not None:
        end = min(end, start + win - 1)
    first_end = min(end, start + win - 1)
    try:
        first, real_total = fetch_range(v_arena_id, kind, ref, start, first_end)
    except RelayError as e:
        _relay_http_error(db, e, video)
    if real_total and real_total != total:  # metadado desatualizado: corrige e pede nova tentativa
        if video is not None:
            video.bytes = real_total
            db.commit()
        raise HTTPException(409, "metadados do arquivo mudaram; tente novamente")
    if len(first) != first_end - start + 1:
        raise HTTPException(502, "resposta incompleta da arena")

    def body():
        yield first
        pos = first_end + 1
        while pos <= end:
            chunk_end = min(end, pos + win - 1)
            try:
                data, _ = fetch_range(v_arena_id, kind, ref, pos, chunk_end)
            except RelayError:
                return  # conexão se encerra; o player/gerenciador de download retoma com Range
            yield data
            pos = chunk_end + 1

    h = {"Accept-Ranges": "bytes", "Content-Length": str(end - start + 1), "Cache-Control": "private, no-store",
         "X-Content-Type-Options": "nosniff"}
    if status == 206:
        h["Content-Range"] = f"bytes {start}-{end}/{total}"
    if disp == "attachment":
        h["Content-Disposition"] = f'attachment; filename="{safe_name(filename)}"'
    return StreamingResponse(body(), status_code=status, media_type=mime, headers=h)


def _relay_http_error(db: Session, e: RelayError, video: Video | None):
    if e.code == "not_found":
        if video is not None:
            video.media_state = "missing"
            db.commit()
        raise HTTPException(404, "Arquivo não encontrado na arena")
    if e.code == "integrity":
        raise HTTPException(502, "falha de integridade na entrega")
    raise HTTPException(503, "A arena está indisponível no momento; tente novamente", headers={"Retry-After": "10"})

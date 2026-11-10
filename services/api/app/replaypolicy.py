"""Publicação automática e música por replay. Regras puras + gravação segura (idempotente, sem condição de corrida)."""
from sqlalchemy import update
from sqlalchemy.orm import Session

from . import audit, policy
from .db import utcnow
from .models import Arena, Court, MusicTrack, Video


def is_valid(v: Video) -> bool:
    """Replay 'reproduzível': não removido, processado, arquivo confirmado pelo agente e com tamanho/duração reais."""
    return (v.deleted_at is None and v.processing_status == "ready" and v.media_state == "ok" and v.bytes > 0 and v.duration > 0)


def auto_publish(db: Session, v: Video, arena: Arena, court: Court) -> bool:
    """Publica na arena quando válido. NUNCA republica vídeo removido, bloqueado/oculto pela moderação ou já publicado antes.
    Quadra com visibility='private' é uma retenção administrativa (só o Portal altera)."""
    if v.visibility == "arena" or v.published_at is not None:
        return False
    if v.moderation_state != "none" or v.deleted_at is not None:
        return False
    if arena.status != "active" or court.visibility != "arena" or not is_valid(v):
        return False
    v.visibility, v.published_at = "arena", utcnow()
    audit.record(db, None, v.arena_id, "video.auto_publish", "video", v.id, {"court": court.local_court_key})
    return True


def pick_music(db: Session, arena: Arena, attempts: int = 5) -> tuple[str | None, str]:
    """Seleciona a próxima faixa da biblioteca da PRÓPRIA arena (ativa, com direitos e licença vigente), em rotação sem repetir
    a anterior quando há alternativa. O cursor da arena é avançado por compare-and-swap: replays simultâneos recebem faixas
    diferentes e nunca a mesma duas vezes seguidas. Retorna (music_id|None, motivo)."""
    if arena.audio_default_mode not in ("music", "mix"):
        return None, "fallback_ambient:modo_da_arena_nao_usa_musica"
    for _ in range(attempts):
        tracks = policy.usable_tracks(db, arena.id)
        if not tracks:
            return None, "fallback_ambient:biblioteca_sem_faixas_validas"
        ids = [t.id for t in tracks]
        db.refresh(arena)
        last = arena.music_last_id
        if len(ids) == 1:
            chosen, reason = ids[0], "single_track"
        else:
            nxt = ids[(ids.index(last) + 1) % len(ids)] if last in ids else ids[0]
            chosen, reason = nxt, "rotation"
        cond = (Arena.music_last_id.is_(None) if last is None else Arena.music_last_id == last)
        done = db.execute(update(Arena).where(Arena.id == arena.id, cond).values(music_last_id=chosen).execution_options(synchronize_session=False))
        if done.rowcount == 1:
            db.refresh(arena)
            return chosen, reason
        db.expire(arena)  # outro replay avançou o cursor: tenta de novo
    return None, "fallback_ambient:disputa_de_selecao"


def assign_music(db: Session, v: Video, arena: Arena) -> None:
    """Atribui (uma única vez) a música do replay e registra o motivo; não altera atribuição existente."""
    if v.music_id or v.music_reason:
        return
    mid, reason = pick_music(db, arena)
    v.music_id, v.music_reason = mid, reason
    if mid is None and v.has_audio is False and reason.startswith("fallback_ambient"):
        v.music_reason = reason + "|sem_audio_ambiente"
    audit.record(db, None, v.arena_id, "video.music_assign", "video", v.id, {"music_id": mid, "reason": v.music_reason})


def finalize_new_replay(db: Session, v: Video, court: Court) -> None:
    arena = db.get(Arena, v.arena_id)
    assign_music(db, v, arena)
    auto_publish(db, v, arena, court)


def moderate(db: Session, v: Video, action: str, actor_id: str, reason: str) -> None:
    """Moderação excepcional (só o Portal/administrador global). hide|block tiram da arena e impedem republicação automática;
    restore volta a publicar (ação explícita do administrador)."""
    now = utcnow()
    if action in ("hide", "block"):
        v.visibility, v.moderation_state = "private", ("hidden" if action == "hide" else "blocked")
    elif action == "restore":
        v.moderation_state = "none"
        if is_valid(v):
            v.visibility = "arena"
            v.published_at = v.published_at or now
    else:
        raise ValueError(action)
    v.moderation_reason, v.moderated_by, v.moderated_at = (reason or "")[:200], actor_id, now

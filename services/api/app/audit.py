import json

from sqlalchemy.orm import Session

from .models import AuditLog

_SECRET_KEYS = {"password", "password_hash", "token", "secret", "mfa_secret", "token_hash"}


def record(db: Session, actor_id: str | None, arena_id: str | None, action: str, resource_type: str,
           resource_id: str | None = None, changes: dict | None = None) -> None:
    clean = {k: ("***" if k in _SECRET_KEYS else v) for k, v in (changes or {}).items()}
    db.add(AuditLog(actor_id=actor_id, arena_id=arena_id, action=action, resource_type=resource_type,
                    resource_id=resource_id, changed_fields=json.dumps(clean, default=str, ensure_ascii=False)))

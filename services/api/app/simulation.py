"""Provisionamento de arenas FICTÍCIAS para simulação/testes de capacidade. Nunca roda em produção por padrão:
só é chamado por testes e por scripts/simulate_fleet.py contra um banco descartável."""
from sqlalchemy.orm import Session

from .models import Arena, ArenaAgent, Court, Plan
from .security import hash_agent_token


def provision_arenas(db: Session, n: int, courts_per_arena: int = 2, max_storage_mb: int = 1024, prefix: str = "sim") -> list[dict]:
    plan = db.query(Plan).filter_by(name="Simulação").first() or Plan(name="Simulação", max_courts=max(courts_per_arena, 2),
                                                                      max_storage_mb=max_storage_mb)
    db.add(plan)
    db.flush()
    out = []
    for i in range(1, n + 1):
        tag = f"{prefix}{i:02d}"
        ar = Arena(slug=f"{prefix}-{i:02d}-arena", name=f"Arena fictícia {i:02d}", plan_id=plan.id)
        db.add(ar)
        db.flush()
        keys = []
        for j in range(1, courts_per_arena + 1):
            key = f"REPLAY_{j:02d}"
            db.add(Court(arena_id=ar.id, label=f"Quadra {j:02d}", number=j, local_court_key=key))
            keys.append(key)
        token = f"rag_sim_token_{tag}"  # fictício, só para banco descartável
        db.add(ArenaAgent(arena_id=ar.id, label=f"agente-{tag}", token_hash=hash_agent_token(token)))
        out.append({"tag": tag, "arenaId": ar.id, "token": token, "courtKeys": keys})
    db.commit()
    return out

"""Limpeza segura e explícita de prévias antigas, sem tocar nas mídias originais.

Padrão somente relatório. Execute manualmente apenas em ambiente de homologação
ou com processo operacional aprovado. Não apaga jobs em fila/processamento.
"""
import argparse
import json
from datetime import timedelta

from sqlalchemy.orm import Session

from .db import SessionLocal, utcnow
from .models import ExportJob
from . import storage


def prune_previews(db: Session, days: int = 7, limit: int = 200, apply: bool = False) -> dict:
    if not (1 <= days <= 3650 and 1 <= limit <= 1000):
        raise ValueError("days deve ficar entre 1 e 3650 e limit entre 1 e 1000")
    cutoff = utcnow() - timedelta(days=days)
    jobs = (db.query(ExportJob)
            .filter(ExportJob.purpose == "preview",
                    ExportJob.status.in_(("ready", "failed")),
                    ExportJob.created_at < cutoff)
            .order_by(ExportJob.created_at.asc())
            .limit(limit).all())
    scanned, removed, skipped, bytes_freed = len(jobs), 0, 0, 0
    for job in jobs:
        # Somente arquivos gerados pela fila de prévias dentro da pasta de exports.
        # Nunca apagar vídeo bruto, música, thumbnail ou logotipo por este comando.
        expected_key = storage.make_key(job.arena_id, "exports", f"{job.id}.mp4")
        if job.output_key and job.output_key != expected_key:
            skipped += 1
            continue
        if not apply:
            continue
        if job.output_key:
            path = storage.path_for(job.output_key)
            if path.is_file():
                size = path.stat().st_size
                path.unlink()
                bytes_freed += size
        db.delete(job)
        db.commit()
        removed += 1
    return {"dry_run": not apply, "eligible": scanned, "removed": removed,
            "skipped_unsafe": skipped, "bytes_freed": bytes_freed, "older_than_days": days}


def main() -> None:
    p = argparse.ArgumentParser(description="Prévia patrocinada: listar ou limpar cache antigo")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--apply", action="store_true", help="Remove somente cache de prévias elegível")
    p.add_argument("--confirm", default="", help="Exige PREVIEWS_ONLY ao usar --apply")
    args = p.parse_args()
    if args.apply and args.confirm != "PREVIEWS_ONLY":
        p.error("uso destrutivo requer --apply --confirm PREVIEWS_ONLY")
    with SessionLocal() as db:
        print(json.dumps(prune_previews(db, days=args.days, limit=args.limit, apply=args.apply),
                         ensure_ascii=False))


if __name__ == "__main__":
    main()

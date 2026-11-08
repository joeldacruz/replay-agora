"""Envio de e-mail transacional (convite/redefinição). Com SMTP_HOST configurado envia de verdade; sem SMTP guarda na caixa de saída
(somente o global_admin vê, no Portal) para homologação. O link de ativação NUNCA é devolvido por endpoint de gestão de arena."""
import smtplib
import ssl
from email.message import EmailMessage

from sqlalchemy.orm import Session

from .config import settings
from .models import OutboxMail


def send(db: Session, to: str, subject: str, body: str, kind: str = "") -> OutboxMail:
    row = OutboxMail(to_email=to, subject=subject[:200], body=body, kind=kind)
    if settings.smtp_host:
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
            msg.set_content(body)
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                if settings.smtp_user:
                    s.login(settings.smtp_user, settings.smtp_password)
                s.send_message(msg)
            row.status, row.body = "sent", "(enviado por SMTP; conteúdo não retido)"
        except (OSError, smtplib.SMTPException):
            row.status = "failed"
            row.body = "(falha no envio SMTP; reenvie o convite)"
    db.add(row)
    db.flush()
    return row

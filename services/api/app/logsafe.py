"""Redação de segredos em logs: tokens de agente, Bearer, chaves de assinatura e assinaturas hexadecimais longas."""
import logging
import re

_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=\-]{8,}"), r"\1***"),
    (re.compile(r"\brag_[A-Za-z0-9_\-]{8,}"), "rag_***"),
    (re.compile(r"(?i)((?:sig_?key|signing_?key|x-agent-sig|token|secret|password)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._~+/=\-]{8,}"), r"\1***"),
    (re.compile(r"\b[0-9a-f]{64}\b"), "<hex64>"),
]


def redact(text: str) -> str:
    for rx, rep in _PATTERNS:
        text = rx.sub(rep, text)
    return text


_installed = False


def install_redaction() -> None:
    """Troca a fábrica de LogRecord: toda mensagem de log de qualquer logger passa pela redação."""
    global _installed
    if _installed:
        return
    old = logging.getLogRecordFactory()

    def factory(*a, **k):
        rec = old(*a, **k)
        try:
            msg = rec.getMessage()
        except Exception:  # noqa: BLE001
            return rec
        red = redact(msg)
        if red != msg:
            rec.msg, rec.args = red, ()
        return rec
    logging.setLogRecordFactory(factory)
    _installed = True

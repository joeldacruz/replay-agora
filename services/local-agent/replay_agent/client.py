"""Cliente autenticado da API central. Qualquer falha de rede/5xx vira CentralUnavailable (o motor segue local).

Cada chamada leva Bearer + (se houver chave) assinatura HMAC com timestamp e nonce únicos (anti-replay).
mTLS: passe `cert=(crt, key)` e `verify=ca` ao criar o httpx.Client (ver make_http); o certificado fica só no agente.
"""
import json as _json
import time
from urllib.parse import urlsplit

import httpx

from .signing import sign_headers


class CentralUnavailable(Exception):
    pass


class CentralRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


def make_http(base_url: str, cert: tuple[str, str] | None = None, ca: str | None = None, timeout: float = 30.0) -> httpx.Client:
    """Cliente HTTPS de produção: valida o servidor (ca opcional) e apresenta certificado de cliente quando configurado."""
    return httpx.Client(base_url=base_url, cert=cert, verify=ca or True, timeout=timeout)


class CentralClient:
    def __init__(self, http, token: str, node_id: str, signing_key: str | None = None, base: str = "/api/agent/v1", clock=time.time):
        self.http, self.node_id, self.base, self.clock = http, node_id, base, clock
        self._token, self._key = token, signing_key
        self.epoch = 0           # maior epoch de fencing já visto
        self.epoch_changed = False

    def __repr__(self) -> str:  # nunca expõe credenciais
        return f"CentralClient(node={self.node_id!r}, signed={bool(self._key)}, epoch={self.epoch})"

    def set_credentials(self, token: str, signing_key: str | None) -> None:
        self._token, self._key = token, signing_key

    @property
    def signed(self) -> bool:
        return bool(self._key)

    def _headers(self, method: str, url: str, body: bytes) -> dict:
        h = {"Authorization": f"Bearer {self._token}", "X-Agent-Node": self.node_id}
        if self.epoch:
            h["X-Agent-Epoch"] = str(self.epoch)
        if self._key:
            parts = urlsplit(url)
            h |= sign_headers(self._key, method, parts.path, parts.query, body, now=self.clock())
        return h

    def request_raw(self, method: str, path: str, content: bytes = b"", headers: dict | None = None, timeout: float = 60):
        url = self.base + path
        hdrs = {**(headers or {}), **self._headers(method, url, content)}
        try:
            r = self.http.request(method, url, content=content or None, headers=hdrs, timeout=timeout)
        except (httpx.TransportError, OSError) as e:
            raise CentralUnavailable(type(e).__name__) from e
        ep = r.headers.get("x-central-epoch")
        if ep and ep.isdigit():
            ep = int(ep)
            if ep < self.epoch:  # resposta de primário obsoleto (pós-failover): ignorar
                raise CentralUnavailable(f"epoch obsoleto {ep}<{self.epoch}")
            if ep > self.epoch:
                self.epoch_changed = self.epoch != 0 or self.epoch_changed
                self.epoch = ep
        if r.status_code >= 500:
            raise CentralUnavailable(f"http {r.status_code}")
        if r.status_code == 409 and "epoch obsoleto" in r.text:
            raise CentralUnavailable("nó obsoleto")
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise CentralRejected(r.status_code, str(detail))
        return r

    def call(self, method: str, path: str, json: dict | None = None, timeout: float = 30):
        body = _json.dumps(json, separators=(",", ":")).encode() if json is not None else b""
        r = self.request_raw(method, path, body, {"Content-Type": "application/json"} if body else None, timeout)
        return None if r.status_code == 204 or not r.content else r.json()

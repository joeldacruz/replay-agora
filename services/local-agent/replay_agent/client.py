"""Cliente autenticado da API central. Qualquer falha de rede/5xx vira CentralUnavailable (o motor segue local)."""
import httpx


class CentralUnavailable(Exception):
    pass


class CentralRejected(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"{status}: {detail}")
        self.status, self.detail = status, detail


class CentralClient:
    def __init__(self, http, token: str, node_id: str, base: str = "/api/agent/v1"):
        self.http, self.node_id, self.base = http, node_id, base
        self._h = {"Authorization": f"Bearer {token}", "X-Agent-Node": node_id}
        self.epoch = 0           # maior epoch de fencing já visto
        self.epoch_changed = False

    def call(self, method: str, path: str, json: dict | None = None):
        headers = dict(self._h)
        if self.epoch:
            headers["X-Agent-Epoch"] = str(self.epoch)
        try:
            r = self.http.request(method, self.base + path, json=json, headers=headers, timeout=15)
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
        return r.json()

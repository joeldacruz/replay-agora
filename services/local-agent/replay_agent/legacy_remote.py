"""Consumidor de revisões v3 para o legado, independente do motor de captura."""

from contextlib import contextmanager
import copy
import fcntl
import hashlib
import json
import re
import subprocess

from .client import CentralRejected
from .legacy_bridge import replay_profile
from .remote import ApplyError, RemoteManager, SCHEMA
from .renderjobs import RenderError, probe
from .vault import _atomic_write
from threading import Lock


class LegacyRemote(RemoteManager):
    """Use uma raiz exclusiva do adaptador. Cada poll trata só revisão de replay."""

    def __init__(self, client, data_dir, court_keys, **kwargs):
        super().__init__(None, client, data_dir, **kwargs)
        self.court_keys = frozenset(court_keys)
        if not self.court_keys:
            raise ValueError("declare as chaves locais das quadras")
        self.outbox = self.dir / "legacy-result.json"

    @contextmanager
    def _writer(self):
        with self.lock, (self.dir / "legacy.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.state = self._load_state()
            yield

    def _persist(self, path, data):
        _atomic_write(path, json.dumps(data, allow_nan=False).encode())

    def _prepare(self, rev):
        snap = rev["snapshot"]
        if snap["schema"] != SCHEMA:
            raise ValueError("esquema de configuração incompatível")
        courts = []
        seen = set()
        assets = {asset["versionId"]: asset for asset in (rev.get("assets") or [])}
        for court in snap["courts"]:
            key = court["key"]
            if key not in self.court_keys or key in seen:
                raise ValueError("quadra desconhecida ou duplicada")
            seen.add(key)
            if type(court["active"]) is not bool:
                raise ValueError("estado da quadra inválido")
            if not court["active"]:
                continue
            frames = court["frames"]
            if type(frames["enabled"]) is not bool:
                raise ValueError("estado das molduras inválido")
            rp = court["replay"]
            formats = rp["formats"]
            if not isinstance(formats, list) or any(f not in ("horizontal", "vertical") for f in formats):
                raise ValueError("formatos inválidos")
            for orientation in formats if frames["enabled"] else ():
                frame = frames.get(orientation)
                if frame is not None:
                    self._prepare_png(frame, assets)
            rotation = (court.get("camera") or {}).get("rotationDeg", 0)
            # Persiste somente campos consumidos pela ponte. Ignora segredos e controladores.
            courts.append({"key": key, "active": True, "camera": {"rotationDeg": rotation},
                           "frames": {"enabled": frames["enabled"],
                                      **{f: frames[f] for f in formats if f in frames}},
                           "replay": {k: rp[k] for k in ("preS", "postS", "formats")}})
        version = rev["version"]
        candidate = {"activeVersion": version, "highestVersion": version,
                     "snapshot": {"schema": SCHEMA, "courts": courts},
                     "prevVersion": self.active_version, "prevSnapshot": self.state.get("snapshot")}
        reader = copy.copy(self)
        reader.state = candidate
        reader.lock = Lock()
        for court in courts:
            replay_profile(reader, court["key"])
        return candidate

    def _prepare_png(self, frame, assets):
        key, sha = frame["versionId"], frame["sha256"]
        path = self._frame_path(key)
        if not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise ValueError("checksum inválido")
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise ValueError("moldura em cache diverge da revisão")
        else:
            asset = assets[key]
            if asset["sha256"] != sha or asset["url"] != f"/remote/assets/{key}":
                raise ValueError("referência da moldura inválida")
            self._stage_frame(asset)
        data = probe(path, self.ffprobe)
        video = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
        if (video.get("codec_name"), video.get("width"), video.get("height")) != (
                "png", frame["width"], frame["height"]):
            raise ValueError("PNG não corresponde ao perfil da moldura")
        result = subprocess.run([self.ffmpeg, "-v", "error", "-xerror", "-i", str(path),
                                 "-frames:v", "1", "-f", "null", "-"], capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError("PNG ilegível")

    def poll(self):
        """Busca apenas /remote/pending e preserva o resultado até a confirmação."""
        with self._writer():
            if self.outbox.exists():
                pending = json.loads(self.outbox.read_text())
            else:
                rev = self.client.call("GET", "/remote/pending")
                if not rev:
                    return None
                version = rev["version"]
                if type(version) is not int or version <= 0:
                    raise ValueError("versão inválida")
                candidate = None
                try:
                    if version <= self.state.get("highestVersion", self.active_version):
                        raise ValueError("versão antiga para esta instalação")
                    candidate = self._prepare(rev)
                except (ApplyError, ValueError, TypeError, KeyError, OSError, RenderError, subprocess.TimeoutExpired) as exc:
                    notice = {"kind": "replay", "key": "", "ok": False,
                              "message": f"Perfil rejeitado ({type(exc).__name__})"}
                else:
                    notice = {"kind": "replay", "key": "", "ok": True,
                              "message": "Janela, formatos e molduras do legado prontos. Captura e controladores fora do escopo."}
                report = {"status": "aplicado" if candidate else "falha",
                          "activeVersion": version if candidate else self.active_version,
                          "items": [notice], "rolledBack": False}
                if candidate is None:
                    report["error"] = notice["message"]
                pending = {"version": version, "candidate": candidate, "report": report}
                self._persist(self.outbox, pending)

            version, report = pending["version"], pending["report"]
            try:
                self.client.call("POST", f"/remote/{version}/received", {})
                self.client.call("POST", f"/remote/{version}/progress", {})
                reply = self.client.call("POST", f"/remote/{version}/result", report)
            except CentralRejected as exc:
                if exc.status != 409:
                    raise
                self.outbox.unlink()
                return None
            if reply["status"] == "substituido" or reply["status"] == "revertido":
                self.outbox.unlink()
                return None
            if reply["status"] != report["status"]:
                raise ValueError("central retornou resultado diferente para a revisão")
            if pending["candidate"] is not None:
                self._persist(self.state_path, pending["candidate"])
                self.state = pending["candidate"]
            self.outbox.unlink()
            return report

    def rollback(self):
        """Restaura o perfil anterior localmente. A central precisa de nova revisão de reversão."""
        with self._writer():
            if self.outbox.exists():
                raise RuntimeError("há confirmação de revisão pendente")
            old = self.state.get("prevSnapshot")
            if old is None:
                return False
            state = {"activeVersion": self.state["prevVersion"], "snapshot": old,
                     "highestVersion": self.state.get("highestVersion", self.active_version),
                     "prevVersion": 0, "prevSnapshot": None}
            self._persist(self.state_path, state)
            self.state = state
            return True

"""Relay do agente: classificação EXPLÍCITA de erro de arquivo (errno) — Issue #15 (Quadra 02 respondia `not_found` com o arquivo íntegro).

Defeito reproduzido: qualquer falha de acesso (permissão, E/S, laço de symlink…) virava `not_found`, o agente rebaixava `file_ok=0` + `pending`
e a central marcava `media_state='missing'` (parava de oferecer o vídeo); `reconcile_files` (os.path.isfile) engolia todo OSError igualmente.
Só ausência DEFINITIVA (ENOENT/ENOTDIR) pode rebaixar o arquivo; permissão/E-S são `io_error`, transitórios e SEM envenenar o catálogo."""
import errno
import logging
import os
import threading
import time
from pathlib import Path

import pytest

from app.db import SessionLocal
from app.models import RelayRequest, Video
from replay_agent import filecheck
from replay_agent.filecheck import probe_file
from test_delivery import SIZE, blob, rig, url_for  # noqa: F401  (fixture `rig` reutilizada)


def row(rig, k):
    return rig.agent.store.get(rig.files[k][0])


def central_state(rig, k):
    db = SessionLocal()
    try:
        return db.get(Video, rig.ids[k]).media_state
    finally:
        db.close()


def last_relay_error(rig):
    db = SessionLocal()
    try:
        return [r.error for r in db.query(RelayRequest).order_by(RelayRequest.created_at)]
    finally:
        db.close()


def deny(monkeypatch, target: Path, fn="stat", err=errno.EACCES):
    """Simula o erro SOMENTE para `target` (os demais arquivos seguem normais)."""
    real = getattr(os, fn) if fn != "getsize" else os.path.getsize
    t = os.fspath(target)

    def fake(p, *a, **k):
        if os.fspath(p) == t:
            raise OSError(err, os.strerror(err), t) if err != errno.EACCES else PermissionError(err, os.strerror(err), t)
        return real(p, *a, **k)
    if fn == "getsize":
        monkeypatch.setattr(os.path, "getsize", fake)
    else:
        monkeypatch.setattr(os, fn, fake)


# ---------------- probe_file: errno explícito e componente que falhou ----------------
def test_probe_distinguishes_missing_permission_loop_and_not_regular(tmp_path, monkeypatch):
    f = tmp_path / "a" / "b.mp4"
    f.parent.mkdir()
    f.write_bytes(b"x" * 10)
    ok = probe_file(f)
    assert ok.ok and ok.size == 10 and ok.status == "ok"
    gone = probe_file(tmp_path / "a" / "nao-existe.mp4")
    assert gone.status == "missing" and gone.errno == "ENOENT" and gone.component == str(tmp_path / "a" / "nao-existe.mp4") and gone.definitive_absence
    nodir = probe_file(tmp_path / "faltando" / "x" / "b.mp4")
    assert nodir.status == "missing" and nodir.component == str(tmp_path / "faltando")           # aponta o PRIMEIRO componente ausente
    notdir = probe_file(f / "dentro.mp4")                                                       # um arquivo usado como diretório
    assert notdir.status == "missing" and notdir.errno == "ENOTDIR"
    d = probe_file(tmp_path / "a")
    assert d.status == "not_regular" and d.definitive_absence
    (tmp_path / "l1").symlink_to(tmp_path / "l2")
    (tmp_path / "l2").symlink_to(tmp_path / "l1")
    loop = probe_file(tmp_path / "l1")
    assert loop.status == "symlink_loop" and loop.errno == "ELOOP" and not loop.definitive_absence
    deny(monkeypatch, f)
    perm = probe_file(f)
    assert perm.status == "permission" and perm.errno == "EACCES" and not perm.definitive_absence and not perm.ok
    assert probe_file("/tmp/a\x00b.mp4").status == "invalid_path"


# ---------------- reprodução do defeito: permissão NÃO pode virar "arquivo sumiu" ----------------
def test_permission_error_is_io_error_and_does_not_poison_catalog_or_central(rig, monkeypatch):
    rig.start()
    _, f2, data2 = rig.files["A_02"]
    deny(monkeypatch, f2)
    r = rig.client.get(url_for(rig, "A_02"), headers={"Range": "bytes=0-99"})
    assert r.status_code == 503 and r.headers.get("retry-after")                   # transitório; NÃO 404 "não encontrado"
    assert "io_error" in last_relay_error(rig)
    assert row(rig, "A_02")["file_ok"] == 1 and row(rig, "A_02")["sync_state"] == "synced"   # catálogo local intacto (sem rebaixar)
    assert central_state(rig, "A_02") == "ok"                                           # central NÃO marca missing
    monkeypatch.undo()                                                                  # permissão volta: o MESMO replay toca, sem intervenção
    r = rig.client.get(url_for(rig, "A_02"), headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data2[:100]


def test_definitive_absence_is_still_not_found_demotes_and_recovers_when_file_returns(rig):
    rig.start()
    _, f2, data2 = rig.files["A_02"]
    keep = f2.with_suffix(".bak")
    f2.rename(keep)
    r = rig.client.get(url_for(rig, "A_02"), headers={"Range": "bytes=0-9"})
    assert r.status_code == 404                                                         # ausência real continua sendo not_found
    assert "not_found" in last_relay_error(rig)
    assert row(rig, "A_02")["file_ok"] == 0 and row(rig, "A_02")["sync_state"] == "pending"
    assert central_state(rig, "A_02") == "missing"
    keep.rename(f2)                                                                     # o arquivo volta
    rig.agent.link.tick()                                                               # reconcilia + sincroniza (fileAvailable=True)
    assert row(rig, "A_02")["file_ok"] == 1 and central_state(rig, "A_02") == "ok"
    r = rig.client.get(url_for(rig, "A_02"), headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and r.content == data2[:100]


def test_relay_heals_file_ok_when_a_flagged_file_is_served_again(rig):
    """Se `file_ok=0` ficou de uma falha antiga mas o arquivo está bom, a própria entrega bem-sucedida corrige o catálogo."""
    rig.start()
    lid = rig.files["A_02"][0]
    rig.agent.store.set_file_ok(lid, False)
    rig.agent.store.mark(lid, "synced")
    res = rig.relay._resolve_ex("video", lid)
    assert res.path is not None and res.code is None
    assert row(rig, "A_02")["file_ok"] == 1 and row(rig, "A_02")["sync_state"] == "pending"   # a central será avisada (fileAvailable=True)


def test_failure_in_one_court_does_not_affect_the_other_or_kill_workers(rig, monkeypatch):
    rig.start(2)
    _, f1, d1 = rig.files["A_01"]
    _, f2, _ = rig.files["A_02"]
    deny(monkeypatch, f2)
    for _ in range(4):                                                                  # alterna Q2 (falha) e Q1 (ok)
        assert rig.client.get(url_for(rig, "A_02"), headers={"Range": "bytes=0-9"}).status_code == 503
        r = rig.client.get(url_for(rig, "A_01"), headers={"Range": "bytes=0-99"})
        assert r.status_code == 206 and r.content == d1[:100]
    assert row(rig, "A_01")["file_ok"] == 1 and central_state(rig, "A_01") == "ok"
    assert all(t.is_alive() for t in rig.threads)                                       # nenhum worker morreu


def test_unexpected_oserror_while_reading_is_reported_not_raised(rig, monkeypatch):
    srv = rig.relay
    _, f2, _ = rig.files["A_02"]
    deny(monkeypatch, f2, fn="getsize", err=errno.EIO)                                  # arquivo passa no stat, falha na leitura
    sent = []
    monkeypatch.setattr(srv.client, "call", lambda m, p, body=None, timeout=30: ({"requestId": "r1", "kind": "video", "ref": rig.files["A_02"][0], "start": 0, "end": 9}
                                                                                if p.startswith("/relay/next") else sent.append((p, body))))
    assert srv.serve_one(0) is True                                                     # não propaga OSError
    assert sent and sent[0][0] == "/relay/r1/error" and sent[0][1]["code"] == "io_error"
    assert row(rig, "A_02")["file_ok"] == 1


# ---------------- fronteira de raízes, symlinks (allowlist intacta) ----------------
def _srv(rig, roots):
    from replay_agent.relay import RelayServer
    return RelayServer(rig.relay.client, rig.agent.store, roots)


def test_symlinked_root_and_symlinked_path_are_served_but_escape_and_prefix_tricks_are_refused(rig, tmp_path):
    lid, f2, _ = rig.files["A_02"]
    storage = tmp_path / "storage" / "quadra02" / "replays"
    storage.mkdir(parents=True)
    real = storage / f2.name
    real.write_bytes(f2.read_bytes())
    link_root = tmp_path / "srv" / "quadra02" / "replays"
    link_root.parent.mkdir(parents=True)
    link_root.symlink_to(storage)                                                       # igual à arena: /srv/…/replays -> /srv/…-storage/…/replays
    srv = _srv(rig, [link_root])                                                        # a raiz é resolvida (symlink) no construtor
    for stored in (real, link_root / f2.name):                                          # caminho gravado pelo destino OU pelo symlink
        rig.agent.store._x("UPDATE replays SET path=? WHERE local_id=?", (str(stored), lid))
        res = srv._resolve_ex("video", lid)
        assert res.path == real.resolve() and res.code is None
    # symlink DENTRO da raiz apontando para FORA: recusado, sem rebaixar (o arquivo existe)
    outside = tmp_path / "fora.mp4"
    outside.write_bytes(b"segredo" * 20)
    (storage / "escape.mp4").symlink_to(outside)
    rig.agent.store._x("UPDATE replays SET path=?, file_ok=1 WHERE local_id=?", (str(storage / "escape.mp4"), lid))
    res = srv._resolve_ex("video", lid)
    assert res.path is None and res.code == "not_found" and res.reason == "outside_allowed_roots" and row(rig, "A_02")["file_ok"] == 1
    # diretório irmão com o mesmo prefixo ("replays-evil") não é a raiz
    evil = tmp_path / "storage" / "quadra02" / "replays-evil"
    evil.mkdir()
    (evil / "x.mp4").write_bytes(b"y" * 50)
    rig.agent.store._x("UPDATE replays SET path=? WHERE local_id=?", (str(evil / "x.mp4"), lid))
    assert srv._resolve_ex("video", lid).reason == "outside_allowed_roots"
    # o caminho apontando para a própria raiz (diretório) não é arquivo
    rig.agent.store._x("UPDATE replays SET path=? WHERE local_id=?", (str(storage), lid))
    assert srv._resolve_ex("video", lid).reason == "file_missing"


# ---------------- observabilidade sanitizada ----------------
def test_failure_log_has_errno_stage_and_ref_but_no_secrets(rig, monkeypatch, caplog):
    lid, f2, _ = rig.files["A_02"]
    deny(monkeypatch, f2)
    with caplog.at_level(logging.WARNING, logger="replay_agent.relay"):
        res = rig.relay._resolve_ex("video", lid)
    assert res.code == "io_error" and res.reason == "permission"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert f"ref={lid}" in msg and "errno=EACCES" in msg and "reason=permission" in msg and f"component={f2}" in msg and "uid=" in msg
    for secret in (rig.world.A.token, "Authorization", "password", "rtsp://"):
        assert secret not in msg


# ---------------- reconcile_files não envenena por erro de permissão ----------------
def test_reconcile_only_flips_on_definitive_absence(rig, monkeypatch):
    store = rig.agent.store
    _, f1, _ = rig.files["A_01"]
    _, f2, _ = rig.files["A_02"]
    deny(monkeypatch, f2)
    assert store.reconcile_files() == 0 and row(rig, "A_02")["file_ok"] == 1 and store.last_reconcile_unknown == 1   # sem rebaixar
    monkeypatch.undo()
    f1.rename(f1.with_suffix(".bak"))
    assert store.reconcile_files() == 1 and row(rig, "A_01")["file_ok"] == 0
    f1.with_suffix(".bak").rename(f1)
    assert store.reconcile_files() == 1 and row(rig, "A_01")["file_ok"] == 1


# ---------------- erro REAL de permissão (usuário sem privilégio), sem simulação ----------------
@pytest.mark.skipif(os.geteuid() != 0, reason="precisa ser root para trocar de usuário no processo filho")
def test_real_eacces_for_unprivileged_user_is_permission_not_missing():
    import shutil
    import tempfile
    base = Path(tempfile.mkdtemp(prefix="relay-eacces-"))
    os.chmod(base, 0o755)                                                               # o tmp do pytest é 0700: aqui a árvore é atravessável
    d = base / "quadra02" / "replays"
    d.mkdir(parents=True)
    f = d / "replay.mp4"
    f.write_bytes(b"x" * 100)
    os.chmod(d.parent, 0o000)                                                           # root vê o arquivo; o serviço não atravessa o diretório
    try:
        assert probe_file(f).ok                                                         # como root: parece íntegro (o enigma da Issue #15)
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.setgid(65534)
            os.setuid(65534)
            p = probe_file(f)
            os.write(w, f"{p.status}|{p.errno}|{p.component}".encode())
            os._exit(0)
        os.waitpid(pid, 0)
        status, err, comp = os.read(r, 200).decode().split("|")
        assert (status, err) == ("permission", "EACCES") and comp == str(d)             # primeiro componente que o serviço não alcança (entrar em quadra02 é negado)
    finally:
        os.chmod(d.parent, 0o755)
        shutil.rmtree(base, ignore_errors=True)

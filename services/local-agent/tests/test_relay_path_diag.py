"""O script de diagnóstico (somente leitura) da Issue #15 funciona e não escreve nada."""
import hashlib
import os
import runpy
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "homologacao" / "relay_path_diag.py"


def run(*args):
    r = subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_reports_ok_file_missing_file_and_name_mismatch_without_modifying_anything(tmp_path):
    d = tmp_path / "quadra02" / "replays"
    d.mkdir(parents=True)
    f = d / "replay-1.mp4"
    f.write_bytes(b"x" * 64)
    cat = tmp_path / "cat.sqlite"
    c = sqlite3.connect(cat)
    c.execute("create table replays(local_id text, path text, file_ok int, sync_state text, deleted int)")
    c.execute("insert into replays values('REF-OK-0001',?,1,'synced',0)", (str(f),))
    c.execute("insert into replays values('REF-GONE-001',?,0,'pending',0)", (str(d / "sumiu.mp4"),))
    c.execute("insert into replays values('REF-CASE-001',?,1,'synced',0)", (str(d / "REPLAY-1.MP4"),))
    c.commit(); c.close()
    before = hashlib.sha256(cat.read_bytes()).hexdigest(), hashlib.sha256(f.read_bytes()).hexdigest()
    ok = run("--catalog", str(cat), "--ref", "REF-OK-0001", "--root", str(d.parent), "--pid", str(os.getpid()))
    assert "status=ok" in ok and "nome exato presente: True" in ok and "dentro da raiz: True" in ok and "Uid:" in ok
    gone = run("--catalog", str(cat), "--ref", "REF-GONE-001")
    assert "status=missing" in gone and "errno=ENOENT" in gone and "nome exato presente: False" in gone and f"componente={d / 'sumiu.mp4'}" in gone
    case = run("--catalog", str(cat), "--ref", "REF-CASE-001")
    assert "nomes equivalentes" in case and "replay-1.mp4" in case                      # aponta o nome real que difere só por maiúsculas
    r = subprocess.run([sys.executable, str(SCRIPT), "--catalog", str(cat), "--ref", "REF-NAO-EXISTE-1"], capture_output=True, text=True)
    assert r.returncode == 1 and "NÃO ENCONTRADA" in r.stdout                           # ref desconhecida: sai com 1 e diz isso
    assert (hashlib.sha256(cat.read_bytes()).hexdigest(), hashlib.sha256(f.read_bytes()).hexdigest()) == before   # nada foi escrito

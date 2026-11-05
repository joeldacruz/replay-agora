"""Configuração por quadra, adaptadores para o legado e inventário somente leitura."""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from replay_agent.adapters import ExternalCommandRenderer, LogTailTrigger, Unrecoverable
from replay_agent.config import ConfigError, load_config, parse, redact_text, redact_url
from replay_agent.render import SimRenderer

ROOT = Path(__file__).resolve().parents[3]


def base(**over):
    cfg = {"arena_tag": "arena01", "data_dir": "/tmp/x", "serial": [{"name": "b1", "port": "/dev/ttyUSB0"}],
           "courts": [{"key": "REPLAY_01", "label": "Quadra 01", "camera": {"url": "rtsp://u:p@10.0.0.11/s1"}, "trigger": {"buttons": ["REPLAY_01"]}},
                      {"key": "REPLAY_02", "label": "Quadra 02", "camera": {"url": "rtsp://u:p@10.0.0.12/s1", "segment_s": 1}, "trigger": {"buttons": ["REPLAY_02", "REPLAY_02_NT"]},
                       "clip": {"pre_s": 10, "post_s": 4, "min_pre_s": 3, "min_post_s": 1}}]}
    cfg.update(over)
    return cfg


def test_each_court_has_independent_settings_and_defaults():
    c = parse(base())
    a, b = c.court("REPLAY_01"), c.court("REPLAY_02")
    assert (a.clip.pre_s, a.clip.post_s, a.camera.segment_s) == (15, 5, 2.0) and (b.clip.pre_s, b.clip.post_s, b.camera.segment_s) == (10, 4, 1)
    assert c.button_map() == {"REPLAY_01": "REPLAY_01", "REPLAY_02": "REPLAY_02", "REPLAY_02_NT": "REPLAY_02"}   # várias botoeiras por quadra


@pytest.mark.parametrize("mut,frag", [
    (lambda d: d["courts"][0]["camera"].update(url="http://x"), "camera.url"),
    (lambda d: d["courts"][0]["camera"].update(buffer_s=5), "buffer_s"),
    (lambda d: d["courts"][0]["trigger"].update(buttons=["REPLAY_02"]), "duas quadras"),
    (lambda d: d["courts"][0].update(key="bad key"), "key inválida"),
    (lambda d: d["courts"][1].update(key="REPLAY_01"), "repetidas"),
    (lambda d: d["courts"][0].setdefault("clip", {}).update(min_pre_s=99), "janelas inválidas"),
    (lambda d: d["courts"][0].setdefault("clip", {}).update(renderer="external_command"), "{out}"),
    (lambda d: d["courts"][0]["camera"].update(surprise=1), "campo desconhecido"),
    (lambda d: d["serial"][0].update(port="relative/tty"), "/dev/"),
    (lambda d: d.update(resources={"hwaccel": "magic"}), "hwaccel"),
    (lambda d: d.update(resources={"max_ffmpeg": 0}), "max_ffmpeg"),
])
def test_invalid_configs_are_rejected_with_clear_messages(mut, frag):
    d = base()
    mut(d)
    with pytest.raises(ConfigError) as e:
        parse(d)
    assert frag in str(e.value)


def test_config_file_must_be_private_and_secrets_are_redacted(tmp_path):
    p = tmp_path / "agent.json"
    p.write_text(json.dumps(base()))
    p.chmod(0o644)
    with pytest.raises(ConfigError, match="0600"):
        load_config(p)
    p.chmod(0o600)
    cfg = load_config(p)
    dump = json.dumps(cfg.public_summary())
    assert "u:p@" not in dump and "***@10.0.0.11" in dump
    assert redact_url("rtsp://admin:S3nh4@192.168.0.5:554/h264?x=1") == "rtsp://***@192.168.0.5:554/h264"
    assert "S3nh4" not in redact_text("erro abrindo rtsp://admin:S3nh4@192.168.0.5:554/h264 timeout")


def test_external_command_renderer_runs_without_shell_and_validates_output(tmp_path):
    fake = tmp_path / "fake_replay.py"
    fake.write_text("import sys, subprocess\nout=sys.argv[-1]\nsubprocess.run(['ffmpeg','-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=10:duration=2','-pix_fmt','yuv420p',out],check=True)\n"
                    "print('court',sys.argv[1],'t0',sys.argv[2],'pre',sys.argv[3],'post',sys.argv[4])\n")
    r = ExternalCommandRenderer([sys.executable, str(fake), "{court}", "{t0}", "{pre}", "{post}", "{out}"], timeout_s=60)
    out = tmp_path / "o.mp4"
    res = r.render("REPLAY_01", out, 15, 5, datetime(2026, 11, 5, 12, 0, 0))
    assert res.duration == pytest.approx(2.0, abs=0.3) and out.is_file()
    bad = ExternalCommandRenderer([sys.executable, "-c", "pass", "{out}"])
    with pytest.raises(Unrecoverable):
        bad.render("REPLAY_01", tmp_path / "none.mp4", 1, 1, None)
    boom = ExternalCommandRenderer([sys.executable, "-c", "import sys; sys.exit(3)", "{out}"])
    with pytest.raises(RuntimeError, match="falhou"):
        boom.render("REPLAY_01", tmp_path / "x.mp4", 1, 1, None)
    with pytest.raises(ValueError):
        ExternalCommandRenderer(["echo"])


def test_log_tail_shadow_mode_emits_only_new_complete_lines_and_survives_rotation(tmp_path):
    log = tmp_path / "leitor.log"
    log.write_text("REPLAY_01 antigo (histórico não reproduz)\n")
    got = []
    t = LogTailTrigger("shadow", r"(REPLAY_[0-9A-Z_]+)", log_file=str(log), poll_s=0.02)
    t.start(lambda m, src, ts: got.append((m, src)))
    time.sleep(0.15)
    with open(log, "a") as f:
        f.write("2026 evento REPLAY_02 recebido\nruído sem acionamento\nREPLAY_0")      # última linha incompleta
    time.sleep(0.2)
    assert got == [("REPLAY_02", "log:shadow")]
    with open(log, "a") as f:
        f.write("1 ok\n")
    time.sleep(0.2)
    assert got[-1][0] == "REPLAY_01"
    log.write_text("REPLAY_02\n")                                                    # rotação (arquivo menor)
    time.sleep(0.2)
    assert got[-1][0] == "REPLAY_02" and t.diagnostics()["matches"] == 3
    t.stop()


def test_inventory_script_is_static_readonly_and_masks_secrets(tmp_path):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    marker = tmp_path / "EXECUTED"
    (legacy / "buffer.py").write_text(
        f"import subprocess, argparse\nopen({str(marker)!r}, 'w').write('x')\nURL = 'rtsp://admin:S3nh4@192.168.0.5/stream'\nPASSWORD = 'senha=abc123'\n"
        "def grab(court, seconds):\n    '''grava'''\n    subprocess.Popen(['ffmpeg'])\nclass Buf:\n    def push(self): pass\n"
        "p = argparse.ArgumentParser(); p.add_argument('--court')\nif __name__ == '__main__': pass\n")
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "inventory_legacy.py"), str(legacy / "buffer.py"), str(legacy / "nope.py")],
                         capture_output=True, text=True)
    assert out.returncode == 0 and not marker.exists()                                # nunca executa o arquivo analisado
    rep = json.loads(out.stdout)
    f = rep["files"][0]
    assert rep["readOnly"] and f["functions"][0]["name"] == "grab" and f["classes"][0]["methods"] == ["push"] and "--court" in f["cli_arguments"]
    assert "Popen" in f["notable_calls"] and f["has_main_guard"]
    assert "S3nh4" not in out.stdout and "abc123" not in out.stdout and "rtsp://***@192.168.0.5/stream" in out.stdout
    assert rep["files"][1]["error"] == "não encontrado"
    refuse = subprocess.run([sys.executable, str(ROOT / "scripts" / "inventory_legacy.py"), str(legacy / "buffer.py"), "--out", str(legacy / "r.json")],
                            capture_output=True, text=True)
    assert refuse.returncode == 2 and not (legacy / "r.json").exists()                # não escreve junto do código analisado

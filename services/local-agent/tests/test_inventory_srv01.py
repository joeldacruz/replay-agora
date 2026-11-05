"""scripts/inventario_srv01.py: somente leitura, sem segredos, sem abrir portas seriais, com degradação graciosa."""
import builtins
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "inventario_srv01.py"
SECRETS = ["S3nh4Cam", "S3cretPwd", "abc123tokenXYZ", "envTokenValue123", "tok_ABCDEFGHIJKLMNOP1234", "hunterDois"]

pytestmark = pytest.mark.timeout(120)


def load():
    spec = importlib.util.spec_from_file_location("inv", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def build_legacy(base: Path) -> Path:
    root = base / "srv" / "replayagora"
    (root / "botoeira").mkdir(parents=True)
    (root / "quadra01" / "app").mkdir(parents=True)
    (root / "quadra01" / "buffer").mkdir(parents=True)
    (root / "botoeira" / "leitor.py").write_text(
        "import serial, re, time\nPASSWORD = 'S3cretPwd!'\nser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)\nrx = re.compile(r'(REPLAY_0[12])')\n"
        "while True:\n    line = ser.readline()\n    time.sleep(0.05)\n")
    (root / "quadra01" / "app" / "buffer.py").write_text(
        "import subprocess\nCAM = 'rtsp://admin:S3nh4Cam@10.0.0.11:554/Streaming/Channels/101?token=abc123tokenXYZ'\n"
        "def run():\n    subprocess.Popen(['ffmpeg','-rtsp_transport','tcp','-i',CAM,'-c','copy','-f','segment','-segment_time','2','-strftime','1','/srv/replayagora/quadra01/buffer/%s.ts'])\n")
    (root / "quadra01" / "app" / "replay.py").write_text(
        "import argparse\np = argparse.ArgumentParser()\np.add_argument('--t0')\np.add_argument('--pre')\nif __name__ == '__main__':\n    p.parse_args()\n")
    (root / "quadra01" / "config.json").write_text(json.dumps({"camera": "rtsp://admin:S3nh4Cam@10.0.0.11:554/s", "password": "S3cretPwd!", "fps": 25,
                                                                 "baud": 9600, "api_token": "tok_ABCDEFGHIJKLMNOP1234"}, indent=1))
    (root / "quadra01" / ".env").write_text("API_TOKEN=envTokenValue123\nQUADRA=1\n")
    now = int(time.time())
    for i in range(8):
        p = root / "quadra01" / "buffer" / f"seg_{now - 20 + 2 * i}.ts"
        p.write_bytes(b"\0" * 5000)
        os.utime(p, (now - 20 + 2 * i, now - 20 + 2 * i))
    return root


def snapshot(root: Path):
    out = {}
    for p in sorted(root.rglob("*")):
        st = p.lstat()
        out[str(p)] = (st.st_size, st.st_mtime_ns, st.st_mode, hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
    return out


def run_script(*args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=110)


@pytest.fixture
def fake_ffmpeg_process():
    p = subprocess.Popen(["bash", "-c", 'exec -a "ffmpeg -rtsp_transport tcp -i rtsp://admin:S3nh4Cam@10.0.0.11:554/s1 -c copy -f segment -segment_time 2 /tmp/x/%s.ts" sleep 60'])
    time.sleep(0.4)
    yield p
    p.kill()
    p.wait()


def test_full_report_is_complete_secret_free_and_leaves_the_legacy_tree_untouched(tmp_path, fake_ffmpeg_process):
    legacy = build_legacy(tmp_path)
    before = snapshot(tmp_path / "srv")
    out = tmp_path / "out"
    r = run_script("--out", str(out), "--legacy-root", str(legacy), "--sample-seconds", "0", "--quick")
    assert r.returncode == 0, r.stdout + r.stderr
    assert snapshot(tmp_path / "srv") == before                                   # NADA mudou nas pastas analisadas
    files = sorted(p.name for p in out.iterdir())
    assert files == ["LEIA-ME.txt", "SHA256SUMS", "inventario.json", "inventario.txt"]
    assert all((out / f).stat().st_mode & 0o777 == 0o600 for f in files) and out.stat().st_mode & 0o777 == 0o700
    blob = "".join((out / f).read_text() for f in files)
    for s in SECRETS:
        assert s not in blob, s                                                    # nenhum segredo em NENHUM arquivo
    assert "Varredura de segredos" in r.stdout and "OK" in r.stdout
    sums = dict(reversed(l.split("  ")) for l in (out / "SHA256SUMS").read_text().splitlines())
    assert all(hashlib.sha256((out / n).read_bytes()).hexdigest() == h for n, h in sums.items() if n != "SHA256SUMS")
    j = json.loads((out / "inventario.json").read_text())
    # arquivos legados identificados, com hash e análise estática
    arq = j["legado"]["arquivos"]
    assert all(len(arq[n]) == 1 for n in ("leitor.py", "buffer.py", "replay.py")) and j["legado"]["naoEncontrados"] == []
    assert arq["leitor.py"][0]["sha256"] == hashlib.sha256((legacy / "botoeira" / "leitor.py").read_bytes()).hexdigest()
    assert arq["leitor.py"][0]["chamadasSerial"] == [{"porta": "/dev/ttyUSB0", "baud": 9600, "timeout": 1}] and arq["leitor.py"][0]["usaPySerial"]
    assert any("REPLAY_" in x for x in arq["leitor.py"][0]["regexes"])
    assert "--t0" in arq["replay.py"][0]["argumentosCLI"] and arq["replay.py"][0]["temMainGuard"]
    ffc = arq["buffer.py"][0]["comandosFFmpeg"]
    assert ffc and "-segment_time 2" in ffc[0] and "S3nh4Cam" not in ffc[0] and "rtsp://***@10.0.0.11:554" in ffc[0]
    # câmeras (sem credencial/token), parâmetros não sensíveis e só NOMES de chaves
    cams = j["camerasEConfigs"]["camerasConfiguradas"]
    assert len(cams) == 1 and cams[0]["host"] == "10.0.0.11" and cams[0]["porta"] == 554
    assert all("token" not in u and "@" not in u for u in cams[0]["caminhosVistos"])
    pars = {(p["chave"], p["valor"]) for p in j["camerasEConfigs"]["parametrosNaoSensiveis"]}
    assert ("fps", "25") in pars and ("baud", "9600") in pars and not any(k.lower() in ("password", "api_token") for k, _ in pars)
    keys = j["camerasEConfigs"]["chavesPorArquivo(somente_nomes)"]
    assert any("API_TOKEN" in v for v in keys.values())                           # o nome aparece, o valor não
    assert "/dev/ttyUSB0" in "".join(j["camerasEConfigs"]["portasSeriaisCitadasNosArquivos"])
    # armazenamento: pasta de segmentos reconhecida
    st = j["legadoArmazenamento"][0]
    buf = next(b for b in st["possiveisBuffersOuPastasDeReplay"] if b["diretorio"].endswith("buffer"))
    assert buf["padraoDeNome"] == "epoch-segundos" and buf["intervaloMedianoEntreArquivosS"] == 2.0 and buf["arquivosNaPasta"] == 8
    assert st["extensoes"][".ts"] == 8
    # processo FFmpeg em execução: entrada mascarada, parâmetros de segmentação capturados
    ff = [f for f in j["processos"]["ffmpeg"] if f["pid"] == fake_ffmpeg_process.pid]
    assert ff and ff[0]["entradas"] == ["rtsp://10.0.0.11:554/s1"] and ff[0]["opcoes"]["-segment_time"] == "2" and ff[0]["opcoes"]["-c"] == "copy"
    assert {"hardware", "armazenamento", "horario", "rede", "gpu", "software", "servicos", "serial", "resumo", "limitacoes", "comandos"} <= set(j)
    ids = {c["id"] for c in j["resumo"]}
    assert {"python", "ffmpeg", "legado-arquivos", "cameras", "serial", "gpu", "ntp", "cpu", "memoria"} <= ids


def test_refuses_output_inside_analysed_folders_or_non_empty_dir(tmp_path):
    legacy = build_legacy(tmp_path)
    r = run_script("--out", str(legacy / "quadra01" / "relatorio"), "--legacy-root", str(legacy), "--quick")
    assert r.returncode == 2 and not (legacy / "quadra01" / "relatorio").exists() and "RECUSADO" in r.stderr
    r = run_script("--out", str(legacy.parent), "--legacy-root", str(legacy), "--quick")            # --out que CONTÉM a raiz
    assert r.returncode == 2
    existing = tmp_path / "ja_existe"
    existing.mkdir()
    (existing / "x.txt").write_text("não sobrescrever")
    r = run_script("--out", str(existing), "--legacy-root", str(legacy), "--quick")
    assert r.returncode == 2 and (existing / "x.txt").read_text() == "não sobrescrever"
    r = run_script("--legacy-root", str(legacy))                                                    # sem --out nem --stdout
    assert r.returncode != 0


def test_stdout_mode_writes_nothing(tmp_path):
    legacy = build_legacy(tmp_path)
    before = snapshot(tmp_path)
    r = run_script("--stdout", "--legacy-root", str(legacy), "--quick", "--sample-seconds", "0")
    assert r.returncode == 0 and "RESUMO PARA A HOMOLOGAÇÃO" in r.stdout and snapshot(tmp_path) == before
    assert not any(s in r.stdout for s in SECRETS)


def test_command_allowlist_blocks_anything_that_could_change_the_system():
    m = load()
    for bad in (["systemctl", "restart", "replayagora-leitor"], ["systemctl", "stop", "x"], ["systemctl", "start", "x"], ["rm", "-rf", "/tmp/x"],
                ["cat", "/dev/ttyUSB0"], ["stty", "-F", "/dev/ttyUSB0"], ["screen", "/dev/ttyUSB0"], ["kill", "-9", "1"], ["chmod", "777", "/"],
                ["bash", "-c", "echo oi"], ["journalctl", "--vacuum-time=1d", "-u", "x", "-o", "cat"], ["ffmpeg", "-i", "rtsp://cam/x", "-f", "null", "-"],
                ["ffprobe", "rtsp://cam/x"], ["udevadm", "trigger"], ["docker", "rm", "x"], ["curl", "http://x"], ["python3", "-c", "print(1)"], ["ip", "link", "set", "eth0", "down"]):
        with pytest.raises(m.Forbidden):
            m.run(bad)
    with pytest.raises(m.Forbidden):
        m.read_text("/dev/ttyUSB0")                                              # nunca ler dispositivo
    with pytest.raises(m.Forbidden):
        m.read_text("/dev/serial/by-id/usb-1a86")
    assert m.run(["uname", "-r"])[0] == 0                                         # e o que é permitido funciona


def test_never_opens_serial_ports_or_writes_outside_the_report(tmp_path):
    """Roda main() em processo, espiando TODA abertura de arquivo: nenhum /dev/tty*, nenhuma escrita fora de --out."""
    m = load()
    legacy = build_legacy(tmp_path)
    opened, real_open, real_os_open = [], builtins.open, os.open
    out = str(tmp_path / "rel")

    def spy_open(file, mode="r", *a, **k):
        opened.append((str(file), mode))
        return real_open(file, mode, *a, **k)

    def spy_os_open(path, flags, *a, **k):
        opened.append((str(path), "os:%d" % flags))
        return real_os_open(path, flags, *a, **k)
    builtins.open, os.open = spy_open, spy_os_open
    try:
        argv = sys.argv
        sys.argv = ["inv", "--out", out, "--legacy-root", str(legacy), "--quick", "--sample-seconds", "0"]
        try:
            assert m.main() == 0
        finally:
            sys.argv = argv
    finally:
        builtins.open, os.open = real_open, real_os_open
    assert not [p for p, _ in opened if p.startswith(("/dev/tty", "/dev/serial", "/dev/replayagora"))]
    writes = [p for p, md in opened if isinstance(md, str) and any(c in md for c in "wa+x")]
    assert writes and all(p.startswith(out) for p in writes)                       # a única escrita é o relatório
    assert not any("/environ" in p for p, _ in opened)                              # nunca lê o ambiente de processos


def test_redaction_patterns_and_final_secret_scan():
    m = load()
    cases = ["rtsp://admin:S3nh4Cam@10.0.0.11/s?token=abc123tokenXYZ", "Authorization: Bearer abcdefghijklmnop1234", "password=hunterDois", '"api_token": "tok_ABCDEFGHIJKLMNOP1234"',
             "ffmpeg -headers 'Authorization: Basic Zm9vOmJhcg==' -i x", "prog --password hunterDois --x 1", "SECRET_KEY: hunterDois", "rag_AAAABBBBCCCCDDDD1111"]
    for c in cases:
        out = m.redact(c)
        assert not any(s in out for s in SECRETS + ["abcdefghijklmnop1234", "Zm9vOmJhcg==", "rag_AAAABBBB"]), (c, out)
        assert not m.secret_scan(out), (c, out)
    assert m.secret_scan("rtsp://admin:segredo@host/x") and m.secret_scan("password=abcdef123") and m.secret_scan("-----BEGIN RSA PRIVATE KEY-----")
    h = "a" * 64
    assert h in m.redact("sha256 " + h)                                              # hashes de arquivo não são mascarados
    assert m.mask_url("rtsp://u:p@cam.local:8554/live/ch1?x=1")["url"] == "rtsp://cam.local:8554/live/ch1"
    assert m.mask_url("rtsp://cam/a/token123/b")["url"].endswith("/***/b")


def test_degrades_gracefully_when_tools_are_missing(tmp_path):
    legacy = build_legacy(tmp_path)
    out = tmp_path / "o"
    r = run_script("--out", str(out), "--legacy-root", str(legacy), "--quick", "--sample-seconds", "0",
                   env={"INV_FORCE_MISSING": "ffmpeg,ffprobe,systemctl,ip,ss,lscpu,journalctl,udevadm,timedatectl,df,lsblk,ps,dpkg-query"})
    assert r.returncode == 0, r.stderr
    j = json.loads((out / "inventario.json").read_text())
    assert j["software"]["ffmpeg"]["versao"] == "indisponível"
    assert any(c["id"] == "ffmpeg" and c["status"] == "CRÍTICO" for c in j["resumo"])
    assert any(c.get("status") == "ausente" for c in j["comandos"])
    assert all(len(j["legado"]["arquivos"][n]) == 1 for n in ("leitor.py", "buffer.py", "replay.py"))   # a parte de arquivos não depende de ferramentas


def test_missing_legacy_root_is_reported_not_fatal(tmp_path):
    r = run_script("--stdout", "--legacy-root", str(tmp_path / "nao_existe"), "--quick", "--sample-seconds", "0")
    assert r.returncode == 0 and "Arquivos legados localizados" in r.stdout and "ausentes: ['leitor.py', 'buffer.py', 'replay.py']" in r.stdout


def test_script_is_python38_compatible_single_file():
    src = SCRIPT.read_text()
    import ast
    tree = ast.parse(src, feature_version=(3, 8))                                     # sem match/walrus/| em tipos
    assert not any(isinstance(n, ast.Match) for n in ast.walk(tree)) if hasattr(ast, "Match") else True
    imports = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | \
              {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    stdlib = set(sys.stdlib_module_names) if hasattr(sys, "stdlib_module_names") else imports
    assert imports <= stdlib                                                            # somente biblioteca padrão
    assert "shell=True" not in src and "os.system" not in src and "rmtree" not in src and "os.remove" not in src and "os.unlink" not in src

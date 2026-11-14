#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""INVENTÁRIO SOMENTE LEITURA do replayagora-srv01 (execução MANUAL pelo responsável).

Objetivo: levantar, SEM alterar nada, o que existe no servidor para planejar a integração do agente novo com o motor legado
(leitor.py, buffer.py, replay.py): serviços, processos FFmpeg, portas seriais, câmeras configuradas, armazenamento, versões,
CPU/memória/GPU/rede.

GARANTIAS (verificáveis lendo este arquivo — um único arquivo, só biblioteca padrão, Python >= 3.8):
  1. SOMENTE LEITURA. Todo comando externo passa por `run()`, que só executa programas de uma LISTA FIXA com argumentos
     permitidos (ALLOW). Nada de shell, nada de escrita, nenhum `systemctl start/stop/restart`, nenhum `kill`, nenhum `chmod`.
     A única escrita é o relatório, em um diretório NOVO que você indica (--out) e que NÃO pode ficar dentro das pastas analisadas.
  2. NÃO ABRE PORTAS SERIAIS. Abrir /dev/ttyUSB*/ttyACM* reinicia o Arduino (DTR) e pode interferir no leitor.py. O script só usa
     sysfs/udev (`udevadm info`) e `/proc/<pid>/fd` (readlink) para saber quem usa a porta. `read_text()` recusa qualquer /dev/*.
  3. NÃO COLETA SEGREDOS. Nunca lê `/proc/<pid>/environ`. Todo texto livre passa por `redact()` (URLs com usuário:senha,
     chave=valor sensível, Authorization/Bearer, tokens longos). Arquivos de configuração: só NOMES de chaves e valores de uma
     lista curta de parâmetros não sensíveis (baud, fps, segmentos...). Ao final, uma VARREDURA de segredos reescreve e sinaliza
     qualquer coisa suspeita que tenha escapado.
  4. NÃO CONTATA CÂMERAS. As câmeras são identificadas só pelo que está configurado/em uso (host:porta); não há ping/ffprobe/RTSP.
  5. NÃO EXECUTA O CÓDIGO LEGADO. Os .py são analisados estaticamente (ast), nunca importados.
  6. Sem rede por padrão. (--check-https URL e --probe-gpu são opcionais e explícitos.)

Uso:   python3 inventario_srv01.py --out ~/inventario-srv01
       python3 inventario_srv01.py --out ~/inventario-srv01 --legacy-root /srv/replayagora --sample-seconds 10
       sudo python3 inventario_srv01.py --out /root/inventario-srv01     # (opcional) coleta mais (dmesg, processos de outros usuários)
Saída: LEIA-ME.txt, inventario.txt (humano), inventario.json (máquina), SHA256SUMS.  Veja docs/INVENTARIO-SRV01.md.
"""
import argparse
import ast
import collections
import datetime
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time

VERSION = "1.0.0"
SECRET_KEYS = r"(?:pass(?:word|wd)?|senha|secret|token|api[_-]?key|apikey|auth(?:orization)?|credential|bearer|private[_-]?key|cookie|session[_-]?id|pwd)"
SAFE_PARAM_KEYS = re.compile(r"(?i)^(baud(?:rate)?|port|fps|framerate|width|height|resolution|seconds|segment(?:_?time|_?seconds)?|duration|pre(?:_?seconds)?|post(?:_?seconds)?|"
                             r"cooldown|debounce|buffer(?:_?seconds|_?size)?|codec|bitrate|transport|device|quadra|court|channel|fps_?target|timeout|retries|log_?level)$")
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages", ".cache"}
VIDEO_EXT = {".ts", ".mp4", ".mkv", ".m4s", ".avi", ".mov", ".flv", ".h264", ".h265"}
TEXT_EXT = {".py", ".json", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".env", ".service", ".sh", ".txt", ".toml", ".md", ".rules"}
MAX_TEXT = 1_500_000


# ------------------------------------------------------------------------------------------------ redação de segredos
_URL_CREDS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*://)([^/\s:@'\"<>]+)(?::([^/\s@'\"<>]*))?@")
_URL_QUERY = re.compile(r"(?i)\b((?:rtsps?|rtmps?|https?|http)://[^\s'\"<>?#]+)\?[^\s'\"<>]+")
_KV = re.compile(r"(?i)((?:[\w\-]*" + SECRET_KEYS + r"[\w\-]*))(\"?\s*[:=]\s*\"?|'?\s*[:=]\s*'?)([^\s\"',;}{\)]+)")
_FLAG = re.compile(r"(?i)(\s--?[\w\-]*" + SECRET_KEYS + r"[\w\-]*)(\s+)(?!-)([^\s]+)")
_AUTH = re.compile(r"(?i)(authorization\s*[:=]\s*)(bearer\s+|basic\s+|token\s+)?[A-Za-z0-9._~+/=\-]{6,}")
_BEARER = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{12,}")
_RAG = re.compile(r"\brag_[A-Za-z0-9_\-]{8,}")
_PRIVKEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)
_LONGTOK = re.compile(r"(?<![A-Za-z0-9/_\.\-])([A-Za-z0-9+/_\-]{40,})(?![A-Za-z0-9/_\.\-])")
_FFHDR = re.compile(r"(?i)(-headers\s+)(\"[^\"]*\"|'[^']*'|\S+)")
_FFUSER = re.compile(r"(?i)(-(?:rtsp_)?(?:user|username|password|pass|auth_key)\s+)\S+")


def redact(text):
    if not text:
        return text
    t = _PRIVKEY.sub("<chave-privada-removida>", text)
    t = _FFHDR.sub(r"\1<cabecalhos-removidos>", t)
    t = _FFUSER.sub(r"\1***", t)
    t = _URL_CREDS.sub(lambda m: m.group(1) + "***@", t)
    t = _URL_QUERY.sub(r"\1?***", t)
    t = _AUTH.sub(r"\1***", t)
    t = _BEARER.sub(r"\1 ***", t)
    t = _RAG.sub("rag_***", t)
    t = _FLAG.sub(lambda m: m.group(1) + m.group(2) + "***", t)
    t = _KV.sub(lambda m: m.group(1) + m.group(2) + "***", t)
    t = _LONGTOK.sub(lambda m: "<token-longo>" if not re.fullmatch(r"[0-9a-f]{40,}", m.group(1)) or len(m.group(1)) != 64 else m.group(1), t)
    return t


def mask_url(u):
    """Devolve só esquema://host:porta/caminho (sem usuário, senha, query). Usado para listar câmeras."""
    m = re.match(r"(?i)^([a-z][a-z0-9+.\-]*)://(?:[^/@\s]*@)?([^/\s:?#]+)(?::(\d+))?(/[^?#\s'\"]*)?", u)
    if not m:
        return None
    scheme, host, port, path = m.group(1).lower(), m.group(2), m.group(3), m.group(4) or ""
    path = re.sub(r"(?i)/[^/]*" + SECRET_KEYS + r"[^/]*", "/***", path)
    return {"url": "%s://%s%s%s" % (scheme, host, (":" + port) if port else "", path), "scheme": scheme, "host": host,
            "port": int(port) if port else {"rtsp": 554, "rtsps": 322, "http": 80, "https": 443, "rtmp": 1935}.get(scheme)}


# ------------------------------------------------------------------------------------------------ execução controlada
class Forbidden(Exception):
    pass


def _args_in(*allowed):
    sets = [tuple(a) for a in allowed]
    return lambda args: tuple(args) in sets


ALLOW = {
    "uname": _args_in(["-a"], ["-r"], ["-m"]),
    "uptime": _args_in([], ["-p"], ["-s"]),
    "nproc": _args_in([]),
    "lscpu": _args_in([]),
    "free": _args_in(["-m"]),
    "df": _args_in(["-hPT"], ["-iP"]),
    "lsblk": _args_in(["-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,ROTA,MODEL"]),
    "hostnamectl": _args_in(["status"]),
    "timedatectl": _args_in(["status"]),
    "chronyc": _args_in(["tracking"]),
    "ip": _args_in(["-br", "addr"], ["route"], ["-br", "link"]),
    "ss": _args_in(["-tulpn"], ["-tn", "state", "established"]),
    "systemctl": lambda a: (len(a) >= 1 and a[0] in ("list-units", "list-unit-files", "list-timers", "is-active", "is-enabled", "show", "cat")
                            and not any(x in ("start", "stop", "restart", "reload", "enable", "disable", "mask", "kill", "daemon-reload") for x in a)),
    # forma única e fixa: journalctl -u UNIDADE --since "7 days ago" --no-pager -o short-iso  (somente leitura do journal)
    "journalctl": lambda a: (len(a) == 7 and a[0] == "-u" and re.fullmatch(r"[\w@.\-]+", a[1]) is not None and a[2] == "--since" and a[3] == "7 days ago"
                             and a[4] == "--no-pager" and a[5] == "-o" and a[6] == "short-iso"),
    "dmesg": _args_in([]),
    "ps": lambda a: a[:1] == ["-eo"] and len(a) == 2,
    "vmstat": lambda a: len(a) == 2 and a[0].isdigit() and a[1].isdigit(),
    "sensors": _args_in([]),
    "lspci": _args_in([], ["-nn"]),
    "lsusb": _args_in([]),
    "udevadm": lambda a: len(a) == 4 and a[:3] == ["info", "--query=property", "--name"] or (len(a) == 3 and a[:2] == ["info", "--query=property"] and a[2].startswith("--path=/sys/")),
    "dpkg-query": lambda a: a[:1] == ["-W"] and len(a) >= 3 and a[1].startswith("-f="),
    "ffmpeg": _args_in(["-hide_banner", "-version"], ["-hide_banner", "-hwaccels"], ["-hide_banner", "-encoders"]),
    "ffprobe": _args_in(["-hide_banner", "-version"]),
    "python3": _args_in(["-V"], ["--version"]),
    "docker": _args_in(["ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"]),
    "supervisorctl": _args_in(["status"]),
    "crontab": _args_in(["-l"]),
    "vainfo": _args_in([]),                                  # somente com --probe-gpu
    "curl": lambda a: a[:4] == ["-sS", "-I", "--max-time", "8"] and len(a) == 5 and a[4].startswith("https://"),   # só com --check-https
}
# interpretadores de venv descobertos: só `-m pip list --format=freeze` (versões de pacotes; sem rede)
_PIP_OK = lambda argv: len(argv) == 6 and argv[1:] == ["-m", "pip", "list", "--format=freeze", "--disable-pip-version-check"]  # noqa: E731

_CMD_LOG = []


def have(cmd):
    forced = (os.environ.get("INV_FORCE_MISSING") or "").split(",")   # gancho de teste: simula ferramentas ausentes
    return cmd not in forced and shutil.which(cmd) is not None


def run(argv, timeout=10, keep=60000):
    """Executa SOMENTE comandos da lista ALLOW. Sem shell. Retorna (rc, texto_redigido)."""
    name = os.path.basename(argv[0])
    ok = ALLOW.get(name, lambda a: False)(list(argv[1:])) or (name.startswith("python") and _PIP_OK(argv) and "/" in argv[0])
    if not ok:
        raise Forbidden("comando fora da lista permitida: %s" % " ".join(argv[:3]))
    if not have(name) and "/" not in argv[0]:
        _CMD_LOG.append({"cmd": name, "status": "ausente"})
        return 127, ""
    env = {"LANG": "C", "LC_ALL": "C", "PATH": os.environ.get("PATH", "/usr/sbin:/usr/bin:/sbin:/bin")}
    try:
        p = subprocess.run(list(argv), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
        out = p.stdout.decode("utf-8", "replace")
        _CMD_LOG.append({"cmd": " ".join(argv[:4]), "rc": p.returncode})
        return p.returncode, redact(out[:keep])
    except subprocess.TimeoutExpired:
        _CMD_LOG.append({"cmd": " ".join(argv[:4]), "status": "timeout"})
        return 124, ""
    except OSError as e:
        _CMD_LOG.append({"cmd": name, "status": "erro:%s" % type(e).__name__})
        return 126, ""


def utc_str(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def unit_state(unit):
    """active/inactive/failed/... ou 'indisponível' (sem systemd, sem permissão)."""
    rc, out = run(["systemctl", "is-active", unit])
    st = out.strip().splitlines()[0] if out.strip() else ""
    return st if st in ("active", "inactive", "failed", "activating", "deactivating", "unknown") else "indisponível"


def read_text(path, limit=MAX_TEXT):
    """Leitura de arquivo texto. Recusa /dev/* (nunca tocar em portas seriais) e arquivos grandes."""
    rp = os.path.realpath(path)
    if rp.startswith("/dev/") or path.startswith("/dev/"):
        raise Forbidden("leitura de dispositivo recusada: %s" % path)
    try:
        with open(rp, "rb") as f:
            return f.read(limit).decode("utf-8", "replace")
    except OSError:
        return None


def sha256_file(path):
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def stat_info(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    import pwd
    import grp
    try:
        owner = pwd.getpwuid(st.st_uid).pw_name
    except KeyError:
        owner = str(st.st_uid)
    try:
        group = grp.getgrgid(st.st_gid).gr_name
    except KeyError:
        group = str(st.st_gid)
    return {"mode": oct(st.st_mode & 0o7777), "owner": owner, "group": group, "bytes": st.st_size,
            "mtime": utc_str(st.st_mtime)}


# ------------------------------------------------------------------------------------------------ coletores
def sec_meta(a):
    return {"scriptVersion": VERSION, "geradoEmUTC": utc_str(time.time()), "hostname": socket.gethostname(),
            "usuario": os.environ.get("USER") or str(os.geteuid()), "euid": os.geteuid(), "executouComoRoot": os.geteuid() == 0,
            "python": sys.version.split()[0], "opcoes": {"legacyRoots": a.legacy_root, "sampleSeconds": a.sample_seconds, "probeGpu": a.probe_gpu,
                                                         "checkHttps": a.check_https, "quick": a.quick},
            "avisos": ([] if os.geteuid() == 0 else ["Executado sem root: processos/arquivos de outros usuários, dmesg e alguns serviços podem ficar de fora (ver 'limitacoes')."])}


def sec_hw():
    d = {}
    d["os"] = {k: v for k, v in (l.split("=", 1) for l in (read_text("/etc/os-release") or "").splitlines() if "=" in l)
               if k in ("NAME", "VERSION", "VERSION_ID", "PRETTY_NAME")}
    d["os"] = {k: v.strip('"') for k, v in d["os"].items()}
    d["kernel"] = run(["uname", "-a"])[1].strip()
    d["arch"] = platform.machine()
    d["uptime"] = run(["uptime", "-p"])[1].strip()
    d["loadavg"] = (read_text("/proc/loadavg") or "").strip()
    lscpu = run(["lscpu"])[1]
    resumo = {}
    for l in lscpu.splitlines():
        if ":" in l and re.match(r"^(Model name|CPU\(s\)|Thread|Core|Socket|CPU max MHz|CPU MHz|Vendor ID|Architecture)", l):
            k, v = l.split(":", 1)
            resumo[k.strip()] = v.strip()
    d["lscpuResumo"] = resumo
    cpuinfo = read_text("/proc/cpuinfo") or ""
    flags = ""
    m = re.search(r"^flags\s*:\s*(.+)$", cpuinfo, re.M)
    if m:
        flags = m.group(1)
    d["cpuFlagsRelevantes"] = [f for f in ("sse4_2", "avx", "avx2", "avx512f", "aes", "vmx") if f in flags.split()]
    d["nproc"] = os.cpu_count()
    mem = {}
    for l in (read_text("/proc/meminfo") or "").splitlines():
        k, _, v = l.partition(":")
        if k in ("MemTotal", "MemAvailable", "SwapTotal", "SwapFree", "Buffers", "Cached"):
            mem[k] = int(v.split()[0]) // 1024
    d["memoriaMB"] = mem
    d["temperaturas"] = run(["sensors"])[1].strip()[:1500] if have("sensors") else "indisponível (lm-sensors não instalado)"
    return d


def sec_storage(roots, a):
    d = {"df": run(["df", "-hPT"])[1], "inodes": run(["df", "-iP"])[1], "lsblk": run(["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,ROTA,MODEL"])[1]}
    mounts = []
    for l in (read_text("/proc/mounts") or "").splitlines():
        p = l.split()
        if len(p) >= 4 and p[1] in ("/", "/srv", "/var", "/home", "/tmp") or any(len(p) >= 4 and r.startswith(p[1]) and p[1] != "/" for r in roots):
            mounts.append({"dispositivo": p[0], "ponto": p[1], "fs": p[2], "opcoes": p[3]})
    d["mountsRelevantes"] = mounts
    return d


def sec_time():
    return {"timedatectl": run(["timedatectl", "status"])[1], "chrony": run(["chronyc", "tracking"])[1] if have("chronyc") else "indisponível"}


def sec_network(a):
    d = {"enderecos": run(["ip", "-br", "addr"])[1], "rotas": run(["ip", "route"])[1], "interfaces": run(["ip", "-br", "link"])[1]}
    d["dnsServidores"] = re.findall(r"^nameserver\s+(\S+)", read_text("/etc/resolv.conf") or "", re.M)
    rc, ss = run(["ss", "-tulpn"])
    d["portasEscuta"] = [l for l in ss.splitlines() if "LISTEN" in l or l.startswith("udp")][:80]
    rc, est = run(["ss", "-tn", "state", "established"])
    rtsp = collections.Counter()
    for l in est.splitlines():
        cols = l.split()
        if len(cols) >= 4 and cols[-1].rsplit(":", 1)[-1] in ("554", "8554", "322"):
            rtsp[cols[-1]] += 1
    d["sessoesRtspEstabelecidas"] = dict(rtsp)
    if a.check_https:
        d["checkHttps"] = {a.check_https: run(["curl", "-sS", "-I", "--max-time", "8", a.check_https])[1][:400]}
    return d


def sec_gpu(a):
    d = {"dri": {}, "lspci": ""}
    try:
        for n in sorted(os.listdir("/dev/dri")):
            d["dri"][n] = stat_info("/dev/dri/" + n)
    except OSError:
        d["dri"] = "ausente (/dev/dri não existe: sem GPU DRM disponível)"
    rc, pci = run(["lspci"])
    d["lspci"] = "\n".join(l for l in pci.splitlines() if re.search(r"(?i)vga|display|3d|video", l))
    rc, hw = run(["ffmpeg", "-hide_banner", "-hwaccels"])
    d["ffmpegHwaccels"] = [l.strip() for l in hw.splitlines() if l.strip() and not l.startswith("Hardware")]
    rc, enc = run(["ffmpeg", "-hide_banner", "-encoders"], keep=400000)
    d["encodersH264Hw"] = [l.split()[1] for l in enc.splitlines() if re.search(r"\b(h264|hevc)_(vaapi|qsv|nvenc)\b", l) and len(l.split()) > 1]
    try:
        import grp
        d["gruposDoUsuario"] = sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
    except Exception:  # noqa: BLE001
        d["gruposDoUsuario"] = []
    if a.probe_gpu:
        d["vainfo"] = run(["vainfo"], timeout=15)[1][:3000]
    else:
        d["vainfo"] = "não executado (use --probe-gpu para abrir /dev/dri/renderD* e listar perfis VAAPI)"
    return d


def sec_software(extra_pythons):
    d = {}
    d["python3"] = run(["python3", "--version"])[1].strip()
    d["pythonPath"] = shutil.which("python3")
    rc, ffv = run(["ffmpeg", "-hide_banner", "-version"])
    d["ffmpeg"] = {"versao": ffv.splitlines()[0] if ffv else "indisponível", "caminho": shutil.which("ffmpeg"),
                   "recursos": [f for f in ("--enable-vaapi", "--enable-libmfx", "--enable-libvpl", "--enable-libx264", "--enable-libx265", "--enable-gnutls",
                                            "--enable-openssl", "--enable-nvenc", "--enable-libfdk-aac") if f in ffv]}
    d["ffprobe"] = (run(["ffprobe", "-hide_banner", "-version"])[1].splitlines() or ["indisponível"])[0]
    pk = ["python3", "python3-serial", "python3-opencv", "python3-pip", "python3-venv", "ffmpeg", "libva2", "libva-drm2", "vainfo", "intel-media-va-driver",
          "intel-media-va-driver-non-free", "i965-va-driver", "mesa-va-drivers", "libmfx1", "libvpl2", "modemmanager", "brltty", "chrony", "ntp", "systemd-timesyncd",
          "v4l-utils", "gstreamer1.0-tools", "gstreamer1.0-rtsp", "udev", "lm-sensors"]
    rc, out = run(["dpkg-query", "-W", "-f=${Package}\t${Version}\t${Status}\n"] + pk, timeout=15)
    d["pacotesRelevantes"] = {l.split("\t")[0]: l.split("\t")[1] for l in out.splitlines() if "installed" in l and "not-installed" not in l and len(l.split("\t")) > 2}
    pips = {}
    for py in extra_pythons[:6]:
        rc, o = run([py, "-m", "pip", "list", "--format=freeze", "--disable-pip-version-check"], timeout=20)
        pips[py] = [x for x in o.splitlines() if "==" in x][:150] if rc == 0 else "não foi possível listar (pip ausente?)"
    d["pipPorInterpretador"] = pips
    return d


def parse_exec(line):
    return redact(line)


def sec_services(keywords):
    d = {}
    rc, out = run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"])
    rows = [l.split(None, 4) for l in out.splitlines() if l.strip()]
    d["totalUnidades"] = len(rows)
    d["emExecucao"] = [r[0] for r in rows if len(r) >= 4 and r[3] == "running"]
    d["falhas"] = [r[0] for r in rows if len(r) >= 3 and r[2] == "failed"]
    kw = re.compile("|".join(keywords), re.I)
    relevantes = [r[0] for r in rows if kw.search(r[0]) or (len(r) > 4 and kw.search(r[4]))]
    d["relevantes"] = []
    for u in relevantes[:40]:
        rc, show = run(["systemctl", "show", u, "-p", "Id,ActiveState,SubState,User,Group,WorkingDirectory,Restart,MainPID,MemoryCurrent,CPUUsageNSec,"
                        "ExecMainStartTimestamp,FragmentPath,Nice,SupplementaryGroups,KillMode"])
        props = dict(l.split("=", 1) for l in show.splitlines() if "=" in l)
        rc, cat = run(["systemctl", "cat", u])
        safe = []
        for l in cat.splitlines():
            if re.match(r"^\s*(Environment|EnvironmentFile)\s*=", l):
                names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)=", l.split("=", 1)[1])
                safe.append("%s=<valores omitidos; nomes: %s>" % (l.split("=", 1)[0].strip(), ",".join(names) or "arquivo"))
            else:
                safe.append(l)
        d["relevantes"].append({"unidade": u, "propriedades": props, "arquivoMascarado": "\n".join(safe)[:4000]})
    rc, tm = run(["systemctl", "list-timers", "--all", "--no-pager", "--no-legend"])
    d["timers"] = tm.splitlines()[:40]
    d["modemManagerAtivo"] = unit_state("ModemManager.service")
    d["brlttyAtivo"] = unit_state("brltty.service")
    d["docker"] = run(["docker", "ps", "--format", "{{.Names}}\t{{.Image}}\t{{.Status}}"])[1] if have("docker") else "indisponível"
    d["supervisor"] = run(["supervisorctl", "status"])[1] if have("supervisorctl") else "indisponível"
    d["crontabUsuario"] = run(["crontab", "-l"])[1][:2000] if have("crontab") else "indisponível"
    d["cronD"] = sorted(os.listdir("/etc/cron.d")) if os.path.isdir("/etc/cron.d") else []
    return d


def parse_ffmpeg_cmd(args):
    """Extrai de uma linha de comando FFmpeg o que importa (já redigida)."""
    toks = args.split()
    info = {"entradas": [], "saidas": [], "opcoes": {}}
    i = 0
    while i < len(toks):
        t = toks[i]
        if t == "-i" and i + 1 < len(toks):
            u = mask_url(toks[i + 1])
            info["entradas"].append(u["url"] if u else toks[i + 1])
            i += 2
            continue
        if t in ("-c", "-c:v", "-c:a", "-codec", "-f", "-segment_time", "-segment_format", "-rtsp_transport", "-r", "-s", "-b:v", "-g", "-hls_time",
                 "-hls_list_size", "-segment_list_size", "-segment_wrap", "-strftime", "-reset_timestamps", "-t", "-ss", "-vf", "-preset", "-crf") and i + 1 < len(toks):
            info["opcoes"][t] = toks[i + 1]
            i += 2
            continue
        i += 1
    if toks:
        info["saidas"].append(toks[-1])
    return info


def sec_processes(keywords):
    rc, out = run(["ps", "-eo", "pid,ppid,user:20,etimes,pcpu,pmem,rss,nlwp,args"], keep=400000)
    rows = []
    kw = re.compile("|".join(keywords) + r"|ffmpeg|ffprobe|gst-launch|vlc|leitor|buffer\.py|replay\.py", re.I)
    for l in out.splitlines()[1:]:
        p = l.split(None, 8)
        if len(p) < 9:
            continue
        if kw.search(p[8]) and not re.search(r"inventario_srv01", p[8]):
            rows.append({"pid": int(p[0]), "ppid": int(p[1]), "usuario": p[2], "ativoSegundos": int(p[3]), "cpuPct": float(p[4]), "memPct": float(p[5]),
                         "rssMB": round(int(p[6]) / 1024, 1), "threads": int(p[7]), "linhaDeComando": p[8][:600]})
    ff = []
    for r in rows:
        if re.search(r"(^|/)ffmpeg(\s|$)", r["linhaDeComando"]):
            ff.append({"pid": r["pid"], "usuario": r["usuario"], "cpuPct": r["cpuPct"], "rssMB": r["rssMB"], "ativoSegundos": r["ativoSegundos"],
                       **parse_ffmpeg_cmd(r["linhaDeComando"])})
    top = []
    rc, t = run(["ps", "-eo", "pid,user:20,pcpu,pmem,comm"], keep=300000)
    lines = t.splitlines()[1:]
    lines.sort(key=lambda l: -float(l.split(None, 4)[2]) if len(l.split(None, 4)) > 2 and re.match(r"[\d.]+$", l.split(None, 4)[2]) else 0)
    top = lines[:12]
    return {"processosRelevantes": rows[:60], "ffmpeg": ff, "top12CpuApenasNomes": top}


def holders_of_ttys():
    """Quem tem /dev/ttyUSB*/ttyACM* ABERTO — lendo só /proc/<pid>/fd (readlink). NUNCA abre a porta."""
    res = {}
    for pid in (p for p in os.listdir("/proc") if p.isdigit()):
        fd_dir = "/proc/%s/fd" % pid
        try:
            for fd in os.listdir(fd_dir):
                try:
                    tgt = os.readlink("%s/%s" % (fd_dir, fd))
                except OSError:
                    continue
                if re.match(r"^/dev/tty(USB|ACM)\d+$", tgt) or tgt.startswith("/dev/replayagora/"):
                    comm = (read_text("/proc/%s/comm" % pid) or "?").strip()
                    cmd = redact((read_text("/proc/%s/cmdline" % pid) or "").replace("\0", " ").strip())[:300]
                    res.setdefault(tgt, []).append({"pid": int(pid), "comm": comm, "cmdline": cmd})
        except OSError:
            continue
    return res


def sec_serial(a):
    d = {"dispositivos": [], "porSerialById": {}, "porPath": {}, "replayagoraDev": {}}
    devs = sorted(glob.glob("/sys/class/tty/ttyUSB*") + glob.glob("/sys/class/tty/ttyACM*"))
    for sysdev in devs:
        name = os.path.basename(sysdev)
        props = {}
        rc, out = run(["udevadm", "info", "--query=property", "--name", "/dev/" + name])
        for l in out.splitlines():
            if "=" in l:
                k, v = l.split("=", 1)
                if k in ("ID_VENDOR_ID", "ID_MODEL_ID", "ID_VENDOR", "ID_MODEL", "ID_SERIAL", "ID_SERIAL_SHORT", "ID_PATH", "ID_PATH_TAG", "ID_USB_DRIVER", "DEVPATH",
                         "ID_MM_DEVICE_IGNORE", "ID_USB_INTERFACE_NUM", "SUBSYSTEM", "DEVLINKS"):
                    props[k] = v
        d["dispositivos"].append({"nome": name, "node": stat_info("/dev/" + name), "udev": props, "ehCH340": props.get("ID_VENDOR_ID") == "1a86" and props.get("ID_MODEL_ID") == "7523"})
    for sub, key in (("/dev/serial/by-id", "porSerialById"), ("/dev/serial/by-path", "porPath"), ("/dev/replayagora", "replayagoraDev")):
        if os.path.isdir(sub):
            for n in sorted(os.listdir(sub)):
                try:
                    d[key][n] = os.readlink(os.path.join(sub, n))
                except OSError:
                    pass
    d["quemTemAbertas"] = holders_of_ttys()
    rc, lsusb = run(["lsusb"])
    d["lsusb"] = "\n".join(l for l in lsusb.splitlines() if re.search(r"(?i)1a86|ch34|arduino|serial|ftdi|cp210|prolific", l))
    rc, dm = run(["dmesg"], keep=400000)
    d["dmesgSerial"] = [l for l in dm.splitlines() if re.search(r"(?i)ttyUSB|ttyACM|ch341|cdc_acm|brltty", l)][-25:] if rc == 0 else "indisponível (dmesg exige root em muitos sistemas)"
    d["gruposDoUsuario"] = sec_gpu_groups()
    d["aviso"] = "As portas NÃO foram abertas por este script (abrir reinicia o Arduino e pode interferir no leitor)."
    return d


def sec_gpu_groups():
    try:
        import grp
        return sorted(grp.getgrgid(g).gr_name for g in os.getgroups())
    except Exception:  # noqa: BLE001
        return []


def find_pythons(roots):
    res = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        bd = r.rstrip("/").count("/")
        for dp, dn, fn in os.walk(r):
            dn[:] = [x for x in dn if x not in SKIP_DIRS - {"venv", ".venv"} and not x.startswith(".git")]
            if dp.count("/") - bd > 4:
                dn[:] = []
                continue
            if os.path.basename(dp) == "bin" and "python3" in fn and "/site-packages/" not in dp:
                res.append(os.path.join(dp, "python3"))
    return res[:6]


# ------------------------------------------------------------------------------------------------ código legado
def find_legacy(roots, names=("leitor.py", "buffer.py", "replay.py")):
    found = {n: [] for n in names}
    for r in roots:
        if not os.path.isdir(r):
            continue
        base_depth = r.rstrip("/").count("/")
        for dp, dn, fn in os.walk(r):
            dn[:] = [x for x in dn if x not in SKIP_DIRS and not x.startswith(".")]
            if dp.count("/") - base_depth > 7:
                dn[:] = []
                continue
            for n in names:
                if n in fn:
                    found[n].append(os.path.join(dp, n))
    return found


def analyze_py(path):
    src = read_text(path) or ""
    info = {"shebang": src.splitlines()[0] if src.startswith("#!") else None, "linhas": src.count("\n") + 1}
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        info["erro"] = "sintaxe inválida (talvez Python 2): linha %s" % e.lineno
        return info
    consts = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, (str, int, float)):
            consts[n.targets[0].id] = n.value.value
    fns, cls, imports, argparse_args = [], [], set(), []
    serial_calls, ff_cmds, regexes, sleeps, writes, globs, fmts, listens, threads = [], [], [], [], [], [], [], [], False

    def const(n):
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, ast.Name) and n.id in consts:      # constante simples de módulo (ex.: CAM = "rtsp://...")
            return consts[n.id]
        return None

    def flatten_list(n):
        out = []
        for e in getattr(n, "elts", []):
            v = const(e)
            out.append(str(v) if isinstance(v, (str, int, float)) else "<expr>")
        return out

    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fns.append({"nome": n.name, "args": [x.arg for x in n.args.args], "doc": (ast.get_docstring(n) or "")[:100]})
        elif isinstance(n, ast.ClassDef):
            cls.append({"nome": n.name, "metodos": [m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]})
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name.split(".")[0] for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module.split(".")[0])
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "add_argument" and n.args and const(n.args[0]) is not None:
                argparse_args.append(str(const(n.args[0])))
            if name == "Serial":
                kw = {k.arg: const(k.value) for k in n.keywords}
                serial_calls.append({"porta": const(n.args[0]) if n.args else kw.get("port"), "baud": const(n.args[1]) if len(n.args) > 1 else kw.get("baudrate"),
                                     "timeout": kw.get("timeout")})
            if name in ("Popen", "run", "call", "check_call", "check_output", "system") and n.args:
                a0 = n.args[0]
                lst = flatten_list(a0) if isinstance(a0, (ast.List, ast.Tuple)) else ([str(const(a0))] if isinstance(const(a0), str) else [])
                if lst and any("ffmpeg" in x or "ffprobe" in x for x in lst[:2] + [lst[0]]):
                    ff_cmds.append(redact(" ".join(lst))[:500])
            if name in ("compile", "search", "match", "findall", "fullmatch") and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "re" and n.args:
                v = const(n.args[0])
                if isinstance(v, str):
                    regexes.append(redact(v)[:160])
            if name == "sleep" and n.args and isinstance(const(n.args[0]), (int, float)):
                sleeps.append(const(n.args[0]))
            if name in ("glob", "listdir", "iglob") and n.args and isinstance(const(n.args[0]), str):
                globs.append(redact(const(n.args[0]))[:200])
            if name == "strftime" and n.args and isinstance(const(n.args[0]), str):
                fmts.append(const(n.args[0]))
            if name == "open" and len(n.args) > 1 and isinstance(const(n.args[1]), str) and any(c in const(n.args[1]) for c in "wa+") and isinstance(const(n.args[0]), str):
                writes.append(redact(const(n.args[0]))[:200])
            if name in ("Thread", "ThreadPoolExecutor", "Process"):
                threads = True
            if name in ("HTTPServer", "run", "listen", "bind") and isinstance(f, ast.Attribute) and getattr(f.value, "id", "") in ("app", "socket", "sock", "server"):
                listens.append(name)
    lits = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if re.search(r"(/dev/tty|/srv/|/var/|/etc/|REPLAY_|\.mp4|\.ts\b|\.mkv|rtsp://|\.sock\b|https?://)", v):
                lits.add(redact(v)[:160])
    info.update({"funcoes": fns[:60], "classes": cls[:30], "imports": sorted(imports), "argumentosCLI": argparse_args, "temMainGuard": "__main__" in src,
                 "chamadasSerial": serial_calls, "comandosFFmpeg": ff_cmds[:10], "regexes": regexes[:15], "sleeps": sorted(set(sleeps))[:12],
                 "caminhosGlobListdir": globs[:15], "formatosStrftime": fmts[:10], "arquivosAbertosParaEscrita": writes[:15], "usaThreads": threads,
                 "possivelServidorRede": listens[:5], "literaisRelevantes": sorted(lits)[:60],
                 "usaPySerial": "serial" in imports, "usaOpenCV": "cv2" in imports, "usaSubprocess": "subprocess" in imports,
                 "usaTempoDoSistema": bool(re.search(r"\btime\.time\(|datetime\.(utc)?now\(", src))})
    return info


def sec_legacy(roots, a):
    found = find_legacy(roots)
    d = {"raizes": [{"caminho": r, "existe": os.path.isdir(r), "stat": stat_info(r)} for r in roots], "arquivos": {}, "naoEncontrados": []}
    for n, paths in found.items():
        if not paths:
            d["naoEncontrados"].append(n)
        d["arquivos"][n] = []
        for p in paths:
            info = {"caminho": p, "stat": stat_info(p), "sha256": sha256_file(p)}
            info.update(analyze_py(p))
            sib = []
            try:
                for e in sorted(os.listdir(os.path.dirname(p)))[:60]:
                    sib.append({"nome": e, "stat": stat_info(os.path.join(os.path.dirname(p), e))})
            except OSError:
                pass
            info["arquivosVizinhos"] = sib
            gitdir = os.path.join(os.path.dirname(p), ".git", "HEAD")
            info["gitHead"] = (read_text(gitdir) or "").strip()[:60] or None
            d["arquivos"][n].append(info)
    return d, found


def line_shape(l):
    s = redact(l)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.,\d]*(Z|[+-]\d{2}:?\d{2})?", "<DATA-HORA>", s)
    s = re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "<HORA>", s)
    s = re.sub(r"\b\d+\b", "#", s)
    return s.strip()[:160]


def sec_configs_cameras(roots, a):
    """Câmeras configuradas (host:porta; sem credencial), parâmetros não sensíveis e NOMES de chaves de arquivos de configuração."""
    cams = collections.OrderedDict()
    params = []
    keysonly = {}
    serial_lit = set()
    scanned = 0
    for r in roots:
        if not os.path.isdir(r):
            continue
        base_depth = r.rstrip("/").count("/")
        for dp, dn, fn in os.walk(r):
            dn[:] = [x for x in dn if x not in SKIP_DIRS]
            if dp.count("/") - base_depth > 6:
                dn[:] = []
                continue
            for f in fn:
                ext = os.path.splitext(f)[1].lower()
                if ext in VIDEO_EXT or (ext not in TEXT_EXT and not f.startswith(".env") and f not in ("Dockerfile",)):
                    continue
                path = os.path.join(dp, f)
                try:
                    if os.path.getsize(path) > MAX_TEXT:
                        continue
                except OSError:
                    continue
                txt = read_text(path)
                if txt is None:
                    continue
                scanned += 1
                if scanned > 3000:
                    break
                for ln, line in enumerate(txt.splitlines(), 1):
                    for m in re.finditer(r"(?i)\b(rtsps?|rtmps?)://[^\s'\"<>]+", line):
                        u = mask_url(m.group(0))
                        if u:
                            key = "%s:%s" % (u["host"], u["port"])
                            c = cams.setdefault(key, {"host": u["host"], "porta": u["port"], "esquema": u["scheme"], "caminhosVistos": [], "ondeAparece": []})
                            if u["url"] not in c["caminhosVistos"]:
                                c["caminhosVistos"].append(u["url"])
                            ref = "%s:%d" % (path, ln)
                            if ref not in c["ondeAparece"] and len(c["ondeAparece"]) < 6:
                                c["ondeAparece"].append(ref)
                    for m in re.finditer(r"/dev/(?:tty(?:USB|ACM)\d+|replayagora/[\w\-]+|serial/by-[a-z]+/[\w\-:.]+)", line):
                        serial_lit.add("%s (em %s:%d)" % (m.group(0), os.path.basename(path), ln))
                    m = re.match(r"^\s*[\"']?([A-Za-z_][\w\-\.]*)[\"']?\s*[:=]\s*(.*?)[,]?\s*$", line)
                    if m and ext in (".json", ".ini", ".cfg", ".conf", ".yaml", ".yml", ".env", ".toml", ".service", ".sh", "") or (m and f.startswith(".env")):
                        k, v = m.group(1), m.group(2).strip().strip("\"'")
                        keysonly.setdefault(path, set()).add(k)
                        if SAFE_PARAM_KEYS.match(k) and v and not re.search(r"://|@", v) and len(v) < 40:
                            params.append({"arquivo": path, "linha": ln, "chave": k, "valor": v})
    return {"camerasConfiguradas": list(cams.values()), "parametrosNaoSensiveis": params[:120], "portasSeriaisCitadasNosArquivos": sorted(serial_lit)[:30],
            "chavesPorArquivo(somente_nomes)": {k: sorted(v)[:60] for k, v in list(keysonly.items())[:60]}, "arquivosTextoVarridos": scanned}


# ------------------------------------------------------------------------------------------------ armazenamento do legado
def classify_names(names):
    pats = []
    for n in names[:20]:
        if re.fullmatch(r".*\d{10}(\.\d+)?\.\w+", n):
            pats.append("epoch-segundos")
        elif re.search(r"\d{8}[T_\-]?\d{6}", n):
            pats.append("data-hora")
        elif re.search(r"\d{3,6}\.\w+$", n):
            pats.append("contador")
        else:
            pats.append("outro")
    c = collections.Counter(pats)
    return c.most_common(1)[0][0] if c else None


def walk_storage(root, max_files):
    ext = collections.Counter()
    size_ext = collections.Counter()
    newest, oldest = 0.0, 1e18
    recent_bytes, n = 0, 0
    now = time.time()
    dirs = collections.defaultdict(lambda: {"arquivos": 0, "bytes": 0, "videos": [], "ultimaModificacao": 0.0})
    for dp, dn, fn in os.walk(root):
        dn[:] = [x for x in dn if x not in SKIP_DIRS]
        for f in fn:
            n += 1
            if n > max_files:
                return {"truncadoEm": max_files, "porExtensao": dict(ext), "bytesPorExtensao": dict(size_ext), "dirs": dict(dirs), "recentBytes": recent_bytes,
                        "newest": newest, "oldest": oldest}
            p = os.path.join(dp, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            e = os.path.splitext(f)[1].lower() or "(sem)"
            ext[e] += 1
            size_ext[e] += st.st_size
            d = dirs[dp]
            d["arquivos"] += 1
            d["bytes"] += st.st_size
            d["ultimaModificacao"] = max(d["ultimaModificacao"], st.st_mtime)
            if e in VIDEO_EXT:
                if e != "(sem)":
                    newest, oldest = max(newest, st.st_mtime), min(oldest, st.st_mtime)
                if now - st.st_mtime < 600:
                    recent_bytes += st.st_size
                if len(d["videos"]) < 40:
                    d["videos"].append((f, st.st_mtime, st.st_size))
    return {"porExtensao": dict(ext), "bytesPorExtensao": dict(size_ext), "dirs": dict(dirs), "recentBytes": recent_bytes, "newest": newest, "oldest": oldest}


def sec_legacy_storage(roots, a):
    out = []
    for r in roots:
        if not os.path.isdir(r):
            continue
        w = walk_storage(r, a.max_files)
        buffers = []
        for dp, info in w["dirs"].items():
            vids = sorted(info["videos"], key=lambda x: x[1])
            if len(vids) >= 3:
                mt = [v[1] for v in vids]
                deltas = sorted(b - a_ for a_, b in zip(mt, mt[1:]) if 0 < b - a_ < 600)
                buffers.append({"diretorio": dp, "arquivosNaPasta": info["arquivos"], "bytesNaPasta": info["bytes"],
                                "amostraDeNomes": [v[0] for v in vids[:5]] + [v[0] for v in vids[-5:]], "padraoDeNome": classify_names([v[0] for v in vids]),
                                "tamanhoMedioArquivoKB": round(sum(v[2] for v in vids) / len(vids) / 1024, 1),
                                "intervaloMedianoEntreArquivosS": round(deltas[len(deltas) // 2], 2) if deltas else None,
                                "ultimaModificacao": utc_str(info["ultimaModificacao"]),
                                "idadeUltimoArquivoS": round(time.time() - info["ultimaModificacao"]) if info["ultimaModificacao"] else None})
        buffers.sort(key=lambda b: -b["arquivosNaPasta"])
        du = None
        if not a.quick and have("df"):
            try:
                total = shutil.disk_usage(r)
                du = {"totalGB": round(total.total / 1e9, 1), "livreGB": round(total.free / 1e9, 1), "livrePct": round(100 * total.free / total.total, 1)}
            except OSError:
                pass
        out.append({"raiz": r, "extensoes": w["porExtensao"], "bytesPorExtensao": w["bytesPorExtensao"], "truncado": w.get("truncadoEm"),
                    "bytesEscritosUltimos10min": w["recentBytes"], "taxaEscritaEstimadaKBs": round(w["recentBytes"] / 600 / 1024, 1),
                    "videoMaisAntigo": utc_str(w["oldest"]) if w["oldest"] < 1e17 else None,
                    "videoMaisNovo": utc_str(w["newest"]) if w["newest"] else None,
                    "possiveisBuffersOuPastasDeReplay": buffers[:12], "disco": du})
    return out


def sec_event_log(units, a):
    """Somente CONTAGENS e o FORMATO (com números/datas normalizados) das linhas de acionamento; o conteúdo das linhas não é guardado."""
    d = {}
    if not have("journalctl"):
        return {"aviso": "journalctl indisponível"}
    for u in units[:6]:
        rc, out = run(["journalctl", "-u", u, "--since", "7 days ago", "--no-pager", "-o", "short-iso"], timeout=30, keep=3_000_000)
        if rc != 0:
            d[u] = {"aviso": "sem acesso ao journal desta unidade (tente com sudo ou pertencer ao grupo systemd-journal/adm)"}
            continue
        by_day, by_btn, shapes = collections.Counter(), collections.Counter(), collections.Counter()
        total = 0
        for l in out.splitlines():
            total += 1
            m = re.search(r"(REPLAY_[A-Za-z0-9_\-]+)", l)
            if m:
                by_btn[m.group(1)] += 1
                by_day[l[:10]] += 1
                shapes[line_shape(l[l.find(" ", 20):] if len(l) > 20 else l)] += 1
        d[u] = {"linhasNoPeriodo": total, "acionamentosPorBotao": dict(by_btn), "acionamentosPorDia": dict(sorted(by_day.items())),
                "formatosDeLinhaMaisComuns": [s for s, _ in shapes.most_common(4)]}
    return d


# ------------------------------------------------------------------------------------------------ resumo / checklist
def checklist(R):
    C = []

    def add(i, status, titulo, detalhe, acao=""):
        C.append({"id": i, "status": status, "titulo": titulo, "detalhe": detalhe, "acao": acao})

    pyv = R["software"]["python3"]
    m = re.search(r"(\d+)\.(\d+)", pyv)
    ok = bool(m) and (int(m.group(1)), int(m.group(2))) >= (3, 10)
    add("python", "OK" if ok else "ATENÇÃO", "Python >= 3.10 para o agente", pyv, "" if ok else "O agente usa sintaxe 3.10+; instalar python3.10+ (venv próprio) ou portar.")
    ff = R["software"]["ffmpeg"]["versao"]
    add("ffmpeg", "OK" if ff != "indisponível" else "CRÍTICO", "FFmpeg instalado", ff, "" if ff != "indisponível" else "Instalar ffmpeg (apt).")
    legacy = R["legado"]["arquivos"]
    miss = R["legado"]["naoEncontrados"]
    add("legado-arquivos", "OK" if not miss else "ATENÇÃO", "Arquivos legados localizados", "encontrados: %s; ausentes: %s" % (
        [n for n, v in legacy.items() if v], miss or "nenhum"), "Se ausentes, informar o caminho real com --legacy-root." if miss else "")
    cams = R["camerasEConfigs"]["camerasConfiguradas"]
    add("cameras", "OK" if cams else "DESCONHECIDO", "Câmeras RTSP configuradas", "%d câmera(s): %s" % (len(cams), ", ".join("%s:%s" % (c["host"], c["porta"]) for c in cams) or "nenhuma encontrada em texto"),
        "" if cams else "As URLs podem estar em outro caminho, em variável de ambiente do serviço ou cifradas: confirmar com o responsável.")
    sess = R["rede"]["sessoesRtspEstabelecidas"]
    add("sessoes-rtsp", "INFO", "Sessões RTSP ativas do servidor", json.dumps(sess) if sess else "nenhuma sessão RTSP estabelecida agora",
        "Cada câmera tem limite de sessões; o agente abrirá +1 por câmera. Confirmar o limite no manual/painel da câmera.")
    ser = R["serial"]["dispositivos"]
    ch = [d for d in ser if d["ehCH340"]]
    add("serial", "OK" if ser else "DESCONHECIDO", "Porta serial da botoeira", "%d dispositivo(s) tty USB; CH340: %d" % (len(ser), len(ch)),
        "" if ser else "Nenhuma porta serial visível (Arduino desconectado/diferente?).")
    hold = R["serial"]["quemTemAbertas"]
    add("serial-dono", "INFO" if hold else "DESCONHECIDO", "Processo que usa a porta serial", json.dumps({k: [h["comm"] + ":" + str(h["pid"]) for h in v] for k, v in hold.items()}) if hold else
        "ninguém visível (sem root não se vê processos de outros usuários)", "Só um processo pode abrir a porta: o cutover exige parar o dono atual (janela autorizada).")
    stable = R["serial"]["replayagoraDev"] or R["serial"]["porSerialById"]
    add("serial-nome-estavel", "OK" if R["serial"]["replayagoraDev"] else "ATENÇÃO", "Nome estável da porta", "by-id: %d; /dev/replayagora: %d" % (len(R["serial"]["porSerialById"]), len(R["serial"]["replayagoraDev"])),
        "" if R["serial"]["replayagoraDev"] else "CH340 não tem número de série: criar regra udev por porta USB física (deploy/udev/99-replayagora.rules) na homologação.")
    mm = R["servicos"]["modemManagerAtivo"]
    add("modemmanager", "ATENÇÃO" if mm == "active" else ("DESCONHECIDO" if mm == "indisponível" else "OK"), "ModemManager", mm, "ModemManager ativo pode sondar a serial e atrapalhar o Arduino; a regra udev define ID_MM_DEVICE_IGNORE." if mm == "active" else "")
    br = R["servicos"]["brlttyAtivo"]
    add("brltty", "ATENÇÃO" if br == "active" else ("DESCONHECIDO" if br == "indisponível" else "OK"), "brltty (conflito conhecido com CH340)", br, "Se ativo, ele toma o ttyUSB do CH340; avaliar remover/mascarar em janela autorizada." if br == "active" else "")
    hw = R["gpu"]
    has_dri = isinstance(hw["dri"], dict) and any(k.startswith("renderD") for k in hw["dri"])
    add("gpu", "OK" if has_dri and hw["encodersH264Hw"] else "INFO", "Aceleração por hardware (VAAPI/QSV)", "dispositivos DRM: %s; encoders HW no FFmpeg: %s" % (
        list(hw["dri"]) if isinstance(hw["dri"], dict) else hw["dri"], hw["encodersH264Hw"] or "nenhum"), "Sem GPU/driver: usar CPU (padrão). Com GPU: testar depois com --probe-gpu e recuo para CPU.")
    cpus = R["hardware"]["nproc"] or 1
    add("cpu", "OK" if cpus >= 4 else "ATENÇÃO", "CPUs", "%s CPU(s)" % cpus, "" if cpus >= 4 else "Poucos núcleos: manter max_ffmpeg=1 e corte em modo 'copy'.")
    mem = R["hardware"]["memoriaMB"]
    add("memoria", "OK" if mem.get("MemAvailable", 0) >= 2048 else "ATENÇÃO", "Memória disponível", "%s MB livres de %s MB" % (mem.get("MemAvailable"), mem.get("MemTotal")), "")
    for s in R["legadoArmazenamento"]:
        dk = s.get("disco")
        if dk:
            add("disco-" + s["raiz"], "OK" if dk["livrePct"] >= 20 else ("ATENÇÃO" if dk["livrePct"] >= 10 else "CRÍTICO"), "Espaço livre em " + s["raiz"], "%s%% livres (%s GB)" % (dk["livrePct"], dk["livreGB"]), "")
        bufs = s["possiveisBuffersOuPastasDeReplay"]
        add("buffer-" + s["raiz"], "INFO" if bufs else "DESCONHECIDO", "Pasta com segmentos/vídeos em uso", "; ".join("%s (%s arq., padrão %s, intervalo ~%ss)" % (
            b["diretorio"], b["arquivosNaPasta"], b["padraoDeNome"], b["intervaloMedianoEntreArquivosS"]) for b in bufs[:3]) or "nenhuma pasta com vídeos encontrada",
            "Define se o agente poderá usar 'external_dir' (somente leitura) ou precisará gravar o próprio buffer.")
    t = R["horario"]["timedatectl"]
    synced = bool(re.search(r"System clock synchronized:\s*yes", t)) or bool(re.search(r"NTP (service|synchronized):\s*(active|yes)", t))
    add("ntp", "OK" if synced else "ATENÇÃO", "Relógio sincronizado (NTP)", "sincronizado" if synced else "não confirmado", "O instante do acionamento depende do relógio do servidor: configurar chrony/timesyncd." if not synced else "")
    ffp = R["processos"]["ffmpeg"]
    add("ffmpeg-em-uso", "INFO", "Processos FFmpeg ativos", "%d processo(s)" % len(ffp), "Mostram como o legado captura (entradas, segmentos, codec). Ver seção 'processos'.")
    return C


# ------------------------------------------------------------------------------------------------ saída
def render_text(R):
    L = []
    w = L.append
    w("=" * 100)
    w("INVENTÁRIO SOMENTE LEITURA — %s — %s (script v%s)" % (R["meta"]["hostname"], R["meta"]["geradoEmUTC"], VERSION))
    w("=" * 100)
    for a in R["meta"]["avisos"]:
        w("AVISO: " + a)
    w("\n## RESUMO PARA A HOMOLOGAÇÃO (leia primeiro)")
    order = {"CRÍTICO": 0, "ATENÇÃO": 1, "DESCONHECIDO": 2, "INFO": 3, "OK": 4}
    for c in sorted(R["resumo"], key=lambda x: order.get(x["status"], 9)):
        w("[%-12s] %s — %s" % (c["status"], c["titulo"], c["detalhe"]))
        if c["acao"]:
            w("               → " + c["acao"])
    w("\n## LIMITAÇÕES DESTA COLETA")
    for l in R["limitacoes"]:
        w("- " + l)

    def block(title, obj):
        w("\n## " + title)
        w(json.dumps(obj, indent=2, ensure_ascii=False, default=str))
    for k, t in (("hardware", "CPU / memória / sistema"), ("armazenamento", "Armazenamento"), ("horario", "Horário"), ("rede", "Rede"), ("gpu", "GPU / aceleração"),
                 ("software", "Versões de software"), ("servicos", "Serviços"), ("processos", "Processos"), ("serial", "Portas seriais"), ("legado", "Código legado (análise estática)"),
                 ("camerasEConfigs", "Câmeras e configurações (sem segredos)"), ("legadoArmazenamento", "Armazenamento do legado"), ("logDeAcionamentos", "Acionamentos registrados (contagens)")):
        block(t, R[k])
    w("\n## COMANDOS EXTERNOS EXECUTADOS (todos da lista permitida, somente leitura)")
    for c in R["comandos"]:
        w("- " + json.dumps(c, ensure_ascii=False))
    return "\n".join(L) + "\n"


SECRET_SCAN = [
    ("url-com-credencial", re.compile(r"(?i)[a-z][a-z0-9+.\-]*://(?!\*\*\*@)[^/\s:@'\"<>*]+:[^/\s@'\"<>*]+@")),
    ("chave-valor-sensivel", re.compile(r"(?i)\b[\w\-]*" + SECRET_KEYS + r"[\w\-]*\"?\s*[:=]\s*\"?(?!\*\*\*|<|\"|'|,|\s|$|\{|\[|null|none|true|false|\d+$)[^\s\"',;]{4,}")),
    ("bearer", re.compile(r"(?i)\b(bearer|basic)\s+(?!\*\*\*)[A-Za-z0-9._~+/=\-]{12,}")),
    ("token-rag", re.compile(r"\brag_(?!\*\*\*)[A-Za-z0-9_\-]{8,}")),
    ("chave-privada", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
]


def secret_scan(text):
    hits = []
    for name, rx in SECRET_SCAN:
        for m in rx.finditer(text):
            hits.append({"tipo": name, "trecho": m.group(0)[:12] + "…"})
    return hits


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", help="diretório NOVO para o relatório (não pode estar dentro das pastas analisadas)")
    ap.add_argument("--legacy-root", action="append", default=[], help="pasta do código/dados legados (padrão: /srv/replayagora); repetível")
    ap.add_argument("--sample-seconds", type=int, default=5, help="segundos de amostragem de CPU (vmstat); 0 desliga")
    ap.add_argument("--probe-gpu", action="store_true", help="executa vainfo (abre /dev/dri/renderD*; sem efeito colateral conhecido)")
    ap.add_argument("--check-https", help="URL https:// da central para um teste de conectividade (HEAD, 8 s)")
    ap.add_argument("--max-files", type=int, default=200000, help="limite de arquivos percorridos por raiz")
    ap.add_argument("--quick", action="store_true", help="pula a amostragem e o uso de disco")
    ap.add_argument("--stdout", action="store_true", help="não grava arquivos: imprime o relatório na tela")
    ap.add_argument("--keywords", default="replay|leitor|quadra|buffer|camera|rtsp|arduino|botoeira|ffmpeg", help="regex para destacar serviços/processos")
    a = ap.parse_args()
    roots = [os.path.realpath(r) for r in (a.legacy_root or ["/srv/replayagora"])]
    if not a.stdout:
        if not a.out:
            ap.error("informe --out DIR (ou use --stdout)")
        out = os.path.realpath(os.path.expanduser(a.out))
        for r in roots:
            if out == r or out.startswith(r + os.sep) or r.startswith(out + os.sep):
                print("RECUSADO: --out não pode ficar dentro (nem conter) as pastas analisadas: %s" % r, file=sys.stderr)
                return 2
        if os.path.exists(out) and os.listdir(out):
            print("RECUSADO: %s já existe e não está vazio (nada será sobrescrito)." % out, file=sys.stderr)
            return 2
    keywords = a.keywords.split("|")
    R = {"meta": sec_meta(a)}
    R["hardware"] = sec_hw()
    R["armazenamento"] = sec_storage(roots, a)
    R["horario"] = sec_time()
    R["rede"] = sec_network(a)
    R["gpu"] = sec_gpu(a)
    legacy, found = sec_legacy(roots, a)
    venvs = find_pythons(roots)
    R["software"] = sec_software(venvs)
    R["servicos"] = sec_services(keywords)
    R["processos"] = sec_processes(keywords)
    R["serial"] = sec_serial(a)
    R["legado"] = legacy
    R["camerasEConfigs"] = sec_configs_cameras(roots, a)
    R["legadoArmazenamento"] = sec_legacy_storage(roots, a)
    units = [u["unidade"] for u in R["servicos"]["relevantes"] if re.search(r"(?i)leitor|botoeira|arduino|serial|replay", u["unidade"])] or \
            [u["unidade"] for u in R["servicos"]["relevantes"]][:3]
    R["logDeAcionamentos"] = sec_event_log(units, a)
    if a.sample_seconds and not a.quick:
        rc, vm = run(["vmstat", "1", str(a.sample_seconds)], timeout=a.sample_seconds + 15)
        R["hardware"]["amostraVmstat"] = vm
    R["resumo"] = checklist(R)
    lim = []
    if os.geteuid() != 0:
        lim += ["Sem root: não foi possível ver processos/arquivos abertos de outros usuários, dmesg e, possivelmente, parte do journal."]
    if not R["meta"]["opcoes"]["probeGpu"]:
        lim.append("vainfo não executado (sem --probe-gpu): perfis VAAPI não verificados.")
    lim += ["Nenhuma câmera foi contatada; sessões RTSP e limites por câmera precisam ser confirmados à parte.",
            "Portas seriais não foram abertas; protocolo e firmware do Arduino não foram verificados (só o que o sistema operacional informa).",
            "Valores de configuração sensíveis foram omitidos: apenas nomes de chaves e parâmetros técnicos não sensíveis aparecem."]
    R["limitacoes"] = lim
    R["comandos"] = _CMD_LOG
    # ---- varredura final de segredos (reescreve e sinaliza)
    text = render_text(R)
    js = json.dumps(R, indent=2, ensure_ascii=False, default=str)
    hits = secret_scan(text) + secret_scan(js)
    status = "OK (nenhum padrão de segredo encontrado)"
    if hits:
        text, js = redact(text), redact(js)
        left = secret_scan(text) + secret_scan(js)
        status = "ALERTA: %d trecho(s) suspeito(s) foram reescritos%s — REVISE antes de enviar" % (len(hits), "; AINDA HÁ %d suspeito(s)" % len(left) if left else "")
        R["varreduraDeSegredos"] = {"hits": hits[:20]}
    R["varreduraDeSegredosStatus"] = status
    text += "\n## VARREDURA FINAL DE SEGREDOS\n%s\n" % status
    if a.stdout:
        sys.stdout.write(text)
        return 0
    os.makedirs(out, mode=0o700, exist_ok=True)
    os.chmod(out, 0o700)
    files = {"inventario.txt": text, "inventario.json": js,
             "LEIA-ME.txt": ("Relatório gerado por inventario_srv01.py v%s em %s.\n1) Abra inventario.txt e leia o RESUMO PARA A HOMOLOGAÇÃO.\n2) Revise o conteúdo (IPs e nomes de host podem ser "
                             "sensíveis) antes de compartilhar; segredos foram removidos e há varredura automática (%s).\n3) Envie inventario.txt, inventario.json e SHA256SUMS por canal privado.\n"
                             % (VERSION, R["meta"]["geradoEmUTC"], status))}
    for n, c in files.items():
        with open(os.path.join(out, n), "w", encoding="utf-8") as f:
            f.write(c)
    with open(os.path.join(out, "SHA256SUMS"), "w") as f:
        for n in sorted(files):
            f.write("%s  %s\n" % (hashlib.sha256(files[n].encode("utf-8")).hexdigest(), n))
    for n in list(files) + ["SHA256SUMS"]:
        os.chmod(os.path.join(out, n), 0o600)
    print("Relatório em: %s\nVarredura de segredos: %s" % (out, status))
    return 0 if not hits else 3


if __name__ == "__main__":
    sys.exit(main())

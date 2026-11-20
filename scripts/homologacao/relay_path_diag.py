#!/usr/bin/env python3
"""Diagnóstico SOMENTE LEITURA de por que o relay não alcança um MP4 (Issue #15: arquivo íntegro para o root, `not_found` para o serviço).

NÃO instrumenta nem reinicia o relay, NÃO sincroniza, NÃO escreve em banco/arquivos, NÃO faz requisição à central, NÃO imprime credenciais.
Responde: qual `errno` o serviço recebe, em QUAL componente do caminho, o caminho do catálogo bate com o nome real no disco
(Unicode/maiúsculas/espaço/byte nulo), qual mount cobre o arquivo e com que opções, e com quem o serviço roda (uid/gid/capacidades).

Uso (na arena; leitura):
  python3 relay_path_diag.py --catalog /opt/replayagora-stage/media-only/catalogo.sqlite --ref <local_id> \\
      --root /srv/replayagora/quadra01/replays --root /srv/replayagora/quadra02/replays --pid $(systemctl show -p MainPID --value replayagora-media-relay.service)
Para ver exatamente o que o SERVIÇO vê, rode como o usuário dele DENTRO do namespace de mount dele (leitura):
  sudo nsenter -t <PID> -m -- setpriv --reuid=<uid> --regid=<gid> --init-groups python3 relay_path_diag.py --catalog … --ref … --root …
"""
import argparse, os, sqlite3, stat, sys, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "local-agent"))
try:
    from replay_agent.filecheck import probe_file
except Exception:  # noqa: BLE001 — o script também funciona isolado
    probe_file = None


def out(k, v=""):
    print(f"{k}: {v}")


def path_hygiene(p: str) -> None:
    nfc = unicodedata.normalize("NFC", p)
    out("repr(path)", repr(p))
    out("byte nulo / caractere de controle", f"{'\x00' in p} / {any(ord(c) < 32 or ord(c) == 127 for c in p)}")
    out("espaço/CR/LF no início ou fim", p != p.strip())
    out("já está em NFC (normalização Unicode)", nfc == p)
    out("é absoluto", os.path.isabs(p))


def walk(p: str) -> None:
    out("--- componentes (lstat de cada nível, como ESTE usuário)")
    cur = "/"
    for part in [x for x in os.path.normpath(p).split("/") if x]:
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
            kind = "symlink→" + os.readlink(cur) if stat.S_ISLNK(st.st_mode) else ("dir" if stat.S_ISDIR(st.st_mode) else "arquivo" if stat.S_ISREG(st.st_mode) else "outro")
            print(f"  OK   {cur}  {kind}  modo={stat.filemode(st.st_mode)} uid={st.st_uid} gid={st.st_gid} dev={st.st_dev}")
        except OSError as e:
            print(f"  FALHA {cur}  errno={e.errno} ({os.strerror(e.errno)})")
            break


def name_match(p: str) -> None:
    parent, base = os.path.dirname(p), os.path.basename(p)
    out("--- path_match: o nome do catálogo existe EXATAMENTE no diretório?")
    try:
        names = os.listdir(parent)
    except OSError as e:
        out("listdir(pai)", f"errno={e.errno} ({os.strerror(e.errno)})"); return
    out("entradas no diretório", len(names))
    exact = base in names
    out("nome exato presente", exact)
    if not exact:
        norm = lambda s: unicodedata.normalize("NFC", s).casefold().strip()  # noqa: E731
        near = [n for n in names if norm(n) == norm(base)]
        out("nomes equivalentes (NFC/maiúsculas/espaços)", [repr(n) for n in near] or "nenhum")


def mount_for(p: str, pid: str | None) -> None:
    src = f"/proc/{pid}/mountinfo" if pid else "/proc/self/mountinfo"
    out(f"--- mount que cobre o arquivo ({'visão do PID ' + pid if pid else 'deste processo'})")
    best = None
    try:
        for line in open(src):
            f = line.split()
            mp = f[4].replace("\\040", " ")
            if (p == mp or p.startswith(mp.rstrip("/") + "/")) and (best is None or len(mp) > len(best[0])):
                sep = f.index("-")
                best = (mp, f[sep + 1], f[sep + 2], f[5])
    except OSError as e:
        out("mountinfo", f"errno={e.errno}"); return
    out("ponto de montagem / tipo / fonte / opções", best or "não encontrado")


def process_view(pid: str, p: str) -> None:
    out(f"--- processo {pid}")
    try:
        want = ("Name", "Uid", "Gid", "Groups", "CapEff", "NoNewPrivs", "Seccomp")
        for line in open(f"/proc/{pid}/status"):
            if line.split(":")[0] in want:
                print("  " + line.strip())
    except OSError as e:
        out("status", f"errno={e.errno}")
    try:
        os.stat(f"/proc/{pid}/root{p}")
        out(f"stat(/proc/{pid}/root<caminho>) (visão de mount do serviço, com SEU usuário)", "OK")
    except OSError as e:
        out(f"stat(/proc/{pid}/root<caminho>)", f"errno={e.errno} ({os.strerror(e.errno)})")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog"); ap.add_argument("--ref"); ap.add_argument("--path"); ap.add_argument("--root", action="append", default=[]); ap.add_argument("--pid")
    a = ap.parse_args()
    p = a.path
    if a.catalog and a.ref:
        c = sqlite3.connect(f"file:{a.catalog}?mode=ro", uri=True, timeout=5)
        r = c.execute("select path, file_ok, sync_state, deleted from replays where local_id=?", (a.ref,)).fetchone()
        out("catálogo (ref)", a.ref)
        if not r:
            out("linha", "NÃO ENCONTRADA"); return 1
        p = r[0]; out("file_ok / sync_state / deleted", f"{r[1]} / {r[2]} / {r[3]}")
    if not p:
        ap.error("informe --catalog + --ref ou --path")
    out("python", sys.version.split()[0]); out("uid/gid/grupos deste processo", f"{os.geteuid()}/{os.getegid()}/{os.getgroups()}")
    path_hygiene(p)
    out("--- verificações (o mesmo arquivo, três APIs)")
    for name, fn in (("os.stat", lambda: bool(os.stat(p))), ("pathlib.Path.is_file()", lambda: Path(p).is_file()), ("os.path.isfile()", lambda: os.path.isfile(p))):
        try:
            out(name, fn())
        except Exception as e:  # noqa: BLE001
            out(name, f"LEVANTOU {type(e).__name__} errno={getattr(e, 'errno', None)}")
    if probe_file:
        pr = probe_file(p)
        out("filecheck.probe_file (o que o relay corrigido usa)", f"status={pr.status} errno={pr.errno} componente={pr.component} tamanho={pr.size}")
    walk(p); name_match(p); mount_for(os.path.realpath(p), a.pid)
    for root in a.root:
        rr = os.path.realpath(root)
        out(f"raiz {root}", f"→ {rr} | caminho resolvido dentro da raiz: {os.path.realpath(p).startswith(rr.rstrip('/') + '/')}")
    if a.pid:
        process_view(a.pid, p)
    return 0


if __name__ == "__main__":
    sys.exit(main())

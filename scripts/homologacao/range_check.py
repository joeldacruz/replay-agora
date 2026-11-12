#!/usr/bin/env python3
"""Verifica HTTP Range (RFC 9110) de um link de mídia assinado, SEM alterar nada e SEM imprimir o link/token.

Somente requisições GET de leitura. Não segue redirecionamentos, valida TLS e limita o volume lido por requisição.
O caminho assinado (ex.: /media/<token>) é lido de ARQUIVO (--path-file) ou da entrada padrão — nunca da linha de comandos,
para não ficar no histórico do shell nem em `ps`.

  python3 range_check.py --base https://replayagora.com.br --path-file /caminho/privado/link.txt [--expect-size N] [--expect-sha256 HEX] [--full]
  python3 range_check.py --selftest        # valida o próprio verificador contra um servidor local (sem rede externa)
Saída: PASSOU/REPROVOU por critério e código de saída 0/1.
"""
import argparse, hashlib, http.client, os, ssl, sys, tempfile, threading
from urllib.parse import urlsplit

LIMIT = 8 * 1024 * 1024
RES = []


def check(name, ok, detail=""):
    RES.append(ok)
    print(f"{'PASSOU ' if ok else 'REPROVOU'} {name}" + (f" — {detail}" if detail else ""))
    return ok


def get(base, path, rng=None, limit=LIMIT):
    u = urlsplit(base)
    cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    kw = {"context": ssl.create_default_context()} if u.scheme == "https" else {}
    c = cls(u.hostname, u.port, timeout=20, **kw)
    h = {"Accept-Encoding": "identity"}
    if rng:
        h["Range"] = rng
    c.request("GET", path, headers=h)
    r = c.getresponse()
    body = r.read(limit) if r.status != 416 else r.read(1024)
    hd = {k.lower(): v for k, v in r.getheaders()}
    c.close()
    return r.status, hd, body


def run(base, path, expect_size=None, expect_sha=None, full=False):
    st, hd, body = get(base, path, "bytes=0-1023")
    ok = st == 206 and hd.get("accept-ranges", "").lower() == "bytes" and len(body) == 1024 and hd.get("content-range", "").startswith("bytes 0-1023/")
    total = int(hd["content-range"].split("/")[1]) if st == 206 and "/" in hd.get("content-range", "") else None
    check("bytes=0-1023 → 206 + Content-Range + Accept-Ranges", ok, f"HTTP {st}, Content-Range={hd.get('content-range')}, {len(body)} bytes")
    if st != 206 or not total:
        print("Sem 206 não há como continuar (arquivo indisponível, link expirado ou servidor sem suporte a Range)."); return
    ctype = hd.get("content-type", "")
    check("Content-Type de vídeo", ctype.startswith("video/") or "mp4" in ctype, ctype)
    check("assinatura MP4 (ftyp) nos primeiros bytes", body[4:8] == b"ftyp", body[4:12].hex())
    if expect_size is not None:
        check("tamanho total == esperado", total == expect_size, f"{total} vs {expect_size}")
    st2, hd2, b2 = get(base, path, "bytes=-1024")
    check("bytes=-1024 (sufixo) → 206 com os últimos 1024 bytes", st2 == 206 and len(b2) == 1024 and hd2.get("content-range", "").endswith(f"/{total}") and f"{total - 1}/" in hd2.get("content-range", ""), f"HTTP {st2}, {hd2.get('content-range')}")
    mid = total // 2
    st3, hd3, b3 = get(base, path, f"bytes={mid}-{mid + 99}")
    check("intervalo no meio (seek) → 206, 100 bytes", st3 == 206 and len(b3) == 100 and hd3.get("content-range") == f"bytes {mid}-{mid + 99}/{total}", f"HTTP {st3}, {hd3.get('content-range')}")
    st4, hd4, _ = get(base, path, f"bytes={total + 10}-")
    check("intervalo fora do arquivo → 416 com Content-Range */total", st4 == 416 and hd4.get("content-range") == f"bytes */{total}", f"HTTP {st4}, {hd4.get('content-range')}")
    st5, hd5, b5 = get(base, path, None, limit=4096)
    check("sem Range → 200 e Content-Length == tamanho", st5 == 200 and int(hd5.get("content-length", -1)) == total, f"HTTP {st5}, CL={hd5.get('content-length')}")
    if full or expect_sha:
        sha, n, pos = hashlib.sha256(), 0, 0
        while pos < total:                       # reconstrói o arquivo por faixas de 1 MiB (também prova a concatenação de ranges)
            end = min(pos + 1024 * 1024, total) - 1
            s, h, b = get(base, path, f"bytes={pos}-{end}", limit=1024 * 1024 + 1)
            if s != 206 or len(b) != end - pos + 1:
                check(f"faixa {pos}-{end}", False, f"HTTP {s}, {len(b)} bytes"); break
            sha.update(b); pos = end + 1; n += 1
        else:
            print(f"  sha256 reconstruído por {n} faixa(s): {sha.hexdigest()}")
            if expect_sha:
                check("sha256 por faixas == sha256 do original no servidor da arena", sha.hexdigest() == expect_sha.lower())


def selftest():
    from starlette.applications import Starlette
    from starlette.responses import FileResponse
    from starlette.routing import Route
    import uvicorn, time, socket
    d = tempfile.mkdtemp(); f = os.path.join(d, "t.mp4")
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=15", "-pix_fmt", "yuv420p", f], check=True)
    sha = hashlib.sha256(open(f, "rb").read()).hexdigest(); size = os.path.getsize(f)
    app = Starlette(routes=[Route("/media/tok", lambda r: FileResponse(f, media_type="video/mp4"))])
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")); th = threading.Thread(target=srv.run, daemon=True); th.start()
    time.sleep(1.5)
    run(f"http://127.0.0.1:{port}", "/media/tok", expect_size=size, expect_sha=sha, full=True)
    print("-- selftest: caminho inexistente deve REPROVAR (404 não é 206):")
    n = len(RES); run(f"http://127.0.0.1:{port}", "/media/nao-existe"); neg = RES[n:]
    srv.should_exit = True
    return all(RES[:n]) and not any(neg)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base"); ap.add_argument("--path-file"); ap.add_argument("--expect-size", type=int); ap.add_argument("--expect-sha256")
    ap.add_argument("--full", action="store_true"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        ok = selftest(); print("SELFTEST:", "OK" if ok else "FALHOU"); sys.exit(0 if ok else 1)
    if not a.base:
        ap.error("--base é obrigatório")
    path = (open(a.path_file).read() if a.path_file else sys.stdin.readline()).strip()
    if not path.startswith("/"):
        ap.error("o caminho assinado deve começar com / (ex.: /media/<token>)")
    run(a.base.rstrip("/"), path, a.expect_size, a.expect_sha256, a.full)
    sys.exit(0 if all(RES) else 1)

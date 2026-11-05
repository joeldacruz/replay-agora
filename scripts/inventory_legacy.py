#!/usr/bin/env python3
"""Inventário SOMENTE LEITURA do código legado de captura (leitor.py, buffer.py, replay.py) para levantar o CONTRATO real.

* NÃO importa nem executa nenhum arquivo analisado (análise estática com `ast`).
* NÃO escreve nada além da saída padrão (ou do arquivo indicado em --out, que não pode estar sob o diretório analisado).
* Mascara credenciais em URLs RTSP e qualquer literal que pareça senha/token.
Uso (no replayagora-srv01, como usuário sem privilégio, após autorização):
  python3 inventory_legacy.py /srv/replayagora/botoeira/leitor.py /srv/replayagora/quadra01/app/buffer.py /srv/replayagora/quadra01/app/replay.py
"""
import argparse
import ast
import hashlib
import json
import re
import sys
from pathlib import Path

SECRET = re.compile(r"(?i)(pass(word)?|senha|token|secret|key)\s*[=:]")
URL = re.compile(r"(rtsps?|http)://[^\s'\"@/]+@")


def mask(s: str) -> str:
    s = URL.sub(lambda m: m.group(1) + "://***@", s)
    return "<mascarado>" if SECRET.search(s) else s[:160]


def literal_strings(tree: ast.AST) -> list[str]:
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            v = n.value
            if re.search(r"(/dev/|/srv/|/var/|/etc/|rtsp|REPLAY_|\.mp4|\.ts|ffmpeg|\.sock|\bhttp)", v):
                out.add(mask(v))
    return sorted(out)[:80]


def describe(path: Path) -> dict:
    raw = path.read_bytes()
    info = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "mtime": int(path.stat().st_mtime)}
    try:
        tree = ast.parse(raw.decode("utf-8", "replace"))
    except SyntaxError as e:
        return {**info, "error": f"sintaxe inválida: {e.msg} (linha {e.lineno})"}
    funcs, classes, imports, args, calls = [], [], set(), [], set()
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.append({"name": n.name, "args": [a.arg for a in n.args.args], "doc": (ast.get_docstring(n) or "")[:120]})
        elif isinstance(n, ast.ClassDef):
            classes.append({"name": n.name, "methods": [m.name for m in n.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]})
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            imports.update(a.name for a in n.names)
        elif isinstance(n, ast.ImportFrom) and n.module:
            imports.add(n.module)
        elif isinstance(n, ast.Call):
            f = n.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", "")
            if name == "add_argument" and n.args and isinstance(n.args[0], ast.Constant):
                args.append(str(n.args[0].value))
            if name in ("Popen", "run", "call", "check_output", "system", "Serial", "VideoCapture", "open"):
                calls.add(name)
    info.update({"functions": funcs, "classes": classes, "imports": sorted(imports), "cli_arguments": args, "notable_calls": sorted(calls),
                 "has_main_guard": "__main__" in raw.decode("utf-8", "replace"), "literals_of_interest": literal_strings(tree)})
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out")
    a = ap.parse_args()
    files = [Path(f).resolve() for f in a.files]
    if a.out:
        out = Path(a.out).resolve()
        if any(f.parent in out.parents or f.parent == out.parent for f in files):
            print("RECUSADO: --out não pode ficar na pasta do código analisado", file=sys.stderr)
            return 2
    report = {"readOnly": True, "files": [describe(f) if f.is_file() else {"path": str(f), "error": "não encontrado"} for f in files]}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if a.out:
        Path(a.out).write_text(text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())

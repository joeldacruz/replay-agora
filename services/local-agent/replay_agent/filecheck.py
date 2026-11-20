"""Verificação de arquivo com `errno` EXPLÍCITO.

`Path.is_file()` / `os.path.isfile()` respondem apenas verdadeiro/falso: `os.path.isfile` engole QUALQUER OSError (permissão, E/S,
laço de symlink…) e `Path.is_file` engole vários. Para o relay isso era grave: uma falha de acesso do SERVIÇO (que não é a mesma que
um `ls` como root) virava "arquivo não encontrado", rebaixava o replay (`file_ok=0`, `pending`) e a central passava a marcar `missing`.

Aqui cada causa é distinta e só a AUSÊNCIA DEFINITIVA (ENOENT/ENOTDIR, caminho inválido, não ser arquivo regular) conta como "não existe".
Permissão, E/S e laços são condições do ambiente: reportadas, mas não alteram o estado do catálogo.
"""
import errno
import os
import stat
from dataclasses import dataclass

OK = "ok"
MISSING = "missing"            # ENOENT / ENOTDIR: o arquivo (ou um diretório do caminho) não existe NESTA visão do sistema
NOT_REGULAR = "not_regular"    # existe, mas é diretório, dispositivo, socket…
INVALID = "invalid_path"       # byte nulo / caminho vazio: nunca resolve
PERMISSION = "permission"      # EACCES / EPERM: o usuário do serviço não alcança o arquivo
LOOP = "symlink_loop"          # ELOOP
IO = "io_error"                # EIO, ESTALE, ENAMETOOLONG, EMFILE… (transitório ou do ambiente)

_DEFINITIVE = {MISSING, NOT_REGULAR, INVALID}


@dataclass(frozen=True)
class Probe:
    status: str
    errno: str | None = None        # nome simbólico (ENOENT, EACCES…)
    component: str | None = None    # primeiro componente do caminho que falhou (para diagnóstico)
    size: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def definitive_absence(self) -> bool:
        return self.status in _DEFINITIVE


def errno_name(code: int | None) -> str | None:
    return None if code is None else errno.errorcode.get(code, str(code))


def classify(code: int | None) -> str:
    if code in (errno.ENOENT, errno.ENOTDIR):
        return MISSING
    if code in (errno.EACCES, errno.EPERM):
        return PERMISSION
    if code == errno.ELOOP:
        return LOOP
    return IO


def failing_component(path: str) -> str:
    """Primeiro componente (do caminho absoluto) que não pode ser consultado; ajuda a achar diretório ausente/sem permissão."""
    parts = os.path.normpath(path).split(os.sep)
    cur = os.sep if path.startswith(os.sep) else ""
    for part in parts:
        if not part:
            continue
        cur = os.path.join(cur, part) if cur else part
        try:
            os.stat(cur)
        except OSError:
            return cur
    return path


def probe_file(path) -> Probe:
    p = os.fspath(path)
    if not p or "\x00" in p:
        return Probe(INVALID)
    try:
        st = os.stat(p)
    except ValueError:
        return Probe(INVALID)
    except OSError as e:
        return Probe(classify(e.errno), errno_name(e.errno), failing_component(p))
    if not stat.S_ISREG(st.st_mode):
        return Probe(NOT_REGULAR, None, p)
    return Probe(OK, None, None, st.st_size)

"""Varredura independente em modo sombra para os novos MP4s da Quadra 02."""
import argparse
import json
from pathlib import Path
import resource
import stat
import subprocess
import time
import uuid

from .legacy_rotation import rotation_for_court
from .legacy_rotation_handoff import prepare_rotation_handoff

COURT_KEY = "QUADRA_02"
NAMESPACE = uuid.UUID("b60f62dd-b871-4d7a-94b7-f65736ca59bc")
REPLAY_ROOTS = (
    Path("/srv/replayagora-storage/quadra01/replays"),
    Path("/srv/replayagora-storage/quadra02/replays"),
)
STABLE_NS = 5_000_000_000


def local_id_for(path):
    return str(uuid.uuid5(NAMESPACE, COURT_KEY + ":" + path.name))


def log(event, **fields):
    print(json.dumps({"event": event, "courtKey": COURT_KEY, **fields}), flush=True)


def cpu_seconds():
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return time.process_time() + children.ru_utime + children.ru_stime


def scan(source, output, applied_state_path, since_ns):
    source = source.resolve(strict=True)
    output = output.resolve(strict=True)
    for monitored in (*REPLAY_ROOTS, source):
        monitored = monitored.resolve()
        if output.is_relative_to(monitored) or monitored.is_relative_to(output):
            raise ValueError("pasta de variantes deve ser isolada da árvore de replays")
    if not source.is_dir() or not output.is_dir():
        raise ValueError("pasta de replays ou variantes inválida")

    now = time.time_ns()
    if not 0 < since_ns <= now:
        raise ValueError("marco inicial deve ser um timestamp passado em nanossegundos")
    state = json.loads(applied_state_path.read_text(encoding="utf-8"))
    rotation_for_court(state, COURT_KEY)

    for path in sorted(source.glob("replay-*.mp4")):
        started, cpu_start = time.monotonic(), cpu_seconds()
        local_id = local_id_for(path)
        try:
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or not info.st_size:
                continue
            if not since_ns <= info.st_ctime_ns <= now - STABLE_NS:
                continue
            result = prepare_rotation_handoff(
                path, output, court_key=COURT_KEY, local_id=local_id, applied_state=state,
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
            log("rotation_failed", localId=local_id, error=type(exc).__name__,
                elapsedSeconds=time.monotonic() - started, cpuSeconds=cpu_seconds() - cpu_start)
            return 1
        log("variant_ready", localId=local_id, configVersion=result["configVersion"],
            rotationAppliedDeg=result["rotationAppliedDeg"],
            width=result["width"], height=result["height"],
            elapsedSeconds=time.monotonic() - started, cpuSeconds=cpu_seconds() - cpu_start)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--applied-state", required=True, type=Path,
                        help="arquivo local confiável de estado aplicado")
    parser.add_argument("--since-ns", required=True, type=int)
    args = parser.parse_args()
    try:
        return scan(REPLAY_ROOTS[1], args.output, args.applied_state, args.since_ns)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log("scan_failed", error=type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

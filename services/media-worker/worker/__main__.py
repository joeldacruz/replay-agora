import argparse

from .runner import main

ap = argparse.ArgumentParser(description="Worker de exportação FFmpeg do Replay Agora")
ap.add_argument("--once", action="store_true", help="processa a fila e sai")
ap.add_argument("--poll", type=float, default=1.0)
a = ap.parse_args()
main(a.poll, a.once)

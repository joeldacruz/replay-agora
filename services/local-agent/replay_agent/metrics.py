"""Amostragem de recursos do computador da arena (Linux /proc, sem dependências extras)."""
import os
import resource
import shutil
import time


def _cpu_times() -> tuple[float, float] | None:
    try:
        with open("/proc/stat") as f:
            parts = [float(x) for x in f.readline().split()[1:]]
        return sum(parts), parts[3] + (parts[4] if len(parts) > 4 else 0)  # total, idle+iowait
    except OSError:
        return None


def mem_used_pct() -> float | None:
    try:
        info = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                info[k] = float(v.split()[0])
        return round(100 * (1 - info["MemAvailable"] / info["MemTotal"]), 1)
    except (OSError, KeyError, ValueError):
        return None


class SystemSampler:
    def __init__(self, path: str):
        self.path = path
        self._last = _cpu_times()

    def sample(self) -> dict:
        cpu = None
        now = _cpu_times()
        if now and self._last and now[0] > self._last[0]:
            cpu = round(100 * (1 - (now[1] - self._last[1]) / (now[0] - self._last[0])), 1)
        self._last = now
        try:
            du = shutil.disk_usage(self.path)
            free, total = du.free, du.total
        except OSError:
            free = total = None
        try:
            load1 = round(os.getloadavg()[0], 2)
        except OSError:
            load1 = None
        return {"cpuPct": cpu, "load1": load1, "memUsedPct": mem_used_pct(), "diskFreeBytes": free, "diskTotalBytes": total}


class ProcessMeter:
    """CPU e pico de memória do PRÓPRIO processo (+ filhos, ex.: FFmpeg) entre start() e stop()."""

    def start(self) -> None:
        self.t0, self.c0 = time.perf_counter(), self._cpu()

    @staticmethod
    def _cpu() -> float:
        a, b = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
        return a.ru_utime + a.ru_stime + b.ru_utime + b.ru_stime

    def stop(self) -> dict:
        wall, cpu = time.perf_counter() - self.t0, self._cpu() - self.c0
        peak = max(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        return {"wallS": round(wall, 2), "cpuS": round(cpu, 2), "cpuCoresAvg": round(cpu / wall, 2) if wall else 0,
                "peakRssMb": round(peak / 1024, 1)}

"""Validate aggregate Docker memory capacity before starting/scaling this stack.

Never prints the interpolated Compose configuration (it contains credentials).
Build images first with `docker compose build api worker`.
"""
import argparse
import json
import re
import subprocess
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024**2


def host_free_memory():
    if os.name == "nt":
        import ctypes
        class Status(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                (name, ctypes.c_ulonglong) for name in ("total_physical", "available_physical", "total_page", "available_page", "total_virtual", "available_virtual", "extended")]
        status = Status()
        status.length = ctypes.sizeof(status)
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            raise OSError("Cannot read host memory")
        return status.available_physical
    info = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
    return int(info["MemAvailable"].split()[0]) * 1024


def command(*args):
    return subprocess.run(args, cwd=ROOT, check=True, capture_output=True, text=True).stdout


def size(value):
    if isinstance(value, (int, float)):
        return int(value)
    match = re.fullmatch(r"\s*([\d.]+)\s*([a-zA-Z]*)\s*", str(value))
    if not match:
        raise ValueError("Unrecognized memory quantity")
    multipliers = {"": 1, "b": 1, "k": 1024, "kb": 1000, "kib": 1024,
                   "m": MIB, "mb": 1000**2, "mib": MIB,
                   "g": 1024**3, "gb": 1000**3, "gib": 1024**3}
    return int(float(match[1]) * multipliers[match[2].lower()])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gpu", action="store_true", help="Include the optional GPU container")
    parser.add_argument("--cpu-workers", type=int, default=1)
    parser.add_argument("--reserve-mb", type=int, default=1024, help="Keep this memory free for Docker/other growth")
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    if args.cpu_workers < 1 or args.reserve_mb < 512:
        parser.error("At least one CPU worker and 512 MiB reserve are required")
    compose = ["docker", "compose"] + (["--profile", "gpu"] if args.gpu else [])
    config = json.loads(command(*compose, "config", "--format", "json"))
    selected = ["postgres", "object-store", "api", "worker"] + (["gpu-worker"] if args.gpu else [])
    limits = {name: size(config["services"][name]["mem_limit"]) for name in selected}
    limits["worker"] *= args.cpu_workers
    ceiling = int(command("docker", "info", "--format", "{{.MemTotal}}"))
    # Include current usage of unrelated containers; never stop or resize them.
    own_ids = set(command(*compose, "ps", "-q").split())
    other_usage = 0
    for line in command("docker", "stats", "--no-stream", "--format", "{{json .}}").splitlines():
        row = json.loads(line)
        if not any(identifier.startswith(row["ID"]) for identifier in own_ids):
            other_usage += size(row["MemUsage"].split("/")[0])
    total = sum(limits.values()) + other_usage + args.reserve_mb * MIB
    host_available = host_free_memory()
    safe = total <= ceiling and host_available >= 2048 * MIB
    print(json.dumps({"safe_to_start": safe, "configured_limits_mib": {k: round(v / MIB) for k, v in limits.items()},
        "other_container_usage_mib": round(other_usage / MIB), "reserve_mib": args.reserve_mb,
        "required_mib": round(total / MIB), "docker_ceiling_mib": round(ceiling / MIB),
        "host_available_mib": round(host_available / MIB)}, indent=2))
    if not safe:
        raise SystemExit("Insufficient memory headroom. Reduce worker count/budgets or increase Docker/host capacity before starting.")
    if not args.check_only:
        subprocess.run([*compose, "up", "-d", "--scale", f"worker={args.cpu_workers}", "--wait", "--wait-timeout", "180"], cwd=ROOT, check=True)


if __name__ == "__main__":
    main()

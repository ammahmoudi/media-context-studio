"""Conservative admission and runtime limits, including Linux cgroup memory.

Checkpoints are cooperative. Container/Windows job limits provide the hard boundary.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import psutil

MIB = 1024 ** 2


class ResourceExhausted(RuntimeError):
    """Do not retry this job automatically with the same resource budget."""


def number(name, default, minimum=0):
    value = float(os.getenv("VIDEO_CONTEXT_" + name, str(default)))
    if not minimum <= value < float("inf"):
        raise ValueError(f"Invalid resource setting: {name}")
    return value


def memory_budget(root=Path("/sys/fs/cgroup")):
    host = psutil.virtual_memory()
    result = {"available_mb": host.available / MIB, "limit_mb": host.total / MIB,
              "scope": "host"}
    # v2 and legacy v1. Ignore unlimited sentinel values.
    for limit_path, usage_path in [(root / "memory.max", root / "memory.current"),
                                   (root / "memory/memory.limit_in_bytes", root / "memory/memory.usage_in_bytes")]:
        try:
            limit, usage = int(limit_path.read_text()), int(usage_path.read_text())
            if 0 < limit < host.total:
                try:
                    stats = dict(line.split() for line in (usage_path.parent / "memory.stat").read_text().splitlines())
                    reclaimable = min(usage, int(stats.get("inactive_file", stats.get("total_inactive_file", 0))))
                except (OSError, ValueError):
                    reclaimable = 0
                result.update(available_mb=min(result["available_mb"], max(0, limit - usage + reclaimable) / MIB),
                              limit_mb=limit / MIB, scope="cgroup")
        except (OSError, ValueError):
            continue
        break
    return result


def gpu_memory():
    try:
        result = subprocess.run(["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
            "--format=csv,noheader,nounits", "--id=" + os.getenv("VIDEO_CONTEXT_GPU_INDEX", "0")],
            check=True, capture_output=True, text=True, timeout=2)
        used, total, utilization = map(float, result.stdout.strip().split(","))
        return {"gpu_memory_used_mb": used, "gpu_memory_total_mb": total,
                "gpu_memory_free_mb": total - used, "gpu_utilization_percent": utilization}
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}


def check_disk(path, extra_bytes=0):
    free = shutil.disk_usage(path).free
    required = number("MIN_FREE_DISK_MB", 2048) * MIB + extra_bytes
    if free < required:
        raise ResourceExhausted(f"Scratch disk needs {required / MIB:.0f} MiB free; {free / MIB:.0f} MiB available")


def validate_media(source):
    if source["duration_seconds"] > number("MAX_DURATION_SECONDS", 21600, 1):
        raise ResourceExhausted("Recording exceeds configured maximum duration")
    if source["width"] * source["height"] > number("MAX_VIDEO_PIXELS", 3840 * 2160, 1):
        raise ResourceExhausted("Video exceeds configured decoded pixel limit; resize before submitting")


class ResourceGuard:
    def __init__(self, data, device):
        self.data, self.device = Path(data), device
        self.data.mkdir(parents=True, exist_ok=True)
        self.peak_rss_mb = 0.0
        self.peak_gpu_mb = 0.0
        self.minimum_available_mb = float("inf")
        self.process = psutil.Process()
        self.last_gpu = {}
        self.last_gpu_at = 0

    def reset(self):
        self.peak_rss_mb = self.peak_gpu_mb = 0.0
        self.minimum_available_mb = float("inf")

    def sample(self, gpu=True):
        rss = self.process.memory_info().rss
        for child in self.process.children(recursive=True):
            try:
                rss += child.memory_info().rss
            except psutil.Error:
                pass
        memory = memory_budget()
        self.peak_rss_mb = max(self.peak_rss_mb, rss / MIB)
        self.minimum_available_mb = min(self.minimum_available_mb, memory["available_mb"])
        values = {"memory_available_mb": round(memory["available_mb"], 1),
                  "memory_limit_mb": round(memory["limit_mb"], 1), "memory_scope": memory["scope"],
                  "process_tree_memory_mb": round(rss / MIB, 1),
                  "peak_memory_mb": round(self.peak_rss_mb, 1),
                  "minimum_available_mb": round(self.minimum_available_mb, 1)}
        if gpu and self.device == "cuda":
            current = gpu_memory()
            if current:
                self.last_gpu, self.last_gpu_at = current, time.monotonic()
            age = time.monotonic() - self.last_gpu_at
            # Driver queries occasionally time out during native model loading.
            # Admission requires fresh data; running jobs tolerate ten seconds.
            if current or self.last_gpu and age <= 10:
                values.update(current or self.last_gpu)
            values.update(gpu_telemetry_age_seconds=round(age, 1), gpu_telemetry_cached=not bool(current))
            self.peak_gpu_mb = max(self.peak_gpu_mb, values.get("gpu_memory_used_mb", 0))
        values["peak_gpu_memory_mb"] = self.peak_gpu_mb
        return values

    def check(self, starting=False, gpu=True, model_loaded=False):
        values = self.sample(gpu)
        required = number("START_FREE_RAM_MB", 1536) if starting else number("MIN_FREE_RAM_MB", 384)
        if values["memory_available_mb"] < required:
            raise ResourceExhausted(f"RAM pressure: {values['memory_available_mb']:.0f} MiB free; {required:.0f} MiB required")
        if values["process_tree_memory_mb"] > number("WORKER_MAX_RAM_MB", 5120, 256):
            raise ResourceExhausted("Worker process tree exceeded its RAM budget")
        check_disk(self.data)
        if self.device == "cuda" and gpu:
            if "gpu_memory_free_mb" not in values or starting and values.get("gpu_telemetry_cached"):
                raise ResourceExhausted("GPU memory telemetry unavailable; refusing an unchecked GPU job")
            required_gpu = (number("WARM_FREE_VRAM_MB", 768) if model_loaded else number("START_FREE_VRAM_MB", 3072)) if starting else number("MIN_FREE_VRAM_MB", 256)
            if values["gpu_memory_free_mb"] < required_gpu:
                raise ResourceExhausted(f"GPU pressure: {values['gpu_memory_free_mb']:.0f} MiB free; {required_gpu:.0f} MiB required")
        return values


def is_resource_error(error):
    return isinstance(error, (MemoryError, ResourceExhausted)) or any(
        text in str(error).lower() for text in ("out of memory", "cuda_error_out_of_memory", "cannot allocate memory", "no space left on device"))


def install_native_memory_limit():
    """Windows commit limit includes child processes; retain the handle until exit."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                    "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x200  # JOB_OBJECT_LIMIT_JOB_MEMORY
    info.JobMemoryLimit = int(number("NATIVE_MEMORY_LIMIT_MB", 6144, 256) * MIB)
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.get_last_error()
        kernel.CloseHandle(handle)
        raise ctypes.WinError(error)
    return handle

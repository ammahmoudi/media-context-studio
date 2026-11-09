"""Register project-local CUDA DLLs without modifying the system installation."""
import ctypes
import os
import site
from pathlib import Path

_handles = []
_libraries = []
_prepared = False


def prepare_cuda():
    global _prepared
    if os.name != "nt" or _prepared:
        return
    directories = []
    for base in site.getsitepackages():
        for package in ("cublas", "cuda_nvrtc"):
            directory = Path(base) / "nvidia" / package / "bin"
            if directory.is_dir():
                directories.append(directory)
    cudnn_root = Path("C:/Program Files/NVIDIA/CUDNN")
    if cudnn_root.is_dir():
        directories.extend(sorted(cudnn_root.glob("*/bin/12*"), reverse=True)[:1])
    for directory in directories:
        _handles.append(os.add_dll_directory(str(directory)))
        os.environ["PATH"] = str(directory) + os.pathsep + os.environ["PATH"]
    # Eager loading also handles native libraries using LoadLibrary by basename.
    for filename in ("cublasLt64_12.dll", "cublas64_12.dll"):
        for directory in directories:
            path = directory / filename
            if path.is_file():
                _libraries.append(ctypes.WinDLL(str(path)))
                break
    _prepared = True

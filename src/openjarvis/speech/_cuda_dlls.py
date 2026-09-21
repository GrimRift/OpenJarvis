"""Make the pip-installed CUDA runtime findable by native code on Windows.

There is no system-wide CUDA Toolkit on this machine, only the
``nvidia-cublas-cu12`` / ``nvidia-cudnn-cu12`` runtime packages, whose DLLs
live under site-packages where Windows never looks. ``os.add_dll_directory``
alone does NOT fix this: CTranslate2 and onnxruntime resolve ``cublas64_12``
with a bare ``LoadLibrary``, which consults only the real ``PATH`` (confirmed
by trial -- add_dll_directory left it failing with "Library cublas64_12.dll is
not found"; prepending PATH fixed it outright). Must run before the native
module is imported, so every importer calls this at module top.
"""

from __future__ import annotations

import os

_done = False


def ensure_cuda_dll_dirs() -> list[str]:
    """Prepend the CUDA runtime DLL directories to PATH once. Returns them."""
    global _done
    if os.name != "nt":
        return []
    dirs: list[str] = []
    try:
        import nvidia
        import nvidia.cublas
        import nvidia.cudnn
    except ImportError:
        return dirs
    candidates = []
    for mod in (nvidia.cublas, nvidia.cudnn):
        # PEP 420 namespace packages (no __file__): the directory comes from
        # __path__ instead.
        pkg_dir = next(iter(mod.__path__), None)
        if pkg_dir:
            candidates.append(os.path.join(pkg_dir, "bin"))
    # CUDA 13 (onnxruntime-gpu 1.30+ needs it; CTranslate2 still links
    # CUDA 12) ships its DLLs one level up, under nvidia/cu13/bin/x86_64.
    # The two runtimes coexist: cublas64_12 and cublas64_13 are different
    # files. cuDNN 9 is one file set shared by both.
    nvidia_root = (
        next(iter(nvidia.__path__), None) if hasattr(nvidia, "__path__") else None
    )
    if nvidia_root:
        candidates.append(os.path.join(nvidia_root, "cu13", "bin", "x86_64"))
    for bin_dir in candidates:
        if os.path.isdir(bin_dir):
            dirs.append(bin_dir)
            if not _done:
                # Covers Python-level extension loading too.
                os.add_dll_directory(bin_dir)
    if dirs and not _done:
        current = os.environ.get("PATH", "")
        os.environ["PATH"] = os.pathsep.join(dirs) + os.pathsep + current
    _done = True
    return dirs

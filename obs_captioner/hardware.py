"""Hardware Detection, GPU Acceleration, and Process Memory Management.

Supports:
- AMD Radeon GPUs (Radeon RX 580, Vega, RDNA) via Microsoft DirectML and multi-threaded CPU.
- NVIDIA GeForce / RTX / GTX GPUs via CUDA and cuDNN.
- Apple Silicon (M1/M2/M3/M4) via Metal Performance Shaders (MPS).
- Memory lifecycle cleanup (garbage collection, PyTorch cache clearing, Windows working set trimming).
"""

from __future__ import annotations

import ctypes
import gc
import json
import logging
import os
import subprocess
import sys
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("obs_captioner.hardware")

# Cached GPU detection result
_CACHED_GPU_INFO: Optional[Dict[str, Any]] = None


def get_gpu_info(force_refresh: bool = False) -> Dict[str, Any]:
    """Detect available GPU hardware and capabilities across Windows, macOS, and Linux."""
    global _CACHED_GPU_INFO
    if _CACHED_GPU_INFO is not None and not force_refresh:
        return _CACHED_GPU_INFO

    info: Dict[str, Any] = {
        "vendor": "CPU",
        "name": "Standard CPU",
        "vram_mb": 0,
        "backend": "CPU",
        "is_cuda": False,
        "is_directml": False,
        "is_mps": False,
    }

    # 1. Check NVIDIA CUDA via PyTorch
    try:
        import torch
        if torch.cuda.is_available():
            info["vendor"] = "NVIDIA"
            info["name"] = torch.cuda.get_device_name(0)
            info["backend"] = "CUDA"
            info["is_cuda"] = True
            try:
                info["vram_mb"] = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
            except Exception:
                pass
            _CACHED_GPU_INFO = info
            return info
    except Exception:
        pass

    # 2. Check Apple Silicon MPS via PyTorch
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            info["vendor"] = "Apple"
            info["name"] = "Apple Silicon GPU (Metal)"
            info["backend"] = "MPS"
            info["is_mps"] = True
            _CACHED_GPU_INFO = info
            return info
    except Exception:
        pass

    # 3. Check DirectML via torch_directml
    try:
        import torch_directml
        if torch_directml.is_available():
            info["backend"] = "DirectML"
            info["is_directml"] = True
            try:
                name = torch_directml.device_name(0)
                if name:
                    info["name"] = name
                    if "amd" in name.lower() or "radeon" in name.lower():
                        info["vendor"] = "AMD"
                    elif "nvidia" in name.lower() or "geforce" in name.lower():
                        info["vendor"] = "NVIDIA"
                    elif "intel" in name.lower():
                        info["vendor"] = "Intel"
            except Exception:
                pass
    except Exception:
        pass

    # 4. On Windows, query Win32_VideoController via PowerShell if vendor is still CPU
    if sys.platform == "win32" and info["vendor"] == "CPU":
        try:
            cmd = [
                "powershell",
                "-NoProfile",
                "-Command",
                "Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterRAM | ConvertTo-Json",
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.0)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout.strip())
                items = data if isinstance(data, list) else [data]
                discrete_item = None
                for it in items:
                    c_name = str(it.get("Name", "")).strip()
                    if any(k in c_name.lower() for k in ["radeon", "amd", "geforce", "nvidia", "rtx", "gtx"]):
                        discrete_item = it
                        break
                selected = discrete_item or items[0]
                detected_name = str(selected.get("Name", "")).strip()
                if detected_name:
                    info["name"] = detected_name
                    lower = detected_name.lower()
                    if "radeon" in lower or "amd" in lower:
                        info["vendor"] = "AMD"
                        info["backend"] = "DirectML / OpenMP CPU"
                    elif "nvidia" in lower or "geforce" in lower:
                        info["vendor"] = "NVIDIA"
                        info["backend"] = "CUDA"
                    elif "intel" in lower:
                        info["vendor"] = "Intel"
                        info["backend"] = "DirectML / CPU"

                    raw_ram = selected.get("AdapterRAM")
                    if raw_ram and isinstance(raw_ram, (int, float)) and raw_ram > 0:
                        info["vram_mb"] = int(raw_ram / (1024 * 1024))
        except Exception as e:
            logger.debug(f"PowerShell GPU detection fallback: {e}")

    _CACHED_GPU_INFO = info
    return info


def get_ram_usage_mb() -> float:
    """Return current process physical RAM footprint (Working Set / RSS) in megabytes."""
    if sys.platform == "win32":
        try:
            class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
                _fields_ = [
                    ("cb", ctypes.c_ulong),
                    ("PageFaultCount", ctypes.c_ulong),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]
            counters = PROCESS_MEMORY_COUNTERS()
            counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return round(counters.WorkingSetSize / (1024.0 * 1024.0), 1)
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024.0 * 1024.0), 1)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            import resource
            return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0, 1)
        except Exception:
            pass
    return 0.0


def release_stt_memory(old_engine=None, log_details: bool = True) -> float:
    """Aggressively deallocate STT weights, run garbage collection, and trim physical OS working set RAM.

    Returns the number of MB freed.
    """
    initial_ram = get_ram_usage_mb()

    # 1. Release references on the old engine if provided
    if old_engine is not None:
        try:
            if hasattr(old_engine, "model"):
                old_engine.model = None
            if hasattr(old_engine, "tokenizer"):
                old_engine.tokenizer = None
            if hasattr(old_engine, "processor"):
                old_engine.processor = None
        except Exception:
            pass

    # 2. Force multiple generations of Python Garbage Collection
    try:
        gc.collect(0)
        gc.collect(1)
        gc.collect(2)
    except Exception:
        pass

    # 3. Flush PyTorch CUDA / DirectML caching allocators
    try:
        if "torch" in sys.modules:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
    except Exception:
        pass

    # 4. Flush OS Process Working Set on Windows
    if sys.platform == "win32":
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.psapi.EmptyWorkingSet(handle)
        except Exception:
            pass

    final_ram = get_ram_usage_mb()
    freed = max(0.0, round(initial_ram - final_ram, 1))
    if log_details:
        logger.info(f"🧹 [RAM PURGE] STT memory reclaimed: {initial_ram}MB -> {final_ram}MB ({freed}MB returned to OS).")
    return freed


def get_torch_device() -> Tuple[Any, str]:
    """Determine the optimal PyTorch compute device (DirectML / CUDA / MPS / CPU).

    Returns:
        Tuple[device_object_or_string, device_label_string]
    """
    # 1. DirectML (AMD Radeon RX 580, Intel Arc, etc. on Windows)
    try:
        import torch_directml
        if torch_directml.is_available():
            dml_device = torch_directml.device()
            gpu_info = get_gpu_info()
            label = f"DirectML GPU ({gpu_info.get('name', 'AMD Radeon')})"
            return dml_device, label
    except Exception:
        pass

    # 2. NVIDIA CUDA
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda", f"NVIDIA CUDA GPU ({torch.cuda.get_device_name(0)})"
    except Exception:
        pass

    # 3. Apple Silicon Metal (MPS)
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            return "mps", "Apple Silicon GPU (MPS)"
    except Exception:
        pass

    # 4. High-Performance CPU
    return "cpu", "CPU"

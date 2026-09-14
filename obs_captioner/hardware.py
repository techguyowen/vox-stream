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

    # 4. On Windows, query Display Class in Registry (instant 0ms lookup, no subprocess)
    if sys.platform == "win32" and info["vendor"] == "CPU":
        try:
            import winreg
            class_path = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, class_path) as class_key:
                num_subkeys = winreg.QueryInfoKey(class_key)[0]
                for i in range(num_subkeys):
                    try:
                        subkey_name = winreg.EnumKey(class_key, i)
                        if not subkey_name.isdigit():
                            continue
                        with winreg.OpenKey(class_key, subkey_name) as dev_key:
                            try:
                                desc, _ = winreg.QueryValueEx(dev_key, "DriverDesc")
                            except OSError:
                                continue
                            desc_lower = str(desc).lower()
                            if any(k in desc_lower for k in ["radeon", "amd", "geforce", "nvidia", "rtx", "gtx", "intel", "arc"]):
                                info["name"] = str(desc).strip()
                                if "radeon" in desc_lower or "amd" in desc_lower:
                                    info["vendor"] = "AMD"
                                    info["backend"] = "DirectML / OpenMP CPU"
                                elif "nvidia" in desc_lower or "geforce" in desc_lower:
                                    info["vendor"] = "NVIDIA"
                                    info["backend"] = "CUDA"
                                elif "intel" in desc_lower:
                                    info["vendor"] = "Intel"
                                    info["backend"] = "DirectML / CPU"

                                # Query VRAM from registry
                                for vram_key in ["HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"]:
                                    try:
                                        raw_vram, _ = winreg.QueryValueEx(dev_key, vram_key)
                                        if raw_vram and isinstance(raw_vram, (int, float)) and raw_vram > 0:
                                            info["vram_mb"] = int(raw_vram / (1024 * 1024))
                                            break
                                    except OSError:
                                        pass
                                break
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"winreg GPU detection: {e}")

        # 4b. PowerShell fallback if registry didn't find GPU or VRAM was 0
        if info["vendor"] == "CPU" or info["vram_mb"] == 0:
            try:
                cmd = [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -Property Name, AdapterRAM | ConvertTo-Json",
                ]
                res = subprocess.run(cmd, capture_output=True, text=True, timeout=2.5)
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
                    if detected_name and info["vendor"] == "CPU":
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

                    if info["vram_mb"] == 0:
                        raw_ram = selected.get("AdapterRAM")
                        if raw_ram and isinstance(raw_ram, (int, float)) and raw_ram > 0:
                            info["vram_mb"] = int(raw_ram / (1024 * 1024))
            except Exception as e:
                logger.debug(f"PowerShell GPU detection fallback: {e}")

    _CACHED_GPU_INFO = info
    return info


def get_ram_usage_mb() -> float:
    """Return current process physical RAM footprint (Working Set / RSS) in megabytes."""
    # 0. Fast psutil check if available (cross-platform, sub-millisecond)
    try:
        import psutil
        rss = psutil.Process().memory_info().rss
        if rss and rss > 0:
            return round(rss / (1024.0 * 1024.0), 1)
    except Exception:
        pass

    if sys.platform == "win32":
        # 1. Native Windows K32GetProcessMemoryInfo / GetProcessMemoryInfo via ctypes
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

            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel32.GetCurrentProcess()

            # On Windows 7/8/10/11, K32GetProcessMemoryInfo is in kernel32; psapi is fallback
            get_mem_info = getattr(kernel32, "K32GetProcessMemoryInfo", None)
            if get_mem_info is None and hasattr(ctypes.windll, "psapi"):
                get_mem_info = getattr(ctypes.windll.psapi, "GetProcessMemoryInfo", None)
            if get_mem_info is None:
                get_mem_info = getattr(kernel32, "GetProcessMemoryInfo", None)

            if get_mem_info is not None:
                get_mem_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), ctypes.c_ulong]
                get_mem_info.restype = ctypes.c_int
                counters = PROCESS_MEMORY_COUNTERS()
                counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
                if get_mem_info(handle, ctypes.byref(counters), counters.cb):
                    val = round(counters.WorkingSetSize / (1024.0 * 1024.0), 1)
                    if val > 0:
                        return val
        except Exception as e:
            logger.debug(f"Windows ctypes memory query error: {e}")

        # 2. Windows tasklist CLI fallback (built into every Windows installation)
        try:
            import csv, io, os, subprocess
            pid = os.getpid()
            CREATE_NO_WINDOW = 0x08000000
            out = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                creationflags=CREATE_NO_WINDOW,
                timeout=1.0,
            ).decode("utf-8", errors="ignore")
            reader = csv.reader(io.StringIO(out.strip()))
            for row in reader:
                if len(row) >= 5:
                    mem_str = row[4].replace(",", "").replace(" ", "").upper()
                    if mem_str.endswith("K"):
                        mem_str = mem_str[:-1]
                    val = round(float(mem_str) / 1024.0, 1)
                    if val > 0:
                        return val
        except Exception:
            pass
    elif sys.platform == "darwin":
        # Mach task_info accurately reports real-time physical resident size (RSS)
        try:
            class mach_task_basic_info(ctypes.Structure):
                _fields_ = [
                    ("virtual_size", ctypes.c_uint64),
                    ("resident_size", ctypes.c_uint64),
                    ("resident_size_max", ctypes.c_uint64),
                    ("user_time", ctypes.c_uint32 * 2),
                    ("system_time", ctypes.c_uint32 * 2),
                    ("policy", ctypes.c_int32),
                    ("suspend_count", ctypes.c_int32),
                ]
            libc = ctypes.CDLL(None)
            task_info = libc.task_info
            task_info.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            task_info.restype = ctypes.c_int
            mach_task_self = libc.mach_task_self
            mach_task_self.restype = ctypes.c_uint32
            info = mach_task_basic_info()
            count = ctypes.c_uint32(ctypes.sizeof(info) // 4)
            if task_info(mach_task_self(), 20, ctypes.byref(info), ctypes.byref(count)) == 0:
                return round(info.resident_size / (1024.0 * 1024.0), 1)
        except Exception:
            pass
        try:
            import os, subprocess
            out = subprocess.check_output(["ps", "-o", "rss=", "-p", str(os.getpid())], timeout=0.5)
            return round(float(out.strip()) / 1024.0, 1)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        # Real-time physical VmRSS from /proc/self/status
        try:
            with open("/proc/self/status", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        parts = line.split()
                        return round(float(parts[1]) / 1024.0, 1)
        except Exception:
            pass
        try:
            import os
            with open("/proc/self/statm", "r", encoding="utf-8") as f:
                pages = int(f.read().split()[1])
                return round((pages * os.sysconf("SC_PAGE_SIZE")) / (1024.0 * 1024.0), 1)
        except Exception:
            pass

    # Generic Unix fallback (peak maxrss)
    try:
        import resource
        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        scale = (1024.0 * 1024.0) if sys.platform == "darwin" else 1024.0
        return round(raw / scale, 1)
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
            if hasattr(old_engine, "recognizer"):
                old_engine.recognizer = None
            if hasattr(old_engine, "trim_memory"):
                old_engine.trim_memory()
        except Exception:
            pass

    # 2. Force multiple generations of Python Garbage Collection
    try:
        gc.collect(0)
        gc.collect(1)
        gc.collect(2)
    except Exception:
        pass

    # 3. Flush PyTorch CUDA / MPS / XPU caching allocators
    try:
        if "torch" in sys.modules:
            import torch
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.empty_cache()
                if hasattr(torch.cuda, "ipc_collect"):
                    torch.cuda.ipc_collect()
            if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                torch.mps.empty_cache()
            if hasattr(torch, "xpu") and hasattr(torch.xpu, "empty_cache"):
                torch.xpu.empty_cache()
    except Exception:
        pass

    # 4. Flush OS Process Working Set / Heap Allocator Pressure
    if sys.platform == "win32":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.GetCurrentProcess.restype = ctypes.c_void_p
            handle = kernel32.GetCurrentProcess()
            empty_ws = getattr(kernel32, "K32EmptyWorkingSet", None)
            if empty_ws is None and hasattr(ctypes.windll, "psapi"):
                empty_ws = getattr(ctypes.windll.psapi, "EmptyWorkingSet", None)
            if empty_ws is None:
                empty_ws = getattr(kernel32, "EmptyWorkingSet", None)
            if empty_ws:
                empty_ws.argtypes = [ctypes.c_void_p]
                empty_ws.restype = ctypes.c_int
                empty_ws(handle)
        except Exception:
            pass
    elif sys.platform == "darwin":
        try:
            libc = ctypes.CDLL(None)
            if hasattr(libc, "malloc_zone_pressure_relief"):
                if hasattr(libc, "malloc_default_zone"):
                    libc.malloc_default_zone.restype = ctypes.c_void_p
                    zone = libc.malloc_default_zone()
                else:
                    zone = None
                libc.malloc_zone_pressure_relief.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
                libc.malloc_zone_pressure_relief.restype = None
                libc.malloc_zone_pressure_relief(zone, 0)
        except Exception:
            pass
    elif sys.platform.startswith("linux"):
        try:
            libc = ctypes.CDLL("libc.so.6")
            if hasattr(libc, "malloc_trim"):
                libc.malloc_trim.argtypes = [ctypes.c_size_t]
                libc.malloc_trim.restype = ctypes.c_int
                libc.malloc_trim(0)
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


def get_local_ip() -> str:
    """Detect the machine's primary local network (LAN) IPv4 address."""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Connect to public DNS address without sending network packets
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "127.0.0.1"

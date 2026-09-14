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

# Cached GPU detection results
_CACHED_AVAILABLE_GPUS: Optional[List[Dict[str, Any]]] = None
_CACHED_GPU_INFO: Optional[Dict[str, Any]] = None


def _parse_vram_bytes(raw_val: Any) -> int:
    """Safely decode registry integer or binary memory size into bytes."""
    if isinstance(raw_val, (int, float)):
        return int(raw_val)
    if isinstance(raw_val, (bytes, bytearray)):
        try:
            return int.from_bytes(raw_val, byteorder="little")
        except Exception:
            return 0
    return 0


def get_available_gpus(force_refresh: bool = False) -> List[Dict[str, Any]]:
    """Enumerate all compute/GPU devices on the system, ranked best-first with CPU fallback."""
    global _CACHED_AVAILABLE_GPUS
    if _CACHED_AVAILABLE_GPUS is not None and not force_refresh:
        return _CACHED_AVAILABLE_GPUS

    gpus: List[Dict[str, Any]] = []

    # Check CUDA capability
    has_cuda = False
    cuda_name = None
    cuda_vram = 0
    try:
        from .engines.local_whisper import _setup_windows_cuda_dlls
        _setup_windows_cuda_dlls()
    except Exception:
        pass
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            has_cuda = True
    except Exception:
        pass
    try:
        import torch
        if torch.cuda.is_available():
            has_cuda = True
            cuda_name = torch.cuda.get_device_name(0)
            try:
                cuda_vram = int(torch.cuda.get_device_properties(0).total_memory / (1024 * 1024))
            except Exception:
                pass
    except Exception:
        pass

    # Check DirectML capability
    has_directml = False
    try:
        import torch_directml
        if torch_directml.is_available():
            has_directml = True
    except Exception:
        pass
    if not has_directml:
        try:
            import onnxruntime
            if "DmlExecutionProvider" in onnxruntime.get_available_providers():
                has_directml = True
        except Exception:
            pass

    # Check Apple Silicon MPS
    has_mps = False
    try:
        import torch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            has_mps = True
    except Exception:
        pass

    # 1. Windows Hardware Display Adapters
    if sys.platform == "win32":
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
                            desc_str = str(desc).strip()
                            desc_lower = desc_str.lower()
                            if any(k in desc_lower for k in ["radeon", "amd", "geforce", "nvidia", "rtx", "gtx", "intel", "arc"]):
                                vram_mb = 0
                                for vram_key in ["HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"]:
                                    try:
                                        raw_vram, _ = winreg.QueryValueEx(dev_key, vram_key)
                                        parsed = _parse_vram_bytes(raw_vram)
                                        if parsed > 0:
                                            vram_mb = int(parsed / (1024 * 1024))
                                            break
                                    except OSError:
                                        pass

                                is_discrete = any(k in desc_lower for k in ["geforce", "nvidia", "rtx", "gtx", "radeon", "amd"])
                                if "nvidia" in desc_lower or "geforce" in desc_lower or "rtx" in desc_lower or "gtx" in desc_lower:
                                    vendor = "NVIDIA"
                                    is_cuda = has_cuda
                                    backend = "CUDA" if is_cuda else "CPU"
                                elif "radeon" in desc_lower or "amd" in desc_lower:
                                    vendor = "AMD"
                                    is_cuda = False
                                    backend = "DirectML" if has_directml else "DirectML / OpenMP CPU"
                                elif "intel" in desc_lower or "arc" in desc_lower:
                                    vendor = "Intel"
                                    is_cuda = False
                                    backend = "DirectML / CPU"
                                else:
                                    vendor = "Other"
                                    is_cuda = False
                                    backend = "CPU"

                                dev_id = f"gpu_{vendor.lower()}_{len(gpus)}"
                                gpus.append({
                                    "id": dev_id,
                                    "device_id": dev_id,
                                    "name": desc_str,
                                    "vendor": vendor,
                                    "vram_mb": vram_mb or (cuda_vram if vendor == "NVIDIA" else 0),
                                    "backend": backend,
                                    "is_cuda": is_cuda,
                                    "is_directml": has_directml or vendor in ("AMD", "Intel"),
                                    "is_mps": False,
                                    "is_discrete": is_discrete,
                                    "recommended": False,
                                })
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"winreg available GPUs detection: {e}")

        # Fallback to PowerShell if registry was empty
        if not gpus:
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
                    for it in items:
                        c_name = str(it.get("Name", "")).strip()
                        c_lower = c_name.lower()
                        if any(k in c_lower for k in ["radeon", "amd", "geforce", "nvidia", "rtx", "gtx", "intel", "arc"]):
                            is_discrete = any(k in c_lower for k in ["geforce", "nvidia", "rtx", "gtx", "radeon", "amd"])
                            vendor = "NVIDIA" if ("nvidia" in c_lower or "geforce" in c_lower) else ("AMD" if ("radeon" in c_lower or "amd" in c_lower) else "Intel")
                            raw_ram = it.get("AdapterRAM", 0) or 0
                            vram_mb = int(raw_ram / (1024 * 1024)) if raw_ram > 0 else 0
                            dev_id = f"gpu_{vendor.lower()}_{len(gpus)}"
                            gpus.append({
                                "id": dev_id,
                                "device_id": dev_id,
                                "name": c_name,
                                "vendor": vendor,
                                "vram_mb": vram_mb,
                                "backend": "CUDA" if (vendor == "NVIDIA" and has_cuda) else "DirectML / CPU",
                                "is_cuda": vendor == "NVIDIA" and has_cuda,
                                "is_directml": True,
                                "is_mps": False,
                                "is_discrete": is_discrete,
                                "recommended": False,
                            })
            except Exception as e:
                logger.debug(f"PowerShell available GPUs fallback: {e}")

    # 2. Apple Silicon
    elif sys.platform == "darwin" and has_mps:
        gpus.append({
            "id": "gpu_apple_0",
            "device_id": "gpu_apple_0",
            "name": "Apple Silicon GPU (Metal)",
            "vendor": "Apple",
            "vram_mb": 0,
            "backend": "MPS",
            "is_cuda": False,
            "is_directml": False,
            "is_mps": True,
            "is_discrete": True,
            "recommended": True,
        })

    # 3. Linux with CUDA
    elif sys.platform.startswith("linux") and has_cuda:
        try:
            import torch
            num_cuda = torch.cuda.device_count() if torch.cuda.is_available() else 1
            for i in range(num_cuda):
                d_name = torch.cuda.get_device_name(i) if torch.cuda.is_available() else (cuda_name or "NVIDIA CUDA GPU")
                d_vram = int(torch.cuda.get_device_properties(i).total_memory / (1024 * 1024)) if torch.cuda.is_available() else cuda_vram
                gpus.append({
                    "id": f"gpu_nvidia_{i}",
                    "device_id": f"gpu_nvidia_{i}",
                    "name": d_name,
                    "vendor": "NVIDIA",
                    "vram_mb": d_vram,
                    "backend": "CUDA",
                    "is_cuda": True,
                    "is_directml": False,
                    "is_mps": False,
                    "is_discrete": True,
                    "recommended": (i == 0),
                })
        except Exception:
            gpus.append({
                "id": "gpu_nvidia_0",
                "device_id": "gpu_nvidia_0",
                "name": cuda_name or "NVIDIA CUDA GPU",
                "vendor": "NVIDIA",
                "vram_mb": cuda_vram,
                "backend": "CUDA",
                "is_cuda": True,
                "is_directml": False,
                "is_mps": False,
                "is_discrete": True,
                "recommended": True,
            })

    # Deduplicate by name if needed
    unique_gpus = []
    seen_names = set()
    for g in gpus:
        if g["name"] not in seen_names:
            seen_names.add(g["name"])
            unique_gpus.append(g)

    # Sort so discrete GPUs with highest VRAM come first
    unique_gpus.sort(key=lambda g: (1 if g["is_discrete"] else 0, g["vram_mb"]), reverse=True)
    if unique_gpus:
        unique_gpus[0]["recommended"] = True

    # 4. CPU Option is always available as safe fallback
    cpu_option = {
        "id": "cpu",
        "device_id": "cpu",
        "name": "CPU Only (Safe Mode / Multi-core Int8)",
        "vendor": "CPU",
        "vram_mb": 0,
        "backend": "CPU",
        "is_cuda": False,
        "is_directml": False,
        "is_mps": False,
        "is_discrete": False,
        "recommended": False if unique_gpus else True,
    }

    result = unique_gpus + [cpu_option]
    _CACHED_AVAILABLE_GPUS = result
    return result


def get_gpu_info(preferred_gpu: Optional[str] = "auto", force_refresh: bool = False) -> Dict[str, Any]:
    """Detect and return compute/GPU hardware info, respecting preferred_gpu setting.
    
    Defaults to the best recommended discrete GPU when preferred_gpu is 'auto' or empty.
    """
    global _CACHED_GPU_INFO
    pref = (preferred_gpu or "auto").strip().lower()
    
    # If using cached result with identical preference and not force_refresh
    if _CACHED_GPU_INFO is not None and not force_refresh and _CACHED_GPU_INFO.get("preferred_gpu") == pref:
        return _CACHED_GPU_INFO

    all_gpus = get_available_gpus(force_refresh=force_refresh)

    selected = None
    if pref in ("cpu", "none"):
        selected = next((g for g in all_gpus if g["id"] == "cpu"), None)
    elif pref not in ("auto", "default", ""):
        # Match by ID, vendor, or substring in name
        for g in all_gpus:
            if g["id"].lower() == pref or g["vendor"].lower() == pref or pref in g["name"].lower():
                selected = g
                break

    if selected is None:
        # Auto mode: select the best recommended GPU
        selected = next((g for g in all_gpus if g.get("recommended")), all_gpus[0])

    res = dict(selected)
    res["id"] = selected.get("id") or selected.get("device_id")
    res["device_id"] = selected.get("device_id") or selected.get("id")
    res["preferred_gpu"] = preferred_gpu or "auto"
    res["available_gpus"] = all_gpus
    _CACHED_GPU_INFO = res
    return res


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


def get_torch_device(preferred_gpu: Optional[str] = "auto") -> Tuple[Any, str]:
    """Determine compute device (CUDA / DirectML / MPS / CPU) respecting preferred_gpu setting.

    Returns:
        Tuple[device_object_or_string, device_label_string]
    """
    pref = (preferred_gpu or "auto").strip().lower()
    if pref in ("cpu", "none"):
        return "cpu", "Standard CPU (Safe Mode)"

    gpu_info = get_gpu_info(preferred_gpu)

    # 1. NVIDIA CUDA
    if gpu_info.get("is_cuda"):
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda", f"NVIDIA CUDA GPU ({torch.cuda.get_device_name(0)})"
        except Exception:
            pass
        return "cuda", f"NVIDIA CUDA GPU ({gpu_info.get('name', 'NVIDIA')})"

    # 2. DirectML (AMD Radeon, Intel Arc, etc. on Windows)
    if gpu_info.get("is_directml"):
        try:
            import torch_directml
            if torch_directml.is_available():
                dml_device = torch_directml.device()
                label = f"DirectML GPU ({gpu_info.get('name', 'DirectML')})"
                return dml_device, label
        except Exception:
            pass

    # 3. Apple Silicon Metal (MPS)
    if gpu_info.get("is_mps"):
        try:
            import torch
            if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
                return "mps", "Apple Silicon GPU (MPS)"
        except Exception:
            pass

    # 4. CPU Fallback
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

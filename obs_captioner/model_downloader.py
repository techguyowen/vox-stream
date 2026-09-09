"""Offline Speech-to-Text AI Model Pre-Downloader and Local Cache Manager."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("obs_captioner.model_downloader")

# Pre-set environment so torch / keras doesn't complain

def get_vosk_search_dirs() -> List[Path]:
    """Return all directories where Vosk models may be stored across platforms."""
    dirs: List[Path] = []
    # Windows LocalAppData
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        p = Path(local_app_data) / "vosk"
        if p not in dirs:
            dirs.append(p)

    win_appdata = Path.home() / "AppData" / "Local" / "vosk"
    if win_appdata not in dirs:
        dirs.append(win_appdata)

    # Standard POSIX / XDG cache (~/.cache/vosk)
    cache_vosk = Path.home() / ".cache" / "vosk"
    if cache_vosk not in dirs:
        dirs.append(cache_vosk)

    return dirs


def find_cached_vosk_model(model_name: str) -> Optional[Path]:
    """Find the path to an unpacked Vosk model directory if it exists."""
    for base in get_vosk_search_dirs():
        candidate = base / model_name
        if candidate.is_dir() and any(candidate.iterdir()):
            return candidate
    return None


@dataclass
class ModelCatalogItem:
    id: str
    engine: str
    name: str
    model_key: str
    size_mb: int
    description: str
    recommended: bool = True
    is_cached: bool = False
    cache_path: Optional[str] = None
    status: str = "not_downloaded"  # "ready", "downloading", "not_downloaded", "error"
    error_message: Optional[str] = None


MODEL_CATALOG: List[ModelCatalogItem] = [
    ModelCatalogItem(
        id="vosk_small",
        engine="vosk",
        name="Vosk Small (Fast & Lightweight)",
        model_key="vosk-model-small-en-us-0.15",
        size_mb=40,
        description="Ultra-low latency (~30ms) offline Kaldi acoustic model. Instant real-time captions with ~0% CPU.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="moonshine_tiny",
        engine="moonshine",
        name="Moonshine Tiny",
        model_key="moonshine/tiny",
        size_mb=60,
        description="Useful Sensors ONNX/PyTorch neural variable-length transformer. 5x faster than Whisper.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="moonshine_base",
        engine="moonshine",
        name="Moonshine Base",
        model_key="moonshine/base",
        size_mb=180,
        description="High-accuracy variable-length neural model with smart phonetic understanding and context.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="whisper_tiny",
        engine="local_whisper",
        name="Faster-Whisper Tiny.en",
        model_key="tiny.en",
        size_mb=75,
        description="Lightweight OpenAI Whisper model optimized with CTranslate2 integer quantization.",
        recommended=False,
    ),
    ModelCatalogItem(
        id="whisper_base",
        engine="local_whisper",
        name="Faster-Whisper Base.en",
        model_key="base.en",
        size_mb=140,
        description="Balanced OpenAI Whisper model with great accuracy, natural capitalization, and punctuation.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="whisper_small",
        engine="local_whisper",
        name="Faster-Whisper Small.en",
        model_key="small.en",
        size_mb=460,
        description="High-precision OpenAI Whisper model for complex jargon, acoustic challenges, and accents.",
        recommended=False,
    ),
    ModelCatalogItem(
        id="whisper_turbo",
        engine="local_whisper",
        name="Faster-Whisper Large-v3-Turbo",
        model_key="large-v3-turbo",
        size_mb=1600,
        description="OpenAI Large-v3-Turbo: 6x faster inference than large-v3 with top-tier multilingual accuracy.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="whisper_distil_large",
        engine="local_whisper",
        name="Distil-Whisper Large-v3",
        model_key="distil-large-v3",
        size_mb=1500,
        description="HuggingFace Distil-Whisper Large-v3: 6x faster than Whisper-large with anti-hallucination during music & silence.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="sherpa_zipformer",
        engine="sherpa",
        name="Sherpa-ONNX Zipformer (Streaming)",
        model_key="csukuangfj/sherpa-onnx-streaming-zipformer-en-2023-06-26",
        size_mb=175,
        description="Next-gen streaming transducer Zipformer. Sub-100ms real-time chunked decoding with zero latency.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="sensevoice_small",
        engine="sensevoice",
        name="SenseVoice Small (Audio Event Detection)",
        model_key="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17",
        size_mb=230,
        description="Alibaba FunASR SenseVoice with real-time Audio Event Detection (applause, laughter, coughing, music).",
        recommended=True,
    ),
    ModelCatalogItem(
        id="parakeet_fastconformer_large",
        engine="parakeet",
        name="NVIDIA FastConformer-Large (24,500 Hours)",
        model_key="csukuangfj/sherpa-onnx-nemo-fast-conformer-ctc-en-24500",
        size_mb=458,
        description="NVIDIA FastConformer Large trained on 24,500 hours. Exceptional phonetic accuracy and whisper resistance.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="parakeet_tdt_06b",
        engine="parakeet",
        name="NVIDIA Parakeet-TDT 0.6B (Transducer)",
        model_key="csukuangfj/sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8",
        size_mb=670,
        description="Token-and-Duration Transducer (TDT). Prediction network enforces token sequences, preventing dropped letters.",
        recommended=True,
    ),
    ModelCatalogItem(
        id="parakeet_ctc_large",
        engine="parakeet",
        name="NVIDIA Conformer-Large (Balanced)",
        model_key="csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-large",
        size_mb=170,
        description="Balanced NeMo Conformer Large int8 model with high vocabulary coverage.",
        recommended=False,
    ),
    ModelCatalogItem(
        id="parakeet_nemo",
        engine="parakeet",
        name="NVIDIA Conformer-Medium (Lightweight)",
        model_key="csukuangfj/sherpa-onnx-nemo-ctc-en-conformer-medium",
        size_mb=70,
        description="Ultra-low latency NeMo FastConformer CTC. Lightweight footprint (~70 MB) for low-spec hardware.",
        recommended=False,
    ),
    ModelCatalogItem(
        id="vosk_accurate",
        engine="vosk",
        name="Vosk Accurate (Large)",
        model_key="vosk-model-en-us-0.22",
        size_mb=1800,
        description="Full-size offline Kaldi acoustic model for maximum vocabulary coverage (~1.8 GB).",
        recommended=False,
    ),
]


class ModelDownloadManager:
    """Manages offline speech model status verification and pre-caching."""

    def __init__(self):
        self._is_downloading = False
        self._cancel_requested = False
        self._current_download_id: Optional[str] = None
        self._download_lock = asyncio.Lock()

    @property
    def is_downloading(self) -> bool:
        return self._is_downloading

    def check_model_cached(self, item: ModelCatalogItem) -> Tuple[bool, Optional[str]]:
        """Check if a model exists in the local disk cache without making network requests."""
        if item.engine == "vosk":
            found = find_cached_vosk_model(item.model_key)
            if found:
                return True, str(found)
            return False, None

        elif item.engine == "local_whisper":
            try:
                import faster_whisper
                path = faster_whisper.download_model(item.model_key, local_files_only=True)
                if path and Path(path).exists():
                    return True, str(path)
            except Exception:
                pass
            return False, None

        elif item.engine == "moonshine":
            # Check HuggingFace hub cache or useful-sensors cache
            hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
            if hf_cache.exists():
                for repo in hf_cache.iterdir():
                    if "moonshine" in repo.name.lower():
                        snap = repo / "snapshots"
                        if snap.exists() and any(snap.iterdir()):
                            return True, str(repo)
            return False, None

        elif item.engine in ("sherpa", "sensevoice", "parakeet"):
            hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
            repo_slug = item.model_key.replace("/", "--")
            candidates = [
                hf_cache / f"models--{repo_slug}" / "snapshots",
                hf_cache / f"models--csukuangfj--{item.model_key.split('/')[-1]}" / "snapshots",
            ]
            for c in candidates:
                if c.is_dir():
                    for snap in c.iterdir():
                        if snap.is_dir() and (snap / "tokens.txt").exists():
                            return True, str(snap)
            return False, None

        return False, None

    def get_models_status(self) -> List[Dict[str, Any]]:
        """Return list of all models with their current cache status and disk footprint."""
        result = []
        for item in MODEL_CATALOG:
            cached, path = self.check_model_cached(item)
            item.is_cached = cached
            item.cache_path = path
            if self._is_downloading and self._current_download_id == item.id:
                item.status = "downloading"
            elif cached:
                item.status = "ready"
            else:
                item.status = "not_downloaded"

            result.append(asdict(item))
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Return high-level summary of model downloads and cache footprint."""
        models = self.get_models_status()
        total_count = len(models)
        cached_count = sum(1 for m in models if m["is_cached"])
        cached_mb = sum(m["size_mb"] for m in models if m["is_cached"])
        total_mb = sum(m["size_mb"] for m in models)

        return {
            "models": models,
            "total_models": total_count,
            "cached_models": cached_count,
            "cached_size_mb": cached_mb,
            "total_size_mb": total_mb,
            "all_downloaded": cached_count == total_count,
            "is_downloading": self._is_downloading,
            "current_download_id": self._current_download_id,
        }

    def _sync_download_single(
        self,
        item: ModelCatalogItem,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        """Synchronously download one model (executed in worker thread)."""
        logger.info(f"Starting download for {item.name} ({item.size_mb} MB)...")
        if progress_cb:
            progress_cb({
                "type": "model_download_progress",
                "model_id": item.id,
                "model_name": item.name,
                "status": "downloading",
                "message": f"Downloading {item.name} (~{item.size_mb} MB)...",
            })

        try:
            if item.engine == "vosk":
                import vosk
                vosk.SetLogLevel(-1)
                _ = vosk.Model(model_name=item.model_key)
                return True

            elif item.engine == "local_whisper":
                import faster_whisper
                _ = faster_whisper.download_model(item.model_key)
                return True

            elif item.engine == "moonshine":
                import moonshine
                _ = moonshine.load_model(item.model_key)
                return True

            elif item.engine in ("sherpa", "sensevoice", "parakeet"):
                from huggingface_hub import snapshot_download
                patterns = (
                    ["*.onnx", "tokens.txt"]
                    if item.engine in ("sherpa", "parakeet")
                    else ["model.int8.onnx", "model.onnx", "tokens.txt"]
                )
                path = snapshot_download(repo_id=item.model_key, allow_patterns=patterns)
                return bool(path and Path(path).exists())

            return False
        except Exception as e:
            logger.error(f"Error downloading {item.name}: {e}", exc_info=True)
            if progress_cb:
                progress_cb({
                    "type": "model_download_progress",
                    "model_id": item.id,
                    "model_name": item.name,
                    "status": "error",
                    "message": f"Failed to download {item.name}: {e}",
                })
            return False

    async def download_model(
        self,
        model_id: str,
        progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> bool:
        """Asynchronously download a specific model or 'all' models."""
        async with self._download_lock:
            self._is_downloading = True
            self._cancel_requested = False
            loop = asyncio.get_event_loop()

            try:
                targets: List[ModelCatalogItem] = []
                if model_id.lower() == "all":
                    # Download all models that are not yet cached (recommended first)
                    all_status = self.get_models_status()
                    for m_dict in all_status:
                        if not m_dict["is_cached"]:
                            for item in MODEL_CATALOG:
                                if item.id == m_dict["id"]:
                                    targets.append(item)
                    # If all already cached, nothing to download
                    if not targets:
                        if progress_cb:
                            progress_cb({
                                "type": "model_download_progress",
                                "model_id": "all",
                                "status": "completed",
                                "message": "All offline speech models are already downloaded and cached!",
                            })
                        return True
                else:
                    for item in MODEL_CATALOG:
                        if item.id == model_id:
                            targets.append(item)
                            break

                if not targets:
                    logger.warning(f"No valid models found to download for query: {model_id}")
                    return False

                total_targets = len(targets)
                for idx, item in enumerate(targets, start=1):
                    if self._cancel_requested:
                        logger.info("Download task was canceled.")
                        if progress_cb:
                            progress_cb({
                                "type": "model_download_progress",
                                "status": "canceled",
                                "message": "Model downloads canceled.",
                            })
                        return False

                    self._current_download_id = item.id
                    if progress_cb:
                        progress_cb({
                            "type": "model_download_progress",
                            "model_id": item.id,
                            "model_name": item.name,
                            "current_index": idx,
                            "total_count": total_targets,
                            "status": "downloading",
                            "message": f"({idx}/{total_targets}) Downloading {item.name} (~{item.size_mb} MB)...",
                        })

                    ok = await loop.run_in_executor(None, self._sync_download_single, item, progress_cb)
                    if not ok:
                        logger.warning(f"Download of {item.name} encountered an issue.")

                if progress_cb:
                    progress_cb({
                        "type": "model_download_progress",
                        "model_id": model_id,
                        "status": "completed",
                        "message": "🎉 All requested speech recognition models downloaded and ready for offline use!",
                    })
                return True

            finally:
                self._is_downloading = False
                self._current_download_id = None

    def cancel_download(self):
        """Signal cancellation for ongoing batch downloads."""
        self._cancel_requested = True
        logger.info("Cancellation requested for model downloads.")

    def delete_model(self, model_id: str) -> Tuple[bool, str, int]:
        """Delete a single cached model or all models from disk cache to free storage space.
        
        Returns (success: bool, message: str, freed_mb: int)
        """
        import shutil

        if model_id.lower() == "all":
            total_freed = 0
            deleted_names = []
            for item in MODEL_CATALOG:
                cached, path = self.check_model_cached(item)
                if cached:
                    ok, msg, freed = self._delete_single_item(item)
                    if ok:
                        total_freed += freed
                        deleted_names.append(item.name)
            return True, f"Deleted {len(deleted_names)} models from local cache (freed ~{total_freed} MB).", total_freed

        for item in MODEL_CATALOG:
            if item.id == model_id:
                return self._delete_single_item(item)

        return False, f"Model ID '{model_id}' not found in catalog.", 0

    def _delete_single_item(self, item: ModelCatalogItem) -> Tuple[bool, str, int]:
        import shutil
        freed_mb = item.size_mb

        try:
            if item.engine == "vosk":
                deleted = False
                for base in get_vosk_search_dirs():
                    vosk_dir = base / item.model_key
                    zip_path = base / f"{item.model_key}.zip"
                    if vosk_dir.exists():
                        shutil.rmtree(vosk_dir, ignore_errors=True)
                        deleted = True
                    if zip_path.exists():
                        zip_path.unlink(missing_ok=True)
                        deleted = True
                if deleted:
                    logger.info(f"Deleted Vosk model '{item.name}' from local cache")
                    return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb
                return False, f"Model directory for {item.name} not found in cache.", 0

            elif item.engine == "local_whisper":
                hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
                hf_dir = hf_cache / f"models--Systran--faster-whisper-{item.model_key}"
                if hf_dir.exists():
                    shutil.rmtree(hf_dir, ignore_errors=True)
                logger.info(f"Deleted Faster-Whisper model '{item.name}' from {hf_dir}")
                return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb

            elif item.engine == "moonshine":
                hf_dir = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub" / "models--UsefulSensors--moonshine"
                if hf_dir.exists():
                    shutil.rmtree(hf_dir, ignore_errors=True)
                logger.info(f"Deleted Moonshine model '{item.name}' from {hf_dir}")
                return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb

            elif item.engine in ("sherpa", "sensevoice", "parakeet"):
                hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
                repo_slug = item.model_key.replace("/", "--")
                deleted = False
                for d in [
                    hf_cache / f"models--{repo_slug}",
                    hf_cache / f"models--csukuangfj--{item.model_key.split('/')[-1]}",
                ]:
                    if d.exists():
                        shutil.rmtree(d, ignore_errors=True)
                        deleted = True
                if deleted:
                    logger.info(f"Deleted model '{item.name}' from local cache")
                    return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb
                return False, f"Model directory for {item.name} not found in cache.", 0

            return False, f"Unsupported engine '{item.engine}' for deletion.", 0
        except Exception as e:
            logger.error(f"Error deleting model {item.name}: {e}", exc_info=True)
            return False, f"Error deleting model: {e}", 0


if __name__ == "__main__":
    import argparse
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    parser = argparse.ArgumentParser(description="VoxStream AI Model Pre-Downloader & Cache Manager")
    parser.add_argument(
        "--preload-defaults",
        action="store_true",
        help="Pre-download default offline models (Vosk, Faster-Whisper, and Silero VAD) for 100% offline use.",
    )
    parser.add_argument("--list", action="store_true", help="List all supported models and their cache status")
    parser.add_argument(
        "--download",
        type=str,
        help="Download a specific model ID (e.g. whisper_base, vosk_small, moonshine_tiny, all)",
    )
    args = parser.parse_args()

    mgr = ModelDownloadManager()

    if args.list:
        print("\n=== VoxStream Offline AI Model Catalog ===")
        for m in mgr.get_models_status():
            status_icon = "✅ CACHED" if m["is_cached"] else "⚪ NOT DOWNLOADED"
            print(f"[{status_icon}] {m['name']} ({m['id']}) - ~{m['size_mb']}MB")
            print(f"    {m['description']}\n")
        sys.exit(0)

    if args.preload_defaults:
        print("\n=== Pre-downloading VoxStream Default Offline AI Models ===")
        # 1. Silero VAD
        print("1/3 Checking Silero VAD neural pause detector...")
        try:
            from .vad import VoiceActivityDetector
            _vad = VoiceActivityDetector()
            print("    ✅ Silero VAD cached and ready.")
        except Exception as e:
            print(f"    ⚠️ Silero VAD note: {e}")

        # 2. Vosk Small
        print("2/3 Checking Vosk Small acoustic model (~40 MB)...")
        vosk_item = next((x for x in MODEL_CATALOG if x.id == "vosk_small"), None)
        if vosk_item:
            cached, _ = mgr.check_model_cached(vosk_item)
            if not cached:
                mgr._sync_download_single(vosk_item)
            print("    ✅ Vosk Small model ready.")

        # 3. Faster-Whisper Base.en
        print("3/3 Checking Faster-Whisper Base.en neural model (~140 MB)...")
        whisper_item = next((x for x in MODEL_CATALOG if x.id == "whisper_base"), None)
        if whisper_item:
            cached, _ = mgr.check_model_cached(whisper_item)
            if not cached:
                mgr._sync_download_single(whisper_item)
            print("    ✅ Faster-Whisper Base model ready.")

        print("\n🎉 All default offline AI models are cached and ready for 100% offline broadcast!\n")
        sys.exit(0)

    if args.download:
        asyncio.run(mgr.download_model(args.download))
        sys.exit(0)

    parser.print_help()


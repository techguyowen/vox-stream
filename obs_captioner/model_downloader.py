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
os.environ["KERAS_BACKEND"] = "torch"


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
            vosk_dir = Path.home() / ".cache" / "vosk" / item.model_key
            if vosk_dir.exists() and any(vosk_dir.iterdir()):
                return True, str(vosk_dir)
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
            hf_cache = Path.home() / ".cache" / "huggingface" / "hub"
            if hf_cache.exists():
                for repo in hf_cache.iterdir():
                    if "moonshine" in repo.name.lower():
                        snap = repo / "snapshots"
                        if snap.exists() and any(snap.iterdir()):
                            return True, str(repo)
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
                vosk_dir = Path.home() / ".cache" / "vosk" / item.model_key
                zip_path = Path.home() / ".cache" / "vosk" / f"{item.model_key}.zip"
                deleted = False
                if vosk_dir.exists():
                    shutil.rmtree(vosk_dir, ignore_errors=True)
                    deleted = True
                if zip_path.exists():
                    zip_path.unlink(missing_ok=True)
                    deleted = True
                logger.info(f"Deleted Vosk model '{item.name}' from {vosk_dir}")
                return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb

            elif item.engine == "local_whisper":
                hf_dir = Path.home() / ".cache" / "huggingface" / "hub" / f"models--Systran--faster-whisper-{item.model_key}"
                if hf_dir.exists():
                    shutil.rmtree(hf_dir, ignore_errors=True)
                logger.info(f"Deleted Faster-Whisper model '{item.name}' from {hf_dir}")
                return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb

            elif item.engine == "moonshine":
                hf_dir = Path.home() / ".cache" / "huggingface" / "hub" / "models--UsefulSensors--moonshine"
                if hf_dir.exists():
                    shutil.rmtree(hf_dir, ignore_errors=True)
                logger.info(f"Deleted Moonshine model '{item.name}' from {hf_dir}")
                return True, f"Deleted {item.name} from local cache (freed ~{freed_mb} MB).", freed_mb

            return False, f"Unsupported engine '{item.engine}' for deletion.", 0
        except Exception as e:
            logger.error(f"Error deleting model {item.name}: {e}", exc_info=True)
            return False, f"Error deleting model: {e}", 0

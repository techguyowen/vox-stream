"""Meta NLLB-200 Offline Neural Translation Engine.

Provides 100% local, offline neural machine translation across 200 languages
using CTranslate2 INT8 quantization and SentencePiece tokenization.
Zero API keys, zero cloud latency, zero external internet required.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("obs_captioner.engines.nllb")

# Mapping of standard ISO 639-1 / 639-2 codes to FLORES-200 language tokens
ISO_TO_NLLB: Dict[str, str] = {
    # Common VoxStream Languages
    "en": "eng_Latn",
    "eng": "eng_Latn",
    "es": "spa_Latn",
    "spa": "spa_Latn",
    "fr": "fra_Latn",
    "fra": "fra_Latn",
    "fre": "fra_Latn",
    "de": "deu_Latn",
    "deu": "deu_Latn",
    "ger": "deu_Latn",
    "pt": "por_Latn",
    "por": "por_Latn",
    "it": "ita_Latn",
    "ita": "ita_Latn",
    "zh": "zho_Hans",
    "zh-cn": "zho_Hans",
    "zh_cn": "zho_Hans",
    "zh-hans": "zho_Hans",
    "zh-tw": "zho_Hant",
    "zh_tw": "zho_Hant",
    "zh-hant": "zho_Hant",
    "zho": "zho_Hans",
    "ja": "jpn_Jpan",
    "jpn": "jpn_Jpan",
    "ko": "kor_Hang",
    "kor": "kor_Hang",
    "ru": "rus_Cyrl",
    "rus": "rus_Cyrl",
    "ar": "arb_Arab",
    "ara": "arb_Arab",
    "hi": "hin_Deva",
    "hin": "hin_Deva",
    "nl": "nld_Latn",
    "nld": "nld_Latn",
    "dut": "nld_Latn",
    "pl": "pol_Latn",
    "pol": "pol_Latn",
    "sv": "swe_Latn",
    "swe": "swe_Latn",
    "tr": "tur_Latn",
    "tur": "tur_Latn",
    "uk": "ukr_Cyrl",
    "ukr": "ukr_Cyrl",
    "vi": "vie_Latn",
    "vie": "vie_Latn",
    "tl": "tgl_Latn",
    "fil": "tgl_Latn",
    "tgl": "tgl_Latn",

    # Additional Popular Languages
    "id": "ind_Latn",
    "ind": "ind_Latn",
    "th": "tha_Thai",
    "tha": "tha_Thai",
    "cs": "ces_Latn",
    "ces": "ces_Latn",
    "cze": "ces_Latn",
    "el": "ell_Grek",
    "ell": "ell_Grek",
    "gre": "ell_Grek",
    "da": "dan_Latn",
    "dan": "dan_Latn",
    "fi": "fin_Latn",
    "fin": "fin_Latn",
    "he": "heb_Hebr",
    "heb": "heb_Hebr",
    "hu": "hun_Latn",
    "hun": "hun_Latn",
    "no": "nob_Latn",
    "nob": "nob_Latn",
    "nor": "nob_Latn",
    "ro": "ron_Latn",
    "ron": "ron_Latn",
    "bg": "bul_Cyrl",
    "bul": "bul_Cyrl",
    "ca": "cat_Latn",
    "cat": "cat_Latn",
    "sk": "slk_Latn",
    "slk": "slk_Latn",
    "sl": "slv_Latn",
    "slv": "slv_Latn",
    "hr": "hrv_Latn",
    "hrv": "hrv_Latn",
    "sr": "srp_Cyrl",
    "srp": "srp_Cyrl",
    "ms": "zsm_Latn",
    "msa": "zsm_Latn",
    "ur": "urd_Arab",
    "urd": "urd_Arab",
    "bn": "ben_Beng",
    "ben": "ben_Beng",
    "ta": "tam_Taml",
    "tam": "tam_Taml",
    "te": "tel_Telu",
    "tel": "tel_Telu",
    "mr": "mar_Deva",
    "mar": "mar_Deva",
    "fa": "pes_Arab",
    "fas": "pes_Arab",
    "sw": "swh_Latn",
    "swh": "swh_Latn",
}


def resolve_flores_code(code: str, default: str = "eng_Latn") -> str:
    """Resolve an ISO 639 or custom language string into a FLORES-200 code token."""
    if not code:
        return default
    clean = code.strip()
    # Check if already a valid FLORES code (e.g. 'spa_Latn' or length 8 with underscore)
    if len(clean) == 8 and "_" in clean:
        return clean
    lower = clean.lower()
    if lower in ("auto", "original", "none"):
        return default
    return ISO_TO_NLLB.get(lower, default)


class NLLBTranslator:
    """Meta NLLB-200 offline neural translation engine using CTranslate2 INT8."""

    REPO_ID = "JustFrederik/nllb-200-distilled-600M-ct2-int8"

    def __init__(
        self,
        model_dir: Optional[str] = None,
        device: str = "auto",
        compute_type: str = "int8",
    ):
        self.model_dir = model_dir
        self.device = device
        self.compute_type = compute_type
        self._translator = None
        self._sp = None
        self._tok = None
        self._lock = threading.Lock()
        self._is_loaded = False

    @classmethod
    def find_cached_model_path(cls) -> Optional[str]:
        """Search local system cache directories for downloaded NLLB model files."""
        # 1. Hugging Face hub cache
        hf_cache = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
        repo_slug = cls.REPO_ID.replace("/", "--")
        snap_dir = hf_cache / f"models--{repo_slug}" / "snapshots"
        if snap_dir.is_dir():
            for snap in sorted(snap_dir.iterdir(), reverse=True):
                if snap.is_dir() and (snap / "model.bin").exists():
                    return str(snap)

        # 2. Designated VoxStream models cache
        vox_dir = Path.home() / ".cache" / "voxstream" / "models" / "nllb-200-600m-int8"
        if vox_dir.is_dir() and (vox_dir / "model.bin").exists():
            return str(vox_dir)

        # 3. Local app directory fallback
        local_dir = Path("models") / "nllb-200-600m-int8"
        if local_dir.is_dir() and (local_dir / "model.bin").exists():
            return str(local_dir.resolve())

        return None

    def is_available(self) -> bool:
        """Check if CTranslate2, SentencePiece/tokenizers, and model weights are present."""
        try:
            import ctranslate2  # noqa: F401
        except ImportError:
            return False

        path = self.model_dir or self.find_cached_model_path()
        return path is not None and Path(path).exists() and (Path(path) / "model.bin").exists()

    def load_model(self) -> bool:
        """Lazily load the CTranslate2 model and tokenizer into memory."""
        if self._is_loaded and self._translator is not None:
            return True

        with self._lock:
            if self._is_loaded and self._translator is not None:
                return True

            model_path = self.model_dir or self.find_cached_model_path()
            if not model_path or not Path(model_path).exists():
                logger.warning("Meta NLLB-200 model weights not found in local cache.")
                return False

            try:
                import ctranslate2
                logger.info(f"Loading Meta NLLB-200 from {model_path} (device={self.device}, compute_type={self.compute_type})...")
                self._translator = ctranslate2.Translator(
                    model_path,
                    device=self.device,
                    compute_type=self.compute_type,
                    inter_threads=1,
                    intra_threads=2,
                )

                # Load tokenizer: prefer SentencePiece, fallback to Tokenizer json
                spm_file = Path(model_path) / "sentencepiece.bpe.model"
                if spm_file.exists():
                    try:
                        import sentencepiece as spm
                        self._sp = spm.SentencePieceProcessor(model_file=str(spm_file))
                        logger.info("NLLB SentencePiece tokenizer loaded successfully.")
                    except ImportError:
                        logger.debug("sentencepiece package not available, trying tokenizers...")

                if self._sp is None:
                    tok_file = Path(model_path) / "tokenizer.json"
                    if tok_file.exists():
                        try:
                            from tokenizers import Tokenizer
                            self._tok = Tokenizer.from_file(str(tok_file))
                            logger.info("NLLB HuggingFace Tokenizer loaded successfully.")
                        except ImportError:
                            logger.error("Neither sentencepiece nor tokenizers could be loaded for NLLB.")
                            return False

                if self._sp is None and self._tok is None:
                    logger.error("No compatible tokenizer found for NLLB-200 in model directory.")
                    return False

                self._is_loaded = True
                logger.info("Meta NLLB-200 neural translation engine ready.")
                return True

            except Exception as e:
                logger.error(f"Failed to load Meta NLLB-200 model: {e}", exc_info=True)
                self._translator = None
                self._sp = None
                self._tok = None
                self._is_loaded = False
                return False

    def unload(self):
        """Unload model from RAM/VRAM to free resources."""
        with self._lock:
            self._translator = None
            self._sp = None
            self._tok = None
            self._is_loaded = False
            logger.info("Meta NLLB-200 unloaded from memory.")

    def sync_prewarm(self) -> bool:
        """Eagerly load model and initialize execution graph so sentence #1 has zero cold start."""
        if not self.is_available():
            return False
        if not self.load_model():
            return False
        try:
            _ = self.sync_translate("Hello", target_lang="spa_Latn", source_lang="eng_Latn")
            logger.info("Meta NLLB-200 pre-warmed successfully (graph ready).")
            return True
        except Exception as e:
            logger.debug(f"NLLB prewarm execution graph warmup skipped: {e}")
            return True

    async def prewarm(self) -> bool:
        """Asynchronously pre-warm the NLLB model in a worker thread."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.sync_prewarm)

    def sync_translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "en",
    ) -> Optional[str]:
        """Synchronously translate text between languages using NLLB-200."""
        if not text or not text.strip():
            return ""

        if not self.load_model():
            return None

        src_nllb = resolve_flores_code(source_lang, default="eng_Latn")
        tgt_nllb = resolve_flores_code(target_lang, default="spa_Latn")

        if src_nllb == tgt_nllb:
            return text.strip()

        try:
            # 1. Tokenize input
            if self._sp:
                pieces = self._sp.encode_as_pieces(text.strip())
            elif self._tok:
                pieces = self._tok.encode(text.strip()).tokens
            else:
                return None

            # 2. Append end-of-sequence and source language token per NLLB spec
            source_tokens = pieces + ["</s>", src_nllb]

            # 3. Run CTranslate2 beam inference
            results = self._translator.translate_batch(
                [source_tokens],
                target_prefix=[[tgt_nllb]],
                beam_size=2,
                max_batch_size=1,
                max_decoding_length=256,
            )

            if not results or not results[0].hypotheses:
                return None

            gen_tokens = results[0].hypotheses[0]
            # Strip target language prefix token if included
            if gen_tokens and gen_tokens[0] == tgt_nllb:
                gen_tokens = gen_tokens[1:]
            # Strip trailing EOS token
            if gen_tokens and gen_tokens[-1] == "</s>":
                gen_tokens = gen_tokens[:-1]

            # 4. Detokenize back to text
            if self._sp:
                translated = self._sp.decode_pieces(gen_tokens).strip()
            elif self._tok:
                translated = self._tok.decode(
                    [self._tok.token_to_id(t) for t in gen_tokens if self._tok.token_to_id(t) is not None]
                ).strip()
            else:
                translated = " ".join(gen_tokens).replace(" ", " ").strip()

            return translated if translated else None

        except Exception as e:
            logger.warning(f"Meta NLLB-200 translation error: {e}", exc_info=True)
            return None

    async def translate(
        self,
        text: str,
        target_lang: str,
        source_lang: str = "en",
    ) -> Optional[str]:
        """Asynchronously translate text without blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.sync_translate,
            text,
            target_lang,
            source_lang,
        )

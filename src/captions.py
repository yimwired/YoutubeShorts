import os
import re
import site
import sysconfig
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

_models = {}
_cuda_unusable = False


def _enable_cuda_runtime() -> None:
    """Put the pip-installed CUDA DLLs on PATH before CTranslate2 loads them.

    Windows-only in effect — the CI runner has no such directory, so this is a
    no-op there. CTranslate2 resolves cuBLAS lazily through LoadLibrary, which
    searches PATH; os.add_dll_directory() does not reach it, and the model loads
    fine without it so the failure only surfaces on the first encode.

    Both the interpreter's site-packages and the user site are searched, because
    `pip install --user` puts the nvidia wheels in the latter.
    """
    site_roots = [sysconfig.get_paths()["purelib"], site.getusersitepackages()]
    current_path = os.environ["PATH"].split(os.pathsep)
    bin_dirs = [
        str(path)
        for root in site_roots
        for path in sorted((Path(root) / "nvidia").glob("*/bin"))
        if str(path) not in current_path
    ]
    if bin_dirs:
        os.environ["PATH"] = os.pathsep.join(bin_dirs + current_path)


def _cuda_encodes(model: WhisperModel) -> bool:
    """Run one encoder pass so a broken CUDA runtime fails here, not mid-batch.

    A model whose cuBLAS cannot be loaded still constructs cleanly; the error only
    appears on the first transcribe, where main.py's best-effort helpers swallow it
    and the short ships with no word timings.
    """
    try:
        model.detect_language(np.zeros(16000, dtype=np.float32))
        return True
    except RuntimeError as exc:
        print(f"  [Whisper] CUDA unusable, falling back to CPU: {exc}")
        return False


def _pick_device() -> tuple[str, str]:
    """GPU locally, CPU on the Actions runner — decided at load, not hardcoded.

    Hardcoding "cpu" is right for CI and wrong on a machine with a card: the same
    medium model runs about 10x faster on CUDA.

    The two are not numerically identical — CUDA runs float16 and CPU int8 — so the
    Actions runner and a local box can transcribe the same audio to slightly different
    captions. Expect that when comparing CI output against a local run.
    """
    if _cuda_unusable:
        return "cpu", "int8"
    try:
        import ctranslate2
        if ctranslate2.get_cuda_device_count() > 0:
            return "cuda", "float16"
    except Exception:
        pass
    return "cpu", "int8"


def _get_model(size: str = "tiny"):
    global _cuda_unusable
    if size not in _models:
        _enable_cuda_runtime()
        device, compute_type = _pick_device()
        print(f"  [Whisper] Loading {size} model on {device}...")
        model = WhisperModel(size, device=device, compute_type=compute_type)
        if device == "cuda" and not _cuda_encodes(model):
            _cuda_unusable = True
            model = WhisperModel(size, device="cpu", compute_type="int8")
        _models[size] = model
    return _models[size]


def _tokenize_thai(text: str) -> list[str]:
    try:
        from pythainlp.tokenize import word_tokenize
        words = word_tokenize(text, engine="newmm", keep_whitespace=False)
        return [w.strip() for w in words if w.strip()]
    except Exception:
        return [w for w in text.split() if w.strip()]


def get_word_timestamps(audio_path: str, lang: str = "en") -> list[dict]:
    """
    EN : word-level via Whisper tiny (fast + accurate)
    TH : segment-level via Whisper base → PyThaiNLP word tokenization within each segment
         gives reliable timing + proper Thai word breaks
    """
    if lang == "en":
        model = _get_model("tiny")
        segs, _ = model.transcribe(audio_path, word_timestamps=True, language="en")
        result = []
        for seg in segs:
            for w in seg.words:
                clean = re.sub(r"[^\w]", "", w.word.strip()).upper()
                if clean:
                    result.append({"word": clean,
                                   "start": round(w.start, 3),
                                   "end":   round(w.end, 3)})
        return result

    # Thai — segment-level timing is reliable; use PyThaiNLP inside each segment
    model = _get_model("base")
    segs, _ = model.transcribe(audio_path, language="th")   # NO word_timestamps

    result = []
    for seg in segs:
        # Extract only Thai characters from transcription
        thai_only = re.sub(r"[^฀-๿\s]", "", seg.text).strip()
        th_words  = _tokenize_thai(thai_only) if thai_only else []

        if not th_words:
            continue

        seg_dur  = max(seg.end - seg.start, 0.1)
        word_dur = seg_dur / len(th_words)
        # Half the slot at most, so fast speech cannot push end before start: a 0.5s segment
        # that newmm splits into 12 tokens gives 0.042s per word, and a flat 0.05s gap would
        # invert every one of them.
        gap = min(0.05, word_dur / 2)
        for i, w in enumerate(th_words):
            result.append({
                "word":  w,
                "start": round(seg.start + i * word_dur, 3),
                "end":   round(seg.start + (i+1)*word_dur - gap, 3),
            })
    return result

import re
from faster_whisper import WhisperModel

_models = {}

def _get_model(size: str = "tiny"):
    if size not in _models:
        print(f"  [Whisper] Loading {size} model...")
        _models[size] = WhisperModel(size, device="cpu", compute_type="int8")
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
        for i, w in enumerate(th_words):
            result.append({
                "word":  w,
                "start": round(seg.start + i * word_dur, 3),
                "end":   round(seg.start + (i+1)*word_dur - 0.05, 3),
            })
    return result

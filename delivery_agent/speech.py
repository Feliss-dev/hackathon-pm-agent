"""Read the plan summary aloud with a Hugging Face text-to-speech model (runs locally, no API key).

TTS_MODEL (env) picks the model: `facebook/mms-tts-vie` (Vietnamese, default) or `facebook/mms-tts-eng` (English; the
summary is then written in English). MMS models read letters only, so numbers are spelled out first.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_TTS_MODEL = "facebook/mms-tts-vie"
MAX_CHARS = 600
_pipelines: dict = {}

_VI = ["không", "một", "hai", "ba", "bốn", "năm", "sáu", "bảy", "tám", "chín"]


def viet_number(n: int) -> str:
    """0..9999 in Vietnamese words ("21" -> "hai mươi mốt"); larger numbers are read digit by digit."""
    if n < 0 or n > 9999:
        return " ".join(_VI[int(d)] for d in str(abs(n)))
    if n < 10:
        return _VI[n]
    if n < 100:
        tens, unit = divmod(n, 10)
        head = "mười" if tens == 1 else f"{_VI[tens]} mươi"
        if unit == 0:
            return head
        tail = "lăm" if unit == 5 else ("mốt" if unit == 1 and tens > 1 else _VI[unit])
        return f"{head} {tail}"
    if n < 1000:
        hundreds, rest = divmod(n, 100)
        head = f"{_VI[hundreds]} trăm"
        if rest == 0:
            return head
        return f"{head} lẻ {_VI[rest]}" if rest < 10 else f"{head} {viet_number(rest)}"
    thousands, rest = divmod(n, 1000)
    head = f"{_VI[thousands]} nghìn"
    if rest == 0:
        return head
    return f"{head} không trăm lẻ {_VI[rest]}" if rest < 10 else (
        f"{head} không trăm {viet_number(rest)}" if rest < 100 else f"{head} {viet_number(rest)}")


def tts_model() -> str:
    return os.getenv("TTS_MODEL", DEFAULT_TTS_MODEL)


def summary_text(plan: dict, model: str | None = None) -> str:
    """One short paragraph about the plan, in the language of the TTS model."""
    c = plan["summary"]["counts"]
    if "eng" in (model or tts_model()):
        return (f"The plan has {c['tasks_extracted']} tasks. {c['to_create']} issues will be created and {c['skipped']} "
                f"skipped. There are {c['estimation_warnings']} estimate warnings and {c['possible_duplicates']} possible duplicates.")
    v = viet_number
    return (f"kế hoạch có {v(c['tasks_extracted'])} nhiệm vụ. sẽ tạo {v(c['to_create'])} issue trên jira, "
            f"bỏ qua {v(c['skipped'])}. có {v(c['estimation_warnings'])} cảnh báo ước lượng "
            f"và {v(c['possible_duplicates'])} nhiệm vụ nghi trùng.")


def synthesize(text: str, path: str | Path, model: str | None = None) -> dict:
    """Write `text` as a wav file. Returns {"path", "seconds", "model"}."""
    model = model or tts_model()
    if model not in _pipelines:
        from transformers import pipeline
        _pipelines[model] = pipeline("text-to-speech", model=model)
    out = _pipelines[model](text[:MAX_CHARS])
    audio = out["audio"].squeeze()
    import scipy.io.wavfile as wavfile
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(path, out["sampling_rate"], audio)
    return {"path": path, "seconds": round(audio.size / out["sampling_rate"], 1), "model": model}

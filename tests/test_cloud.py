"""Cloud OCR (Gemini) unit tests — fully mocked, ZERO network calls.

Usage:  python3 tests/test_cloud.py
Covers: _gemini_ready() gating (no key / OCR_MODE=local / key present),
_cloud_prep() resize+JPEG, _gemini_transcribe() success/429/network paths
via a stubbed requests.post, pacing (_gemini_pace_nolock), smart 429
(_gemini_retry_delay: per-minute wait+retry, per-day give up),
ocr_document_cloud() dict shape, and the fallback: a cloud failure inside
ocr_document() must use the local pipeline (also stubbed, so no OCR
engine runs). Sleeps are mocked so pacing never slows the suite.
"""
import io
import os
import sys
from unittest import mock

sys.path.insert(0, "/home/user/app")
import main as M

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {str(extra)[:200]}")


# ---- 1. gating ---------------------------------------------------------
os.environ.pop("GEMINI_API_KEY", None)
os.environ.pop("OCR_MODE", None)
check("cloud: off without key", M._gemini_ready() is False)
os.environ["GEMINI_API_KEY"] = "test-key"
check("cloud: on with key", M._gemini_ready() is True)
os.environ["OCR_MODE"] = "local"
check("cloud: OCR_MODE=local forces off", M._gemini_ready() is False)
os.environ["OCR_MODE"] = "cloud"
check("cloud: explicit cloud ok", M._gemini_ready() is True)
del os.environ["OCR_MODE"]
del os.environ["GEMINI_API_KEY"]

# ---- 2. _cloud_prep (pure local image work, no network) -----------------
from PIL import Image  # noqa: E402

_big = Image.new("RGB", (3000, 2000), "white")
_buf = io.BytesIO()
_big.save(_buf, "PNG")
raw, preview = M._cloud_prep(_buf.getvalue())
_back = Image.open(io.BytesIO(raw))
check("prep: capped at 1600px", max(_back.size) == 1600, _back.size)
check("prep: jpeg bytes", raw[:3] == b"\xff\xd8\xff")
check("prep: data-url preview", preview.startswith("data:image/jpeg;base64,"))
check("prompt: transcription instruction",
      "transcribe" in M._GEMINI_PROMPT.lower() and "ONLY" in M._GEMINI_PROMPT)


# ---- 3. _gemini_transcribe with stubbed requests.post -------------------
class _Resp:
    def __init__(self, code, payload):
        self.status_code = code
        self._payload = payload
        self.text = str(payload)[:200]

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


_calls = []


def _fake_post(url, params=None, json=None, timeout=None):
    _calls.append((url, params, json, timeout))
    return _Resp(200, {"candidates": [{"content": {"parts": [
        {"text": "LINE ONE\nLINE TWO"}]}}]})


def _post_429(*a, **k):
    _calls.append(("429",))
    return _Resp(429, {"error": "GenerateRequestsPerDayPerProject per day quota exceeded"})


def _post_429_min_then_ok(*a, **k):
    _calls.append(("429min",))
    if len([c for c in _calls if c == ("429min",)]) == 1:
        return _Resp(429, {"error": "GenerateRequestsPerMinutePerProject per minute quota exceeded"})
    return _Resp(200, {"candidates": [{"content": {"parts": [
        {"text": "RETRIED"}]}}]})


def _post_boom(*a, **k):
    raise ConnectionError("network down")


_real_post = M.requests.post
_real_pre = M.preprocess
_real_rapid = M.ocr_with_rapid
_real_tess = M.ocr_with_tesseract_multi
os.environ["GEMINI_API_KEY"] = "k"
_sleep_patch = mock.patch.object(M.time, "sleep")
_sleep_mock = _sleep_patch.start()
try:
    M.requests.post = _fake_post
    t = M._gemini_transcribe(b"fakejpeg")
    check("transcribe: ok text", t == "LINE ONE\nLINE TWO", t)
    check("transcribe: key in params", _calls[0][1] == {"key": "k"}, _calls[0][1])
    check("transcribe: model in url", M.GEMINI_MODEL in _calls[0][0], _calls[0][0])
    _parts = _calls[0][2]["contents"][0]["parts"]
    check("transcribe: prompt+image parts", len(_parts) == 2, _parts)
    check("transcribe: timeout sane", _calls[0][3] == M.GEMINI_TIMEOUT, _calls[0][3])
    check("transcribe: jpeg mime", _parts[1]["inline_data"]["mime_type"] == "image/jpeg")

    M.requests.post = lambda *a, **k: _Resp(200, {"candidates": []})
    try:
        M._gemini_transcribe(b"x")
        check("transcribe: empty raises", False)
    except RuntimeError:
        check("transcribe: empty raises", True)

    _calls.clear()
    M.requests.post = _post_429
    try:
        M._gemini_transcribe(b"x")
        check("transcribe: 429 raises", False)
    except RuntimeError as e:
        check("transcribe: 429-day raises", "429" in str(e), e)
    check("transcribe: 429-day no retry", len(_calls) == 1, len(_calls))

    check("429delay: per-day gives up",
          M._gemini_retry_delay("Quota exceeded, requests per day") is None)
    check("429delay: honors retry-in",
          M._gemini_retry_delay("slow down, retry in 20s") == 20)
    check("429delay: retry-in capped",
          M._gemini_retry_delay("retry in 999s") == 120)
    check("429delay: minute default",
          M._gemini_retry_delay("per minute quota exceeded") == 45)
    check("429delay: unknown defaults to wait",
          M._gemini_retry_delay("weird") == 45)

    M._last_gemini_t = M.time.time()
    _w = M._gemini_pace_nolock()
    check("pace: fresh stamp waits ~gap", 14.0 < _w <= 15.0, _w)
    M._last_gemini_t = 0.0
    _w0 = M._gemini_pace_nolock()
    check("pace: old stamp no wait", _w0 == 0.0, _w0)
    M._last_gemini_t = 0.0

    _calls.clear()
    _sleep_mock.reset_mock()
    M.requests.post = _post_429_min_then_ok
    t_retry = M._gemini_transcribe(b"x")
    check("transcribe: 429/min retries then ok", t_retry == "RETRIED", t_retry)
    check("transcribe: 429/min two calls", len(_calls) == 2, len(_calls))
    check("transcribe: 429/min waited 45s",
          any(c.args == (45,) for c in _sleep_mock.call_args_list),
          _sleep_mock.call_args_list)

    M.requests.post = _post_boom
    try:
        M._gemini_transcribe(b"x")
        check("transcribe: netfail raises", False)
    except RuntimeError as e:
        check("transcribe: netfail raises", "network" in str(e), e)

    # ---- 4. ocr_document_cloud shape ------------------------------------
    M.requests.post = _fake_post
    doc = M.ocr_document_cloud(_buf.getvalue())
    check("cloud doc: merged lines",
          doc["merged_lines"] == ["LINE ONE", "LINE TWO"], doc["merged_lines"])
    check("cloud doc: engines tag", doc["engines"] == ["Gemini-Cloud"], doc["engines"])
    check("cloud doc: preview",
          doc["clean_preview"].startswith("data:image/jpeg"), "")
    check("cloud doc: no cloud_error", doc["cloud_error"] is None,
          doc["cloud_error"])

    # ---- 5. fallback: cloud boom -> local pipeline ----------------------
    M.requests.post = _post_boom  # cloud attempted (key set) but dead
    M.preprocess = lambda b: {"gray": None, "clahe": None, "bw": None,
                              "otsu": None, "clean_preview": "p",
                              "osd_angle": 0, "deskew_angle": 0.0}
    M.ocr_with_rapid = lambda g: [{"text": "FAKE LOCAL LINE ONE TWO", "conf": 0.9}]
    M.ocr_with_tesseract_multi = lambda *a, **k: ("", False)
    doc2 = M.ocr_document(b"whatever")
    check("fallback: local used on cloud boom",
          doc2["engines"] and doc2["engines"][0].startswith("RapidOCR-DL"),
          doc2["engines"])
    check("fallback: merged non-empty", bool(doc2["merged_lines"]), doc2["merged_lines"])
    check("fallback: cloud_error recorded",
          bool(doc2["cloud_error"]) and "cannot identify" in doc2["cloud_error"],
          doc2["cloud_error"])
finally:
    _sleep_patch.stop()
    M._last_gemini_t = 0.0
    M.requests.post = _real_post
    M.preprocess = _real_pre
    M.ocr_with_rapid = _real_rapid
    M.ocr_with_tesseract_multi = _real_tess
    os.environ.pop("GEMINI_API_KEY", None)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

"""Speed-optimization unit tests — pure python, no OCR engine needed.

Usage:  python3 tests/test_perf.py
Covers: _micro_neighbor_token() (zone-aware, gazetteer-exact, no loose
patterns in neighbors), ocr_with_tesseract_multi() early exit (ladder stops
as soon as the text scores solid), _job_photos() manifest building.
"""
import sys
import tempfile
from pathlib import Path

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


def rl(*texts):
    return [{"text": t, "conf": 0.9, "box": []} for t in texts]


# ---- _micro_neighbor_token ----------------------------------------------
CAR = r"^[A-Z]{2,8}$"
check("micro-n: carrosserie exact below",
      M._micro_neighbor_token(rl("Carrosserie/Car body", "CI", "Energie"), 0,
                              "below", CAR, "CARROSSERIE") == "CI")
check("micro-n: carrosserie repair below",
      M._micro_neighbor_token(rl("Carrosserie/Car body", "C1", "Energie"), 0,
                              "below", CAR, "CARROSSERIE") == "CI")
check("micro-n: above-label ignored",
      M._micro_neighbor_token(rl("CI", "Carrosserie/Car body", "Energie"), 1,
                              "below", CAR, "CARROSSERIE") == "")
check("micro-n: loose pattern refused in neighbors",
      M._micro_neighbor_token(rl("Carrosserie/Car body", "XYZ", "Energie"), 0,
                              "below", CAR, "CARROSSERIE") == "")
check("micro-n: gage exact",
      M._micro_neighbor_token(rl("Vehicule gage/Pledged Vehicle", "OUI"), 0,
                              "below", None, "GAGE") == "OUI")
check("micro-n: sexe same-line right",
      M._micro_neighbor_token(rl("SEXE/SEX M", "other"), 0,
                              "right", r"^[MF]$", None) == "M")
check("micro-n: sexe below next line",
      M._micro_neighbor_token(rl("SEXE/SEX", "M"), 0,
                              "below", r"^[MF]$", None) == "M")
check("micro-n: right ignores other lines",
      M._micro_neighbor_token(rl("SEXE/SEX", "M"), 0,
                              "right", r"^[MF]$", None) == "")
check("micro-n: stop tokens skipped",
      M._micro_neighbor_token(rl("Carrosserie/Car body", "CAR", "Energie"), 0,
                              "below", CAR, "CARROSSERIE") == "")
check("micro-n: places digits",
      M._micro_neighbor_token(rl("Places assises/Number of seats", "5"), 0,
                              "below", r"^\d{1,2}$", None) == "5")

# ---- tesseract ladder early exit ------------------------------------------
_real_tess = M._tess
_real_ok = M.TESSERACT_OK
M.TESSERACT_OK = True
try:
    calls = []

    def _strong(img, psm):
        calls.append(psm)
        return "NOM PRENOM NAISSANCE SEXE " + "X" * 150

    M._tess = _strong
    txt, multi = M.ocr_with_tesseract_multi(None, None, None)
    check("ladder: strong first pass stops", calls == [6], calls)
    check("ladder: single pass not multi", multi is False and bool(txt), multi)

    calls.clear()

    def _weak(img, psm):
        calls.append(psm)
        return "zzz"

    M._tess = _weak
    txt, multi = M.ocr_with_tesseract_multi(None, None, None)
    check("ladder: weak runs full ladder", calls == [6, 11, 3, 6, 6], calls)
    check("ladder: weak is multi", multi is True, multi)

    M._tess = lambda img, psm: ""
    txt, multi = M.ocr_with_tesseract_multi(None, None, None)
    check("ladder: all empty", txt == "" and multi is False, (txt, multi))
finally:
    M._tess = _real_tess
    M.TESSERACT_OK = _real_ok

# ---- _job_photos -----------------------------------------------------------
_real_up = M.UPLOADS_DIR
with tempfile.TemporaryDirectory() as td:
    M.UPLOADS_DIR = Path(td)
    (Path(td) / "abcd1234ef56_cni_recto.png").write_bytes(b"x" * 100)
    (Path(td) / "abcd1234ef56_permis.jpg").write_bytes(b"y" * 100)
    (Path(td) / "notes.txt").write_bytes(b"junk")
    (Path(td) / "abcd1234ef56_cni_recto.png").write_bytes(b"x" * 100)
    man = M._job_photos("abcd1234ef56ffff")
    check("photos: manifest 2 fields",
          man == {"cni_recto": "abcd1234ef56_cni_recto.png",
                  "permis": "abcd1234ef56_permis.jpg"}, man)
    check("photos: other job empty",
          M._job_photos("ffffffffffff0000") == {})
    check("photos: short id empty", M._job_photos("abc") == {})
    check("photos: empty id empty", M._job_photos("") == {})
M.UPLOADS_DIR = _real_up

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

#!/usr/bin/env python3
# =====================================================================
#  AGC ASSURANCES - GENERALES DU CAMEROUN
#  Plateforme d'emission RCA par controle OCR triple-document
#  CNI + Carte Grise + Permis de conduire -> Police Conditions
#  Particulieres (conforme Code CIMA)
# =====================================================================
import io
import os
import re
import json
import base64
import uuid
import shutil
import datetime
import unicodedata
import threading
import html
import zipfile
import csv
from concurrent.futures import ThreadPoolExecutor
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from difflib import SequenceMatcher

import cv2
import numpy as np
import pytesseract
from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile, HTTPException, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from PIL import Image

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm

# ---------------------------------------------------------------- config
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

STATIC_DIR = BASE_DIR / "static"
LOGO_PATH = STATIC_DIR / "agc_logo.png"
# STORAGE_DIR allows a persistent disk mount on hosted deployments.
STORAGE_DIR = Path(os.getenv("STORAGE_DIR") or str(BASE_DIR / "storage"))
UPLOADS_DIR = STORAGE_DIR / "documents"
CONTRACTS_PDF_DIR = STORAGE_DIR / "contracts"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
CONTRACTS_PDF_DIR.mkdir(parents=True, exist_ok=True)

# AGC brand colors (sampled from official logo)
AGC_RED = "#F4313F"
AGC_NAVY = "#011689"

# NOTE: all OCR and document processing is 100% local and open-source (RapidOCR +
# Tesseract + dictionary repair). No cloud API is used anywhere in this app.

# Open mode (v5.3): no login screen. The app runs as one shared identity
# (username "agence", role "superviseur"). Anyone with the URL can use it:
# only deploy on a trusted network or behind your own access control.
BACKUP_DIR = STORAGE_DIR / "backups"
DB_SNAP_DIR = BACKUP_DIR / "db"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)
DB_SNAP_DIR.mkdir(parents=True, exist_ok=True)

# Tesseract binary (Windows default paths supported)
if os.name == "nt":
    for p in [r"C:\Program Files\Tesseract-OCR\tesseract.exe",
              r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
              os.path.expanduser(r"~\AppData\Local\Programs\Tesseract-OCR\tesseract.exe")]:
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            break

TESSERACT_OK = False
try:
    if shutil.which(pytesseract.pytesseract.tesseract_cmd) or os.path.exists(pytesseract.pytesseract.tesseract_cmd):
        pytesseract.get_tesseract_version()
        TESSERACT_OK = True
except Exception:
    TESSERACT_OK = False

# RapidOCR (deep-learning engine, pure pip, lazy init w/ lock)
_rapid_engine = None
_rapid_lock = threading.Lock()
_rapid_failed = False

def get_rapid():
    global _rapid_engine, _rapid_failed
    if _rapid_engine is None and not _rapid_failed:
        with _rapid_lock:
            if _rapid_engine is None and not _rapid_failed:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _rapid_engine = RapidOCR()
                except Exception as e:
                    print(f"[OCR] RapidOCR init failed: {e}")
                    _rapid_failed = True
    return _rapid_engine

app = FastAPI(title="AGC Assurances - Emission RCA triple-document", version="5.3.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
from fastapi.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.middleware("http")
async def _no_store(request: Request, call_next):
    """Never let browsers cache pages/API (a stale app shell after an
    upgrade looks like the old version - e.g. a ghost login screen)."""
    resp = await call_next(request)
    resp.headers["Cache-Control"] = "no-store"
    return resp

CONTRACTS_DB: Dict[str, Dict[str, Any]] = {}

# ============================================================ OCR PIPELINE
MIN_WIDTH = 1800  # upscale small captures so characters are not pixelated

def deskew_gray(gray: np.ndarray, max_angle: float = 15.0) -> Tuple[np.ndarray, float]:
    """Auto-rotate tilted captures using the minimum-area text rectangle."""
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if float(np.mean(bw)) < 127:
        bw = 255 - bw
    coords = np.column_stack(np.where(bw == 0))
    if len(coords) < 500:
        return gray, 0.0
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    if abs(angle) < 0.4 or abs(angle) > max_angle:
        return gray, 0.0
    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    fixed = cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_CUBIC,
                           borderMode=cv2.BORDER_REPLICATE)
    return fixed, round(float(angle), 2)

def preprocess(image_bytes: bytes) -> Dict[str, Any]:
    """Grayscale -> resize(>=1800px) -> deskew -> denoise -> binarize."""
    arr = np.frombuffer(image_bytes, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Photo illisible (format non supporte).")
    h, w = img.shape[:2]
    if w < MIN_WIDTH:
        s = MIN_WIDTH / w
        img = cv2.resize(img, (MIN_WIDTH, int(h * s)), interpolation=cv2.INTER_CUBIC)
    orient = _osd_rotation(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    if orient == 90:
        img = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
    elif orient == 180:
        img = cv2.rotate(img, cv2.ROTATE_180)
    elif orient == 270:
        img = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray, angle = deskew_gray(gray)
    gray = cv2.medianBlur(gray, 3)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    bw = cv2.adaptiveThreshold(clahe, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 51, 10)
    _, otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # small preview of the cleaned image (for UI transparency)
    ph = int(bw.shape[0] * 640 / bw.shape[1])
    small = cv2.resize(bw, (640, ph), interpolation=cv2.INTER_AREA)
    _, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
    preview = "data:image/jpeg;base64," + base64.b64encode(buf.tobytes()).decode()
    return {"gray": gray, "bw": bw, "clahe": clahe, "otsu": otsu, "deskew_angle": angle,
            "osd_angle": orient, "size": [int(img.shape[1]), int(img.shape[0])],
            "clean_preview": preview}

def ocr_with_rapid(gray: np.ndarray) -> List[Dict[str, Any]]:
    engine = get_rapid()
    if engine is None:
        return []
    with _rapid_lock:
        try:
            res, _ = engine(gray)
        except Exception as e:
            print(f"[OCR] rapid failed: {e}")
            return []
    lines = []
    for item in (res or []):
        try:
            box, txt, conf = item
            txt = (txt or "").strip()
            if txt:
                lines.append({"text": txt, "conf": round(float(conf), 3),
                              "box": [[round(float(x), 1), round(float(y), 1)] for x, y in box]})
        except Exception:
            continue
    return lines

_tess_lang = None

def _tess_langs() -> str:
    global _tess_lang
    if _tess_lang is None:
        try:
            have = set(pytesseract.get_languages())
            _tess_lang = "+".join([lg for lg in ("fra", "eng") if lg in have]) or "eng"
        except Exception:
            _tess_lang = "eng"
    return _tess_lang

def _tess(img: np.ndarray, psm: int) -> str:
    try:
        return pytesseract.image_to_string(img, lang=_tess_langs(), config=f"--oem 1 --psm {psm}") or ""
    except Exception:
        try:
            return pytesseract.image_to_string(img, config=f"--oem 1 --psm {psm}") or ""
        except Exception:
            return ""

_OCR_LABELS = ("NOM", "PRENOM", "NAISSANCE", "SEXE", "EXPIR", "CNI", "NIC",
    "PROFESSION", "DELIVRANCE", "IMMATRICULATION", "CHASSIS", "TITULAIRE",
    "MARQUE", "GENRE", "MODELE", "POIDS", "PUISSANCE", "CYLINDR", "CARROSSERIE",
    "ENERGIE", "PLACES", "CIRCULATION", "PERMIS", "LICENCE", "CONDUIR",
    "CAMEROUN", "CMR", "TRANSPORT", "CERTIFICAT", "REPUBLIQUE", "SSDT")

def _ocr_score(text: str) -> Tuple[int, int]:
    up = strip_accents(text or "").upper()
    hits = sum(1 for t in _OCR_LABELS if t in up)
    alpha = sum(1 for c in up if c.isalnum())
    return hits, alpha

def _tess_mean_conf(gray_small: np.ndarray) -> float:
    """Mean Tesseract word confidence (upright text scores high, garbage low)."""
    try:
        d = pytesseract.image_to_data(gray_small, lang=_tess_langs(),
                                      config="--oem 1 --psm 6",
                                      output_type=pytesseract.Output.DICT)
        confs = [float(c) for c, t in zip(d.get("conf", []), d.get("text", []))
                 if str(t or "").strip() and float(c) > 0]
        return sum(confs) / len(confs) if confs else 0.0
    except Exception:
        return 0.0

def _osd_rotation(gray: np.ndarray) -> int:
    """Upright sideways/upside-down phone photos via Tesseract OSD.

    OSD can be confidently wrong (it reports 180 deg on some upright pages),
    so a suggested rotation is VERIFIED: it only applies when Tesseract word
    confidence clearly improves after rotating. Otherwise the original is kept
    (never worse than no OSD)."""
    if not TESSERACT_OK:
        return 0
    try:
        h, w = gray.shape[:2]
        small = gray
        if max(h, w) > 1000:
            s = 1000 / max(h, w)
            small = cv2.resize(gray, (max(1, int(w * s)), max(1, int(h * s))),
                               interpolation=cv2.INTER_AREA)
        osd = pytesseract.image_to_osd(small, config="--oem 1")
        mo = re.search(r"Orientation in degrees:\s*(\d+)", osd)
        mc = re.search(r"Orientation confidence:\s*([\d.]+)", osd)
        if not (mo and mc and float(mc.group(1)) >= 2.0):
            return 0
        orient = int(mo.group(1)) % 360
        if orient == 0:
            return 0
        rot = {90: cv2.ROTATE_90_COUNTERCLOCKWISE, 180: cv2.ROTATE_180,
               270: cv2.ROTATE_90_CLOCKWISE}.get(orient)
        if rot is None:
            return 0
        cb = _tess_mean_conf(small)
        cf = _tess_mean_conf(cv2.rotate(small, rot))
        if cf > cb + 10:
            return orient
    except Exception:
        pass
    return 0

_MICRO = [
    # (marker tokens in label | line-regex, zone, pattern, gazetteer-or-None)
    (("PLACESASSISES", "NUMBEROFSEATS"), "below", r"^\d{1,2}$", None),
    (("CARROSSERIE", "CARBODY"), "below", r"^[A-Z]{2,8}$", "CARROSSERIE"),
    (("VEHICULEGAGE", "PLEDGEDVEHICLE"), "below", None, "GAGE"),
    (("SEXE",), "right", r"^[MF]$", None),
    (("CAT9",), "right", r"^[A-E][1E]?$", None),
]
_CARROSSERIE_CODES = {"CI", "CS", "CAB", "CABRIOLET", "BERLINE", "BREAK", "PICKUP",
    "BENNE", "FOURGON", "FOURGONNETTE", "CITERNE", "PLATEAU", "RIDELLE", "TRACTEUR",
    "CAMION", "CAMIONNETTE", "REMORQUE", "SEMI", "MOTO", "CYCLO", "CYCLOMOTEUR",
    "TRICYCLE", "QUAD", "BUS", "CAR", "MINIBUS", "MINICAR", "AMBULANCE", "GRUE",
    "NACELLE", "BETONNIERE", "FRIGORIFIQUE", "BACHE", "SAVOYARDE", "CITADINE", "COUPE"}
_MICRO_CONFUSE = {"I": "L1", "L": "I1", "1": "IL", "O": "D0", "D": "O0", "0": "OD",
    "B": "8", "8": "B", "S": "5", "5": "S", "Z": "2", "2": "Z", "G": "6", "6": "G",
    "U": "V", "V": "U", "Q": "O", "E": "F", "F": "E", "T": "7", "7": "T"}
_MICRO_STOP = {"ENERGIE", "ENERGY", "CYLINDREE", "ENGINE", "CAPACITY", "CAPACITE",
    "CARROSSERIE", "CAR", "BODY", "VEHICULE", "GAGE", "PLEDGED", "VEHICLE", "LEDGE",
    "PLACES", "ASSISES", "NUMBER", "SEATS", "CENTRE", "CENTER", "SSDT", "SERIE",
    "TYPE", "MARQUE", "BRAND", "MODE", "MODEL", "PUISSANCE", "POWER", "FISCALE",
    "REELLE", "POIDS", "VIDE", "TOTAL", "AUTORISE", "CHARGE", "UTILE", "PTAC",
    "SEXE", "SEX", "MASCULIN", "FEMININ", "CATEGORIE", "CATEGORY", "PERMIS",
    "LICENCE", "DRIVING", "NUMERO", "DATE", "LIEU", "PLACE", "NOM", "NAME",
    "PRENOM", "NE", "NEE", "BORN", "SIGNATURE", "TIMBRE", "STAMP"}

def _micro_repair(tok: str, targets) -> str:
    if tok in targets:
        return tok
    if len(tok) > 10:
        return ""
    for i, ch in enumerate(tok):
        for alt in _MICRO_CONFUSE.get(ch, ""):
            cand = tok[:i] + alt + tok[i + 1:]
            if cand in targets:
                return cand
    return ""

def _micro_preps(big):
    """Two threshold variants: plain OTSU (clean glyphs) + illumination-flattened rescue."""
    import numpy as _np
    variants = []
    blur = cv2.medianBlur(big, 3)
    try:
        _, bw = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(bw)
    except Exception:
        pass
    try:
        bg = cv2.medianBlur(blur, 51)
        bg = _np.maximum(bg, _np.ones_like(bg) * 60)
        flat = cv2.divide(blur, bg, scale=255)
        _, bw2 = cv2.threshold(flat, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(bw2)
    except Exception:
        pass
    return variants

def _micro_trim(bw):
    inv = 255 - bw
    H, W = bw.shape[:2]
    try:
        n, _, stats, _ = cv2.connectedComponentsWithStats(inv, 8)
    except Exception:
        return bw
    boxes = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area >= 300 and area <= 0.35 * W * H and w >= 5 and h >= 12:
            boxes.append((x, y, w, h))
    if not boxes:
        return bw
    x0 = max(0, min(b[0] for b in boxes) - 10)
    y0 = max(0, min(b[1] for b in boxes) - 10)
    x1 = min(W, max(b[0] + b[2] for b in boxes) + 10)
    y1 = min(H, max(b[1] + b[3] for b in boxes) + 10)
    if x1 - x0 < 10 or y1 - y0 < 10:
        return bw
    return bw[y0:y1, x0:x1]

def _micro_read(gray, rapid_lines: List[Dict[str, Any]]) -> Dict[int, List[str]]:
    """Zoom into tiny values next to their labels (seats, bodywork, pledge, sex,
    licence category): crop -> 4x upscale -> dual-threshold trim -> PSM ensemble.
    Only pattern-gazetteer validated tokens are injected after their label line."""
    found: Dict[int, List[str]] = {}
    if gray is None or not rapid_lines:
        return found
    H, W = gray.shape[:2]
    for idx, ln in enumerate(rapid_lines):
        if idx in found:
            continue
        text = ln.get("text", "")
        ns = nospace(text)
        for markers, zone, pat, gaz in _MICRO:
            if "CAT9" in markers:
                ok = re.match(r"^9[\s.\-:]{0,3}$", text.strip()) is not None
            else:
                ok = any(mk in ns for mk in markers)
            if not ok:
                continue
            try:
                box = ln.get("box") or []
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                if len(xs) < 4:
                    continue
                x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
                h = max(4.0, y1 - y0)
                if zone == "below":
                    cx0, cy0 = int(x0 - 1 * h), int(y1)
                    cx1, cy1 = int(x1 + 2 * h), int(y1 + 2.0 * h)
                else:
                    cx0, cy0 = int(x1), int(y0 - 0.5 * h)
                    cx1, cy1 = int(x1 + 8 * h), int(y1 + 0.5 * h)
                cx0, cy0 = max(0, cx0), max(0, cy0)
                cx1, cy1 = min(W, cx1), min(H, cy1)
                if cx1 - cx0 < 8 or cy1 - cy0 < 8:
                    continue
                crop = gray[cy0:cy1, cx0:cx1]
                big = cv2.resize(crop, None, fx=4.0, fy=4.0,
                                 interpolation=cv2.INTER_CUBIC)
                win = ""
                for prep_bw in _micro_preps(big):
                    if win:
                        break
                    trimmed = _micro_trim(prep_bw)
                    for psm in (6, 7, 8):
                        if win:
                            break
                        out = _tess(trimmed, psm) or ""
                        for raw in out.split():
                            tok = re.sub(r"[^A-Z0-9]", "",
                                         strip_accents(raw).upper()).strip()
                            if not tok or tok in _MICRO_STOP:
                                continue
                            if gaz == "GAGE":
                                fix = _micro_repair(tok, {"OUI", "NON"})
                                if fix:
                                    win = fix
                                    break
                            elif gaz == "CARROSSERIE":
                                if tok in _CARROSSERIE_CODES:
                                    win = tok
                                    break
                                fix = _micro_repair(tok, _CARROSSERIE_CODES)
                                if fix:
                                    win = fix
                                    break
                                if pat and re.match(pat, tok):
                                    win = tok
                                    break
                            elif pat and re.match(pat, tok):
                                win = tok
                                break
                if win:
                    found.setdefault(idx, [])
                    if win not in found[idx]:
                        found[idx].append(win)
            except Exception:
                continue
    return found

def ocr_with_tesseract_multi(bw: np.ndarray, clahe: np.ndarray,
                             otsu: np.ndarray) -> Tuple[str, bool]:
    """Tesseract multi-pass: uniform block (6) + sparse text (11), with rescue
    passes (full page 3 + contrast variants) when the text looks weak."""
    if not TESSERACT_OK:
        return "", False
    out = [_tess(bw, 6), _tess(bw, 11)]
    base = "\n".join(out)
    hits, alpha = _ocr_score(base)
    rescue = hits < 4 or alpha < 120
    if rescue:
        out += [_tess(bw, 3), _tess(clahe, 6), _tess(otsu, 6)]
    texts = [p for p in out if p and p.strip()]
    return "\n".join(texts), rescue and len(texts) > 2

def ocr_document(image_bytes: bytes) -> Dict[str, Any]:
    """Full OCR of one photo: OSD upright + cleanup + dual engine (multi-pass)
    + rescue passes on weak reads + merged lines."""
    prep = preprocess(image_bytes)
    rapid_lines = ocr_with_rapid(prep["gray"])
    tess_text, tess_multi = ocr_with_tesseract_multi(prep["bw"], prep["clahe"], prep["otsu"])
    rapid_multi = False
    if not rapid_lines or _ocr_score("\n".join(l["text"] for l in rapid_lines) + "\n" + tess_text)[0] < 3:
        extra = ocr_with_rapid(prep["clahe"])
        if extra:
            have = {" ".join(x["text"].split()) for x in rapid_lines}
            rapid_lines = rapid_lines + [l for l in extra if " ".join(l["text"].split()) not in have]
            rapid_multi = True
    try:
        micro = _micro_read(prep["gray"], rapid_lines)
    except Exception:
        micro = {}
    merged, seen = [], set()
    for _ri, ln in enumerate(rapid_lines):
        k = " ".join(ln["text"].split())
        if k and k not in seen:
            seen.add(k)
            merged.append(ln["text"].strip())
        for tok in micro.get(_ri, []):
            if tok not in seen:
                seen.add(tok)
                merged.append(tok)
    for ln in tess_text.splitlines():
        k = " ".join(ln.split())
        if len(k) >= 2 and k not in seen:
            seen.add(k)
            merged.append(ln.strip())
    engines = []
    if rapid_lines:
        engines.append("RapidOCR-DL" + ("+CLAHE" if rapid_multi else ""))
    if tess_text.strip():
        engines.append("Tesseract-OCR" + ("-Multi" if tess_multi else ""))
    if not rapid_lines and tess_text.strip():
        engines.append("Tesseract-Seul")
    return {"rapid_lines": rapid_lines, "tesseract_text": tess_text,
            "merged_lines": merged, "combined_text": "\n".join(merged),
            "clean_preview": prep["clean_preview"], "osd_angle": prep["osd_angle"],
            "deskew_angle": prep["deskew_angle"], "engines": engines}

# ============================================================ TEXT HELPERS
def nospace(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", strip_accents(s).upper())

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "")
                   if unicodedata.category(c) != "Mn")

def norm_name(s: str) -> str:
    return re.sub(r"[^A-Z]", "", strip_accents(s or "").upper())

def name_sim(a: str, b: str) -> float:
    a, b = norm_name(a), norm_name(b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()

def find_date(s: str) -> str:
    m = re.search(r"\b(\d{2})[.\-/](\d{2})[.\-/](\d{4})\b", s or "")
    if m:
        return f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
    return ""

def to_iso(d: str) -> Optional[datetime.date]:
    try:
        return datetime.datetime.strptime(d, "%d/%m/%Y").date()
    except Exception:
        return None

# Name dictionaries for repairing OCR-glued tokens (BIYAEMMANUEL -> BIYA EMMANUEL)
CM_SURNAMES = ["BIYA", "NGANNO", "NDONGO", "ESSOMBA", "MBARGA", "FOUDA", "EYENGA", "NKOLO",
    "ABANDA", "TCHOUMA", "KAMGA", "NJOYA", "MBOUA", "EKANI", "AMOUGOU", "BEKOTO", "NGOUNA",
    "MEKONGO", "NDI", "NGUE", "TCHAPTCHET", "KOUAM", "MOUKOKO", "ONANA", "ATCHOU", "DJUIDJE",
    "FOTSO", "KENGNE", "LEKE", "MBALA", "NANA", "NKEM", "TAMO", "WANDJI", "YONDO", "ZOUE",
    "BANGA", "ESSAMA", "MEDJO", "MBI", "NGO", "TABI", "AVOM", "BESSALA", "DJEUKAM", "EBELLE",
    "NGUEFACK", "TANDENT", "AHANDA", "YANG", "TAKUETE", "NGBANGADI", "NKOUENGUE", "NEOSSI",
    "FONKEU", "NJAMOU", "NKOUAMENI", "MBARGA", "NGUELE", "TANDENT", "PAULE", "SANDRA"]
CM_FIRSTNAMES = ["EMMANUEL", "JEAN", "PIERRE", "PAUL", "MARIE", "CLAIRE", "ALAIN", "SERGE",
    "PATRICK", "ERIC", "ARNOLD", "BERTRAND", "CHRISTIAN", "DANIEL", "DAVID", "ETIENNE",
    "FRANCOIS", "GEORGES", "HENRI", "ISAAC", "JACQUES", "JOSEPH", "LAURENT", "LUC", "MARTIN",
    "MICHEL", "NICOLAS", "OLIVIER", "PASCAL", "PHILIPPE", "RAOUL", "ROBERT", "SAMUEL", "THOMAS",
    "VICTOR", "YVES", "ACHILLE", "AURELIEN", "CELESTIN", "CYRILLE", "DESIRE", "FLORENT",
    "GASTON", "HERVE", "HERMAN", "IRENE", "JUSTIN", "LEON", "MATHIEU", "NATHALIE", "ODETTE",
    "PRUDENCE", "ROSE", "SANDRINE", "SYLVAIN", "THEOPHILE", "ULRICH", "VALENTIN", "WILFRIED",
    "AUGUSTIN", "AUGUSTINE", "CHARLES", "EDWIGE", "BERNADETTE", "FRANCK", "CLAUDINE",
    "DANIEL", "SANDRA", "PAULE"]
_NAME_DICT = sorted(set(CM_SURNAMES + CM_FIRSTNAMES), key=len, reverse=True)

def clean_alpha(v: str) -> str:
    """Keep only plausible name/place characters; cut OCR artifacts (|, (c), ...)."""
    v = re.split(r"[|©_~®]", v or "")[0]
    v = re.sub(r"[^A-Z .'\-]", " ", strip_accents(v).upper())
    return re.sub(r"\s+", " ", v).strip(" .'-")

def repair_glued_names(value: str) -> str:
    """Split glued uppercase tokens using the name dictionary (greedy longest match)."""
    out = []
    for tok in (value or "").upper().split():
        word = re.sub(r"[^A-Z]", "", tok)
        if len(word) < 6:
            out.append(tok)
            continue
        parts, rest = [], word
        while rest:
            hit = next((w for w in _NAME_DICT if rest.startswith(w)), None)
            if hit and (len(rest) == len(hit) or len(rest) - len(hit) >= 2):
                parts.append(hit)
                rest = rest[len(hit):]
            else:
                parts.append(rest)
                break
        out.append(" ".join(parts) if len(parts) > 1 else tok)
    return " ".join(out).strip()

# ============================================================ DOC PARSERS
def _valid_value(cand: str, must_alpha: bool = False) -> bool:
    c = (cand or "").strip()
    if len(c) < 2 or "<" in c or ">" in c:
        return False
    if must_alpha and not re.search(r"[A-Z]{3}", strip_accents(c).upper()):
        return False
    return True

def value_after_label(lines: List[str], keys: List[str], max_jump: int = 3,
                       stop_keys: Optional[List[str]] = None,
                       must_alpha: bool = False, same_line: bool = True) -> str:
    """Find a label line (space-insensitive), return text after it or on next lines."""
    stop_keys = stop_keys or []
    for i, ln in enumerate(lines):
        ns = nospace(ln)
        hit_key = next((k for k in keys if k in ns), None)
        if not hit_key:
            continue
        if same_line:
            up = strip_accents(ln).upper()
            cleaned = re.sub(r"[^A-Z0-9 .'\-]", " ", up)
            for k in keys:
                pattern = r".*?" + r"\W*".join(list(k)) + r"\W*"
                m = re.match(pattern, cleaned)
                if m:
                    rest = cleaned[m.end():].strip(" .:-")
                    if _valid_value(rest, must_alpha) and not any(s in nospace(rest) for s in stop_keys):
                        return rest.title() if rest.isupper() else rest
        for j in range(i + 1, min(i + 1 + max_jump, len(lines))):
            cand = lines[j].strip()
            if any(s in nospace(cand) for s in stop_keys):
                break
            if _valid_value(cand, must_alpha):
                return cand
    return ""

CM_CITIES = ["YAOUNDE", "DOUALA", "BAFOUSSAM", "BAMENDA", "GAROUA", "MAROUA", "NGAOUNDERE",
    "BERTOUA", "BUEA", "LIMBE", "KRIBI", "EDEA", "MBOUDA", "DSCHANG", "KUMBA", "KUMBO",
    "NKONGSAMBA", "BAFIA", "EBOLOWA", "SANGMELIMA", "KAELE", "YAGOUA", "MOKOLO", "KOUSSERI",
    "GUIDER", "TIBATI", "BANYO", "FOUMBAN", "FOUMBOT", "BANGANGTE", "WUM", "NDU", "AKONOLINGA",
    "MBALMAYO", "ESEKA", "LOUM", "PENJA", "TOMBEL", "MUYUKA", "TIKO", "MAMFE", "KUMBO",
    "BOGO", "GASHIGA", "MORA", "TOKOMBERE", "MINDIF", "OBALA", "SA_A", "MONATELE", "MFILOU",
    "TOMBI", "MVENG", "DZENG", "AKOEMAN", "NGOUMOU", "MFOU", "AWAE", "OLAMA", "NGAMBE"]

VEHICLE_MAKES = ["TOYOTA", "NISSAN", "HONDA", "MAZDA", "MITSUBISHI", "SUZUKI", "HYUNDAI",
    "KIA", "MERCEDES", "BENZ", "BMW", "VOLKSWAGEN", "PEUGEOT", "RENAULT", "FORD", "AUDI",
    "JEEP", "LEXUS", "ROVER", "SUBARU", "ISUZU", "MAN", "SCANIA", "VOLVO", "IVECO",
    "NANFAN", "JIANSHE", "YAMAHA", "BAJAJ", "TVS", "HAOJUE", "LIFAN", "ZONGSHEN", "DAYUN",
    "SENKE", "APSONIC", "QINGQI", "HONDA", "SKYGO", "MATIN", "ROYAL", "KTM", "DUCATI",
    "GREATWALL", "HAVAL", "CHERY", "GEELY", "BYD", "CHANGAN", "FAW", "DONGFENG",
    "SINOTRUK", "HOWO", "FOTON", "JAC", "DFSK", "GAC", "MG", "TATA", "MAHINDRA",
    "HINO", "CITROEN", "OPEL", "FIAT", "DAEWOO", "SSANGYONG", "DAF", "SHACMAN"]

CARROSSERIES = ["CI", "SOLO", "VP", "BERLINE", "BREAK", "PICKUP", "PICK-UP", "FOURGON",
    "FOURGAN", "CAMION", "TRACTEUR", "REMORQUE", "MINIBUS", "AUTOCAR", "CABINE", "PLATEAU",
    "BENNE", "CITERNE", "VAN", "SUV", "COUPE", "CABRIOLET", "LIMOSINE", "AMBULANCE"]

def parse_cni(front: Dict[str, Any], back: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    d = {"nom": "", "prenoms": "", "date_naissance": "", "sexe": "", "numero_piece": "",
         "lieu_naissance": "", "profession": "", "date_delivrance": "", "date_expiration": "",
         "numero_carte": "", "mrz": False}
    fl = front.get("merged_lines", []) if front else []
    bl = back.get("merged_lines", []) if back else []
    ALL_LABELS = ["NOMSURNAME", "NOMISURNAME", "PRENOMS", "GIVENNAMES", "DATENAISSANCE",
                  "DATEOFBIRTH", "SEXE", "DATEEXPIRATION", "DATEOFEXPIRY", "NUMEROCNI",
                  "NICNUMBER", "LIEUNAISSANCE", "PLACEOFBIRTH", "PROFESSION", "OCCUPATION",
                  "DATEDELIVRANCE", "DATEOFISSUE", "NOMDUPERE", "NOMDELAMERE", "TAILLE",
                  "HEIGHT", "SIGNATURE", "REPUBLIQUE", "CAMEROUN", "IDENTITE", "NATIONAL"]
    d["nom"] = value_after_label(fl, ["NOMSURNAME", "NOMISURNAME"], stop_keys=ALL_LABELS)
    d["prenoms"] = value_after_label(fl, ["PRENOMSGIVENNAMES", "GIVENNAMES", "PRENOMS"], stop_keys=ALL_LABELS)
    # dates on front: DOB then expiry (positional fallback)
    dob_zone = " ".join(fl)
    m_dob = re.search(r"NAISSANCE.{0,40}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})", strip_accents(dob_zone).upper())
    d["date_naissance"] = find_date(m_dob.group(0)) if m_dob else ""
    m_exp = re.search(r"EXPIR.{0,40}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})", strip_accents(dob_zone).upper())
    d["date_expiration"] = find_date(m_exp.group(0)) if m_exp else ""
    if not d["date_naissance"] or not d["date_expiration"]:
        dates = re.findall(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b", dob_zone)
        if dates and not d["date_naissance"]:
            d["date_naissance"] = find_date(dates[0])
        if len(dates) > 1 and not d["date_expiration"]:
            d["date_expiration"] = find_date(dates[-1])
    m_sex = re.search(r"SEXE?/\s*SEX\s*([MF])\b", strip_accents(dob_zone).upper())
    if m_sex:
        d["sexe"] = m_sex.group(1)
    else:
        m2 = re.search(r"\b([MF])\b", strip_accents(" ".join(fl[-6:])).upper())
        d["sexe"] = m2.group(1) if m2 else ""
    m_card = re.search(r"\b(\d{8,10})\b", dob_zone)
    d["numero_carte"] = m_card.group(1) if m_card else ""
    if bl:
        bt = " ".join(bl)
        m_cni = re.search(r"NIC\s*NUMBER.{0,30}?([A-Z]{2}\d{6,9})", strip_accents(bt).upper())
        if not m_cni:
            m_cni = re.search(r"\b([A-Z]{2}\d{7,9})\b", strip_accents(bt).upper())
        d["numero_piece"] = m_cni.group(1) if m_cni else ""
        d["lieu_naissance"] = value_after_label(bl, ["LIEUDENAISSANCE", "PLACEOFBIRTH"], stop_keys=ALL_LABELS, must_alpha=True)
        d["profession"] = value_after_label(bl, ["PROFESSION", "OCCUPATION"], stop_keys=ALL_LABELS, must_alpha=True)
        m_del = re.search(r"DELIVRANCE.{0,60}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})", strip_accents(bt).upper())
        d["date_delivrance"] = find_date(m_del.group(0)) if m_del else ""
        if not d["date_delivrance"]:
            bd = re.findall(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b", bt)
            d["date_delivrance"] = find_date(bd[0]) if bd else ""
        # --- MRZ zone (2-3 lines with '<'): robust DOB / sex / expiry / names
        cur_yy = datetime.date.today().year % 100
        for ln in bl:
            if ln.count("<") < 3:
                continue
            t = re.sub(r"\s+", "", strip_accents(ln).upper())
            d["mrz"] = True
            m_dt = re.search(r"(\d{6})\d([MF])(\d{6})", t)  # DOB + check digit + sex + expiry
            if m_dt:
                dob, sx, exp = m_dt.group(1), m_dt.group(2), m_dt.group(3)
                dob_full = f"{dob[4:6]}.{dob[2:4]}.{'19' if int(dob[:2]) > cur_yy else '20'}{dob[:2]}"
                exp_full = f"{exp[4:6]}.{exp[2:4]}.20{exp[:2]}"
                if not d["date_naissance"]:
                    d["date_naissance"] = find_date(dob_full)
                if not d["sexe"]:
                    d["sexe"] = sx
                if not d["date_expiration"]:
                    d["date_expiration"] = find_date(exp_full)
            elif t.startswith("I<") or t.startswith("ID<"):
                m_dn = re.search(r"CMR(\d{6,10})", t)
                if m_dn and not d["numero_carte"]:
                    d["numero_carte"] = m_dn.group(1)
            elif "<<" in t and re.search(r"[A-Z]", t) and not re.search(r"\d{4,}", t):
                sur, _, giv = t.partition("<<")
                sur = re.sub(r"<+", " ", sur).strip()
                giv = re.sub(r"<+", " ", giv).strip()
                if sur and not d["nom"]:
                    d["nom"] = repair_glued_names(clean_alpha(sur.title()))
                if giv and not d["prenoms"]:
                    d["prenoms"] = repair_glued_names(clean_alpha(giv.title()))
    d["nom"] = clean_alpha(repair_glued_names(d["nom"]))
    d["prenoms"] = clean_alpha(repair_glued_names(d["prenoms"]))
    d["lieu_naissance"] = clean_alpha(d["lieu_naissance"])
    d["profession"] = clean_alpha(d["profession"])
    # generic fallback for older CNI types (fills only empty fields)
    _parse_cni_generic(fl + bl, d)
    return d

def _parse_cni_generic(lines: List[str], d: Dict[str, Any]) -> None:
    """Layout-independent harvest for older/unknown CNI card types."""
    text = "\n".join(lines)
    up = strip_accents(text).upper()
    if not d["nom"]:
        m = re.search(r"\bNOM(?:S)?(?:\s*(?:ET\s*PRENOMS?|/SURNAME))?\s*[:.]?\s*([A-Z][A-Z .'\-]{2,40})", re.sub(r"\s+", " ", up))
        if m:
            cand = clean_alpha(m.group(1))
            if len(cand) >= 3 and "REPUBLIQUE" not in cand:
                d["nom"] = repair_glued_names(cand)
    if not d["prenoms"]:
        m = re.search(r"PRENOMS?\s*[:.]?\s*([A-Z][A-Z .'\-]{2,40})", re.sub(r"\s+", " ", up))
        if m:
            d["prenoms"] = clean_alpha(repair_glued_names(m.group(1)))
    if not d["date_naissance"]:
        m = re.search(r"N[EE]?\s*LE\s*.{0,12}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})", re.sub(r"\s+", " ", up))
        if m:
            d["date_naissance"] = find_date(m.group(0))
    if not d["numero_piece"]:
        m = re.search(r"(?:CNI|CARTE|NUMERO|NIC)[^A-Z0-9]{0,12}([A-Z]{0,3}\d{8,15})", re.sub(r"\s+", " ", up))
        if m:
            d["numero_piece"] = m.group(1)
    if not d["sexe"]:
        m = re.search(r"SEXE\s*[:.]?\s*([MF])\b", up)
        if m:
            d["sexe"] = m.group(1)
    if not d["profession"]:
        m = re.search(r"PROFESSION\s*[:.]?\s*([A-Z][A-Z .'\-]{2,30})", re.sub(r"\s+", " ", up))
        if m:
            d["profession"] = clean_alpha(m.group(1))
    if not d["lieu_naissance"]:
        for city in CM_CITIES:
            if re.search(r"\b" + city.replace("_", "[ _]*") + r"\b", up):
                d["lieu_naissance"] = city.replace("_", "'")
                break

def parse_permis(recto: Dict[str, Any], verso: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = {"nom": "", "prenoms": "", "date_naissance": "", "lieu_naissance": "",
         "delivre_le": "", "expire_le": "", "autorite": "", "reference": "",
         "numero_permis": "", "categorie": "", "serial": "",
         "ren": "", "categories_verso": [], "verso_match_expire": False}
    doc = recto
    lines = doc.get("merged_lines", []) if doc else []
    # unglue field markers fused to dates: "20224C" -> "2022 4C"
    lines = [re.sub(r"(\d)(4\s*[ABCD])", r"\1 \2", ln, flags=re.IGNORECASE) for ln in lines]
    text = "\n".join(lines)
    up = strip_accents(text).upper()
    for ln in lines:
        c = strip_accents(ln).upper().strip()
        if re.match(r"^1[\s.\-:]*[A-Z]", c) and not d["nom"]:
            d["nom"] = re.sub(r"^1[\s.\-:]*", "", c).strip(" .:-")
        elif re.match(r"^2[\s.\-:]*[A-Z]", c) and not d["prenoms"]:
            d["prenoms"] = re.sub(r"^2[\s.\-:]*", "", c).strip(" .:-")
        if re.match(r"^3[\s.\-:]+\d", c) or (c.startswith("3") and re.search(r"\d{2}.\d{2}.\d{4}", c)):
            d["date_naissance"] = d["date_naissance"] or find_date(c)
            if not d["lieu_naissance"]:
                after = re.sub(r"^3[\s.\-:]*\d{2}[.\-/]\d{2}[.\-/]\d{4}[\s.,:]*", "", c).strip(" .,:-")
                d["lieu_naissance"] = re.sub(r"[^A-Z\- ]", "", after).strip()
        if "4A" in c.replace(" ", ""):
            d["delivre_le"] = d["delivre_le"] or find_date(c)
        if "4B" in c.replace(" ", ""):
            d["expire_le"] = d["expire_le"] or find_date(c)
        if "4C" in c.replace(" ", "") and not d["autorite"]:
            rest = re.split(r"4\s*C", c)[-1]
            rest = re.sub(r"4\s*D.*$", "", rest)
            rest = re.sub(r"[^A-Z. ]", "", rest).strip(" .")
            d["autorite"] = rest
        if "4D" in c.replace(" ", "") and not d["reference"]:
            m = re.search(r"([A-Z]{2}\s*-\s*\d{3}\s*-\s*\d{3,4}\s*-\s*\d{1,2})", c)
            d["reference"] = re.sub(r"\s+", "", m.group(1)) if m else ""
        if re.match(r"^5[\s.\-:]*[A-Z0-9]", c) and not d["numero_permis"]:
            m = re.search(r"([A-Z]{2}\s*-\s*\d{4,6}\s*-\s*\d{2})", c)
            d["numero_permis"] = re.sub(r"\s+", "", m.group(1)) if m else re.sub(r"^5[\s.\-:]*", "", c).strip()
        if re.match(r"^9[\s.\-:]*", c) and not d["categorie"]:
            m = re.search(r"9[\s.\-:]*([A-E][1E]?)", c)
            if m:
                d["categorie"] = m.group(1)
    if not d["nom"]:
        # "1." marker often unread: longest alpha line just before the "2." line
        for i, ln in enumerate(lines):
            if re.match(r"^2[\s.\-:]*[A-Z]", strip_accents(ln).upper().strip()):
                cands = []
                for k in range(i - 1, max(i - 4, -1), -1):
                    c = strip_accents(lines[k]).upper().strip().strip(".:-")
                    c = re.sub(r"^1[\s.\-:]*", "", c).strip()
                    if (re.fullmatch(r"[A-Z][A-Z .'\-]{2,40}", c)
                            and not any(w in c for w in ("CAMEROUN", "REPUBLIQUE", "PERMIS", "LICENCE", "DRIVING", "CMR"))):
                        cands.append(c)
                if cands:
                    d["nom"] = max(cands, key=len)
                break
    if not d["numero_permis"]:
        m = re.search(r"\b([A-Z]{2}-\d{4,6}-\d{2})\b", up)
        d["numero_permis"] = m.group(1) if m else ""
    if not d["categorie"]:
        m = re.search(r"\b9\.\s*([A-E])\b", up)
        d["categorie"] = m.group(1) if m else ""
    if not d["serial"]:
        for ln in lines:
            c = ln.strip()
            if re.fullmatch(r"\d{6,8}", c):
                d["serial"] = c
                break
    # authority normalization (DIME I. B. / NGATOUNOU R. N. E. A. / glued "B4C.")
    if d["autorite"]:
        a = re.sub(r"^[A-Z]\s*4\s*C", "", d["autorite"].upper()).strip().strip(".")
        a = re.sub(r"\s+", " ", a).strip()
        nos = re.sub(r"[^A-Z]", "", a)
        m = re.match(r"(DIME)([A-Z])([A-Z])$", nos)
        if m:
            d["autorite"] = f"{m.group(1)} {m.group(2)}. {m.group(3)}."
        else:
            d["autorite"] = re.sub(r"\.([A-Z])", r". \1", a).title() if a else ""
    d["nom"] = clean_alpha(repair_glued_names(d["nom"]))
    d["prenoms"] = clean_alpha(repair_glued_names(d["prenoms"]))
    d["lieu_naissance"] = clean_alpha(d["lieu_naissance"])
    # --- verso: category table (REN + validity date pairs per category)
    vl = verso.get("merged_lines", []) if verso else []
    if vl:
        vt = " ".join(vl)
        vup = strip_accents(vt).upper()
        m_ren = re.search(r"REN\s*:?\s*([A-Z]{2}\s*-?\s*\d{4,6}\s*-?\s*\d{2})", vup)
        if m_ren:
            d["ren"] = re.sub(r"\s+", "", m_ren.group(1))
        seen = set()
        def _add_pair(a: str, b: str) -> None:
            pair = (find_date(a), find_date(b))
            if pair[0] and pair[1] and pair not in seen:
                seen.add(pair)
                d["categories_verso"].append({"du": pair[0], "au": pair[1]})
        # pairs on the same line (glued "24-11-201225-07-2033" or spaced) - never across lines
        for ln in vl:
            for g in re.finditer(r"(\d{2}[.\-/]\d{2}[.\-/]\d{4})\s*(\d{2}[.\-/]\d{2}[.\-/]\d{4})", ln):
                _add_pair(g.group(1), g.group(2))
        # fallback: one date per line on consecutive lines (OCR split the row)
        if not d["categories_verso"]:
            singles = [find_date(ln) for ln in vl]
            for i in range(len(singles) - 1):
                a, b = singles[i], singles[i + 1]
                if a and b and len(re.findall(r"\d{2}[.\-/]\d{2}[.\-/]\d{4}", vl[i])) == 1 \
                        and len(re.findall(r"\d{2}[.\-/]\d{2}[.\-/]\d{4}", vl[i + 1])) == 1:
                    _add_pair(a, b)
        if d["categories_verso"] and d["expire_le"]:
            d["verso_match_expire"] = any(p["au"] == d["expire_le"] for p in d["categories_verso"])
    return d

def split_company_name(v: str) -> str:
    """Insert spaces before company keywords in glued titulaire tokens."""
    v = strip_accents(v or "").upper().strip()
    v = re.sub(r"(?<=[A-Z])(STE|SARLU|SARL|ETS|LOCATAIRE|GROUPE|COMPANY)(?=[A-Z]|$)", r" \1", v)
    v = re.sub(r"(^|\s)(STE|ETS)([A-Z]{4,})", r"\1\2 \3", v)
    v = re.sub(r"\s+", " ", v).strip()
    return clean_alpha(repair_glued_names(v))

CG_LABELS = ["MARQUEDUVEHICULE", "VEHICLEMARK", "GENREDEVEHICULE", "TYPEOFVEHICLE",
    "MODELE", "MODEL", "VEHICULEGAGE", "PLEDGEDVEHICLE", "CARROSSERIE", "CARBODY",
    "ENERGIE", "POWERSOURCE", "CYLINDRE", "ENGINECAPACITY", "PUISSANCE", "POWER",
    "POIDSTOTAL", "TOTALAUTHORIZED", "POIDSAVIDE", "NETWEIGHT", "CHARGEUTILE",
    "CARRYINGCAPACITY", "MISEENCIRCULATION", "DATEFIRSTOFUSE", "PLACESASSISES",
    "NUMBEROFSEATS", "CENTRESSDT", "SSDT", "DELIVRE", "ISSUEDON", "DUPLICATA", "ORIGINAL"]

def parse_carte_grise(docs: List[Dict[str, Any]]) -> Dict[str, Any]:
    d = {"marque": "", "modele": "", "genre": "", "carrosserie": "", "energie": "",
         "cylindree": "", "puissance": "", "poids_total": "", "poids_vide": "",
         "charge_utile": "", "mise_circulation": "", "places": "", "centre_ssdt": "",
         "delivre_le": "", "delivre_a": "", "serial": "",
         "immatriculation": "", "titulaire": "", "titulaire_type": "", "numero_serie": "",
         "validite_du": "", "validite_au": "", "ssdt_id": "", "gage": "", "adresse_titulaire": "",
         "prec_immat": ""}
    lines: List[str] = []
    for doc in docs:
        lines += doc.get("merged_lines", [])
    text = "\n".join(lines)
    up = strip_accents(text).upper()

    def val(keys: List[str], **kw) -> str:
        return value_after_label(lines, keys, stop_keys=CG_LABELS, **kw)

    # --- marque: direct search in makes dictionary (2-column layout safe)
    for ln in lines:
        ns = nospace(ln)
        for mk in VEHICLE_MAKES:
            if mk in ns and len(ns) <= 30:
                d["marque"] = mk
                break
        if d["marque"]:
            break
    # --- genre: direct search
    for ln in lines:
        ns = nospace(ln)
        if "VOITUREDETOURISME" in ns:
            d["genre"] = "VOITURE DE TOURISME"
            break
        if "MOTOCYCLE" in ns:
            d["genre"] = "MOTOCYCLETTES"
            break
    if not d["genre"]:
        d["genre"] = val(["GENREDEVEHICULE", "TYPEOFVEHICLE"], same_line=False).upper()
    # --- modele: window after MODELE label with strict validation (keep spaces)
    for i, ln in enumerate(lines):
        if "MODELE" in nospace(ln) or "MODEL" in nospace(ln):
            for j in range(i + 1, min(i + 5, len(lines))):
                raw = strip_accents(lines[j]).upper().strip()
                cand = re.sub(r"[^A-Z0-9\-]", "", raw)
                if (2 <= len(cand) <= 14 and re.search(r"[A-Z]", cand)
                        and not re.match(r"^(OU\d+|LT\d+|[A-Z]{2}\d{2,4}|NON|OUI|ESS.+|CARO.+|VEHI.+|CENTR.+|SSDT.+)$", cand)
                        and not any(s in cand for s in ("DUVEHICULE", "TYPEOF", "POWER"))):
                    d["modele"] = re.sub(r"[^A-Z0-9\- ]", "", raw).strip()
                    break
            if d["modele"]:
                break
    # --- energie: direct search for short fuel codes
    for ln in lines:
        c = re.sub(r"[^A-Z]", "", strip_accents(ln).upper())
        if c in ("ESS", "ESSENCE", "GASOIL", "DIESEL", "GAZOLE", "ELECTRIQUE", "HYBRIDE", "GPL"):
            d["energie"] = "ESSENCE" if c in ("ESS", "ESSENCE") else c
            break
    # --- carrosserie: whitelist search
    for ln in lines:
        toks = re.findall(r"[A-Z][A-Z\-]{1,12}", strip_accents(ln).upper())
        for t in toks:
            if t in CARROSSERIES:
                d["carrosserie"] = t
                break
        if d["carrosserie"]:
            break
    # --- cylindree: full-text harvest
    m_cyl = re.search(r"\b(\d{3,4})\s*CM3\b", up)
    d["cylindree"] = f"{m_cyl.group(1)} CM3" if m_cyl else ""
    # --- puissance (strict: digits + CV only, never label garbage)
    d["puissance"] = ""
    m_puis = re.search(r"\b(\d{1,2})\s*CV\b", up)
    if m_puis:
        d["puissance"] = f"{m_puis.group(1)} CV"
    else:
        cand = val(["PUISSANCE", "POWER"], same_line=False)
        m2 = re.search(r"(\d{1,2})\s*CV", strip_accents(cand).upper())
        if m2:
            d["puissance"] = f"{m2.group(1)} CV"
    # --- centre SSDT: code after CENTRE label (OU001, LT001, ...), OU fallback
    for i, ln in enumerate(lines):
        if "CENTRE" in nospace(ln):
            for j in range(i + 1, min(i + 3, len(lines))):
                m_c = re.search(r"\b([A-Z]{2})\s*(\d{2,4})\b", strip_accents(lines[j]).upper())
                if m_c:
                    d["centre_ssdt"] = m_c.group(1) + m_c.group(2)
                    break
            if d["centre_ssdt"]:
                break
    if not d["centre_ssdt"]:
        m_ou = re.search(r"\b(OU\s*\d{2,4})\b", up)
        d["centre_ssdt"] = re.sub(r"\s+", "", m_ou.group(1)) if m_ou else ""
    # weights: all KG values in reading order -> total, vide, utile
    kgs = re.findall(r"\b(\d{2,5})\s*KG\b", up)
    if len(kgs) >= 1:
        d["poids_total"] = f"{kgs[0]} KG"
    if len(kgs) >= 2:
        d["poids_vide"] = f"{kgs[1]} KG"
    if len(kgs) >= 3:
        d["charge_utile"] = f"{kgs[2]} KG"
    # dates: mise en circulation (first date near label), delivre (second zone)
    m_mise = re.search(r"CIRCULATION.{0,40}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})", re.sub(r"\s+", " ", up))
    d["mise_circulation"] = find_date(m_mise.group(0)) if m_mise else ""
    m_del = re.search(r"(?:DELIVRE|ISSUED).{0,40}?(\d{2}[.\-/]\d{2}[.\-/]\d{4})\s*([A-Z\- ]{3,20})?",
                      re.sub(r"\s+", " ", up))
    if m_del:
        d["delivre_le"] = find_date(m_del.group(0))
        if m_del.group(2):
            d["delivre_a"] = re.sub(r"[^A-Z\- ]", "", m_del.group(2)).strip()
    if not d["mise_circulation"] or not d["delivre_le"]:
        dates = re.findall(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b", text)
        if dates and not d["mise_circulation"]:
            d["mise_circulation"] = find_date(dates[0])
        if len(dates) > 1 and not d["delivre_le"]:
            d["delivre_le"] = find_date(dates[1])
    # delivre_a: Cameroon cities dictionary search
    for city in CM_CITIES:
        if re.search(r"\b" + city.replace("_", "[ _]*") + r"\b", up):
            d["delivre_a"] = city.replace("_", "'")
            break
    # places: standalone 1-2 digit line after the PLACES label
    start = 0
    for i, ln in enumerate(lines):
        if "PLACESASSISES" in nospace(ln) or "NUMBEROFSEATS" in nospace(ln):
            start = i
            break
    for ln in lines[start:start + 14]:
        c = ln.strip().strip(":.,()")
        if re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= 60:
            d["places"] = c
            break
    if not d["places"]:
        for i, ln in enumerate(lines):
            if "PLACESASSISES" in nospace(ln) or "NUMBEROFSEATS" in nospace(ln):
                for cand in lines[i:i + 14]:
                    c = cand.strip().strip(":.,()")
                    if re.fullmatch(r"\d{1,2}", c) and 1 <= int(c) <= 60:
                        d["places"] = c
                        break
                if d["places"]:
                    break
    # serial: 13 digits + DUPLICATA/ORIGINAL
    m_ser = re.search(r"(\d{12,14})\s*-\s*(DUPLICATA|ORIGINA\w*|ORIGINAL)", up)
    d["serial"] = f"{m_ser.group(1)}-{m_ser.group(2)}" if m_ser else ""
    # --- NEW-FORMAT FRONT SIDE (Certificat d'Immatriculation) ---
    # plate: window after IMMATRICULATION label (handles glued "LT991NN")
    for i, ln in enumerate(lines):
        ns = nospace(ln)
        if "IMMATRICULATION" in ns or "REGISTRATIONNUMBER" in ns:
            for j in range(i + 1, min(i + 4, len(lines))):
                tok = nospace(lines[j])
                m = re.fullmatch(r"([A-Z]{2})(\d{3,4})([A-Z]{1,2})", tok)
                if m and "CMR" not in tok:
                    d["immatriculation"] = f"{m.group(1)} {m.group(2)} {m.group(3)}"
                    break
            if d["immatriculation"]:
                break
    # previous plate (NEUF or old number) after PREC. IMMAT label
    for i, ln in enumerate(lines):
        ns = nospace(ln)
        if "PREC" in ns and ("IMMAT" in ns or "PREV" in ns or "REGIS" in ns):
            for j in range(i + 1, min(i + 6, len(lines))):
                t = re.sub(r"[^A-Z0-9 ]", "", strip_accents(lines[j]).upper()).strip()
                if t in ("NEUF", "NEUVE", "NEUVES"):
                    d["prec_immat"] = "NEUF"
                    break
                m = re.fullmatch(r"([A-Z]{2})\s*(\d{3,4})\s*([A-Z]{1,2})", t)
                if m and "CMR" not in t:
                    d["prec_immat"] = f"{m.group(1)} {m.group(2)} {m.group(3)}"
                    break
            break
    # VIN: window after CHASSIS label (17 chars, spaces tolerated)
    for i, ln in enumerate(lines):
        if "CHASSIS" in nospace(ln):
            for j in range(i + 1, min(i + 4, len(lines))):
                tok = re.sub(r"[^A-Z0-9]", "", strip_accents(lines[j]).upper())
                if re.fullmatch(r"[A-Z0-9]{17}", tok) and not re.search(r"[IOQ]", tok):
                    d["numero_serie"] = tok
                    break
            if d["numero_serie"]:
                break
    # titulaire (person or company) + address + SSDT ID
    d["titulaire"] = val(["NOMETPRENOM", "NAMEANDSURNAME"], same_line=False, max_jump=3)
    d["titulaire"] = split_company_name(d["titulaire"])
    if re.search(r"STE|SARL|ETS|GROUPE|LOCATAIRE|COMPANY", nospace(d["titulaire"])):
        d["titulaire_type"] = "societe"
    elif d["titulaire"]:
        d["titulaire_type"] = "personne"
    for i, ln in enumerate(lines):
        if "ADRESSE" in nospace(ln) or "ADRESS" in nospace(ln):
            window = " ".join(lines[i + 1:i + 5])
            m_bp = re.search(r"\bBP\s*(\d{2,6})\s*([A-Z]{2,12})?", strip_accents(window).upper())
            if m_bp:
                d["adresse_titulaire"] = f"BP {m_bp.group(1)}" + (f" {m_bp.group(2)}" if m_bp.group(2) else "")
                break
    m_ssdt = re.search(r"\b([A-Z]\d{6,8})\b", up)
    d["ssdt_id"] = m_ssdt.group(1) if m_ssdt else ""
    # validity: two dates after VALIDE...AU labels
    for i, ln in enumerate(lines):
        if "VALIDE" in nospace(ln) or "VALIDFROM" in nospace(ln):
            found = []
            for j in range(i + 1, min(i + 9, len(lines))):
                for mdd in re.finditer(r"\b\d{2}[.\-/]\d{2}[.\-/]\d{4}\b", lines[j]):
                    found.append(find_date(mdd.group(0)))
            if len(found) >= 1:
                d["validite_du"] = found[0]
            if len(found) >= 2:
                d["validite_au"] = found[1]
            break
    # gage: OUI / NON after label
    gag = val(["VEHICULEGAGE", "PLEDGEDVEHICLE"], same_line=False, max_jump=3)
    if not gag:
        for i, ln in enumerate(lines):
            if "VEHICULEGAGE" in nospace(ln) or "PLEDGEDVEHICLE" in nospace(ln):
                for j in range(i + 1, min(i + 8, len(lines))):
                    if strip_accents(lines[j]).upper().strip() in ("OUI", "NON"):
                        gag = lines[j].strip()
                        break
                break
    if re.fullmatch(r"\s*(OUI|NON)\s*", strip_accents(gag).upper()):
        d["gage"] = strip_accents(gag).upper().strip()
    # --- old-format fallback (ancien modele): immat, titulaire, chassis
    if not d["immatriculation"]:
        m_im = re.search(r"\b([A-Z]{2})\s*-?\s*(\d{3,4})\s*-?\s*([A-Z]{1,2})\b", up)
        if m_im and "CMR" not in m_im.group(0):
            d["immatriculation"] = f"{m_im.group(1)} {m_im.group(2)} {m_im.group(3)}"
    if not d["numero_serie"]:
        m_ch = re.search(r"CHASSIS.{0,30}?([A-Z0-9 ]{14,24})", re.sub(r"\s+", " ", up))
        if m_ch:
            cand = re.sub(r"[^A-Z0-9]", "", m_ch.group(1))
            if len(cand) >= 14:
                d["numero_serie"] = cand[:17]
    if not d["titulaire"]:
        m_tit = re.search(r"TITULAIRE.{0,10}?([A-Z][A-Z .\-]{4,40})", re.sub(r"\s+", " ", up))
        if m_tit:
            d["titulaire"] = split_company_name(m_tit.group(1).strip(" .:-"))
            d["titulaire_type"] = "personne" if d["titulaire"] else ""
    # energie/carrosserie short values cleanup
    d["energie"] = re.sub(r"[^A-Z/]", "", d["energie"].upper())[:12]
    d["carrosserie"] = re.sub(r"[^A-Z0-9/ ]", "", d["carrosserie"].upper())[:16]
    d["marque"] = re.sub(r"[^A-Z0-9\- ]", "", d["marque"].upper()).strip()[:24]
    d["modele"] = re.sub(r"[^A-Z0-9\- ]", "", d["modele"].upper()).strip()[:24]
    d["centre_ssdt"] = re.sub(r"[^A-Z0-9]", "", d["centre_ssdt"].upper())[:8]
    return d

# ============================================================ VERIFICATION
def esc(v: Any) -> str:
    """Escape dynamic text before HTML/PDF rendering (prevents markup injection)."""
    return html.escape(str(v), quote=True) if v is not None else ""

def check(status: str, label: str, detail: str = "") -> Dict[str, str]:
    return {"status": status, "label": esc(label), "detail": esc(detail)}

def verify_all(cni: Dict[str, Any], permis: Dict[str, Any], cg: Dict[str, Any],
               cni_docs: int, cg_docs: int, pp_docs: int = 1) -> Tuple[List[Dict[str, str]], int]:
    checks: List[Dict[str, str]] = []
    today = datetime.date.today()
    # 1. names CNI <-> permis
    n1 = f"{cni.get('nom','')} {cni.get('prenoms','')}"
    n2 = f"{permis.get('nom','')} {permis.get('prenoms','')}"
    if norm_name(n1) and norm_name(n2):
        s = name_sim(n1, n2)
        if s >= 0.85:
            checks.append(check("ok", "Identite CNI / Permis concordante", f"Similarite {s:.0%}"))
        elif s >= 0.6:
            checks.append(check("warn", "Ecart de nom CNI / Permis - verifier", f"Similarite {s:.0%}"))
        else:
            checks.append(check("fail", "Nom CNI / Permis differents", f"Similarite {s:.0%}"))
    else:
        checks.append(check("na", "Identite CNI / Permis", "Donnee manquante"))
    # 2. DOB CNI <-> permis
    if cni.get("date_naissance") and permis.get("date_naissance"):
        if cni["date_naissance"] == permis["date_naissance"]:
            checks.append(check("ok", "Date de naissance concordante", cni["date_naissance"]))
        else:
            checks.append(check("fail", "Dates de naissance differentes",
                                f"CNI: {cni['date_naissance']} / Permis: {permis['date_naissance']}"))
    else:
        checks.append(check("na", "Date de naissance", "Donnee manquante"))
    # 3. titulaire CG (personne ou societe / leasing)
    if cg.get("titulaire"):
        if cg.get("titulaire_type") == "societe":
            checks.append(check("warn", "Titulaire = societe (gage / leasing ?)", f"{cg['titulaire']} - verifier le lien avec l'assure"))
        elif norm_name(n1):
            s = name_sim(cg["titulaire"], n1)
            if s >= 0.7:
                checks.append(check("ok", "Titulaire carte grise = assure", f"Similarite {s:.0%}"))
            else:
                checks.append(check("warn", "Titulaire carte grise different - verifier", cg["titulaire"]))
        else:
            checks.append(check("na", "Titulaire carte grise", cg["titulaire"]))
    else:
        checks.append(check("na", "Titulaire carte grise", "Non lisible : photographier la face administrative"))
    # 4. permis validity
    pe = to_iso(permis.get("expire_le", ""))
    if pe:
        if pe >= today:
            checks.append(check("ok", "Permis de conduire en cours de validite", f"Expire le {permis['expire_le']}"))
        else:
            checks.append(check("fail", "Permis de conduire EXPIRE", f"Expire le {permis['expire_le']}"))
    else:
        checks.append(check("na", "Validite du permis", "Date 4b non lue"))
    # 5. CNI validity
    ce = to_iso(cni.get("date_expiration", ""))
    if ce:
        if ce >= today:
            checks.append(check("ok", "CNI en cours de validite", f"Expire le {cni['date_expiration']}"))
        else:
            checks.append(check("fail", "CNI EXPIREE", f"Expiree le {cni['date_expiration']}"))
    else:
        checks.append(check("na", "Validite de la CNI", "Date non lue"))
    # 6. completeness per doc
    cni_keys = ["nom", "date_naissance", "numero_piece"]
    cni_hit = sum(1 for k in cni_keys if cni.get(k))
    checks.append(check("ok" if cni_hit == 3 else "warn", f"CNI lisible ({cni_hit}/3 champs cles)",
                        "Nom + Naissance + N° CNI" + ("" if cni_hit == 3 else " - verso manquant ?" if cni_docs < 2 else "")))
    p_hit = sum(1 for k in ["nom", "numero_permis", "expire_le"] if permis.get(k))
    checks.append(check("ok" if p_hit == 3 else "warn", f"Permis lisible ({p_hit}/3 champs cles)", "Nom + N° Permis + Validite"))
    g_hit = sum(1 for k in ["marque", "puissance", "mise_circulation"] if cg.get(k))
    checks.append(check("ok" if g_hit == 3 else "warn", f"Carte grise lisible ({g_hit}/3 champs cles)", "Marque + Puissance + Mise en circulation"))
    # 9. MRZ zone (CNI verso)
    if cni.get("mrz"):
        checks.append(check("ok", "CNI : zone MRZ lue au verso", "Naissance / sexe / validite fiabilises"))
    elif cni_docs > 1:
        checks.append(check("na", "CNI : zone MRZ", "Non lue sur le verso"))
    # 10. permis verso: REN + validity vs 4b
    if permis.get("ren") and permis.get("numero_permis"):
        if nospace(permis["ren"]) == nospace(permis["numero_permis"]):
            checks.append(check("ok", "Permis : verso coherent (REN)", permis["ren"]))
        else:
            checks.append(check("fail", "Permis : REN du verso different",
                                f"Verso: {permis['ren']} / Recto: {permis['numero_permis']}"))
    elif pp_docs > 1:
        checks.append(check("warn", "Permis : verso partiellement lisible", "REN non lu"))
    if permis.get("categories_verso"):
        if permis.get("verso_match_expire"):
            checks.append(check("ok", "Permis : validite verso = date 4b", permis["categories_verso"][0]["au"]))
        else:
            checks.append(check("warn", "Permis : validite verso a controler",
                                " / ".join(f"{p['du']}->{p['au']}" for p in permis["categories_verso"][:2])))
    # 11. PTAC arithmetic: vide + charge = total
    try:
        tv, vv, cu = _poids_kg(cg.get("poids_total")), _poids_kg(cg.get("poids_vide")), _poids_kg(cg.get("charge_utile"))
    except Exception:
        tv = vv = cu = None
    if tv and vv and cu:
        if vv + cu == tv:
            checks.append(check("ok", "Poids coherents (vide + charge = total)", f"{vv} + {cu} = {tv} KG"))
        else:
            checks.append(check("fail", "Poids INCOHERENTS (vide + charge / total)",
                                f"{vv} + {cu} = {vv + cu} KG mais total lu {tv} KG"))
    elif tv or vv or cu:
        checks.append(check("na", "Coherence des poids", "Poids incomplets"))
    score = 0
    for c in checks:
        score += {"ok": 100, "warn": 55, "na": 40, "fail": 0}[c["status"]]
    return checks, round(score / max(len(checks), 1))

# ============================================================ TARIFF (from real AGC docs)
ACCESSOIRES = 2500
FRAIS_FICHIER = 250
CARTE_ROSE = 1000
TVA_TAUX = 0.1925

def prime_annuelle(genre: str, puissance_cv: int) -> int:
    g = (genre or "").upper()
    if "MOTO" in g:
        return 25000
    if puissance_cv <= 6:
        return 60000
    if puissance_cv <= 9:
        return 70877
    if puissance_cv <= 11:
        return 85000
    return 100000

def taux_courte_duree(jours: int) -> float:
    if jours <= 30:
        return 0.15
    if jours <= 60:
        return 0.20
    if jours <= 90:
        return 0.30
    if jours <= 180:
        return 0.50
    if jours <= 270:
        return 0.75
    return 1.0

def compute_prime(genre: str, puissance_cv: int, jours: int) -> Dict[str, int]:
    annuelle = prime_annuelle(genre, puissance_cv)
    nette = int(round(annuelle * taux_courte_duree(jours)))
    base_tva = nette + ACCESSOIRES + FRAIS_FICHIER
    tva = int(round(base_tva * TVA_TAUX))
    ttc = base_tva + tva + CARTE_ROSE
    return {"prime_annuelle": annuelle, "prime_nette": nette, "accessoires": ACCESSOIRES,
            "frais_fichier": FRAIS_FICHIER, "tva": tva, "carte_rose": CARTE_ROSE,
            "dta": 0, "prime_ttc": ttc, "taux": taux_courte_duree(jours)}

def categorie_tarif(genre: str) -> str:
    g = (genre or "").upper()
    if "MOTO" in g:
        return "CAT2 (MOTOS)"
    if "TOURISME" in g or "VP" in g or not g:
        return "201 - CAT1 (TOURISME)"
    return "CAT1 (TOURISME)"

# Official registration genre codes printed on contracts (order matters: longest first)
GENRE_CODES = [
    ("SEMI-REMORQUE", "SREM"), ("SEMI REMORQUE", "SREM"),
    ("TRACTEUR ROUTIER", "TRR"), ("TRACTEUR", "TRR"),
    ("TRANSPORT EN COMMUN", "TC"), ("TRANSPORT COMMUN", "TC"),
    ("VOITURE DE TOURISME", "VP"), ("VOITURE PARTICULIERE", "VP"),
    ("TOURISME", "VP"), ("PARTICULIER", "VP"),
    ("CAMIONNETTE", "VU"), ("UTILITAIRE", "VU"),
    ("MOTOCYCLETTE", "MOTO"), ("MOTOCYCLE", "MOTO"), ("MOTO", "MOTO"),
    ("CYCLOMOTEUR", "CYCL"), ("VELOMOTEUR", "CYCL"),
    ("AUTOCAR", "CAR"), ("AUTOBUS", "CAR"),
    ("AMBULANCE", "AMB"), ("AMBULANT", "AMB"),
    ("REMORQUE", "REM"), ("CAMION", "CAM"),
    ("ENGIN", "ENG"), ("TAXI", "TAXI"),
]

def genre_code(raw: str) -> str:
    """Map a Carte Grise genre wording to its official registration code."""
    g = strip_accents(raw or "").upper().strip()
    if not g:
        return ""
    if g in {code for _, code in GENRE_CODES}:
        return g
    for wording, code in GENRE_CODES:
        if wording in g:
            return code
    return g

# ============================================================ CAMEROON PHONE CHECK
def validate_cm_phone(raw: str) -> Dict[str, Any]:
    """Normalize +237 numbers, detect operator. Non-blocking: never raises."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("237") and len(digits) > 9:
        digits = digits[3:]
    digits = digits.lstrip("0")
    out = {"ok": False, "normalized": (raw or "").strip(), "operateur": "", "warning": ""}
    if not re.fullmatch(r"6\d{8}", digits):
        out["warning"] = "Format inattendu : un mobile camerounais compte 9 chiffres et commence par 6."
        return out
    p2, p3 = digits[:2], digits[:3]
    op = ""
    if p2 == "67" or (650 <= int(p3) <= 654) or (680 <= int(p3) <= 683):
        op = "MTN"
    elif p2 == "69" or (655 <= int(p3) <= 659):
        op = "Orange"
    elif p2 == "66":
        op = "Nexttel"
    elif p3 == "620":
        op = "Camtel"
    out["ok"] = True
    out["operateur"] = op
    out["normalized"] = f"+237 {digits[0]} {digits[1:3]} {digits[3:5]} {digits[5:7]} {digits[7:9]}"
    if not op:
        out["warning"] = "Préfixe inconnu : numéro plausible mais opérateur non identifié."
    return out

# ============================================================ CUSTOMER REGISTRY (SQLite)
import sqlite3

REGISTRY_DB = STORAGE_DIR / "registre.db"

def _db():
    cx = sqlite3.connect(REGISTRY_DB)
    cx.row_factory = sqlite3.Row
    cx.execute("""CREATE TABLE IF NOT EXISTS contracts(
        police_no TEXT PRIMARY KEY, quittance_no TEXT, nom TEXT, prenoms TEXT,
        telephone TEXT, operateur TEXT, numero_cni TEXT, date_naissance TEXT,
        souscripteur_pc TEXT, nui TEXT, immatriculation TEXT,
        marque TEXT, modele TEXT, categorie TEXT, effet TEXT, expiration TEXT,
        prime_ttc INTEGER, canal TEXT, paiement TEXT, created_at TEXT,
        statut TEXT DEFAULT 'actif', motif_annulation TEXT DEFAULT '',
        date_annulation TEXT DEFAULT '', branche TEXT DEFAULT '001',
        avenant_count INTEGER DEFAULT 0, police_prec TEXT DEFAULT '',
        agent TEXT DEFAULT '', data_json TEXT DEFAULT '')""")
    cols = {r[1] for r in cx.execute("PRAGMA table_info(contracts)").fetchall()}
    for col, ddl in (("date_naissance", "TEXT"), ("souscripteur_pc", "TEXT"), ("nui", "TEXT"),
                     ("statut", "TEXT DEFAULT 'actif'"), ("motif_annulation", "TEXT DEFAULT ''"),
                     ("date_annulation", "TEXT DEFAULT ''"), ("branche", "TEXT DEFAULT '001'"),
                     ("avenant_count", "INTEGER DEFAULT 0"), ("police_prec", "TEXT DEFAULT ''"),
                     ("agent", "TEXT DEFAULT ''"), ("data_json", "TEXT DEFAULT ''"),
                     ("signature_src", "TEXT DEFAULT ''")):
        if col not in cols:
            cx.execute(f"ALTER TABLE contracts ADD COLUMN {col} {ddl}")
    cx.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT DEFAULT '')""")
    cx.execute("""CREATE TABLE IF NOT EXISTS audit(
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, username TEXT,
        action TEXT, police_no TEXT DEFAULT '', detail TEXT DEFAULT '')""")
    cx.execute("""CREATE TABLE IF NOT EXISTS branches(
        code TEXT PRIMARY KEY, name TEXT NOT NULL, active INTEGER DEFAULT 1)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS sequences(
        name TEXT PRIMARY KEY, last_no INTEGER DEFAULT 0)""")
    cx.execute("""CREATE TABLE IF NOT EXISTS payments(
        id INTEGER PRIMARY KEY AUTOINCREMENT, police_no TEXT NOT NULL,
        amount INTEGER DEFAULT 0, method TEXT DEFAULT '', reference TEXT DEFAULT '',
        at TEXT, agent TEXT DEFAULT '')""")
    cx.execute("""CREATE TABLE IF NOT EXISTS avenants(
        id INTEGER PRIMARY KEY AUTOINCREMENT, police_no TEXT NOT NULL,
        numero INTEGER DEFAULT 0, type TEXT DEFAULT '', motif TEXT DEFAULT '',
        prime_ttc INTEGER DEFAULT 0, prime_diff INTEGER DEFAULT 0,
        at TEXT, agent TEXT DEFAULT '')""")
    cx.execute("INSERT OR IGNORE INTO branches(code, name) VALUES ('001', 'Agence principale')")
    for k, v in DEFAULT_SETTINGS.items():
        cx.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    cx.commit()
    return cx

# Admin-editable settings (Administration > Parametres). Seeded on first run.
DEFAULT_SETTINGS = {
    "agence_nom": "AGC Assurances - Generales du Cameroun",
    "reseau": "BUREAUX DIRECTS",
    "intermediaire": "ESPACE CLIENTS",
    "code_intermediaire": "1031",
    "lieu_emission": "Douala",
    "sauvegarde_auto": "1",
    "sauvegarde_retention": "14",
    "langue": "fr",
}

def get_setting(key: str, default: str = "") -> str:
    try:
        cx = _db()
        r = cx.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        cx.close()
        if r is not None:
            return r["value"]
    except Exception:
        pass
    return DEFAULT_SETTINGS.get(key, default)

def get_settings_all() -> Dict[str, str]:
    out = dict(DEFAULT_SETTINGS)
    try:
        cx = _db()
        for r in cx.execute("SELECT key, value FROM settings").fetchall():
            out[r["key"]] = r["value"]
        cx.close()
    except Exception:
        pass
    return out

def _int_setting(key: str, default: int) -> int:
    try:
        return max(1, int(get_setting(key, str(default))))
    except Exception:
        return default

SEQ_LOCK = threading.Lock()

def next_no(name: str) -> int:
    """Thread-safe sequential numbering (per kind / branch / year)."""
    with SEQ_LOCK:
        cx = _db()
        r = cx.execute("SELECT last_no FROM sequences WHERE name = ?", (name,)).fetchone()
        n = (r["last_no"] if r else 0) + 1
        cx.execute("INSERT OR REPLACE INTO sequences(name, last_no) VALUES (?, ?)", (name, n))
        cx.commit()
        cx.close()
        return n

def audit(username: str, action: str, police_no: str = "", detail: str = "") -> None:
    try:
        cx = _db()
        cx.execute("INSERT INTO audit(at, username, action, police_no, detail) VALUES (?, ?, ?, ?, ?)",
                   (datetime.datetime.now().isoformat(timespec="seconds"),
                    username or "", action, police_no or "", (detail or "")[:500]))
        cx.commit()
        cx.close()
    except Exception:
        pass

def registry_save(d: Dict[str, Any]) -> None:
    cx = _db()
    old = cx.execute("SELECT * FROM contracts WHERE police_no = ?",
                     (d.get("police_no"),)).fetchone()
    old = dict(old) if old else {}
    def keep(k: str, default: Any = "") -> Any:
        v = d.get(k)
        return v if v not in (None, "") else old.get(k, default)
    cx.execute("""INSERT OR REPLACE INTO contracts
        (police_no,quittance_no,nom,prenoms,telephone,operateur,numero_cni,date_naissance,
         souscripteur_pc,nui,immatriculation,marque,modele,categorie,effet,expiration,
         prime_ttc,canal,paiement,created_at,statut,motif_annulation,date_annulation,
         branche,avenant_count,police_prec,agent,data_json,signature_src) VALUES
        (:police_no,:quittance_no,:nom,:prenoms,:telephone,:operateur,:numero_cni,:date_naissance,
         :souscripteur_pc,:nui,:immatriculation,:marque,:modele,:categorie,:effet,:expiration,
         :prime_ttc,:canal,:paiement,:created_at,:statut,:motif_annulation,:date_annulation,
         :branche,:avenant_count,:police_prec,:agent,:data_json,:signature_src)""", {
        "police_no": d.get("police_no"), "quittance_no": d.get("quittance_no"),
        "nom": (d.get("nom") or "").upper(), "prenoms": (d.get("prenoms") or "").upper(),
        "telephone": d.get("telephone"), "operateur": d.get("operateur", ""),
        "numero_cni": d.get("numero_cni"), "date_naissance": d.get("date_naissance"),
        "souscripteur_pc": d.get("souscripteur_pc", ""), "nui": d.get("nui", ""),
        "immatriculation": d.get("immatriculation"),
        "marque": d.get("marque"), "modele": d.get("modele"), "categorie": d.get("categorie"),
        "effet": d.get("effet"), "expiration": d.get("expiration"),
        "prime_ttc": int(d.get("prime_ttc") or 0), "canal": d.get("canal", ""),
        "paiement": d.get("paiement", ""), "created_at": d.get("created_at"),
        "statut": keep("statut", "actif"), "motif_annulation": keep("motif_annulation"),
        "date_annulation": keep("date_annulation"), "branche": d.get("branche") or old.get("branche", "001"),
        "avenant_count": int(d.get("avenant_count", old.get("avenant_count", 0)) or 0),
        "police_prec": keep("police_prec"), "agent": keep("agent"),
        "data_json": d.get("data_json", old.get("data_json", "")),
        "signature_src": keep("signature_src")})
    cx.commit()
    cx.close()

def registry_list(q: str = "", branche: str = "", agent: str = "") -> List[Dict[str, Any]]:
    cx = _db()
    base = """SELECT c.*, COALESCE((SELECT SUM(amount) FROM payments p
                WHERE p.police_no = c.police_no), 0) AS encaisse FROM contracts c"""
    clauses, params = [], {}
    if q:
        clauses.append("""(nom LIKE :q OR prenoms LIKE :q OR telephone LIKE :q OR
            police_no LIKE :q OR immatriculation LIKE :q OR numero_cni LIKE :q OR
            nui LIKE :q OR souscripteur_pc LIKE :q)""")
        params["q"] = f"%{q.upper()}%"
    if branche:
        clauses.append("branche = :b")
        params["b"] = branche
    if agent:
        clauses.append("agent = :a")
        params["a"] = agent
    sql = base + (" WHERE " + " AND ".join(clauses) if clauses else "")
    sql += " ORDER BY nom COLLATE NOCASE, prenoms COLLATE NOCASE"
    rows = cx.execute(sql, params).fetchall()
    users = {"agence": "Agence"}
    cx.close()
    out = []
    for r in rows:
        d = dict(r)
        d.pop("data_json", None)
        d["agent_display"] = users.get(d.get("agent") or "", d.get("agent") or "")
        out.append(d)
    return out

# Number-to-words lives in pdfs.py (PDF engine); re-exported here.
from pdfs import montant_en_lettres  # noqa: E402

# ============================================================ API MODELS
class PrimeRequest(BaseModel):
    genre: str = ""
    puissance_cv: int = 7
    jours: int = 365

class ContractSubmission(BaseModel):
    # souscripteur
    titre: str = "MONSIEUR"
    nom: str = Field(..., min_length=1)
    prenoms: str = ""
    date_naissance: str = ""
    lieu_naissance: str = ""
    numero_cni: str = ""
    adresse: str = "BP YDE"
    telephone: str = Field(..., min_length=6)
    email: str = ""
    profession: str = ""
    # vehicule
    immatriculation: str = ""
    marque: str = ""
    modele: str = ""
    genre: str = ""
    energie: str = ""
    places: str = ""
    puissance: str = ""
    puissance_cv: int = 7
    cylindree: str = ""
    poids_vide: str = ""
    charge_utile: str = ""
    carrosserie: str = ""
    mise_circulation: str = ""
    numero_serie: str = ""
    categorie: str = "201 - CAT1 (TOURISME)"
    # permis / conducteur
    conducteur: str = ""
    numero_permis: str = ""
    permis_delivre: str = ""
    permis_expire: str = ""
    permis_categorie: str = ""
    # police
    effet: str = ""
    duree_jours: int = 365
    expiration: str = ""
    mouvement: str = "Affaire nouvelle"
    intermediaire: str = "ESPACE CLIENTS"
    code_intermediaire: str = "1031"
    reseau: str = "BUREAUX DIRECTS"
    lieu_emission: str = "Douala"
    # prime
    prime_annuelle: int = 0
    prime_nette: int = 0
    accessoires: int = ACCESSOIRES
    frais_fichier: int = FRAIS_FICHIER
    tva: int = 0
    carte_rose: int = CARTE_ROSE
    dta: int = 0
    prime_ttc: int = 0
    signature_data: str = ""
    signature_src: str = "agence"
    canal: str = "Agence"
    paiement: str = "Payé"
    souscripteur_pc: str = ""
    nui: str = ""
    poids_total: str = ""
    ptac: str = ""
    branche: str = "001"
    avenant_of: str = ""
    avenant_type: str = ""
    avenant_motif: str = ""
    police_prec: str = ""

# ============================================================ ENDPOINTS
@app.get("/api/status")
async def status():
    return {"status": "online", "version": app.version,
            "tesseract": TESSERACT_OK,
            "rapidocr": get_rapid() is not None,
            "cloud": False,
            "auth": "open",
            "langue": get_setting("langue", "fr")}

def _auth(request: Request) -> Dict[str, Any]:
    """Open mode: single shared superviseur identity, no session token."""
    return {"username": "agence", "display": "Agence", "role": "superviseur"}

def _require(user: Dict[str, Any], *roles: str) -> None:
    return None

def _scope_agent(user: Dict[str, Any]) -> str:
    """No per-agent isolation in open mode: full visibility."""
    return ""

def _own_row(police_no: str, user: Dict[str, Any]):
    """Fetch a contract row (404 if missing)."""
    cx = _db()
    r = cx.execute("SELECT * FROM contracts WHERE police_no = ?", (police_no,)).fetchone()
    cx.close()
    if not r:
        raise HTTPException(404, "Dossier introuvable.")
    return r

_CID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

def _valid_cid(cid: str) -> str:
    """Strict contract id validation (blocks path traversal on file routes)."""
    if not _CID_RE.fullmatch(cid or ""):
        raise HTTPException(400, "Identifiant de contrat invalide.")
    return cid

# OCR job queue: single local worker thread, $0, no broker (fits single-PC deployment).
# /storage is never statically mounted; files are only served through validated routes.
OCR_EXEC = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()

def _job_set(job_id: str, patch: Dict[str, Any]) -> None:
    with JOBS_LOCK:
        JOBS.setdefault(job_id, {}).update(patch)
        while len(JOBS) > 20:
            JOBS.pop(next(iter(JOBS)))

def run_analyze_job(job_id: str, F: Dict[str, Any]) -> None:
    """Heavy OCR + parsing in the background worker. Never raises to the caller."""
    try:
        _job_set(job_id, {"status": "running"})
        ocr_cni_r = ocr_document(F["cni_r"])
        ocr_cg_r = ocr_document(F["cg_r"])
        ocr_permis = ocr_document(F["pp"])
        ocr_cni_v = ocr_document(F["cni_v"]) if F["cni_v"] else None
        ocr_cg_v = ocr_document(F["cg_v"]) if F["cg_v"] else None
        ocr_pp_v = ocr_document(F["pp_v"]) if F.get("pp_v") else None

        cni = parse_cni(ocr_cni_r, ocr_cni_v)
        cg = parse_carte_grise([ocr_cg_r] + ([ocr_cg_v] if ocr_cg_v else []))
        perm = parse_permis(ocr_permis, ocr_pp_v)

        # Local repair for glued names (offline dictionary, $0 - spacing fixes only)
        try:
            for holder in (cni, perm):
                for k in ("nom", "prenoms"):
                    if holder.get(k):
                        fixed = repair_glued_names(holder[k])
                        if norm_name(fixed) == norm_name(holder[k]):
                            holder[k] = fixed.upper()
        except Exception:
            pass

        checks, score = verify_all(cni, perm, cg, 2 if ocr_cni_v else 1, 2 if ocr_cg_v else 1,
                                     2 if ocr_pp_v else 1)

        # merged contract defaults
        m_cv = re.search(r"(\d{1,2})", cg.get("puissance", ""))
        cv = int(m_cv.group(1)) if m_cv else 7
        prime = compute_prime(cg.get("genre", ""), cv, 365)
        today = datetime.date.today()
        _pb = f"{perm.get('prenoms','')} {perm.get('nom','')}".strip()
        _cb = f"{cni.get('prenoms','')} {cni.get('nom','')}".strip()
        _cond = (_pb + (f" (Cat. {perm.get('categorie')})" if perm.get("categorie") else "")) if _pb else _cb
        merged_sources = {
            "nom": "permis" if perm.get("nom") else ("cni" if cni.get("nom") else ""),
            "prenoms": "permis" if perm.get("prenoms") else ("cni" if cni.get("prenoms") else ""),
        }
        merged = {
            "titre": "MADAME" if cni.get("sexe") == "F" else "MONSIEUR",
            "nom": perm.get("nom") or cni.get("nom"),
            "prenoms": perm.get("prenoms") or cni.get("prenoms"),
            "date_naissance": cni.get("date_naissance") or perm.get("date_naissance"),
            "lieu_naissance": cni.get("lieu_naissance") or perm.get("lieu_naissance"),
            "numero_cni": cni.get("numero_piece"),
            "profession": cni.get("profession"),
            "adresse": cg.get("adresse_titulaire") or "",
            "immatriculation": cg.get("immatriculation"),
            "marque": cg.get("marque"), "modele": cg.get("modele"), "genre": cg.get("genre"),
            "energie": cg.get("energie"), "places": cg.get("places"), "puissance": cg.get("puissance"),
            "puissance_cv": cv, "cylindree": cg.get("cylindree"), "poids_vide": cg.get("poids_vide"),
            "charge_utile": cg.get("charge_utile"), "poids_total": cg.get("poids_total"), "ptac": "",
            "carrosserie": cg.get("carrosserie"),
            "mise_circulation": cg.get("mise_circulation"), "numero_serie": cg.get("numero_serie"),
            "categorie": categorie_tarif(cg.get("genre", "")),
            "conducteur": _cond,
            "numero_permis": perm.get("numero_permis"), "permis_delivre": perm.get("delivre_le"),
            "permis_expire": perm.get("expire_le"), "permis_categorie": perm.get("categorie"),
            "effet": today.strftime("%d/%m/%Y"),
            "duree_jours": 365,
            "expiration": (today + datetime.timedelta(days=365)).strftime("%d/%m/%Y"),
            **prime,
        }

        def preview(b: bytes, mime: str) -> str:
            return f"data:{mime};base64," + base64.b64encode(b).decode()

        docs = {
            "cni": {"data": cni, "faces": 2 if ocr_cni_v else 1,
                    "engines": sorted(set(ocr_cni_r["engines"] + (ocr_cni_v["engines"] if ocr_cni_v else []))),
                    "deskew": [ocr_cni_r["deskew_angle"]] + ([ocr_cni_v["deskew_angle"]] if ocr_cni_v else []),
                    "preview": preview(F["cni_r"], F["mime_cni_r"]),
                    "preview_verso": preview(F["cni_v"], "image/jpeg") if F["cni_v"] else None,
                    "clean": ocr_cni_r["clean_preview"]},
            "carte_grise": {"data": cg, "faces": 2 if ocr_cg_v else 1,
                            "engines": sorted(set(ocr_cg_r["engines"] + (ocr_cg_v["engines"] if ocr_cg_v else []))),
                            "deskew": [ocr_cg_r["deskew_angle"]] + ([ocr_cg_v["deskew_angle"]] if ocr_cg_v else []),
                            "preview": preview(F["cg_r"], F["mime_cg_r"]),
                            "preview_verso": preview(F["cg_v"], "image/jpeg") if F["cg_v"] else None,
                            "clean": ocr_cg_r["clean_preview"]},
            "permis": {"data": perm, "faces": 2 if ocr_pp_v else 1,
                       "engines": sorted(set(ocr_permis["engines"] + (ocr_pp_v["engines"] if ocr_pp_v else []))),
                       "deskew": [ocr_permis["deskew_angle"]] + ([ocr_pp_v["deskew_angle"]] if ocr_pp_v else []),
                       "preview": preview(F["pp"], F["mime_pp"]),
                       "preview_verso": preview(F["pp_v"], "image/jpeg") if F.get("pp_v") else None,
                       "clean": ocr_permis["clean_preview"]},
        }
        _job_set(job_id, {"status": "done", "documents": docs, "checks": checks,
                          "score": score, "merged": merged,
                          "merged_sources": merged_sources})
    except Exception as e:
        _job_set(job_id, {"status": "error", "error": str(e)[:300]})

@app.get("/api/job/{job_id}")
async def job_status(job_id: str, request: Request):
    _auth(request)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Tache introuvable.")
    return {"job_id": job_id, **job}

@app.post("/api/analyze")
async def analyze(request: Request,
                  cni_recto: UploadFile = File(...),
                  permis: UploadFile = File(...),
                  cg_recto: UploadFile = File(...),
                  cni_verso: Optional[UploadFile] = File(None),
                  cg_verso: Optional[UploadFile] = File(None),
                  permis_verso: Optional[UploadFile] = File(None)):
    """Triple-document OCR check: CNI + Carte Grise + Permis (queued, non-blocking)."""
    _auth(request)
    for up in [cni_recto, permis, cg_recto] + [x for x in [cni_verso, cg_verso, permis_verso] if x and x.filename]:
        ct = (up.content_type or "").lower()
        ext = (up.filename or "").lower().rsplit(".", 1)[-1] if "." in (up.filename or "") else ""
        if not ct.startswith("image/") or ext not in ("jpg", "jpeg", "png", "webp", "bmp"):
            raise HTTPException(400, f"Fichier refuse ({up.filename}) : seules les photos (JPG/PNG/WebP) sont acceptees.")

    async def save(up: UploadFile, tag: str) -> Tuple[bytes, str]:
        content = await up.read()
        ext = (up.filename or "jpg").split(".")[-1][:4] or "jpg"
        name = f"{datetime.date.today().strftime('%Y%m%d')}_{tag}_{uuid.uuid4().hex[:6]}.{ext}"
        with open(UPLOADS_DIR / name, "wb") as f:
            f.write(content)
        return content, name

    cni_r, _ = await save(cni_recto, "cni-r")
    cg_r, _ = await save(cg_recto, "cg-r")
    pp, _ = await save(permis, "permis")
    cni_v = await save(cni_verso, "cni-v") if cni_verso and cni_verso.filename else (None, None)
    cg_v = await save(cg_verso, "cg-v") if cg_verso and cg_verso.filename else (None, None)
    pp_v = await save(permis_verso, "pp-v") if permis_verso and permis_verso.filename else (None, None)

    job_id = uuid.uuid4().hex
    _job_set(job_id, {"status": "queued"})
    OCR_EXEC.submit(run_analyze_job, job_id, {
        "cni_r": cni_r, "cg_r": cg_r, "pp": pp, "cni_v": cni_v[0], "cg_v": cg_v[0],
        "pp_v": pp_v[0],
        "mime_cni_r": cni_recto.content_type or "image/jpeg",
        "mime_cg_r": cg_recto.content_type or "image/jpeg",
        "mime_pp": permis.content_type or "image/jpeg"})
    return JSONResponse({"status": "queued", "job_id": job_id})

@app.post("/api/prime")
async def prime(request: Request, req: PrimeRequest):
    _auth(request)
    return compute_prime(req.genre, req.puissance_cv, req.jours)

def _poids_kg(v: Any) -> Optional[int]:
    """First integer found in a weight string ('2 500 KG' -> 2500)."""
    if v is None:
        return None
    m = re.search(r"(\d[\d\s]*)", str(v).replace(",", " "))
    if not m:
        return None
    try:
        return int(m.group(1).replace(" ", ""))
    except ValueError:
        return None

@app.post("/api/generate-contract")
async def generate_contract(request: Request, payload: ContractSubmission):
    user = _auth(request)
    agent = user.get("username", "")
    today = datetime.date.today()
    data = payload.model_dump()
    warnings: List[str] = []
    ph = validate_cm_phone(data.get("telephone", ""))
    if ph["ok"]:
        data["telephone"] = ph["normalized"]
        data["operateur"] = ph["operateur"]
        if ph["warning"]:
            warnings.append(ph["warning"])
    else:
        data["operateur"] = ""
        warnings.append(ph["warning"] or "Numero de telephone a verifier.")
    # PTAC = poids a vide + charge utile. A mismatch with the CG total BLOCKS emission.
    vide, utile, total = (_poids_kg(data.get(k)) for k in ("poids_vide", "charge_utile", "poids_total"))
    if vide is not None and utile is not None:
        ptac = vide + utile
        data["ptac"] = f"{ptac:,} KG".replace(",", " ")
        if total is not None and abs(total - ptac) > 50:
            raise HTTPException(422, f"PTAC incoherent : total CG ({total:,} kg) != vide + charge utile "
                                     f"({ptac:,} kg). Corrigez les poids avant emission.".replace(",", " "))
    # Standardize genre wording to its official registration code on all outputs
    data["genre"] = genre_code(data.get("genre", ""))
    # Never trust a missing/invalid expiration (direct API use): effet + duree wins.
    if not _parse_fr(data.get("expiration")):
        try:
            eff = datetime.datetime.strptime((data.get("effet") or "").strip(), "%d/%m/%Y").date()
        except Exception:
            eff = datetime.date.today()
            data["effet"] = eff.strftime("%d/%m/%Y")
        try:
            _duree = int(data.get("duree_jours") or 365)
        except Exception:
            _duree = 365
        data["expiration"] = (eff + datetime.timedelta(days=_duree)).strftime("%d/%m/%Y")
    # Signature: on-screen (agence), client photo (photo), or deferred (differee).
    src = (data.get("signature_src") or "agence").strip().lower()
    if src not in ("agence", "photo", "differee"):
        raise HTTPException(422, "Source de signature invalide.")
    data["signature_src"] = src
    if src == "differee":
        data["signature_data"] = ""
    elif len(data.get("signature_data") or "") < 100:
        raise HTTPException(422, "Signature manquante : signer, joindre la photo, ou cocher differee.")
    branch = (data.get("branche") or "001").strip() or "001"
    cx = _db()
    brow = cx.execute("SELECT name FROM branches WHERE code = ? AND active = 1", (branch,)).fetchone()
    cx.close()
    if not brow:
        raise HTTPException(422, f"Agence inconnue ou inactive : {branch}.")
    year = today.year
    avenant_no = 0
    prime_diff = 0
    if data.get("avenant_of"):
        base = data["avenant_of"]
        cx = _db()
        orow = cx.execute("SELECT * FROM contracts WHERE police_no = ?", (base,)).fetchone()
        cx.close()
        if not orow:
            raise HTTPException(404, "Police d'origine introuvable.")
        if (orow["statut"] or "actif") != "actif":
            raise HTTPException(422, "Police annulee : avenant impossible.")
        police_no = base
        branch = orow["branche"] or branch
        avenant_no = int(orow["avenant_count"] or 0) + 1
        quitt_no = f"{data.get('code_intermediaire') or '1031'}/{year}/{next_no(f'quittance:{branch}:{year}'):06d}"
        prime_diff = int(data.get("prime_ttc") or 0) - int(orow["prime_ttc"] or 0)
        try:
            odata = json.loads(orow["data_json"] or "{}")
        except Exception:
            odata = {}
        data.update({"police_no": police_no, "quittance_no": quitt_no,
                     "attestation_no": odata.get("attestation_no", ""),
                     "carte_rose_no": odata.get("carte_rose_no", ""),
                     "client_no": odata.get("client_no", ""),
                     "created_at": orow["created_at"], "branche": branch,
                     "emitted_at": datetime.datetime.now().isoformat(timespec="seconds"),
                     "avenant_count": avenant_no, "avenant_no": avenant_no})
    else:
        police_no = f"RCA-{branch}-{year}-{next_no(f'police:{branch}:{year}'):05d}"
        quitt_no = f"{data.get('code_intermediaire') or '1031'}/{year}/{next_no(f'quittance:{branch}:{year}'):06d}"
        data.update({"police_no": police_no, "quittance_no": quitt_no,
                     "attestation_no": f"{year}{next_no(f'attestation:{year}'):06d}",
                     "carte_rose_no": f"{year}{next_no(f'carterose:{year}'):06d}",
                     "client_no": f"{year}{next_no(f'client:{year}'):06d}",
                     "created_at": datetime.datetime.now().isoformat(),
                     "emitted_at": datetime.datetime.now().isoformat(timespec="seconds"),
                     "branche": branch, "avenant_count": 0, "avenant_no": 0})
    data["branche_nom"] = brow["name"]
    data["agent"] = agent
    pdf_police = build_police_pdf(data)
    pdf_quittance = build_quittance_pdf(data)
    pdf_facture = build_facture_pdf(data)
    (CONTRACTS_PDF_DIR / f"{police_no}_police.pdf").write_bytes(pdf_police)
    (CONTRACTS_PDF_DIR / f"{police_no}_quittance.pdf").write_bytes(pdf_quittance)
    (CONTRACTS_PDF_DIR / f"{police_no}_facture.pdf").write_bytes(pdf_facture)
    CONTRACTS_DB[police_no] = {"data": data, "police": pdf_police,
                               "quittance": pdf_quittance, "facture": pdf_facture}
    snap = {k: v for k, v in data.items()
            if k not in ("signature_data", "avenant_of", "data_json")}
    data["data_json"] = json.dumps(snap, ensure_ascii=False, default=str)
    try:
        registry_save(data)
        if avenant_no:
            cx = _db()
            cx.execute("""INSERT INTO avenants(police_no, numero, type, motif, prime_ttc,
                            prime_diff, at, agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                       (police_no, avenant_no, data.get("avenant_type", ""),
                        data.get("avenant_motif", ""), int(data.get("prime_ttc") or 0),
                        prime_diff, datetime.datetime.now().isoformat(timespec="seconds"), agent))
            cx.commit()
            cx.close()
        audit(agent, "avenant" if avenant_no else "emission", police_no,
              f"{data.get('nom')} {data.get('prenoms')} - {int(data.get('prime_ttc') or 0)} FCFA"
              + (f" - avenant {avenant_no}" if avenant_no else ""))
    except Exception as e:
        print(f"[REGISTRY] save failed: {e}")
        warnings.append("Contrat emis mais non inscrit au registre (relancer).")
    try:
        _snapshot_db()
    except Exception:
        pass
    return JSONResponse({"status": "success", "contract_id": police_no,
                         "quittance_no": quitt_no, "avenant_no": avenant_no,
                         "prime_diff": prime_diff, "police_prec": data.get("police_prec", ""),
                         "download_url": f"/api/contract/{police_no}/pdf?type=police",
                         "urls": {"police": f"/api/contract/{police_no}/pdf?type=police",
                                  "quittance": f"/api/contract/{police_no}/pdf?type=quittance",
                                  "facture": f"/api/contract/{police_no}/pdf?type=facture",
                                  "dossier": f"/api/contract/{police_no}/dossier.pdf",
                                  "eml": f"/api/contract/{police_no}/email.eml"},
                         "prime_ttc": data["prime_ttc"], "operateur": data.get("operateur", ""),
                         "avertissements": warnings})

@app.get("/api/contract/{cid}/pdf")
async def download_pdf(cid: str, request: Request, type: str = "police"):
    user = _auth(request)
    cid = _valid_cid(cid)
    _own_row(cid, user)
    kind = type if type in ("police", "quittance", "facture") else "police"
    label = {"police": "Police", "quittance": "Quittance", "facture": "Facture"}[kind]
    pdf = None
    if cid in CONTRACTS_DB:
        pdf = CONTRACTS_DB[cid].get(kind) or CONTRACTS_DB[cid].get("pdf")
    if pdf is None:
        p = (CONTRACTS_PDF_DIR / f"{cid}_{kind}.pdf").resolve()
        if not p.exists() and kind == "police":
            p = (CONTRACTS_PDF_DIR / f"{cid}.pdf").resolve()
        if CONTRACTS_PDF_DIR.resolve() not in p.parents or not p.exists():
            raise HTTPException(404, "Document introuvable.")
        pdf = p.read_bytes()
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f"attachment; filename={label}_AGC_{cid}.pdf"})

@app.get("/api/registre")
async def get_registre(request: Request, q: str = "", branche: str = ""):
    user = _auth(request)
    rows = registry_list(q.strip(), branche.strip(), _scope_agent(user))
    return {"status": "success", "count": len(rows), "rows": rows}

@app.get("/api/registre/{police_no}")
async def get_dossier(police_no: str, request: Request):
    user = _auth(request)
    r = _own_row(_valid_cid(police_no), user)
    return {"status": "success", "dossier": dict(r)}

@app.delete("/api/registre/{police_no}")
async def del_dossier(police_no: str, request: Request):
    user = _auth(request)
    _require(user, "superviseur")
    police_no = _valid_cid(police_no)
    cx = _db()
    cur = cx.execute("DELETE FROM contracts WHERE police_no = ?", (police_no,))
    cx.execute("DELETE FROM payments WHERE police_no = ?", (police_no,))
    cx.execute("DELETE FROM avenants WHERE police_no = ?", (police_no,))
    cx.commit()
    cx.close()
    if cur.rowcount == 0:
        raise HTTPException(404, "Dossier introuvable.")
    for kind in ("police", "quittance", "facture"):
        try:
            (CONTRACTS_PDF_DIR / f"{police_no}_{kind}.pdf").unlink(missing_ok=True)
        except Exception:
            pass
    CONTRACTS_DB.pop(police_no, None)
    audit(user.get("username", ""), "suppression", police_no, "Suppression definitive")
    return {"status": "success"}

class PaymentBody(BaseModel):
    police_no: str = ""
    amount: int = 0
    method: str = ""
    reference: str = ""

class AnnulerBody(BaseModel):
    motif: str = ""

@app.get("/api/audit")
async def audit_list(request: Request, limit: int = 200):
    user = _auth(request)
    _require(user, "superviseur")
    cx = _db()
    rows = cx.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?",
                      (max(1, min(int(limit or 200), 1000)),)).fetchall()
    cx.close()
    return {"status": "success", "rows": [dict(r) for r in rows]}

SETTABLE_KEYS = ("agence_nom", "reseau", "intermediaire", "code_intermediaire",
                 "lieu_emission", "sauvegarde_auto", "sauvegarde_retention",
                 "langue")

@app.get("/api/settings")
async def settings_get(request: Request):
    _auth(request)
    return {"status": "success", "settings": get_settings_all()}

@app.put("/api/settings")
async def settings_put(request: Request):
    user = _auth(request)
    _require(user, "superviseur")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Donnees invalides.")
    cx = _db()
    saved = []
    for k in SETTABLE_KEYS:
        if k in body and body[k] is not None:
            v = str(body[k]).strip()[:120]
            if k == "langue" and v not in ("fr", "en"):
                continue
            if k == "sauvegarde_auto" and v not in ("0", "1"):
                continue
            if k == "sauvegarde_retention" and not v.isdigit():
                continue
            cx.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (k, v))
            saved.append(k)
    cx.commit()
    cx.close()
    audit(user.get("username", ""), "parametres", "", ", ".join(saved))
    return {"status": "success", "saved": saved, "settings": get_settings_all()}

@app.post("/api/registre/{police_no}/annuler")
async def annuler_dossier(police_no: str, request: Request, body: AnnulerBody = None):
    user = _auth(request)
    _require(user, "superviseur")
    police_no = _valid_cid(police_no)
    motif = (body.motif if body else "").strip()
    if not motif:
        raise HTTPException(422, "Motif d'annulation requis.")
    cx = _db()
    r = cx.execute("SELECT statut FROM contracts WHERE police_no = ?", (police_no,)).fetchone()
    if not r:
        cx.close()
        raise HTTPException(404, "Dossier introuvable.")
    cx.execute("""UPDATE contracts SET statut = 'annule', motif_annulation = ?,
                  date_annulation = ? WHERE police_no = ?""",
               (motif[:300], datetime.date.today().isoformat(), police_no))
    cx.commit()
    cx.close()
    audit(user.get("username", ""), "annulation", police_no, motif[:300])
    return {"status": "success"}

@app.get("/api/dossier/{police_no}/draft")
async def dossier_draft(police_no: str, request: Request, mode: str = "renouveler"):
    user = _auth(request)
    police_no = _valid_cid(police_no)
    r = _own_row(police_no, user)
    try:
        d = json.loads(r["data_json"] or "{}")
    except Exception:
        d = {}
    if not d:
        d = dict(r)
    d.pop("signature_data", None)
    if mode == "avenant":
        if (r["statut"] or "actif") != "actif":
            raise HTTPException(422, "Police annulee : avenant impossible.")
        d["avenant_of"] = police_no
        d["avenant_no"] = int(r["avenant_count"] or 0) + 1
    else:
        if (r["statut"] or "actif") != "actif":
            raise HTTPException(422, "Police annulee : renouvellement impossible (creez un nouveau dossier).")
        try:
            old_exp = datetime.datetime.strptime(r["expiration"], "%d/%m/%Y").date()
        except Exception:
            old_exp = datetime.date.today()
        effet = max(old_exp + datetime.timedelta(days=1), datetime.date.today())
        duree = 365
        try:
            duree = int(d.get("duree_jours") or 365)
        except Exception:
            pass
        exp = effet + datetime.timedelta(days=duree)
        d.update({"police_no": "", "quittance_no": "", "attestation_no": "", "carte_rose_no": "",
                  "effet": effet.strftime("%d/%m/%Y"), "expiration": exp.strftime("%d/%m/%Y"),
                  "police_prec": police_no, "avenant_of": "", "avenant_no": 0,
                  "canal": "Agence", "paiement": "Payé"})
    return {"status": "success", "mode": mode, "draft": d}

@app.post("/api/payments")
async def payment_add(request: Request, body: PaymentBody = None):
    user = _auth(request)
    police_no = _valid_cid((body.police_no if body else "").strip())
    amount = int(body.amount or 0) if body else 0
    if amount <= 0:
        raise HTTPException(422, "Montant invalide.")
    r = _own_row(police_no, user)
    if (r["statut"] or "actif") != "actif":
        raise HTTPException(422, "Police annulee : encaissement impossible.")
    cx = _db()
    cx.execute("""INSERT INTO payments(police_no, amount, method, reference, at, agent)
                  VALUES (?, ?, ?, ?, ?, ?)""",
               (police_no, amount, (body.method or "")[:30], (body.reference or "")[:60],
                datetime.datetime.now().isoformat(timespec="seconds"), user.get("username", "")))
    cx.commit()
    cx.close()
    audit(user.get("username", ""), "encaissement", police_no, f"{amount} FCFA ({body.method})")
    return {"status": "success"}

@app.get("/api/payments/{police_no}")
async def payments_list(police_no: str, request: Request):
    user = _auth(request)
    police_no = _valid_cid(police_no)
    _own_row(police_no, user)
    cx = _db()
    rows = cx.execute("SELECT * FROM payments WHERE police_no = ? ORDER BY id", (police_no,)).fetchall()
    tot = cx.execute("SELECT COALESCE(SUM(amount), 0) s FROM payments WHERE police_no = ?",
                     (police_no,)).fetchone()["s"]
    cx.close()
    return {"status": "success", "rows": [dict(r) for r in rows], "total": tot}

def _parse_fr(d: str):
    try:
        return datetime.datetime.strptime((d or "").strip(), "%d/%m/%Y").date()
    except Exception:
        return None

@app.get("/api/expirations")
async def expirations(request: Request, jours: int = 60):
    user = _auth(request)
    horizon = max(1, min(int(jours or 60), 365))
    today = datetime.date.today()
    scope = _scope_agent(user)
    cx = _db()
    q = """SELECT police_no, quittance_no, nom, prenoms, telephone, immatriculation,
           marque, modele, effet, expiration, prime_ttc, branche, agent
           FROM contracts WHERE statut = 'actif'"""
    rows = cx.execute(q + (" AND agent = ?" if scope else ""), (scope,) if scope else ()).fetchall()
    cx.close()
    out = []
    for r in rows:
        exp = _parse_fr(r["expiration"])
        if not exp:
            continue
        left = (exp - today).days
        if left <= horizon:
            d = dict(r)
            d["jours_restants"] = left
            out.append(d)
    out.sort(key=lambda x: x["jours_restants"])
    return {"status": "success", "count": len(out), "rows": out}

@app.get("/api/stats")
async def stats(request: Request):
    user = _auth(request)
    scope = _scope_agent(user)
    aw = " AND agent = :a" if scope else ""
    ap = {"a": scope} if scope else {}
    pw = " WHERE police_no IN (SELECT police_no FROM contracts WHERE agent = :a)" if scope else ""
    cx = _db()
    c = cx.execute(f"""SELECT COUNT(*) n, COALESCE(SUM(prime_ttc), 0) t FROM contracts
                      WHERE statut = 'actif'{aw}""", ap).fetchone()
    enc = cx.execute(f"SELECT COALESCE(SUM(amount), 0) s FROM payments{pw}", ap).fetchone()["s"]
    per_agent = cx.execute(f"""SELECT agent, COUNT(*) n, COALESCE(SUM(prime_ttc), 0) t
                              FROM contracts WHERE statut = 'actif'{aw} GROUP BY agent
                              ORDER BY t DESC""", ap).fetchall()
    per_branch = cx.execute(f"""SELECT branche, COUNT(*) n, COALESCE(SUM(prime_ttc), 0) t
                               FROM contracts WHERE statut = 'actif'{aw} GROUP BY branche
                               ORDER BY branche""", ap).fetchall()
    per_method = cx.execute(f"""SELECT method, COUNT(*) n, COALESCE(SUM(amount), 0) t
                               FROM payments{pw} GROUP BY method ORDER BY t DESC""", ap).fetchall()
    recent = cx.execute(f"SELECT created_at, prime_ttc FROM contracts WHERE statut = 'actif'{aw}", ap).fetchall()
    cx.close()
    today = datetime.date.today()
    m_start = today.replace(day=1)
    t_n = t_t = m_n = m_t = 0
    for r in recent:
        try:
            dt = datetime.datetime.fromisoformat(r["created_at"]).date()
        except Exception:
            continue
        if dt == today:
            t_n += 1
            t_t += r["prime_ttc"] or 0
        if dt >= m_start:
            m_n += 1
            m_t += r["prime_ttc"] or 0
    return {"status": "success", "actifs": c["n"], "total_ttc": c["t"], "total_encaisse": enc,
            "jour": {"n": t_n, "ttc": t_t}, "mois": {"n": m_n, "ttc": m_t},
            "par_agent": [dict(r) for r in per_agent],
            "par_agence": [dict(r) for r in per_branch],
            "par_moyen": [dict(r) for r in per_method]}

EXPORT_COLS = ["police_no", "quittance_no", "statut", "nom", "prenoms", "telephone", "operateur",
               "numero_cni", "date_naissance", "souscripteur_pc", "nui", "immatriculation", "marque",
               "modele", "categorie", "effet", "expiration", "prime_ttc", "encaisse", "canal",
               "paiement", "branche", "avenant_count", "police_prec", "agent", "created_at"]

@app.get("/api/export.csv")
async def export_csv(request: Request):
    user = _auth(request)
    rows = registry_list(agent=_scope_agent(user))
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=EXPORT_COLS, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    data = buf.getvalue().encode("utf-8-sig")
    return StreamingResponse(io.BytesIO(data), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=agc_production.csv"})

@app.get("/api/export.json")
async def export_json(request: Request):
    user = _auth(request)
    scope = _scope_agent(user)
    cx = _db()
    pw = " WHERE police_no IN (SELECT police_no FROM contracts WHERE agent = ?)" if scope else ""
    pays = [dict(r) for r in cx.execute(f"SELECT * FROM payments{pw} ORDER BY id",
                                        (scope,) if scope else ()).fetchall()]
    avs = [dict(r) for r in cx.execute(f"SELECT * FROM avenants{pw} ORDER BY id",
                                       (scope,) if scope else ()).fetchall()]
    cx.close()
    return {"status": "success", "exported_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "contracts": registry_list(agent=scope), "payments": pays, "avenants": avs}

@app.post("/api/import.csv")
async def import_csv(request: Request, file: UploadFile = File(...)):
    user = _auth(request)
    _require(user, "superviseur")
    try:
        text = (await file.read()).decode("utf-8-sig")
        rows = list(csv.DictReader(io.StringIO(text)))
    except Exception:
        raise HTTPException(400, "Fichier CSV illisible.")
    n_ok = n_skip = 0
    for r in rows:
        pn = (r.get("police_no") or "").strip()
        if not pn:
            n_skip += 1
            continue
        try:
            registry_save({"police_no": pn, "quittance_no": (r.get("quittance_no") or "").strip(),
                           "nom": r.get("nom", ""), "prenoms": r.get("prenoms", ""),
                           "telephone": r.get("telephone", ""), "numero_cni": r.get("numero_cni", ""),
                           "immatriculation": r.get("immatriculation", ""),
                           "marque": r.get("marque", ""), "modele": r.get("modele", ""),
                           "categorie": r.get("categorie", ""), "effet": r.get("effet", ""),
                           "expiration": r.get("expiration", ""),
                           "prime_ttc": int(float(r.get("prime_ttc") or 0)),
                           "branche": (r.get("branche") or "001").strip() or "001",
                           "created_at": datetime.datetime.now().isoformat()})
            n_ok += 1
        except Exception:
            n_skip += 1
    audit(user.get("username", ""), "import_csv", "", f"{n_ok} lignes, {n_skip} ignorees")
    return {"status": "success", "imported": n_ok, "skipped": n_skip}

def build_backup_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        if REGISTRY_DB.exists():
            z.write(REGISTRY_DB, "registre.db")
        for f in sorted(CONTRACTS_PDF_DIR.glob("*.pdf")):
            z.write(f, f"contracts/{f.name}")
        for f in sorted(UPLOADS_DIR.glob("*")):
            if f.is_file() and f.name != ".gitkeep":
                z.write(f, f"documents/{f.name}")
    return buf.getvalue()

def _snapshot_db() -> None:
    """Cheap safety copy of the register after every emission (keep 30)."""
    if not REGISTRY_DB.exists():
        return
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    shutil.copy(REGISTRY_DB, DB_SNAP_DIR / f"registre_{stamp}.db")
    snaps = sorted(DB_SNAP_DIR.glob("registre_*.db"))
    for old in snaps[:-30]:
        try:
            old.unlink()
        except Exception:
            pass

@app.get("/api/backup")
async def backup(request: Request):
    user = _auth(request)
    _require(user, "superviseur")
    data = build_backup_bytes()
    audit(user.get("username", ""), "sauvegarde")
    stamp = datetime.date.today().strftime("%Y%m%d")
    return StreamingResponse(io.BytesIO(data), media_type="application/zip",
                             headers={"Content-Disposition":
                                      f"attachment; filename=agc_sauvegarde_{stamp}.zip"})

@app.get("/api/backups")
async def backups_list(request: Request):
    user = _auth(request)
    _require(user, "superviseur")
    autos = sorted(BACKUP_DIR.glob("auto_*.zip"), reverse=True)
    snaps = sorted(DB_SNAP_DIR.glob("registre_*.db"), reverse=True)
    return {"status": "success",
            "auto": [{"name": f.name, "size": f.stat().st_size,
                      "at": datetime.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")}
                     for f in autos[:30]],
            "snapshots": len(snaps),
            "derniere_auto": get_setting("derniere_sauvegarde_auto", "")}

@app.get("/api/backups/latest")
async def backups_latest(request: Request):
    user = _auth(request)
    _require(user, "superviseur")
    autos = sorted(BACKUP_DIR.glob("auto_*.zip"), reverse=True)
    if not autos:
        raise HTTPException(404, "Aucune sauvegarde automatique disponible.")
    return StreamingResponse(io.BytesIO(autos[0].read_bytes()), media_type="application/zip",
                             headers={"Content-Disposition":
                                      f"attachment; filename={autos[0].name}"})

def _auto_backup_once() -> None:
    data = build_backup_bytes()
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    (BACKUP_DIR / f"auto_{stamp}.zip").write_bytes(data)
    keep = _int_setting("sauvegarde_retention", 14)
    for old in sorted(BACKUP_DIR.glob("auto_*.zip"), reverse=True)[keep:]:
        try:
            old.unlink()
        except Exception:
            pass
    cx = _db()
    cx.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('derniere_sauvegarde_auto', ?)",
               (datetime.datetime.now().isoformat(timespec="seconds"),))
    cx.commit()
    cx.close()

def _auto_backup_loop() -> None:
    import time as _time
    while True:
        try:
            if get_setting("sauvegarde_auto", "1") == "1":
                last = get_setting("derniere_sauvegarde_auto", "")
                due = True
                if last:
                    try:
                        due = (datetime.datetime.now() -
                               datetime.datetime.fromisoformat(last)).total_seconds() > 24 * 3600
                    except Exception:
                        due = True
                if due and (REGISTRY_DB.exists() or list(CONTRACTS_PDF_DIR.glob("*.pdf"))):
                    _auto_backup_once()
        except Exception as e:
            print(f"[BACKUP] auto failed: {e}")
        _time.sleep(3600)

@app.post("/api/restore")
async def restore(request: Request, file: UploadFile = File(...)):
    user = _auth(request)
    _require(user, "superviseur")
    try:
        raw = await file.read()
        zf = zipfile.ZipFile(io.BytesIO(raw))
        names = zf.namelist()
    except Exception:
        raise HTTPException(400, "Archive illisible.")
    if "registre.db" not in names:
        raise HTTPException(400, "Archive invalide : registre.db manquant.")
    for n in names:
        if n.startswith("/") or ".." in n or not (n == "registre.db" or n.startswith("contracts/")
                                                  or n.startswith("documents/")):
            raise HTTPException(400, f"Archive invalide : {n}.")
    bak = STORAGE_DIR / f"avant_restauration_{datetime.datetime.now():%Y%m%d_%H%M%S}"
    bak.mkdir(parents=True, exist_ok=True)
    if REGISTRY_DB.exists():
        shutil.copy(REGISTRY_DB, bak / "registre.db")
    try:
        test = sqlite3.connect(":memory:")
        test.execute("PRAGMA quick_check")
        test.close()
        zf.extract("registre.db", STORAGE_DIR / "__tmp_restore__")
        tmpdb = STORAGE_DIR / "__tmp_restore__" / "registre.db"
        cx = sqlite3.connect(tmpdb)
        cx.execute("SELECT COUNT(*) FROM contracts").fetchone()
        cx.close()
        shutil.move(str(tmpdb), str(REGISTRY_DB))
        shutil.rmtree(STORAGE_DIR / "__tmp_restore__", ignore_errors=True)
        for n in names:
            if n.startswith("contracts/") and n.endswith(".pdf"):
                zf.extract(n, STORAGE_DIR / "__tmp_restore__")
                shutil.move(str(STORAGE_DIR / "__tmp_restore__" / n),
                            str(CONTRACTS_PDF_DIR / Path(n).name))
            elif n.startswith("documents/") and not n.endswith("/"):
                zf.extract(n, STORAGE_DIR / "__tmp_restore__")
                shutil.move(str(STORAGE_DIR / "__tmp_restore__" / n),
                            str(UPLOADS_DIR / Path(n).name))
        shutil.rmtree(STORAGE_DIR / "__tmp_restore__", ignore_errors=True)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"Restauration impossible : {e}")
    CONTRACTS_DB.clear()
    audit(user.get("username", ""), "restauration", "", f"{len(names)} fichiers")
    return {"status": "success", "restored": len(names)}

def _contract_pdfs(cid: str) -> Dict[str, bytes]:
    out = {}
    for kind in ("police", "quittance", "facture"):
        pdf = (CONTRACTS_DB.get(cid, {}) or {}).get(kind)
        if pdf is None:
            p = CONTRACTS_PDF_DIR / f"{cid}_{kind}.pdf"
            pdf = p.read_bytes() if p.exists() else None
        if pdf is None:
            raise HTTPException(404, "Document introuvable.")
        out[kind] = pdf
    return out

@app.get("/api/contract/{cid}/dossier.pdf")
async def dossier_pdf(cid: str, request: Request):
    user = _auth(request)
    cid = _valid_cid(cid)
    _own_row(cid, user)
    from pypdf import PdfReader, PdfWriter
    blobs = _contract_pdfs(cid)
    w = PdfWriter()
    for kind in ("police", "quittance", "facture"):
        for page in PdfReader(io.BytesIO(blobs[kind])).pages:
            w.add_page(page)
    buf = io.BytesIO()
    w.write(buf)
    return StreamingResponse(io.BytesIO(buf.getvalue()), media_type="application/pdf",
                             headers={"Content-Disposition":
                                      f"attachment; filename=Dossier_AGC_{cid}.pdf"})

@app.get("/api/contract/{cid}/email.eml")
async def dossier_eml(cid: str, request: Request):
    user = _auth(request)
    cid = _valid_cid(cid)
    _own_row(cid, user)
    blobs = _contract_pdfs(cid)
    cx = _db()
    r = cx.execute("SELECT * FROM contracts WHERE police_no = ?", (cid,)).fetchone()
    cx.close()
    nom = f"{(r['prenoms'] or '')} {(r['nom'] or '')}".strip() if r else ""
    msg = EmailMessage()
    msg["Subject"] = f"Vos documents AGC Assurances - Police {cid}"
    msg["From"] = "agence@agc-assurances.com"
    msg["To"] = ""
    msg.set_content(f"""Bonjour {nom},

Veuillez trouver ci-joint vos documents d'assurance automobile RCA :
- Conditions Particulieres (Police {cid})
- Quittance de payement{(f" ({r['quittance_no']})" if r else "")}
- Facture

Cordialement,
AGC Assurances - Generales du Cameroun
""")
    for kind, fname in (("police", f"Police_AGC_{cid}.pdf"),
                        ("quittance", f"Quittance_AGC_{cid}.pdf"),
                        ("facture", f"Facture_AGC_{cid}.pdf")):
        msg.add_attachment(blobs[kind], maintype="application", subtype="pdf", filename=fname)
    return StreamingResponse(io.BytesIO(msg.as_bytes()), media_type="message/rfc822",
                             headers={"Content-Disposition":
                                      f"attachment; filename=Email_AGC_{cid}.eml"})

# ============================================================ PDF DOCUMENTS (see pdfs.py)
from pdfs import build_police_pdf, build_quittance_pdf, build_facture_pdf  # noqa: E402

def today_str() -> str:
    return datetime.date.today().strftime("%d/%m/%Y")

@app.get("/", response_class=HTMLResponse)
async def index():
    return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

@app.on_event("startup")
async def _startup_tasks():
    # Default branch (open mode has no branch manager): ensure "001" exists.
    try:
        cx = _db()
        cx.execute("INSERT OR IGNORE INTO branches(code, name) VALUES ('001', 'Agence')")
        cx.commit()
        cx.close()
    except Exception as e:
        print(f"[STARTUP] default branch failed: {e}")
    # Daily automatic backup scheduler (daemon thread).
    try:
        th = threading.Thread(target=_auto_backup_loop, daemon=True)
        th.start()
    except Exception as e:
        print(f"[BACKUP] scheduler failed: {e}")

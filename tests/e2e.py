"""AGC RCA v5.3 end-to-end suite. Runs against a live server on fresh storage.

Usage:  python3 tests/e2e.py [BASE_URL]
Covers: open mode (no login, no Parametre tab), 6-photo OCR (MRZ, REN, PTAC
checks), names-from-permis, shared identity (agence/Agence), full visibility,
PDFs, payments, exports, backup, and UI markers.
"""
import io
import json
import sys
import time
import urllib.request
import urllib.error

import requests
from PIL import Image, ImageDraw, ImageFont

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8004"
FONT = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 30)

PASS = FAIL = 0
def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"PASS {name}")
    else:
        FAIL += 1
        print(f"FAIL {name} {str(extra)[:300]}")

def api(method, path, obj=None):
    data = json.dumps(obj).encode() if obj is not None else None
    h = {}
    if obj is not None:
        h["Content-Type"] = "application/json"
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

def fx(lines, name):
    img = Image.new("RGB", (1100, 60 + 44 * len(lines)), "white")
    dr = ImageDraw.Draw(img)
    for i, ln in enumerate(lines):
        dr.text((30, 25 + 44 * i), ln, fill="black", font=FONT)
    p = f"/tmp/e2e_{name}.png"
    img.save(p)
    return p

CNI_R = ["REPUBLIQUE DU CAMEROUN", "CARTE NATIONALE D'IDENTITE", "NOM/SURNAME",
         "TANDENT YANG AHANDA", "PRENOMS/GIVEN NAMES", "DANIEL CHARLES AUGUSTINE",
         "DATE DE NAISSANCE/DATE OF BIRTH", "10.12.1975", "SEXE/SEX M",
         "DATE D'EXPIRATION/DATE OF EXPIRY", "12.01.2032", "100000000"]
CNI_V = ["NOM DU PERE /FATHER'S NAME", "FRANCOIS TANDENT A.",
         "LIEU DE NAISSANCE /PLACE OF BIRTH", "YAOUNDE",
         "PROFESSION /OCCUPATION", "INGENIEUR",
         "DATE DE DELIVRANCE /DATE OF ISSUE", "13.01.2022",
         "NUMERO CNI /NIC NUMBER", "AA00000000",
         "I<CMR1000000007<<<<<<<<<<<<<<<", "7512102M3201129CMR<<<<<<<<<<<2",
         "TANDENT<YANG<AHANDA<<DANIEL<CHARLES<AUGUSTINE"]
CG_R = ["CERTIFICAT D'IMMATRICULATION", "N Immatriculation/Registration number",
        "LT 991 NN", "Prec. immat./Prev. regis.", "NEUF",
        "Valide du/Valid from", "14/10/2025", "Au/To", "14/10/2035",
        "N de chassis/V.I.N", "LGWEE4A55SK613951",
        "Nom et prenom/Name and surname", "BICEC LOCATAIRE STE MEDICALEX SARL",
        "Adresse/Adress", "BP 1925 DLA", "SSDT ID", "B1503514"]
CG_V = ["Marque du vehicule/Vehicle mark", "GREAT WALL",
        "1er mise en circulation/Date first of use", "28-08-2025",
        "Genre de vehicule/Type of Vehicle", "VOITURE DE TOURISME",
        "Places assises/Number of seats", "5", "Modele/Model", "HAVAL JOLION PRO.",
        "Centre SSDT/SSDT Center", "LT001", "Vehicule gage/Pledged Vehicle", "OUI",
        "delivre le/Issued on A/At", "14-10-2025 DOUALA", "Carrosserie/Car body",
        "CI", "Energie/Power source", "ESS", "Cylindree/Engine capacity",
        "1499CM3", "Puissance/Power", "8 CV",
        "Poids total en charge/Total authorized bad", "2520 KG",
        "Poids a vide/Net weight", "1825 KG", "Charge utile/Carring capacity",
        "695 KG", "59711116683111-ORIGINAL"]
PP_R = ["Permis de conduire", "1. NLEND MBAY", "2. JEAN PAUL",
        "3. 25-11-1980, TOMBI", "4a. 25-07-2023 4c. NGATOUNOU R. N. e. A.",
        "4b. 25-07-2033 4d. LT-799-0423-18", "5. LT-205584-13", "9. B", "1949744"]
PP_V = ["14. REN: LT-205584-13", "B", "24-11-2012 25-07-2033"]

print("== A. status ==")
s, b = api("GET", "/api/status")
st = json.loads(b)
check("status 200", s == 200, s)
check("version 5.3.1", st.get("version") == "5.3.1", st.get("version"))
check("tesseract on", st.get("tesseract") is True, b[:150])
check("rapidocr on", st.get("rapidocr") is True, b[:150])
check("open mode", st.get("auth") == "open", b[:150])
check("no users_exist flag", "users_exist" not in st, b[:150])
r_nc = requests.get(BASE + "/")
check("no-store anti-cache", r_nc.headers.get("Cache-Control") == "no-store",
      r_nc.headers.get("Cache-Control"))

print("== A2. open mode (no login) ==")
s, b = api("GET", "/api/registre?q=")
check("registre sans session -> 200", s == 200, s)
s, b = api("POST", "/api/auth/setup", {"username": "x", "password": "y"})
check("auth/setup gone (404)", s == 404, s)
s, b = api("POST", "/api/auth/login", {"username": "x", "password": "y"})
check("auth/login gone (404)", s == 404, s)
s, b = api("GET", "/api/auth/me")
check("auth/me gone (404)", s == 404, s)
s, b = api("GET", "/api/users")
check("users gone (404)", s == 404, s)
s, b = api("GET", "/api/branches")
check("branches gone (404)", s == 404, s)

print("== B. 6-photo OCR ==")
paths = {"cni_recto": fx(CNI_R, "cnir"), "cni_verso": fx(CNI_V, "cniv"),
         "cg_recto": fx(CG_R, "cgr"), "cg_verso": fx(CG_V, "cgv"),
         "permis": fx(PP_R, "ppr"), "permis_verso": fx(PP_V, "ppv")}
fh = {k: open(p, "rb") for k, p in paths.items()}
r = requests.post(BASE + "/api/analyze",
                  files={k: ("x.png", v, "image/png") for k, v in fh.items()})
for v in fh.values():
    v.close()
check("analyze queued", r.status_code == 200 and "job_id" in r.json(), r.text[:150])
job = r.json()["job_id"]
d = {}
for _ in range(72):
    time.sleep(5)
    d = requests.get(BASE + f"/api/job/{job}").json()
    if d.get("status") not in ("queued", "running"):
        break
check("job done", d.get("status") == "done", d.get("status"))
m = d.get("merged", {})
docs = d.get("documents", {})
cni, cg, pp = docs["cni"]["data"], docs["carte_grise"]["data"], docs["permis"]["data"]
labels = [(c["status"], c["label"]) for c in d.get("checks", [])]

check("nom FROM PERMIS", m.get("nom") == "NLEND MBAY", m.get("nom"))
check("prenoms FROM PERMIS", m.get("prenoms") == "JEAN PAUL", m.get("prenoms"))
check("sources say permis", (d.get("merged_sources") or {}).get("nom") == "permis",
      d.get("merged_sources"))
check("conducteur + cat", m.get("conducteur") == "JEAN PAUL NLEND MBAY (Cat. B)",
      m.get("conducteur"))
check("profession verso", m.get("profession") == "INGENIEUR", m.get("profession"))
check("adresse CG", m.get("adresse") == "BP 1925 DLA", m.get("adresse"))
check("immat", m.get("immatriculation") == "LT 991 NN", m.get("immatriculation"))
check("marque GREATWALL", m.get("marque") == "GREATWALL", m.get("marque"))
check("modele espaces", m.get("modele") == "HAVAL JOLION PRO", repr(m.get("modele")))
check("centre LT001", cg.get("centre_ssdt") == "LT001", cg.get("centre_ssdt"))
check("poids trio", (m.get("poids_vide"), m.get("charge_utile"), m.get("poids_total")) ==
      ("1825 KG", "695 KG", "2520 KG"), (m.get("poids_vide"), m.get("charge_utile"), m.get("poids_total")))
check("prec_immat NEUF", cg.get("prec_immat") == "NEUF", cg.get("prec_immat"))
check("places = 5", cg.get("places") == "5", repr(cg.get("places")))
check("carrosserie CI", cg.get("carrosserie") == "CI", repr(cg.get("carrosserie")))
check("gage OUI", cg.get("gage") == "OUI", repr(cg.get("gage")))
check("cni MRZ", cni.get("mrz") is True, cni.get("mrz"))
check("cni numero", cni.get("numero_piece") == "AA00000000", cni.get("numero_piece"))
check("permis faces 2", docs["permis"]["faces"] == 2, docs["permis"]["faces"])
check("REN = numero", pp.get("ren") == "LT-205584-13" == pp.get("numero_permis"),
      (pp.get("ren"), pp.get("numero_permis")))
check("verso_match_expire", pp.get("verso_match_expire") is True,
      pp.get("categories_verso"))
check("check MRZ ok", ("ok", "CNI : zone MRZ lue au verso") in labels, labels)
check("check REN ok", ("ok", "Permis : verso coherent (REN)") in labels, labels)
check("check verso=4b ok", ("ok", "Permis : validite verso = date 4b") in labels, labels)
check("check PTAC ok", ("ok", "Poids coherents (vide + charge = total)") in labels, labels)
check("cross-check honest (names differ -> fail)",
      ("fail", "Nom CNI / Permis differents") in labels, labels)

print("== C. emission ==")
SIG = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQD"
       "wAEhQGAhKmMIQAAAABJRU5ErkJggg==")
base_payload = dict(m)
base_payload.update({"telephone": "+237690123456", "signature_data": SIG,
                     "signature_src": "agence"})
bad = dict(base_payload, poids_total="2000 KG")  # ecart 520 kg > tolerance 50 kg
s, b = api("POST", "/api/generate-contract", bad)
check("PTAC incoherent bloque (422)", s == 422, (s, b[:120]))
nosig = dict(base_payload)
nosig.update({"signature_data": "", "signature_src": "agence"})
s, b = api("POST", "/api/generate-contract", nosig)
check("signature agence requise (422)", s == 422, (s, b[:120]))
s, b = api("POST", "/api/generate-contract", base_payload)
d_gen = json.loads(b) if s == 200 else {}
check("emission OK", s == 200 and d_gen.get("contract_id") == "RCA-001-2026-00001", b[:150])
CID = d_gen.get("contract_id", "")

print("== D. PDFs ==")
from pypdf import PdfReader
for kind in ("police", "quittance", "facture"):
    s, raw = api("GET", f"/api/contract/{CID}/pdf?type={kind}")
    pdf_ok = s == 200 and raw[:4] == b"%PDF"
    pages = len(PdfReader(io.BytesIO(raw)).pages) if pdf_ok else -1
    check(f"PDF {kind} 1 page", pdf_ok and pages == 1, (s, pages))
s, b = api("GET", "/api/contract/..%2f..%2fetc%2fpasswd/pdf?type=police")
check("traversal 404", s == 404, s)

print("== E. identite partagee ==")
s, b = api("GET", "/api/registre?q=")
rows = json.loads(b).get("rows", []) if s == 200 else []
check("registre voit le dossier", len(rows) == 1, len(rows))
check("agent = agence", rows and rows[0].get("agent") == "agence",
      rows[0].get("agent") if rows else None)
check("agent_display = Agence", rows and rows[0].get("agent_display") == "Agence",
      rows[0].get("agent_display") if rows else None)
s, b = api("GET", "/api/audit?limit=50")
arows = json.loads(b).get("rows", []) if s == 200 else []
check("journal signe agence",
      any(a.get("username") == "agence" and a.get("action") == "emission" for a in arows),
      arows[:2])
s, b = api("GET", "/api/stats")
par = json.loads(b).get("par_agent", []) if s == 200 else []
check("stats par_agent = agence", any(p.get("agent") == "agence" for p in par), par)

print("== F. visibilite totale ==")
p2 = dict(base_payload, nom="OPENTEST", prenoms="Amina",
          telephone="+237691111111", immatriculation="OU 100 AA")
s, b = api("POST", "/api/generate-contract", p2)
CID2 = json.loads(b).get("contract_id", "") if s == 200 else ""
check("2e contrat", s == 200 and CID2 != "", b[:120])
s, b = api("GET", "/api/registre?q=")
rows = json.loads(b).get("rows", []) if s == 200 else []
check("registre voit les 2 dossiers", len(rows) == 2, len(rows))
s, b = api("GET", f"/api/contract/{CID}/pdf?type=police")
check("PDF dossier 1 lisible", s == 200 and b[:4] == b"%PDF", s)
s, b = api("GET", f"/api/contract/{CID2}/pdf?type=police")
check("PDF dossier 2 lisible", s == 200 and b[:4] == b"%PDF", s)
s, b = api("GET", f"/api/dossier/{CID2}/draft?mode=renouveler")
check("draft renouvellement", s == 200, s)

print("== G. suites metier ==")
s, b = api("GET", "/api/settings")
check("reglages par defaut (API)", s == 200 and "agence_nom" in json.loads(b).get("settings", {}), s)
s, b = api("PUT", "/api/settings", {"lieu_emission": "Yaounde"})
check("reglages modifiables", s == 200 and "lieu_emission" in json.loads(b).get("saved", []), b[:120])
s, b = api("POST", "/api/payments",
           {"police_no": CID2, "amount": 1000, "method": "Cash"})
check("encaissement", s == 200, (s, b[:120]))
s, b = api("GET", "/api/registre?q=OPENTEST")
rows = json.loads(b).get("rows", []) if s == 200 else []
check("encaisse maj", rows and rows[0].get("encaisse") == 1000,
      rows[0].get("encaisse") if rows else None)
s, b = api("GET", "/api/expirations")
check("expirations 200", s == 200, s)
s, b = api("GET", "/api/export.csv")
check("export CSV", s == 200 and b"OPENTEST" in b, s)
s, b = api("GET", "/api/backup")
check("sauvegarde ZIP", s == 200 and b[:2] == b"PK", s)
s, b = api("GET", "/api/audit?limit=50")
check("journal non vide", s == 200 and len(json.loads(b).get("rows", [])) > 3, s)

print("== H. interface (ouverte, sans Parametre) ==")
s, b = api("GET", "/")
html = b.decode("utf-8", "replace")
check("slot permis_verso", 'data-slot="permis_verso"' in html)
check("i18n s1_pm_v", "s1_pm_v" in html)
check("merged_sources", "merged_sources" in html)
check("mention Cree par", "Créé par" in html)
check("onglet NOUVEAU", ">NOUVEAU<" in html)
check("onglet REPERTOIRE", ">RÉPERTOIRE<" in html)
check("onglet PILOTAGE", ">PILOTAGE<" in html)
check("modale apercu PDF", 'id="pdf-modal"' in html and "previewPdf" in html)
check("pastille version", 'id="ver-chip"' in html and "v5.3.1" in html)
check("sauvegarde dans pilotage", 'id="btn-backup"' in html and 'id="restore-file"' in html)
check("import dans pilotage", 'id="import-file"' in html)
check("journal dans pilotage", 'id="audit-body"' in html)
check("PAS de gate login", "login-gate" not in html and 'id="setup-form"' not in html
      and 'id="login-form"' not in html and "login-btn" not in html)
check("PAS de PARAMETRE", ">PARAMÈTRE<" not in html and "PARAMÈTRE" not in html
      and "tab-admin" not in html and "view-admin" not in html and "nav_admin" not in html)
check("PAS de comptes", "btn-logout" not in html and "doSetup" not in html
      and "createUser" not in html and "patchUser" not in html and "/api/users" not in html)
check("PAS d appels auth", "/api/auth" not in html and "X-Access-Token" not in html
      and "loadAdmin" not in html)
check("PAS de settings-body", "settings-body" not in html)
check("PAS de saveSettings", "saveSettings" not in html)
check("PAS de SET_KEYS", "SET_KEYS" not in html)
check("PAS de branches", "reg-branche" not in html and "loadBranches" not in html
      and "createBranch" not in html)
check("PAS de suivi paiement", "doPayment" not in html and "ENCAISSÉ" not in html
      and "RESTE À RECOUVRER" not in html and "Encaisser" not in html
      and "par_moyen" not in html and "Par agence" not in html)
check("PAS de jeton partage", "token-form" not in html and "needadmin-form" not in html)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

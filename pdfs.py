#!/usr/bin/env python3
# =====================================================================
#  AGC ASSURANCES - PDF DOCUMENTS (Police / Quittance / Facture)
#  Layout mirrors the reference AGC documents (1/2/3.pdf), modernised:
#  navy/red brand accents, aligned tables, real Code128 barcodes,
#  QR code on the policy, amount in words on the invoice.
#  Pure reportlab - no extra dependency.
# =====================================================================
import base64
import html
import io
from pathlib import Path
from typing import Any, Dict, List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage,
)

NAVY = colors.HexColor("#011689")
RED = colors.HexColor("#F4313F")
LIGHT_NAVY = colors.HexColor("#E9EDFB")
LIGHT_GRAY = colors.HexColor("#F1F3F8")
MID_GRAY = colors.HexColor("#D7DCE8")
INK = colors.HexColor("#111827")
LOGO = Path(__file__).resolve().parent / "static" / "agc_logo.png"

# ------------------------------------------------------------- helpers
def esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)

def fmt(n: Any) -> str:
    try:
        return f"{int(n or 0):,}".replace(",", " ")
    except Exception:
        return "0"

def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except Exception:
        return 0

def full_name(d: Dict[str, Any]) -> str:
    n = f"{d.get('nom') or ''} {d.get('prenoms') or ''}".strip()
    pc = (d.get("souscripteur_pc") or "").strip()
    return f"{n} P/C {pc}".strip() if pc else n

def av8(d: Dict[str, Any]) -> str:
    try:
        return f"{int(d.get('avenant_no') or 0):08d}"
    except Exception:
        return "00000000"

def emission_dt(d: Dict[str, Any]) -> str:
    import datetime as _dt
    for k in ("emitted_at", "created_at"):
        v = (d.get(k) or "").strip()
        if not v:
            continue
        try:
            return _dt.datetime.fromisoformat(v).strftime("%d/%m/%Y %H:%M:%S")
        except Exception:
            if len(v) >= 10 and v[4] == "-":
                return f"{v[8:10]}/{v[5:7]}/{v[0:4]}"
            return v[:19]
    return _dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")

def ecriture(d: Dict[str, Any]) -> str:
    try:
        return "Annuelle" if int(d.get("duree_jours") or 365) >= 365 else "Courte periode"
    except Exception:
        return "Courte periode"

# ------------------------------------------------------- number words
_N0_19 = ["ZERO", "UN", "DEUX", "TROIS", "QUATRE", "CINQ", "SIX", "SEPT", "HUIT", "NEUF",
          "DIX", "ONZE", "DOUZE", "TREIZE", "QUATORZE", "QUINZE", "SEIZE", "DIX-SEPT",
          "DIX-HUIT", "DIX-NEUF"]
_TENS = ["", "", "VINGT", "TRENTE", "QUARANTE", "CINQUANTE", "SOIXANTE", "SOIXANTE",
         "QUATRE-VINGT", "QUATRE-VINGT"]

def _under100(n: int) -> str:
    if n < 20:
        return _N0_19[n]
    t, r = divmod(n, 10)
    if t in (7, 9):
        if t == 7 and r == 1:
            return "SOIXANTE ET ONZE"
        return _TENS[t] + "-" + _under100(10 + r)
    if r == 0:
        return _TENS[t] + ("S" if t == 8 else "")
    if r == 1 and t != 8:
        return f"{_TENS[t]} ET UN"
    return f"{_TENS[t]}-{_N0_19[r]}"

def _under1000(n: int) -> str:
    h, r = divmod(n, 100)
    s = ""
    if h:
        s = "CENT" if h == 1 else f"{_N0_19[h]} CENT"
        if r == 0 and h > 1:
            s += "S"
    if r:
        s += ("" if not s else " ") + _under100(r)
    return s or "ZERO"

def montant_en_lettres(n: int) -> str:
    if n <= 0:
        return "ZERO"
    parts = []
    m, r = divmod(n, 1000000)
    if m:
        parts.append("UN MILLION" if m == 1 else f"{_under1000(m)} MILLIONS")
    k, r = divmod(r, 1000)
    if k:
        parts.append("MILLE" if k == 1 else f"{_under1000(k)} MILLE")
    if r:
        parts.append(_under1000(r))
    return " ".join(parts) + " FRANCS CFA"

# ---------------------------------------------------------------- styles
def _styles():
    st = getSampleStyleSheet()
    st.add(ParagraphStyle("cell", parent=st["Normal"], fontName="Helvetica",
                          fontSize=7.5, leading=10, textColor=INK))
    st.add(ParagraphStyle("cellr", parent=st["Normal"], fontName="Helvetica",
                          fontSize=7.5, leading=10, textColor=INK, alignment=2))
    st.add(ParagraphStyle("boxh", parent=st["Normal"], fontName="Helvetica-Bold",
                          fontSize=8.5, leading=11, textColor=NAVY, alignment=1))
    st.add(ParagraphStyle("doctitle", parent=st["Normal"], fontName="Helvetica-Bold",
                          fontSize=14, leading=17, textColor=NAVY, alignment=1))
    st.add(ParagraphStyle("sect", parent=st["Normal"], fontName="Helvetica-BoldOblique",
                          fontSize=8.5, leading=11, textColor=INK))
    st.add(ParagraphStyle("bar", parent=st["Normal"], fontName="Helvetica-Bold",
                          fontSize=11, leading=14, textColor=colors.white, alignment=1))
    st.add(ParagraphStyle("bart", parent=st["Normal"], fontName="Helvetica-Bold",
                          fontSize=10, leading=13, textColor=INK, alignment=1))
    st.add(ParagraphStyle("tiny", parent=st["Normal"], fontName="Helvetica",
                          fontSize=7, leading=9, textColor=INK))
    st.add(ParagraphStyle("tinyb", parent=st["Normal"], fontName="Helvetica-Bold",
                          fontSize=7, leading=9, textColor=INK))
    st.add(ParagraphStyle("legal", parent=st["Normal"], fontName="Helvetica-Oblique",
                          fontSize=7, leading=9.5, textColor=INK))
    st.add(ParagraphStyle("sign", parent=st["Normal"], fontName="Helvetica-BoldOblique",
                          fontSize=8.5, leading=11, textColor=INK, alignment=1))
    st.add(ParagraphStyle("words", parent=st["Normal"], fontName="Helvetica",
                          fontSize=8, leading=10.5, textColor=INK))
    return st

def L(label: str, value: Any) -> str:
    """Label (navy bold) + value inline, for reference boxes."""
    return f'<font color="#011689"><b>{esc(label)}</b></font> {esc(value)}'

# -------------------------------------------------------------- pieces
def _header(E: List[Any], st, right_lines: List[str]) -> None:
    cells: List[Any] = []
    if LOGO.exists():
        cells.append(RLImage(str(LOGO), width=30 * mm, height=22 * mm, kind="proportional"))
    else:
        cells.append(Paragraph('<font color="#F4313F"><b>AGC</b></font>', st["doctitle"]))
    cells.append(Paragraph("<br/>".join(right_lines), st["cellr"]))
    E.append(Table([cells], colWidths=[38 * mm, 142 * mm],
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "MIDDLE")])))
    E.append(Spacer(1, 2 * mm))
    E.append(Table([[""]], colWidths=[180 * mm],
                   style=TableStyle([("LINEBELOW", (0, 0), (-1, -1), 1.2, NAVY)])))
    E.append(Spacer(1, 2.5 * mm))

def _refbox(title: str, lines: List[str], st, width_mm: float = 89) -> Table:
    rows = [[Paragraph(f"<b>{esc(title)}</b>", st["boxh"])]]
    for ln in lines:
        rows.append([Paragraph(ln, st["cell"])])
    return Table(rows, colWidths=[width_mm * mm], style=TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_NAVY),
        ("BOX", (0, 0), (-1, -1), 1, NAVY),
        ("ROUNDEDCORNERS", [5, 5, 5, 5]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
    ]))

def _prime_rows(d: Dict[str, Any]) -> List[List[str]]:
    vals = [("Prime Nette", _int(d.get("prime_nette"))),
            ("Accessoires", _int(d.get("accessoires"))),
            ("Frais de fichier", _int(d.get("frais_fichier"))),
            ("TVA", _int(d.get("tva"))),
            ("Carte Rose", _int(d.get("carte_rose"))),
            ("DTA", _int(d.get("dta")))]
    return [[l, (fmt(v) if v else "-")] for l, v in vals]

def _prime_box(d: Dict[str, Any], st, total_label: str = "Prime TTC",
               width_mm: float = 68) -> Table:
    rows = [[Paragraph(f"<b>{esc(l)}</b>", st["cellr"]),
             Paragraph(f"<b>{esc(v)}</b>", st["cellr"])] for l, v in _prime_rows(d)]
    rows.append([Paragraph(f"<b>{esc(total_label)}</b>", st["cellr"]),
                 Paragraph(f"<b>{fmt(d.get('prime_ttc'))}</b>", st["cellr"])])
    n = len(rows)
    return Table(rows, colWidths=[width_mm * 0.62 * mm, width_mm * 0.38 * mm],
                 style=TableStyle([
                     ("BOX", (0, 0), (-1, -1), 1, NAVY),
                     ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                     ("LINEABOVE", (0, n - 1), (-1, n - 1), 1, NAVY),
                     ("BACKGROUND", (0, n - 1), (-1, n - 1), LIGHT_NAVY),
                     ("TOPPADDING", (0, 0), (-1, -1), 2),
                     ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                     ("LEFTPADDING", (0, 0), (-1, -1), 4),
                     ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                 ]))

def _sig_flowable(d: Dict[str, Any], st, width_mm: float = 52):
    src = (d.get("signature_src") or "").strip().lower()
    raw = (d.get("signature_data") or "").strip()
    if src == "differee":
        return Paragraph("<i>Signature du souscripteur a regulariser.</i>", st["tiny"])
    if raw and len(raw) > 100:
        try:
            b64 = raw.split(",", 1)[1] if "," in raw else raw
            img = RLImage(io.BytesIO(base64.b64decode(b64)),
                          width=width_mm * mm, height=20 * mm, kind="proportional")
            return img
        except Exception:
            pass
    return Spacer(1, 16 * mm)

def _barcode(value: str, st):
    num = ParagraphStyle("bcnum", parent=st["tinyb"], alignment=1)
    try:
        from reportlab.graphics.barcode.code128 import Code128
        return [Code128(esc(value), barHeight=11 * mm, barWidth=0.55,
                        humanReadable=False),
                Paragraph(esc(value), num)]
    except Exception:
        return [Paragraph(esc(value), num)]

def _qr(value: str):
    try:
        from reportlab.graphics.barcode.qr import QrCodeWidget
        from reportlab.graphics.shapes import Drawing
        q = QrCodeWidget(esc(value))
        b = q.getBounds()
        w = max(1, (b[2] - b[0]) or 1)
        s = 21 * mm / w
        dr = Drawing(21 * mm, 21 * mm, transform=[s, 0, 0, s, 0, 0])
        dr.add(q)
        return dr
    except Exception:
        return Spacer(1, 21 * mm)

def _footer(text: str):
    def fn(canv, doc):
        canv.saveState()
        canv.setFont("Helvetica", 6.5)
        canv.setFillColor(NAVY)
        canv.drawCentredString(A4[0] / 2, 10 * mm,
                               f"{text}  |  Page {doc.page}")
        canv.restoreState()
    return fn

def _doc(buf: io.BytesIO) -> SimpleDocTemplate:
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                             topMargin=10 * mm, bottomMargin=14 * mm,
                             title="AGC Assurances - RCA", author="AGC Assurances")

# ================================================================ QUITTANCE
def build_quittance_pdf(d: Dict[str, Any]) -> bytes:
    st = _styles()
    E: List[Any] = []
    name = full_name(d)
    _header(E, st, [f"<b>{esc(d.get('agence_nom') or 'AGC Assurances')}</b>",
                    "Assurance Responsabilite Civile Automobile - Code CIMA",
                    f"Agence : {esc(d.get('branche'))} - {esc(d.get('branche_nom'))}"])
    left = _refbox("References du Souscripteur", [
        f"{L('Numero', d.get('client_no'))} &nbsp;&nbsp; {L('Titre', d.get('titre'))}",
        L("Nom", name),
        L("Adresse", d.get("adresse")),
        f"{L('Telephone', d.get('telephone'))} &nbsp;&nbsp; {L('Fax', '-')}",
        L("Profession", d.get("profession") or "-"),
        L("Reseau", d.get("reseau")),
        L("Intermediaire", f"{d.get('intermediaire')}({d.get('code_intermediaire')})"),
    ], st)
    right = _refbox("References de la Quittance", [
        f"{L('Quittance N.', d.get('quittance_no'))}<br/>{L('Emission', emission_dt(d))}",
        f"{L('N. Police', d.get('police_no'))} &nbsp; {L('Avenant N.', av8(d))}",
        L("Assure(e)", name),
        L("Adresse", d.get("adresse")),
        f"{L('Effet', d.get('effet'))} &nbsp;&nbsp; {L('Expiration', d.get('expiration'))}",
        L("Categorie", d.get("categorie")),
        L("Mouvement", d.get("mouvement")),
        f"{L('Ecriture', ecriture(d))} &nbsp;&nbsp; {L('Duree', str(d.get('duree_jours')) + ' jours')}",
    ], st)
    E.append(Table([[left, right]], colWidths=[89 * mm, 89 * mm], spaceBefore=0,
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                     ("LEFTPADDING", (1, 0), (1, 0), 4)])))
    E.append(Spacer(1, 3 * mm))
    av_n = _int(d.get("avenant_no"))
    title = "Quittance de Payement" + (f" - AVENANT N. {av_n}" if av_n else "")
    E.append(Table([[Paragraph(f"<b>{esc(title)}</b>", st["bart"])]], colWidths=[110 * mm],
                   hAlign="CENTER",
                   style=TableStyle([("BOX", (0, 0), (-1, -1), 1, NAVY),
                                     ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                                     ("BACKGROUND", (0, 0), (-1, -1), LIGHT_NAVY),
                                     ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                     ("TOPPADDING", (0, 0), (-1, -1), 4),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])))
    E.append(Spacer(1, 2 * mm))
    E.append(Paragraph("<b>Detail de la prime</b>", st["sect"]))
    nette, access, tva = _int(d.get("prime_nette")), _int(d.get("accessoires")), _int(d.get("tva"))
    tot = nette + access + tva
    c = st["cell"]
    hd = ParagraphStyle("hd", parent=c, fontName="Helvetica-Bold", alignment=1)
    hd2 = ParagraphStyle("hd2", parent=c, fontName="Helvetica-Bold", fontSize=6.5,
                         leading=8, alignment=1)
    rows = [
        [Paragraph("<b>Libelle de la categorie</b>", hd), Paragraph("<b>Prime nette</b>", hd),
         Paragraph("<b>Accessoires</b>", hd), "", "", "",
         Paragraph("<b>Taxes</b>", hd), Paragraph("<b>Prime Totale</b>", hd)],
        ["", "", Paragraph("<b>Compagnie</b>", hd2), Paragraph("<b>Intermed.</b>", hd2),
         Paragraph("<b>Prestataire</b>", hd2), Paragraph("<b>Total</b>", hd2), "", ""],
        [Paragraph("INDIVIDUELLE PERSONNES TRANSPORTEES", c), "", "", "", "", "", "", ""],
        [Paragraph("INDIVIDUELLE CONDUCTEUR", c), "", "", "", "", "", "", ""],
        [Paragraph(esc(d.get("categorie")), c), Paragraph(fmt(nette), c),
         Paragraph(fmt(access), c), "", "", Paragraph(fmt(access), c),
         Paragraph(fmt(tva), c), Paragraph(fmt(tot), c)],
        [Paragraph("<b>TOTAL</b>", hd), Paragraph(f"<b>{fmt(nette)}</b>", hd),
         Paragraph(f"<b>{fmt(access)}</b>", hd), "", "",
         Paragraph(f"<b>{fmt(access)}</b>", hd), Paragraph(f"<b>{fmt(tva)}</b>", hd),
         Paragraph(f"<b>{fmt(tot)}</b>", hd)],
    ]
    cw = [52 * mm, 22 * mm, 20 * mm, 18 * mm, 20 * mm, 18 * mm, 18 * mm, 22 * mm]
    E.append(Table(rows, colWidths=cw, repeatRows=2, style=TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, INK),
        ("BACKGROUND", (0, 0), (-1, 1), MID_GRAY),
        ("BACKGROUND", (0, 5), (-1, 5), LIGHT_NAVY),
        ("SPAN", (2, 0), (5, 0)),
        ("SPAN", (0, 1), (1, 1)), ("SPAN", (6, 1), (7, 1)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ])))
    E.append(Spacer(1, 3 * mm))
    E.append(Table([[_prime_box(d, st), ""]], colWidths=[70 * mm, 110 * mm]))
    E.append(Spacer(1, 4 * mm))
    _bc = _barcode(d.get("quittance_no") or "", st)
    _bc_rows = [[_bc[0]]] + ([[_bc[1]]] if len(_bc) > 1 else [])
    E.append(Table(_bc_rows, colWidths=[120 * mm], hAlign="CENTER",
                   style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                     ("RIGHTPADDING", (0, 0), (-1, -1), 0)])))
    E.append(Spacer(1, 4 * mm))
    E.append(Table([
        [Paragraph("<b>L'Assure(e) ou le Souscripteur</b>", st["sign"]),
         Paragraph("<b>Pour la Compagnie</b>", st["sign"])],
        [_sig_flowable(d, st), ""],
    ], colWidths=[90 * mm, 90 * mm],
        style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                          ("VALIGN", (0, 0), (-1, -1), "TOP")])))
    buf = io.BytesIO()
    _doc(buf).build(E, onFirstPage=_footer(f"AGC Assurances - Quittance {d.get('quittance_no')}"),
                    onLaterPages=_footer(f"AGC Assurances - Quittance {d.get('quittance_no')}"))
    return buf.getvalue()

# ================================================================ POLICE
def build_police_pdf(d: Dict[str, Any]) -> bytes:
    st = _styles()
    E: List[Any] = []
    name = full_name(d)
    av_n = _int(d.get("avenant_no"))
    title = "Conditions particulieres" + (f" - AVENANT N. {av_n}" if av_n else "")
    E.append(Paragraph(f"<b><i>{esc(title)}</i></b>", st["doctitle"]))
    E.append(Spacer(1, 2 * mm))
    ag = f" &nbsp; <font color=\"#011689\"><b>Agence</b></font> {esc(d.get('branche'))}"
    left = _refbox("References du Client", [
        f"{L('Numero', d.get('client_no'))} &nbsp;&nbsp; {L('Titre', d.get('titre'))}{ag}",
        L("NUI", d.get("nui") or "-"),
        L("Nom", name),
        L("Adresse", d.get("adresse")),
        f"{L('Telephone', d.get('telephone'))} &nbsp;&nbsp; {L('Fax', '-')}",
        L("Profession", d.get("profession") or "-"),
        L("Reseau", f"{d.get('reseau')} {d.get('intermediaire')}({d.get('code_intermediaire')})"),
        L("Conseiller", d.get("agent") or "-"),
    ], st)
    right = _refbox("References de la Police", [
        f"{L('Quittance N.', d.get('quittance_no'))}<br/>{L('Emission', emission_dt(d))}",
        f"{L('N. Police', d.get('police_no'))} &nbsp; {L('Avenant N.', av8(d))}",
        L("Assure(e)", name),
        L("Adresse", d.get("adresse")),
        L("Mouvement", d.get("mouvement")),
        L("Categorie", d.get("categorie")),
        (f"{L('Effet', d.get('effet'))} {L('Expiration', d.get('expiration'))} "
         f"{L('Duree', str(d.get('duree_jours')) + ' jours')}"),
        f"{L('Attestation', d.get('attestation_no'))} &nbsp; {L('Carte rose', d.get('carte_rose_no'))}",
    ], st)
    E.append(Table([[left, right]], colWidths=[89 * mm, 89 * mm],
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"),
                                     ("LEFTPADDING", (1, 0), (1, 0), 4)])))
    E.append(Spacer(1, 2.5 * mm))
    E.append(Table([[Paragraph("<b>Identification du Vehicule</b>", st["bar"])]],
                   colWidths=[180 * mm],
                   style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), NAVY),
                                     ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                                     ("TOPPADDING", (0, 0), (-1, -1), 3),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 3)])))
    veh = [
        (f"{L('N. Immatriculation', d.get('immatriculation'))} &nbsp;&nbsp; "
         f"{L('1ere mise en circulation', d.get('mise_circulation'))} &nbsp;&nbsp; "
         f"{L('Energie', d.get('energie'))}"),
        (f"{L('Marque', d.get('marque'))} &nbsp;&nbsp; {L('Genre', d.get('genre'))} &nbsp;&nbsp; "
         f"{L('Carrosserie', d.get('carrosserie') or '-')} &nbsp;&nbsp; "
         f"{L('Nbre de Places', d.get('places'))}"),
        (f"{L('Puissance', str(d.get('puissance_cv') or '-') + ' CV')} &nbsp;&nbsp; "
         f"{L('Puissance reelle', d.get('cylindree') or '-')} &nbsp;&nbsp; "
         f"{L('Poids vide', d.get('poids_vide') or '-')} &nbsp;&nbsp; "
         f"{L('Charge Utile', d.get('charge_utile') or '-')} &nbsp;&nbsp; "
         f"{L('PTAC', d.get('ptac') or '-')}"),
        (f"{L('Type', d.get('modele'))} &nbsp;&nbsp; {L('N. de Serie', d.get('numero_serie'))} "
         f"&nbsp;&nbsp; {L('Zone', 'A')} &nbsp;&nbsp; {L('Valeur a neuf', '-')} &nbsp;&nbsp; "
         f"{L('Valeur venale', '-')}"),
        (f"{L('Tarif', 'Normal')} &nbsp;&nbsp; {L('Categorie', d.get('categorie'))} "
         f"&nbsp;&nbsp; {L('Conducteur habituel', d.get('conducteur') or name)}"),
    ]
    E.append(Table([[Paragraph(v, st["cell"])] for v in veh], colWidths=[180 * mm],
                   style=TableStyle([
                       ("BOX", (0, 0), (-1, -1), 0.8, NAVY),
                       ("ROUNDEDCORNERS", [4, 4, 4, 4]),
                       ("BACKGROUND", (0, 0), (-1, -1), LIGHT_GRAY),
                       ("LEFTPADDING", (0, 0), (-1, -1), 4),
                       ("TOPPADDING", (0, 0), (-1, -1), 1.5),
                       ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5),
                   ])))
    E.append(Spacer(1, 2.5 * mm))
    c = st["cell"]
    hd = ParagraphStyle("hd", parent=c, fontName="Helvetica-Bold", fontSize=6.5,
                        leading=8, alignment=1)
    hd2 = ParagraphStyle("hd2", parent=c, fontName="Helvetica-Bold", fontSize=6,
                         leading=7.5, alignment=1)
    ann, nette = _int(d.get("prime_annuelle")), _int(d.get("prime_nette"))
    rows = [
        [Paragraph("<b>Natures des risques</b>", hd), Paragraph("<b>Etat</b>", hd),
         Paragraph("<b>Somme garantie</b>", hd), Paragraph("<b>Franchise</b>", hd),
         Paragraph("<b>Prime annuelle</b>", hd), Paragraph("<b>Reduction ou Majoration</b>", hd),
         "", "", "", "", Paragraph("<b>Prime Nette</b>", hd),
         Paragraph("<b>Prime Comptant</b>", hd)],
        ["", "", "", "", "", Paragraph("<b>BNS</b>", hd2), Paragraph("<b>Autre</b>", hd2),
         Paragraph("<b>Flotte</b>", hd2), Paragraph("<b>MLS</b>", hd2),
         Paragraph("<b>Montant</b>", hd2), "", ""],
        [Paragraph("Responsabilite Civile", c), Paragraph("Oui", c),
         Paragraph("Illimitee", c), Paragraph("-", c), Paragraph(fmt(ann), c),
         "", "", "", "", "", Paragraph(fmt(nette), c), Paragraph(fmt(nette), c)],
        [Paragraph("<b>S/TOTAL Automobile</b>", hd), "", "", "",
         Paragraph(f"<b>{fmt(ann)}</b>", hd), "", "", "", "", "",
         Paragraph(f"<b>{fmt(nette)}</b>", hd), Paragraph(f"<b>{fmt(nette)}</b>", hd)],
        [Paragraph(f"<b>TOTAL VEHICULE : {esc(d.get('immatriculation'))}</b>", hd), "", "", "",
         Paragraph(f"<b>{fmt(ann)}</b>", hd), "", "", "", "", "",
         Paragraph(f"<b>{fmt(nette)}</b>", hd), ""],
    ]
    cw = [34 * mm, 11 * mm, 22 * mm, 16 * mm, 20 * mm, 11 * mm, 11 * mm,
          11 * mm, 9 * mm, 15 * mm, 18 * mm, 20 * mm]
    E.append(Table(rows, colWidths=cw, style=TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, INK),
        ("BACKGROUND", (0, 0), (-1, 1), MID_GRAY),
        ("BACKGROUND", (0, 4), (-1, 4), MID_GRAY),
        ("SPAN", (5, 0), (9, 0)),
        ("SPAN", (0, 1), (4, 1)), ("SPAN", (10, 1), (11, 1)),
        ("SPAN", (0, 3), (3, 3)), ("SPAN", (5, 3), (9, 3)),
        ("SPAN", (0, 4), (3, 4)), ("SPAN", (5, 4), (9, 4)),
        ("SPAN", (10, 4), (11, 4)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 2.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.5),
    ])))
    E.append(Spacer(1, 2.5 * mm))
    box = _prime_box(d, st, total_label="Total net a payer")
    E.append(Table([["", box]], colWidths=[110 * mm, 70 * mm],
                   style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    E.append(Spacer(1, 2.5 * mm))
    E.append(Paragraph("\"La prime est payable au domicile de l'assureur ou de l'intermediaire "
                       "dans les conditions prevues a l'article 541. La prise d'effet du contrat "
                       "est subordonnee au paiement de la prime par le souscripteur.\"", st["legal"]))
    E.append(Paragraph("\"L'Assure(e) reconnait avoir recu un exemplaire des Conditions Generales "
                       "et des Conditions Particulieres attachees a sa police.\"", st["legal"]))
    E.append(Spacer(1, 3 * mm))
    E.append(Table([
        [Paragraph("<b>L'Assure(e)</b>", st["sign"]), "",
         Paragraph("<b>Pour la Compagnie</b>", st["sign"])],
        [_sig_flowable(d, st, 60), _qr(d.get("police_no") or ""), ""],
    ], colWidths=[70 * mm, 40 * mm, 70 * mm],
        style=TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                          ("VALIGN", (0, 0), (-1, -1), "TOP")])))
    buf = io.BytesIO()
    _doc(buf).build(E, onFirstPage=_footer(f"AGC Assurances - Police {d.get('police_no')}"),
                    onLaterPages=_footer(f"AGC Assurances - Police {d.get('police_no')}"))
    return buf.getvalue()

# ================================================================ FACTURE
def build_facture_pdf(d: Dict[str, Any]) -> bytes:
    st = _styles()
    E: List[Any] = []
    name = full_name(d)
    _header(E, st, [f"<b>{esc(d.get('agence_nom') or 'AGC Assurances')}</b>",
                    "Assurance Responsabilite Civile Automobile - Code CIMA",
                    f"Agence : {esc(d.get('branche'))} - {esc(d.get('branche_nom'))}"])
    E.append(Table([[Paragraph(f"<b><i>Facture N. : &nbsp; {esc(d.get('quittance_no'))}</i></b>",
                               st["bart"])]], colWidths=[180 * mm],
                   style=TableStyle([("BOX", (0, 0), (-1, -1), 1, NAVY),
                                     ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                                     ("BACKGROUND", (0, 0), (-1, -1), MID_GRAY),
                                     ("TOPPADDING", (0, 0), (-1, -1), 5),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 5)])))
    E.append(Spacer(1, 3 * mm))
    E.append(Paragraph("<b><i>Assure ou Souscripteur</i></b>", st["sect"]))
    doit = (f"<b><u>DOIT</u></b> &nbsp; {esc(d.get('titre'))} &nbsp; {esc(name)}"
            f"<br/>{esc(d.get('adresse'))}<br/>NUI : {esc(d.get('nui') or '-')}")
    E.append(Table([[Paragraph(doit, st["cell"])]], colWidths=[180 * mm],
                   style=TableStyle([("BOX", (0, 0), (-1, -1), 1, NAVY),
                                     ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                                     ("LEFTPADDING", (0, 0), (-1, -1), 6),
                                     ("TOPPADDING", (0, 0), (-1, -1), 4),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 4)])))
    E.append(Spacer(1, 3 * mm))
    E.append(Paragraph("<b><i>References Police et Periodicite</i></b>", st["sect"]))
    ref = Table([
        [Paragraph(f"<b>Au titre de la Police</b> &nbsp; {esc(d.get('categorie'))}", st["cell"])],
        [Paragraph(f"<b>Numero :</b> &nbsp; {esc(d.get('police_no'))} &nbsp;&nbsp;&nbsp; "
                   f"<b>Avenant :</b> {av8(d)}", st["cell"])],
        [Paragraph(f"Pour la periode allant du &nbsp; <b>{esc(d.get('effet'))}</b> &nbsp; au &nbsp; "
                   f"<b>{esc(d.get('expiration'))}</b>", st["cell"])],
    ], colWidths=[180 * mm], style=TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, NAVY),
        ("ROUNDEDCORNERS", [6, 6, 6, 6]),
        ("BACKGROUND", (0, 0), (-1, -1), MID_GRAY),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    E.append(ref)
    E.append(Spacer(1, 3 * mm))
    E.append(Paragraph("<b><i>Detail Facture</i></b>", st["sect"]))
    E.append(Table([["", _prime_box(d, st, width_mm=72), ""]],
                   colWidths=[54 * mm, 72 * mm, 54 * mm]))
    E.append(Spacer(1, 3 * mm))
    E.append(Table([[Paragraph(f"<b>PRIME TOTALE A PAYER &nbsp;&nbsp; {fmt(d.get('prime_ttc'))} "
                               "F. CFA</b>", st["bart"])]], colWidths=[180 * mm],
                   style=TableStyle([("BOX", (0, 0), (-1, -1), 1, NAVY),
                                     ("ROUNDEDCORNERS", [6, 6, 6, 6]),
                                     ("BACKGROUND", (0, 0), (-1, -1), MID_GRAY),
                                     ("TOPPADDING", (0, 0), (-1, -1), 5),
                                     ("BOTTOMPADDING", (0, 0), (-1, -1), 5)])))
    E.append(Spacer(1, 2 * mm))
    E.append(Paragraph("<b><i><u>Arrete la presente facture a la somme de :</u></i></b>",
                       st["sect"]))
    E.append(Paragraph(montant_en_lettres(_int(d.get("prime_ttc"))), st["words"]))
    E.append(Spacer(1, 3 * mm))
    E.append(Paragraph("Delai de reglement : <b>A L'EMISSION</b>", st["tiny"]))
    E.append(Paragraph("<b><i><u>Article 13 du Code CIMA</u></i></b>"
                       " : <i>\"A defaut de paiement de la prime dans les delais convenu, "
                       "le contrat est resilie de plein droit. La portion de la prime courue "
                       "reste acquise a l'assureur(AGC), sans prejudice des eventuels frais "
                       "de poursuite et de recouvrement\".</i>", st["tiny"]))
    E.append(Spacer(1, 4 * mm))
    import datetime as _dt
    fait = (f"Fait a {esc(d.get('lieu_emission') or 'Douala')}, le "
            f"{_dt.date.today().strftime('%d/%m/%Y')}")
    E.append(Table([
        [_barcode(d.get("quittance_no") or "", st)[0],
         Paragraph(f"{esc(fait)}<br/><br/><b>Pour la Compagnie</b>", st["cellr"])],
        [Paragraph(esc(d.get("quittance_no") or ""), st["tinyb"]), ""],
    ], colWidths=[90 * mm, 90 * mm],
        style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])))
    buf = io.BytesIO()
    _doc(buf).build(E, onFirstPage=_footer(f"AGC Assurances - Facture {d.get('quittance_no')}"),
                    onLaterPages=_footer(f"AGC Assurances - Facture {d.get('quittance_no')}"))
    return buf.getvalue()

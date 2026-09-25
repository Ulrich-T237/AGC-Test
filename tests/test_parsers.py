"""Deterministic parser unit tests on canned OCR lines (no OCR engine needed).

Usage:  python3 tests/test_parsers.py
Covers the v5.1 extraction mapping: CNI recto/verso (+MRZ), CG both faces,
permis recto/verso (REN, category validity), PTAC arithmetic check.
"""
import sys

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

def doc(lines):
    return {"merged_lines": lines}

# ---- CNI with MRZ verso ----
cni = M.parse_cni(
    doc(["NOM/SURNAME", "TANDENT YANG AHANDA", "PRENOMSIGIVENNAMES",
         "DANIEL CHARLES AUGUSTINE", "DATEDENAISSANCEIDATEOF BIRTH",
         "SEXEISEX", "10.12.1975", "DATEDEXPIRATIONIDATEOFEXPIRY",
         "12.01.2032", "100000000"]),
    doc(["7512102M3201129CMR<<<<<<<<<<2", "ICMR1000000007",
         "TANDENT<YANG<AHANDA<<DANIEL<CHARLES<AUGUSTINE",
         "LIEUDENAISSANCEIPLACEOFBIRTH", "YAOUNDE", "PROFESSION/OCCUPATION",
         "INGENIEUR", "DATEDEDELIVRANCE", "13.01.2022",
         "NUMEROCNI/NICNUMBER", "AA00000000"]))
check("cni nom", cni["nom"] == "TANDENT YANG AHANDA", cni["nom"])
check("cni prenoms", cni["prenoms"] == "DANIEL CHARLES AUGUSTINE", cni["prenoms"])
check("cni dob", cni["date_naissance"] == "10/12/1975", cni["date_naissance"])
check("cni sexe MRZ", cni["sexe"] == "M", cni["sexe"])
check("cni profession", cni["profession"] == "INGENIEUR", cni["profession"])
check("cni lieu", cni["lieu_naissance"] == "YAOUNDE", cni["lieu_naissance"])
check("cni numero", cni["numero_piece"] == "AA00000000", cni["numero_piece"])
check("cni mrz flag", cni["mrz"] is True, cni["mrz"])

# MRZ as fallback when recto unreadable
cni2 = M.parse_cni(doc(["BLURRY GARBAGE"]), doc([
    "7512102M3201129CMR<<<<<<<<<<2", "ICMR1000000007",
    "TANDENT<YANG<AHANDA<<DANIEL<CHARLES<AUGUSTINE"]))
check("mrz fallback dob", cni2["date_naissance"] == "10/12/1975", cni2["date_naissance"])
check("mrz fallback sexe", cni2["sexe"] == "M", cni2["sexe"])
check("mrz fallback expiry", cni2["date_expiration"] == "12/01/2032", cni2["date_expiration"])
check("mrz fallback names", cni2["nom"] == "TANDENT YANG AHANDA" and
      cni2["prenoms"] == "DANIEL CHARLES AUGUSTINE", (cni2["nom"], cni2["prenoms"]))

# ---- CG both faces ----
cg = M.parse_carte_grise([
    doc(["NImmatriculationfRegistration number", "LT 991 NN",
         "Prec. immat JPrev. regis.", "Validedualidfrom", "AufTo", "NEUF",
         "14/10/2025", "14/10/2035", "Nodechassis.I.N", "LGWEE4A55SK613951",
         "Nometprenom/Nameandsurname", "BICECLOCATAIRESTEMEDICALEXSARL",
         "AdresselAdress", "BP1925DLA", "SSDTID", "B1503514"]),
    doc(["Marque du vehiculeVehicle mark", "GREAT WALL",
         "1ermiseencirculation/Datefirstofuse", "28-08-2025",
         "Genre de vehiculefType of Vehicle", "Placesassises/Numberofseats",
         "5", "VOITUREDETOURISME", "ModelefModel", "Centre SSDT/SSDT Center",
         "HAVAL JOLION PRO.", "LT001", "Vehicule gage/ledge Vehicle",
         "OUI", "delivre lelissued on AlAt", "14-10-2025 DOUALA",
         "Carrosserie/Car body", "CI", "Energie/Power source", "ESS",
         "Cylindree/Engine capacity", "1499CM3", "Puissance/Power", "8 CV",
         "Poids total en charge/Total authorized bad", "2520 KG",
         "Poids a vide/Net weight", "1825 KG", "Charge utile/Carring capacity",
         "695 KG", "59711116683111-ORIGINAL"])])
check("cg immat", cg["immatriculation"] == "LT 991 NN", cg["immatriculation"])
check("cg prec_immat", cg["prec_immat"] == "NEUF", cg["prec_immat"])
check("cg vin", cg["numero_serie"] == "LGWEE4A55SK613951", cg["numero_serie"])
check("cg titulaire", cg["titulaire"] == "BICEC LOCATAIRE STE MEDICALEX SARL",
      cg["titulaire"])
check("cg societe", cg["titulaire_type"] == "societe", cg["titulaire_type"])
check("cg adresse", cg["adresse_titulaire"] == "BP 1925 DLA", cg["adresse_titulaire"])
check("cg marque", cg["marque"] == "GREATWALL", cg["marque"])
check("cg modele", cg["modele"] == "HAVAL JOLION PRO", repr(cg["modele"]))
check("cg genre", cg["genre"] == "VOITURE DE TOURISME", cg["genre"])
check("cg places", cg["places"] == "5", repr(cg["places"]))
check("cg centre", cg["centre_ssdt"] == "LT001", cg["centre_ssdt"])
check("cg carrosserie", cg["carrosserie"] == "CI", repr(cg["carrosserie"]))
check("cg energie", cg["energie"] == "ESSENCE", cg["energie"])
check("cg cylindree", cg["cylindree"] == "1499 CM3", cg["cylindree"])
check("cg puissance", cg["puissance"] == "8 CV", cg["puissance"])
check("cg poids", (cg["poids_total"], cg["poids_vide"], cg["charge_utile"]) ==
      ("2520 KG", "1825 KG", "695 KG"))
check("cg mise", cg["mise_circulation"] == "28/08/2025", cg["mise_circulation"])
check("cg gage", cg["gage"] == "OUI", repr(cg["gage"]))

# ---- permis recto + verso ----
pp = M.parse_permis(
    doc(["Permis de conduire", "MLEND MBAY", "2.JEAN PAUL",
         "3. 25-11-1980, TOMBI", "4a.25-07-2023", "B4C.NGATOUNOUR.N.e.A",
         "4b.25-07-2033", "4d.LT-799-0423-18", "5.LT-205584-13", "9.B",
         "1949744"]),
    doc(["14. REN: LT-205584-13", "A1", "24-11-201225-07-2033", "BE"]))
check("pp nom sans marqueur 1.", pp["nom"] == "MLEND MBAY", pp["nom"])
check("pp prenoms", pp["prenoms"] == "JEAN PAUL", pp["prenoms"])
check("pp lieu", pp["lieu_naissance"] == "TOMBI", pp["lieu_naissance"])
check("pp numero", pp["numero_permis"] == "LT-205584-13", pp["numero_permis"])
check("pp cat", pp["categorie"] == "B", pp["categorie"])
check("pp expire", pp["expire_le"] == "25/07/2033", pp["expire_le"])
check("pp autorite", "NGATOUNOU" in pp["autorite"].upper(), pp["autorite"])
check("pp ren", pp["ren"] == "LT-205584-13", pp["ren"])
check("pp verso pair", pp["categories_verso"] == [{"du": "24/11/2012", "au": "25/07/2033"}],
      pp["categories_verso"])
check("pp verso match", pp["verso_match_expire"] is True, pp["verso_match_expire"])

# ---- verify_all: PTAC + verso checks ----
same = M.parse_cni(doc(["NOM/SURNAME", "MLEND MBAY", "PRENOMSIGIVENNAMES",
                         "JEAN PAUL", "DATEDENAISSANCE", "25.11.1980",
                         "SEXE/SEX M", "EXPIRATION", "12.01.2032"]), None)
checks, score = M.verify_all(same, pp, cg, 1, 2, 2)
labels = [(c["status"], c["label"]) for c in checks]
check("v: PTAC ok", ("ok", "Poids coherents (vide + charge = total)") in labels, labels)
check("v: REN ok", ("ok", "Permis : verso coherent (REN)") in labels, labels)
check("v: verso=4b ok", ("ok", "Permis : validite verso = date 4b") in labels, labels)
bad_cg = dict(cg, poids_total="2000 KG")
checks2, _ = M.verify_all(same, pp, bad_cg, 1, 2, 2)
labels2 = [(c["status"], c["label"]) for c in checks2]
check("v: PTAC incoherent -> fail",
      ("fail", "Poids INCOHERENTS (vide + charge / total)") in labels2, labels2)

print(f"\nRESULT: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)

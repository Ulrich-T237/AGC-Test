# AGC Assurances — Émission RCA Triple-Document (v5.3.5, outil interne agence)

Plateforme d'émission de polices d'assurance automobile RCA par **contrôle OCR
croisé de 3 documents obligatoires** : CNI + Carte Grise + Permis de conduire.
Conforme au Code CIMA. Les clients transmettent leurs photos et paient
séparément ; l'agence contrôle, émet et archive.
Chaque émission produit **3 documents PDF** (Conditions Particulières,
Quittance, Facture) et inscrit le client au **répertoire alphabétique**.
Traitement **100 % local et open-source** (RapidOCR + Tesseract + dictionnaires
internes) : aucun appel cloud, aucun coût par document.

**v5.3.2** : photos compressées avant envoi (1800 px, EXIF), rejet
 explicite des HEIC iPhone, détecteur RapidOCR plafonné (tient dans
512 Mo de RAM — hébergement gratuit), micro-lecture du sexe sous le
libellé (nouvelle CNI), erreurs OCR affichées en persistant (jamais de
spinner silencieux), dossier RapidOCR épinglé (1.2.3, reproductible).

**v5.3.3** : suivi temps réel (photo X/6 + chrono), timeouts sur
chaque requête (envoi 3 min, statut 30 s, garde-fou 21 min — plus de
spinner infini silencieux), réessai auto des statuts transitoires,
plafond 20 min, compression avec repli (fichier d'origine si elle
cale), logs serveur par photo ([JOB …] photo X/6 en Ns).

**v5.3.4** : OCR adaptatif (~8x plus rapide : Tesseract page
entière seulement si RapidOCR lit mal) + timeouts durs sur chaque
appel OCR (un appel bloqué n'enlise plus jamais le dossier).

**v5.3.5** : passe « zéro erreur » (imports nettoyés, port .env
sous Linux, blueprint Render 100 % gratuit) + guide d'hébergement
gratuit réécrit (DEPLOY.md).

---

## 1. Parcours (4 écrans + répertoire)

1. **Photos client** : CNI (recto + verso recommandé, tous formats acceptés),
   Carte Grise (face administrative + face technique),
   Permis (recto + verso recommandé : tableau des catégories).
   Photos uniquement (JPG/PNG/WebP). Le contrôle tourne en **tâche de fond**
   (file locale, sans délai d'attente) avec suivi de progression.
2. **Contrôle OCR** : score /100, jusqu'à 12 vérifications croisées (dont zone MRZ
   de la CNI, cohérence REN recto/verso du permis, validité verso = date 4b,
   arithmétique PTAC vide + charge = total), champs par document
   + image nettoyée, dossier fusionné **éditable** (dont P/C, NUI, code
   intermédiaire, poids + PTAC calculé), validation douce du téléphone
   camerounais (indicatif opérateur), genre normalisé en code officiel
   (ex. VOITURE DE TOURISME → VP), prime auto-calculée.
   Un PTAC incohérent **bloque** la suite : corrigez les poids.
3. **Signature** : pavé tactile pour le souscripteur (relevée en agence).
4. **Documents** : aperçu et téléchargement des 3 PDF (Quittance, Conditions
   Particulières, Facture), partage WhatsApp/Email. Bouton retour à chaque étape.
5. **Répertoire clients** : clients par ordre alphabétique, fiche dépliable
   (contrats passés, véhicules, documents), recherche instantanée
   (nom, téléphone, NUI, police, immat), suppression par contrat.
   CNI, naissance, lieu et permis restent **internes** (base de données
   uniquement, jamais imprimés sur les PDF).

## 2. Moteur OCR (double moteur + nettoyage, file locale)

Chaque photo subit : **redimensionnement (≥1800 px)** → niveaux de gris →
**redressement automatique (deskew)** → débruitage médian → égalisation CLAHE →
**double binarisation (adaptative + Otsu)**.
Lecture par **RapidOCR neuronal (principal, 100 % pip)** + **Tesseract (secondaire)**,
puis réparation locale des noms collés (dictionnaires internes). Les deux faces
d'un document sont fusionnées avant extraction. Aucun service cloud.
Le traitement lourd s'exécute dans un **worker local unique** (`ThreadPoolExecutor`,
sans broker ni dépendance) : l'interface interroge `/api/job/{id}` jusqu'au résultat.

## 3. Installation Windows (PC agence)

### Étape 1 — Tesseract OCR (recommandé, moteur secondaire)
1. Téléchargez : https://digi.bib.uni-mannheim.de/tesseract/tesseract-ocr-w64-setup-5.4.0.20240606.exe
2. Installez (Next → Next → Install, dossier par défaut).
3. Sans Tesseract, l'application fonctionne quand même via le moteur neuronal.

### Étape 2 — Lancer
Double-cliquez **`start.bat`**. Premier lancement : 5 à 10 minutes
(téléchargement des librairies + modèle OCR). Le navigateur s'ouvre sur :
`http://localhost:8000`

### Étape 3 — Utilisation (sans connexion)
L'application s'ouvre **directement, sans page de connexion** : tout le
personnel partage la même identité d'agence et voit tous les dossiers.
Chaque émission et annulation reste enregistrée au journal. Les onglets
sont **Nouveau**, **Répertoire** et **Pilotage** (chiffres clés +
sauvegarde + import CSV + journal). Agence unique (code 001) : le code
figure dans le numéro de police (`RCA-001-2026-00021`).

## Nouveautés v5.3.1
- **Correctif affichage** : en-têtes anti-cache (le navigateur ne peut plus
  afficher une ancienne page avec écran de connexion) + version visible
  dans l'en-tête et le pied de page.
- **start.bat** : respecte le `PORT` du `.env`, textes à jour (plus aucune
  mention de cloud : OCR 100 % local).

## Nouveautés v5.3
- **Sans connexion** : l'application s'ouvre directement (plus de page
  de connexion ni comptes) ; identité partagée unique « Agence ».
- **Onglet Paramètre supprimé** : sauvegarde/restauration, import CSV et
  journal d'activité sont regroupés dans **Pilotage**.
- **OCR renforcé** : redressement auto des photos de travers, double
  lecture multi-modes, relecture zoomée des petites mentions
  (places, carrosserie, véhicule gagé) — toujours 100 % local, 0 F.
- **Prêt pour la mise en ligne** : `render.yaml` sans variables
  de comptes ; attention, l'adresse en ligne est **publique**
  (réseau de confiance ou contrôle d'accès requis).

## Nouveautés v5.2
- **Connexion obligatoire** : page de connexion au démarrage (plus de mode
  ouvert ni jeton partagé) ; création du compte admin au premier lancement.
- **Onglets simplifiés** : Nouveau, Répertoire, Pilotage, Paramètre
  (admin uniquement — comptes, sauvegarde, import, journal).
- **Agences supprimées** : agence unique, plus de filtre ni de code à gérer.
- **Prime = payée à l'émission** : encaissé / reste / recouvrement retirés
  du Pilotage et du répertoire (plus de bouton Encaisser).
- **Aperçu PDF avant téléchargement** : au répertoire, chaque document
  (police, quittance, facture, dossier 3-en-1) s'affiche d'abord en
  aperçu, avec bouton Télécharger.

## Nouveautés v5.1
- **Écran Paramètres supprimé** : aucune case à régler ; valeurs d'agence
  fixes (ESPACE CLIENTS / 1031 / Douala), langue = bouton FR/EN en tête.
- **Nom et prénoms repris du permis de conduire** (repli CNI si absent) ;
  l'écran de contrôle indique la source (« Nom repris du : Permis »).
- **Verso du permis exploité** : n° REN recroisé avec le n° du recto,
  validités par catégorie relues et comparées à la date 4b.
- **Zone MRZ de la CNI relue** : naissance / sexe / validité fiabilisés
  même si le recto est flou ; profession et lieu lus au verso.
- **Cartographie extraction** : CNI → souscripteur (nom, profession,
  adresse), Carte Grise (2 faces) → véhicule complet, Permis →
  conducteur habituel + catégorie.
- **Répertoire « Créé par »** : chaque fiche affiche le nom complet
  de l'agent auteur ; comptes créés avec identifiant + nom complet.
- Marques chinoises (Great Wall, Haval, Chery…), centres SSDT OU/LT…,
  modèles multi-mots, suites de tests `tests/` (E2E + unitaires).

## Nouveautés v5.0
- **Connexion sécurisée** : mot de passe exigé (min. 8, lettres+chiffres),
  verrouillage après 5 essais ratés, sessions à durée limitée.
  L'administrateur crée les comptes ; chaque agent ne voit que **ses**
  dossiers, le superviseur voit tout + qui a fait quoi.
- **Français / English** : bouton FR/EN dans l'application
  (les 3 PDF restent en français : documents légaux).
- **Documents au format AGC** : police, quittance et facture reprennent
  la présentation des documents AGC (numéros client, codes-barres,
  QR, montant en lettres), modernisée aux couleurs AGC.
- **Signature à distance** : signer à l'écran, importer la photo de la
  signature du client (WhatsApp), ou émettre avec mention
  « Signature à régulariser ».
- **Sauvegarde automatique** : copie du répertoire à chaque émission +
  sauvegarde complète quotidienne (réglable par l'admin).
- **Paramètres admin** : nom d'agence, réseau, intermédiaire, lieu
  d'émission, durées de session/verrouillage, langue
  (écran retiré en v5.1 : valeurs par défaut fixes, langue = bouton FR/EN).
- **Mise en ligne** : dépôt git + `Dockerfile` + `render.yaml` prêts ;
  suivez **`DEPLOY.md`** (GitHub + Render, comptes obligatoires).
- Glisser-déposer des photos sur PC ; formulaire allégé (champs
  non imprimés regroupés en « vérification interne »).

## Nouveautés v4.0
- **Comptes + journal d'audit** : émissions tracées par agent ; annulation
  avec motif (superviseur) remplaçant la suppression définitive.
- **Numérotation séquentielle par agence/an** : plus de trous ni de doublons.
- **Avenants** : depuis le répertoire, bouton « Avenant » → même police,
  quittance nouvelle, documents régénérés avec le n° d'avenant.
- **Renouvellements** : cloche ◷ (expirations 30/60 j) → dossier pré-rempli,
  nouvelle police liée à l'ancienne.
- **Encaissements** : registre des paiements par police (montant, moyen,
  référence) (v5.2 : prime considérée payée à l'émission, suivi retiré).
- **Pilotage** : primes, production jour/mois, répartition
  par agent + exports CSV/JSON (v5.2 : sans encaissé ni reste).
- **Sauvegarde / Restauration** : un bouton télécharge tout (ZIP) ;
  restauration avec copie de sécurité automatique.
- **Envoi client** : PDF unique 3-en-1 + fichier email prêt (.eml)
  + message WhatsApp pré-rempli.

## Intégration au système central (JSON/CSV)
- Export comptable : `GET /api/export.csv` (registre de production avec
  montants encaissés) et `/api/export.json`
  (contrats + paiements + avenants).
- Import clients : `POST /api/import.csv` (fichier CSV, onglet Pilotage).
- Émettre depuis un autre logiciel :
  `POST /api/generate-contract` (JSON) avec les champs du dossier ;
  `signature_data` optionnel (data-URL PNG).
- Annuler : `POST /api/registre/{police}/annuler` (motif).
- Encaisser : `POST /api/payments` (police_no, amount, method, reference).
- Suivi : `GET /api/stats`, `/api/expirations?jours=30`, `/api/audit`.
API ouverte (v5.3) : aucune authentification ; ne pas exposer l'adresse
sur un réseau non maîtrisé sans contrôle d'accès devant.

## Sauvegarde
Pilotage → « Télécharger la sauvegarde » (registre SQLite + PDF +
photos). À conserver hors du PC (clé USB / disque externe).
Restauration : même écran, la base actuelle est d'abord copiée en
`storage/avant_restauration_*` avant remplacement.

## 4. Barème prime (calibré sur quittances AGC réelles)

- Annuelle CAT1 tourisme : ≤6 CV → 60 000 ; 7–9 → 70 877 ; 10–11 → 85 000 ; 12+ → 100 000.
- Motos : 25 000. Courte durée : ≤30 j → 15 % ; ≤60 j → 20 % ; ≤90 j → 30 % ;
  ≤180 j → 50 % ; ≤270 j → 75 % ; 365 j → 100 %.
- Accessoires 2 500 + Fichier 250 + TVA 19,25 % (sur nette+acc+fichier) + Carte rose 1 000.
- Toutes les valeurs restent **modifiables** par l'agent avant émission.

## 5. Sécurité

- `/storage` n'est **jamais exposé** en statique : documents et PDF uniquement
  via `/api/contract/{id}/pdf`, avec identifiants strictement validés
  (anti-traversée de dossiers) et jeton d'accès si configuré.
- Toutes les valeurs saisies sont **échappées** avant rendu HTML/PDF
  (anti-injection de balises).
- Secrets dans `.env` (exclu par `.gitignore`), modèle fourni via `.env.example`.
- Requêtes SQL 100 % paramétrées ; registre SQLite local.

## 6. Structure

```
app/
├── main.py             # API : file OCR + vérification + tarif + 3 PDF + répertoire
├── static/index.html   # Interface 4 écrans + répertoire (couleurs AGC)
├── static/agc_logo.png # Logo officiel (UI + en-têtes PDF)
├── requirements.txt    # Dépendances Python (cœur)
├── requirements-ocr.txt# Moteur neuronal optionnel
├── .env                # Config locale (privé, voir .env.example)
├── .env.example        # Modèle de configuration
├── .gitignore          # Exclut secrets + données clients
├── start.bat / start.sh
└── storage/            # documents/ + contracts/ + registre.db (créés à l'usage)
```

## 7. Dépannage

| Problème | Solution |
|---|---|
| `rapidocr... No matching distribution` | Normal sur Python 3.13/3.14 : `start.bat` l'installe en optionnel puis continue en mode Tesseract-OCR |
| `start.bat` se referme | Vérifier que `start.bat` contient « patientez » (ancien fichier sinon) ; relancer depuis `cmd` pour voir l'erreur |
| `python` introuvable | Réinstaller Python en cochant « Add Python to PATH » |
| 1er contrôle très long | Normal : téléchargement unique du modèle OCR |
| Champ mal lu | Corriger dans le dossier fusionné (tout est éditable) ; reprendre la photo à plat et nette |
| Port 8000 occupé | Changer `PORT` dans `.env` |
| Émission bloquée « PTAC incohérent » | Corriger les poids : vide + charge utile doit égaler le total CG (±50 kg) |

**v5.4.0** — lecture cloud Gemini en option (`GEMINI_API_KEY` dans `.env`) : quelques secondes/photo, repli local automatique si quota/coupure ; `OCR_MODE=local` = 100 % local.

**v5.4.1** — cloud Gemini rythmé (pause anti-quota/minute, nouvel essai sur 429, repli local par photo) + sous-titre d'analyse adapté au mode cloud.

*AGC Assurances — Générales du Cameroun. Usage interne agence.*

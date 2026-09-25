# Mise en ligne — AGC RCA (GitHub + Render)

Objectif : l'application tourne sur internet, utilisable depuis
l'agence ou la maison.

> **v5.3, mode ouvert : l'application n'a PAS de page de connexion.**
> Toute personne ayant l'adresse peut l'utiliser. Ne la déployez que
> sur un réseau de confiance, ou ajoutez votre propre contrôle d'accès
> devant (ex. Cloudflare Access, VPN, restriction IP).

## Étape 1 — Créer l'organisation GitHub (5 min)

1. Sur https://github.com, créez une organisation, ex. `agc-assurances`
   (profil > Settings > Organizations > New organization, offre gratuite).
2. Dans l'organisation, créez un dépôt **privé** `agc-rca`
   (privé = seul votre personnel y accède).

## Étape 2 — Envoyer l'application (sur votre PC)

Ouvrez un terminal dans le dossier de l'application :

```bash
git remote add origin https://github.com/agc-assurances/agc-rca.git
git branch -M main
git push -u origin main
```

(Identifiez-vous avec votre compte GitHub quand demandé.)

## Étape 3 — Déployer sur Render (10 min)

1. Créez un compte sur https://render.com (connexion via GitHub).
2. **New > Blueprint**, connectez le dépôt `agc-assurances/agc-rca`.
3. Render détecte `render.yaml` : validez, puis **Deploy**
   (aucune variable de compte à renseigner en v5.3).
4. Attendez la fin du déploiement (~5 min, installation de l'OCR).
   Votre adresse : `https://agc-rca.onrender.com` (modifiable ensuite).
5. Ouvrez l'adresse : l'application s'affiche directement
   (Nouveau / Répertoire / Pilotage), prête à émettre.

## Points importants (à lire)

- **Disque persistant obligatoire.** Sans le disque 1 Go (`/data`),
  le répertoire est effacé à chaque redémarrage. Le `render.yaml`
  fourni l'inclut (offre Starter, quelques dollars/mois).
  L'offre gratuite **ne convient pas** à la production.
- **Sauvegardes.** L'application fait une sauvegarde automatique
  quotidienne + une copie du répertoire à chaque émission.
  Téléchargez régulièrement une sauvegarde (onglet Pilotage)
  et conservez-la hors ligne (clé USB).
- **HTTPS inclus.** Render chiffre les connexions (cadenas navigateur).
  Cela ne remplace PAS un contrôle d'accès : l'application reste
  ouverte à qui connaît l'adresse (voir avertissement plus haut).
- **Coûts récurrents.** Hors Render, l'application elle-même est
  100 % locale et gratuite (aucun appel cloud payant).

## Usage mixte (recommandé au démarrage)

Gardez le PC de l'agence (`start.bat`) comme poste principal et
utilisez la version en ligne pour le travail à domicile. Les deux
ont des répertoires séparés : rapprochez-les via
Pilotage > Export CSV si besoin.

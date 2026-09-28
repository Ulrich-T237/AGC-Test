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

## Option B — Test gratuit à distance (Render, sans carte bancaire)

Pour tester à distance quelques jours/semaines, puis tout supprimer.
(Hugging Face demande désormais un abonnement payant pour les applis
Docker : ne pas utiliser.)

> Même avertissement : l'application est **ouverte** (pas de connexion).
> Qui a l'adresse peut l'utiliser. Données de test uniquement, lien
> partagé en privé, supprimez le service après les tests.

Limites du gratuit : le service s'endort après 15 min sans visite
(1re visite = ~1 min de réveil), 512 Mo de RAM (OCR plus lent),
données effacées à chaque redémarrage (pensez au ZIP de sauvegarde
dans Pilotage). Aucune carte requise.

1. Mettez l'application sur GitHub (gratuit) :
   - Créez un compte sur https://github.com, puis un dépôt `agc-rca-test`.
   - Le plus simple : installez **GitHub Desktop**, clonez le dépôt,
     copiez-y le contenu dézippé de l'application, Commit + Push.
2. Créez un compte sur https://render.com (connexion via GitHub).
3. **New > Web Service**, connectez le dépôt `agc-rca-test` :
   Runtime **Docker**, région **Frankfurt** (la plus proche),
   plan **Free**. Aucune variable à renseigner. Create.
4. Attendez le build (~5-15 min, installation de l'OCR).
   Adresse de test : `https://agc-rca-test.onrender.com`
   (le 1er contrôle OCR télécharge les modèles, ~1-2 min en plus).
5. **Retirer :** tableau Render → le service → Settings →
   **Delete** (ou **Suspend** pour le couper en gardant la config).

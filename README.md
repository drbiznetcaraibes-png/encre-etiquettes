# ENCRE POUR TOUS — Étiquettes de téléphones

Application web à déployer sur **Render Free** sans modifier Firebase/TrackCommander.

## Installer sur Render (GitHub)

1. Sur https://github.com/new, créer un dépôt privé `encre-etiquettes`.
2. Sur la page du nouveau dépôt, cliquer **uploading an existing file** / **Add file > Upload files** et déposer tous les fichiers de ce dossier **à la racine du dépôt** (`server.py`, `index.html`, `requirements.txt`, `render.yaml`, `.python-version`, `.gitignore`, `README.md`). Puis **Commit changes**.
3. Se connecter à https://dashboard.render.com/ avec GitHub.
4. Sur Render, **New > Web Service** (et NON Static Site), sélectionner ce dépôt.
5. Paramètres : Language **Python 3**, plan **Free**, Build Command `pip install -r requirements.txt`, Start Command `uvicorn server:app --host 0.0.0.0 --port $PORT`.
6. Cliquer **Deploy Web Service**. Attendre `Live`, puis ouvrir l'URL `.onrender.com`.

Render peut aussi détecter le fichier `render.yaml` via **New > Blueprint**; le parcours Web Service est le plus simple à suivre manuellement.

## Ce que l'application fait

- Recherche en ligne d'un modèle de téléphone par référence constructeur via résultats publics Bing/DuckDuckGo.
- Valide le modèle et la capacité; affiche une erreur si la capacité n'est pas confirmée.
- Saisie du prix TTC et des mensualités ×5, ×12, ×20.
- Planche imprimable de 10 étiquettes 88 × 52 mm par feuille A4.
- Liste conservée **dans le navigateur courant** (localStorage); export/import JSON pour sauvegarde ou transfert.

## Limites à connaître

- **Pas de clé Gemini** et pas d'abonnement API, mais les moteurs publics peuvent bloquer les requêtes. Les références non trouvées ne sont pas générées par invention.
- Les mensualités ne sont **pas calculées automatiquement** : il manque les coefficients de financement fournis par le commerçant. Ils peuvent être ajoutés au programme ultérieurement.
- La liste n'est **pas synchronisée entre les boutiques** : exporter/importer le JSON pour partager les étiquettes.
- Render Free peut s'endormir après 15 minutes sans visite; premier chargement au redémarrage parfois ~1 minute. Service de test sans garantie de disponibilité et soumis aux quotas Render.
- Le serveur est publiquement accessible avec l'URL; pas de connexion utilisateur incluse.

## Test local (optionnel)

```bash
python -m pip install -r requirements.txt
uvicorn server:app --reload
```

Ouvrir http://127.0.0.1:8000/ puis http://127.0.0.1:8000/health.

## Financement automatique (mis à jour le 5 novembre 2026)

Les mensualités sont calculées à partir du prix comptant TTC :
- Paiement **×5** : prix TTC × **0,20748**
- Paiement **×10** : prix TTC × **0,10693**
- Paiement **×20** : prix TTC × **0,055885**

Chaque montant est arrondi au centime. Le formulaire n’exige plus que le prix TTC après identification du smartphone. Les montants des anciennes étiquettes sont recalculés automatiquement ; l’ancienne échéance ×12 n’est plus proposée. Avant tout affichage au public, contrôler que ces coefficients restent valides et vérifier les mentions obligatoires applicables au financement proposé.

## Arrondi des mensualités

Les mensualités x5, x10 et x20 sont calculées avec les coefficients 0,20748, 0,10693 et 0,055885, puis arrondies **à l’euro entier supérieur** (Math.ceil). Ex. 1000 € => 208 €/mois, 107 €/mois et 56 €/mois. Le prix TTC reste inchangé.

# ENCRE POUR TOUS — Recherche IA Gemini (V4)

## Déployer la mise à jour sur votre Render existant

1. Sur GitHub `encre-etiquettes` → **Add file → Upload files**, remplacer les fichiers par ceux de ce ZIP, puis **Commit changes**.
2. Sur Render → service `encre-etiquettes` → **Environment** → **Add Environment Variable** :
   - Key : `GEMINI_API_KEY`
   - Value : votre clé personnelle obtenue sur https://aistudio.google.com/api-keys
3. **Save, deploy** ou **Manual Deploy → Deploy latest commit**. Si nécessaire, **Clear build cache & deploy**.
4. Vérifier `https://VOTRE-APP.onrender.com/health` : `"ai_configured": true`. La clé n'est jamais affichée.
5. Dans l'application, rechercher `Samsung S26 Ultra`. La source doit indiquer `IA + sources Google` lorsqu'une recherche Gemini vérifiée a réussi.

**Ne collez jamais la clé dans GitHub, README, captures, ou navigateur.**

## Coûts et limites

- L'interface Render Free reste gratuite selon les limites du service, mais **l'API Gemini / recherche Google Grounding peut avoir des coûts et quotas** selon votre projet. Consultez https://ai.google.dev/gemini-api/docs/pricing et activez des alertes budgétaires.
- Modèle sélectionné : `gemini-3.8-flash` (configurable sur Render via `GEMINI_MODEL`). Le modèle historique 2.5-flash peut être inaccessible aux nouveaux projets.
- Sans clé Gemini, le serveur fonctionne avec la recherche publique (moins fiable).
- Si Gemini échoue, repli vers les moteurs publics. Une fiche sans preuve web ne doit pas être acceptée comme vérifiée.
- Un nom de téléphone sans capacité exacte laisse « Non précisée ». Pour connaître le stockage précis, entrez idéalement une référence complète.
- La liste des étiquettes est sauvegardée uniquement dans le navigateur (localStorage) : exporter en JSON pour la transférer.

## Financement validé

Prix TTC ×5 = plafond(prix × 0,20748), ×10 = plafond(prix × 0,10693), ×20 = plafond(prix × 0,055885). Arrondi **au prochain euro entier**, pas au centime. Conditions à confirmer avec le partenaire.

## Techniques

Fichiers à la racine du dépôt : `server.py`, `index.html`, `requirements.txt`, `render.yaml`, `.python-version`, `.gitignore`, `README.md`.

Start command Render : `uvicorn server:app --host 0.0.0.0 --port $PORT`.

L'API Gemini est sollicitée **côté serveur uniquement** : `POST /v1beta/models/gemini-3.8-flash:generateContent` avec `google_search` et sources ancrées. Pas de clé dans les téléchargements ni dans les réponses HTTP.


## Version 5 : étiquettes premium
Le modèle d'impression a été entièrement refait avec une composition inspirée de l'affiche validée : bleu marine, jaune vif, trois mensualités très visibles et nom complet du téléphone sur plusieurs lignes. Il n'y a pas de photographie, conformément au format réduit validé. Recherche IA et calculs inchangés.


## Correctif V6 — caractéristiques techniques
- Gemini doit retourner 3 caractéristiques vérifiées minimum (écran, photo, batterie, puce, réseau, RAM).
- Les recherches publiques ne fabriquent aucune fiche si elles ne fournissent pas assez de détails.
- Les étiquettes affichent jusqu'à 4 lignes de spécifications sous le nom du téléphone.
- Un produit précédemment ajouté en V5 doit être supprimé puis recherché à nouveau ; les anciennes étiquettes conservées dans le navigateur ne gagnent pas automatiquement des caractéristiques.
- Si la recherche échoue, vérifier Render > Environment > GEMINI_API_KEY et /health (ai_configured). Ne jamais publier la clé.


## V7 — Design premium définitif pour les planches A4
- Nouveau rendu bleu marine, jaune lumineux, bandeau de marque, montant comptant et blocs financiers très contrastés, basé sur la maquette approuvée.
- Format physique fixe 88 × 52 mm, 2 colonnes × 5 lignes sur A4 (100 % à l'impression).
- Nom du téléphone sur plusieurs lignes, capacité et jusqu'à cinq caractéristiques vérifiées.
- Pas de photo : à cette dimension, l'espace est réservé au nom et aux données techniques, conformément à la demande précédente.
- Recherche Gemini, validations serveur et coefficients de financement inchangés.
- Remplacer les fichiers présents dans GitHub, puis redéployer sur Render.

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

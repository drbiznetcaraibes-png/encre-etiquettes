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


## Correctif V8 : fiches techniques absentes
- Les fiches plus anciennes étaient stockées localement sans `specs` : leur aspect ne se mettait pas à jour malgré les améliorations du serveur.
- Bouton **Compléter les caractéristiques** dans la planche, et **↻ Fiche** par référence pour rechercher sur le serveur sans perdre le prix TTC ni les mensualités.
- L'impression est bloquée tant qu'une étiquette ne contient pas au moins 3 caractéristiques techniques.
- Si la recherche échoue, consulter le message ; vérifier Render Environment (`GEMINI_API_KEY`), les quotas et les sources disponibles. Les faits non vérifiés ne sont pas inventés.


## V9 — Samsung A56/A07 + remise à jour des caractéristiques
- Les recherches « Samsung A56 », « Galaxy A56 5G », « Samsung A07 », « Samsung A07 4G » et « Samsung A07 5G » disposent maintenant de données techniques constructeur sourcées, même si la recherche publique est bloquée.
- Pour « A07 » sans suffixe, affiche la version Galaxy A07 4G ; demander la référence exacte lorsque la version 5G est vendue.
- Le stockage exact n'est pas déduit d'un simple nom : « Non précisée » à moins d'une capacité explicite confirmée.
- Les modèles non repris dans cette base sont recherchés via Gemini/recherche Internet comme précédemment.
- Le bouton « ↻ Compléter les caractéristiques » met à jour les fiches déjà enregistrées dans le navigateur sans toucher aux prix et aux mensualités.
- Tester par exemple : `Samsung A56 256 Go`, `Samsung A07 64 Go`, `Samsung A07 5G`.


## V10 — Fiabilité + prix comptant bien plus visible
- Recherche publique avec une seconde passe ciblée lorsqu’il manque des données, et tolérance à la marque répétée dans la réponse Gemini (sans fusionner les variantes 4G/5G).
- Fiches vérifiées mises en cache dans le navigateur pour éviter de rechercher deux fois un modèle déjà trouvé. Ce cache dépend du navigateur et peut être effacé ; une recherche Internet reste nécessaire pour les nouveaux modèles.
- Prix comptant sur fond JAUNE lumineux, cadre contrasté, texte 6,35 mm (réduit pour les prix longs), bien visible malgré le format 88 × 52 mm. Mensualités ×5/×10/×20 inchangées et toujours fortes.
- 2 colonnes × 5 lignes sur A4 ; pas de photo ; pas de frais d’hébergement Render supplémentaire.
- Ne pas promettre 100 % de reconnaissance : certaines fiches sont introuvables, les moteurs peuvent bloquer Render et les quotas gratuits Gemini varient selon le compte.
- Important : la recherche Gemini peut consommer des quotas ou être facturée selon le niveau de compte. Ne pas laisser une application publique exposée sans contrôle d'accès adapté à une utilisation commerciale.


## V11 — Carte prix TTC épurée
- Affiche le seul montant TTC (aucune mention « prix comptant » sur l'étiquette).
- Fond blanc dégradé léger, liseré bleu marine, fin accent doré, prix centré et en gras.
- Les mensualités ×5, ×10, ×20, leurs coefficients, la recherche IA et les dimensions 88 × 52 mm restent inchangés.
- Seul `index.html` a changé par rapport à la V10 ; le ZIP complet est proposé pour faciliter la mise à jour GitHub.

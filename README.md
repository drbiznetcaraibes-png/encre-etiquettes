# ENCRE POUR TOUS — Générateur d’étiquettes V13

Recherche un smartphone par son nom ou sa référence constructeur, récupère ses caractéristiques et crée une planche A4 d’étiquettes commerciales.

## Utilisation

1. Recherche `iPhone 16 128 Go`, `Samsung A56 256 Go` ou `SM-S938B`.
2. Vérifie le modèle et consulte les sources affichées.
3. Si la référence désigne plusieurs variantes, choisis le stockage dans la liste documentée par le constructeur.
4. Saisis le prix TTC et clique sur **Ajouter à la planche**, puis **Imprimer en A4**.

Le gabarit reste à 88 × 52 mm, dix étiquettes par feuille A4. Les mensualités sont calculées avec les coefficients existants : ×5 = 0,20748 ; ×10 = 0,10693 ; ×20 = 0,055885, arrondies à l’euro supérieur.

## Recherche et fiabilité

- Les fiches constructeur sont consultées **avant Gemini**. Les huit fiches initiales dans `phone_catalog.json` fonctionnent sans clé ni quota IA : iPhone 16, Galaxy S25/S25+/S25 Ultra, A56 5G, A36 5G et A07 4G/5G.
- Pour les autres iPhone, le serveur consulte les fiches techniques Apple en français et vérifie le modèle exact, les sections techniques et les capacités proposées. Cette lecture nécessite l’accès au site Apple.
- Pour les autres modèles, Gemini avec sources et les recherches publiques restent disponibles en complément. Leur succès dépend des sources, du réseau et des quotas Google.
- Le serveur distingue les variantes Pro, Pro Max, Plus/+, Ultra et 4G/5G. Les résultats comparatifs ou mélangeant plusieurs modèles ne servent pas à extraire des caractéristiques.
- Une capacité demandée doit être documentée. Une référence familiale comme `SM-S938B` ne permet pas de déduire le stockage de l’exemplaire vendu. Une capacité impossible pour un modèle du catalogue produit un message indiquant les options documentées.
- L’étiquette exige au moins trois caractéristiques. Le stockage peut rester **Non précisée** tant qu’aucune variante n’a été sélectionnée ; vérifie-le avant impression.

Les sources, la date de vérification et le choix du stockage sont conservés avec les nouvelles étiquettes et dans l’export JSON. Les listes déjà enregistrées restent accessibles dans le même navigateur. Le cache des fiches est renouvelé après sept jours ; les anciennes étiquettes ne sont pas supprimées.

Pour ajouter un téléphone au catalogue, renseigne le modèle exact, ses alias, les références fabricant vérifiées, les capacités et au moins trois caractéristiques communes au modèle avec leurs liens constructeur dans `phone_catalog.json`. N’invente pas la RAM ou le stockage à partir d’une référence familiale.

## Déploiement Render

`render.yaml` conserve la configuration existante. Render installe `requirements.txt` et démarre :

```sh
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Déploie le dernier commit si le déploiement automatique n’est pas activé, puis actualise le navigateur. **Tester le serveur** confirme sa disponibilité ; `/health` indique la version. Ce test ne vérifie pas les quotas Gemini.

`GEMINI_API_KEY` est facultative pour les fiches constructeur. Pour activer l’IA complémentaire, configure la clé uniquement dans les variables d’environnement Render. `GEMINI_MODEL=auto` conserve le choix automatique existant ; un nom explicite reste possible. La facturation et les quotas dépendent du compte Google : [documentation officielle](https://ai.google.dev/gemini-api/docs/pricing).

Le stockage serveur en mémoire disparaît lors d’un redémarrage Render. Les étiquettes sont enregistrées dans le navigateur : utilise **Exporter la liste** pour les sauvegarder. Les limites de requêtes en mémoire ne remplacent pas un contrôle d’accès pour un service public utilisant une clé payante.

## Vérification locale

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
```

Les tests ne nécessitent aucune clé Gemini. Ils couvrent les références et variantes, les capacités, les erreurs et la lecture des sections Apple avec des réponses simulées. L’accès réel aux sites constructeur doit également être vérifié lors d’un déploiement.

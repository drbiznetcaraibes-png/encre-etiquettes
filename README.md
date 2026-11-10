# ENCRE POUR TOUS — Générateur d’étiquettes

Recherche un smartphone par son nom ou sa référence constructeur, récupère ses caractéristiques et crée une planche A4 d’étiquettes commerciales.

## Utilisation

1. Recherche `Xiaomi 17T`, `iPhone 17 Pro Max`, `Samsung S26 Ultra` ou une référence comme `SM-S938B`. Des suggestions apparaissent après deux caractères ; tu peux aussi saisir un modèle absent des suggestions pour lancer sa recherche en ligne.
2. Vérifie le modèle et consulte les sources affichées.
3. Renseigne la **Capacité du téléphone (GB)**, avant ou après la recherche, ou choisis une variante documentée. 1 024 GB correspondent à 1 To. Une saisie manuelle sans preuve constructeur reste distincte des caractéristiques vérifiées.
4. Saisis le prix TTC et clique sur **Ajouter à la planche**, puis **Imprimer en A4**. Clique sur une étiquette pour ouvrir son aperçu agrandi.

Le gabarit reste à 88 × 52 mm, dix étiquettes par feuille A4. Les mensualités sont calculées avec les coefficients existants : ×5 = 0,20748 ; ×10 = 0,10693 ; ×20 = 0,055885, arrondies à l’euro supérieur.

## Recherche et fiabilité

- La base Supabase et les fiches constructeur sont consultées **avant OpenAI**. Les catalogues livrés couvrent actuellement **103 modèles distincts** Apple, Xiaomi/Redmi/POCO et Samsung avec sources et capacités vérifiées. Ils restent utilisables lorsque les sites constructeur ou OpenAI sont indisponibles.
- `apple_catalog.json` contient 40 iPhone et variantes, dont XR/XS, mini/Plus/Pro/Pro Max, Air et les SE 2016/2020/2022. Le lecteur Apple reste disponible pour d’autres fiches exactes. Un SE sans année demande de préciser la génération.
- `xiaomi_catalog.json` contient 45 Xiaomi/Redmi/POCO, dont Xiaomi 17T et 17T Pro. Le lecteur dynamique construit l’URL des fiches France/Global, vérifie leur modèle et lit leurs sections : une nouvelle génération n’exige pas d’ajouter son nom à une liste fermée.
- `samsung_catalog.json` ajoute 11 modèles récents aux fiches initiales, avec lecture des tableaux constructeur pour d’autres gammes S/A/Z et séparation des colonnes par variante.
- Des lecteurs directs supplémentaires consultent les fiches HONOR France, OnePlus France et realme Global. Les sources précisent le pays de la fiche.
- Pour les marques, variantes ou formats de page non pris en charge, OpenAI avec recherche web et les recherches publiques restent disponibles en complément. Leur succès dépend des sources, du réseau, des crédits API et du budget de l’application. La couverture de tous les téléphones du monde n’est pas garantie : un modèle exclusivement chinois, une variante ambiguë ou une fiche inaccessible peuvent rester introuvables.
- Le serveur distingue les variantes Pro, Pro Max, Plus/+, Ultra et 4G/5G. Les résultats comparatifs ou mélangeant plusieurs modèles ne servent pas à extraire des caractéristiques.
- Une capacité demandée doit être documentée. Une référence familiale comme `SM-S938B` ne permet pas de déduire le stockage de l’exemplaire vendu. Une capacité impossible pour un modèle du catalogue produit un message indiquant les options documentées.
- L’étiquette exige au moins trois caractéristiques. Le stockage peut rester **Non précisée** tant qu’aucune variante n’a été sélectionnée ; vérifie-le avant impression.

Les suggestions (`/api/phones/models`) utilisent les catalogues locaux sans appel IA ni consommation du quota de recherche. Une saisie libre reste possible pour les nouveaux modèles.

Les sources, la date de vérification et le choix du stockage sont conservés avec les nouvelles étiquettes et dans l’export JSON. Les listes déjà enregistrées restent accessibles dans le même navigateur. Le cache des fiches est renouvelé après sept jours ; les anciennes étiquettes ne sont pas supprimées.

Pour compléter les catalogues, renseigne le modèle exact, ses alias, les références fabricant vérifiées, les capacités et au moins trois caractéristiques communes au modèle avec leurs liens constructeur dans le fichier de la marque (`apple_catalog.json`, `xiaomi_catalog.json`, `samsung_catalog.json` ou le catalogue initial `phone_catalog.json`). N’invente pas la RAM ou le stockage à partir d’une référence familiale.

## Déploiement Render

`render.yaml` conserve la configuration existante. Render installe `requirements.txt` et démarre :

```sh
uvicorn server:app --host 0.0.0.0 --port $PORT
```

Le déploiement en production reste manuel, après les vérifications de [OPERATIONS.md](OPERATIONS.md). **Tester le serveur** confirme sa disponibilité et la présence de la configuration IA ; `/health` indique la version. Ces contrôles ne vérifient ni la validité de la clé OpenAI ni ses crédits.

Pour connecter OpenAI au service existant :

1. Vérifie que Supabase contient le schéma complet de `supabase_schema.sql`, notamment `ai_usage` et la fonction `reserve_ai_call`. La réservation persistante est obligatoire avant tout appel payant.
2. Dans Render → service **encre-etiquettes** → **Environment**, conserve `APP_ACCESS_TOKEN`, `SUPABASE_URL` et la clé serveur Supabase existants (`SUPABASE_SECRET_KEY` ou `SUPABASE_SERVICE_ROLE_KEY`).
3. Ajoute `OPENAI_API_KEY` depuis un [projet API OpenAI](https://platform.openai.com/api-keys) disposant de crédits. Garde `OPENAI_MODEL=gpt-5-mini` et `OPENAI_DAILY_MAX=3`. Saisis la clé uniquement dans Render, jamais dans GitHub ou dans un message. L’utilisation de l’API est facturée séparément de l’abonnement ChatGPT.
4. Applique les variables via un déploiement validé. Une modification enregistrée sans déploiement n’est pas encore utilisée par le serveur.
5. Une fois connecté avec l’utilisateur `encre`, consulte `/api/status` pour la présence des configurations et `/api/admin/usage` pour le compteur mensuel. Ces deux lectures ne consomment aucun appel OpenAI.
6. Vérifie ensuite un modèle absent des catalogues et contrôle les sources affichées. Un résultat constructeur ne prouve pas que l’appel OpenAI fonctionne : le mode d’une fiche issue d’OpenAI est `openai_grounded`.

Le modèle autorisé reste `gpt-5-mini`, avec trois tentatives par jour et par processus, et **40 tentatives / 4 EUR de réserve par mois UTC** dans Supabase. Chaque tentative réserve 0,10 EUR, même en erreur. Cette réserve est une provision, pas la facture réelle ; les [tarifs OpenAI](https://developers.openai.com/api/docs/pricing) et les alertes du projet doivent aussi être suivis. Gemini n’est plus appelé par la recherche.

Le cache serveur en mémoire disparaît lors d’un redémarrage Render ; les fiches sauvegardées dans Supabase persistent. Les étiquettes sont enregistrées dans le navigateur : utilise **Exporter la liste** pour les sauvegarder.

## Vérification locale

```sh
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests -v
APP_ACCESS_TOKEN=local-test .venv/bin/uvicorn server:app --host 127.0.0.1 --port 8000
```

Connecte-toi en local avec l’utilisateur `encre` et le mot de passe `local-test`. Les tests ne nécessitent aucune clé réelle et ne font aucun appel OpenAI payant. Ils couvrent les références et variantes, les capacités, les erreurs, les protections de budget et les lectures constructeur avec des réponses simulées. L’accès réel aux sources et à OpenAI doit également être vérifié lors d’un déploiement.

## Base centralisée et recherche OpenAI

La recherche consulte Supabase, les catalogues et les sources constructeur, puis OpenAI si nécessaire et si le budget persistant l'autorise. Les nouvelles fiches vérifiées sont sauvegardées ; les recherches incomplètes sont placées dans une file hebdomadaire ciblée. Gemini n'est plus appelé dans ce parcours.

Le site exige un accès salarié (utilisateur `encre`, secret `APP_ACCESS_TOKEN`). Voir [OPERATIONS.md](OPERATIONS.md) pour la configuration Supabase/Render, l'import des quatre catalogues, les limites IA, les tests et l'activation facultative de la maintenance. Aucun déploiement automatique n'est prévu : validation obligatoire avant production.

# ENCRE POUR TOUS — Générateur d’étiquettes

Recherche un smartphone par son nom ou sa référence constructeur, récupère ses caractéristiques et crée une planche A4 d’étiquettes commerciales.

## Utilisation

1. Recherche `Xiaomi 17T`, `iPhone 17 Pro Max`, `Samsung S26 Ultra` ou une référence comme `SM-S938B`. Des suggestions apparaissent après deux caractères ; tu peux aussi saisir un modèle absent des suggestions pour lancer sa recherche en ligne.
2. Vérifie le modèle et consulte les sources affichées.
3. Renseigne la **Capacité du téléphone (GB)**, avant ou après la recherche, ou choisis une variante documentée. 1 024 GB correspondent à 1 To. Une saisie manuelle sans preuve constructeur reste distincte des caractéristiques vérifiées.
4. Saisis le prix TTC et clique sur **Ajouter à la planche**, puis **Imprimer en A4**. Clique sur une étiquette pour ouvrir son aperçu agrandi.

Le gabarit reste à 88 × 52 mm, dix étiquettes par feuille A4. Les mensualités sont calculées avec les coefficients existants : ×5 = 0,20748 ; ×10 = 0,10693 ; ×20 = 0,055885, arrondies à l’euro supérieur.

Les trois grilles ×5 / ×10 / ×20 restent visibles. Leurs montants sont laissés vides si le prix comptant est **inférieur ou égal à 120 EUR**. Au-dessus de ce prix, chaque échéance arrondie **inférieure ou égale à 20 EUR** reste également vide. Ces règles s’appliquent au formulaire, aux anciennes étiquettes enregistrées, à l’aperçu et à l’impression.

Le quota IA est uniquement mensuel : jusqu’à 40 tentatives partagées par mois UTC, sans blocage après trois recherches dans une journée. Les recherches résolues par la base ou un constructeur ne consomment pas ce quota.

L’import de novembre 2026 a contrôlé 120 références récentes : 9 Apple, 50 Samsung, 51 Xiaomi/Redmi/POCO et 10 Google. Les capacités sont séparées par variante et les sources constructeur sont conservées dans [l’instantané d’import](data/recent_catalogue_2026-11.json). Les fiches vérifiées déjà en base sont conservées ; les candidats sans caractéristiques confirmées ne sont pas présentés comme vérifiés. La collecte périodique inclut désormais Google et les gammes régionales Samsung M/F.

## Recherche et fiabilité

- La base Supabase et les fiches constructeur sont consultées **avant OpenAI**. Les catalogues livrés couvrent actuellement **113 modèles distincts** Apple, Xiaomi/Redmi/POCO, Samsung et Google avec sources et capacités vérifiées. Ils restent utilisables lorsque les sites constructeur ou OpenAI sont indisponibles.
- `apple_catalog.json` contient 40 iPhone et variantes, dont XR/XS, mini/Plus/Pro/Pro Max, Air et les SE 2016/2020/2022. Le lecteur Apple reste disponible pour d’autres fiches exactes. Un SE sans année demande de préciser la génération.
- `xiaomi_catalog.json` contient 45 Xiaomi/Redmi/POCO, dont Xiaomi 17T et 17T Pro. Le lecteur dynamique construit l’URL des fiches France/Global, vérifie leur modèle et lit leurs sections, y compris la famille REDMI A comme le A7 Pro. Une nouvelle génération n’exige pas d’ajouter son nom à une liste fermée.
- Les noms courts Samsung `A17`, `A18`, `A26` et `A37` sont acceptés. Si le catalogue local ne connaît pas un modèle, le catalogue produit officiel France/Royaume-Uni/Allemagne et les archives Samsung sont consultés automatiquement. Les références SKU, le pays de la source et les versions 4G/5G restent distincts. Un modèle absent d’un index en cache déclenche son actualisation dès que cette copie a plus d’une minute.
- `google_catalog.json` contient les 10 Pixel 2025–2026, lus dans les tableaux techniques officiels Google. Le lecteur distingue les modèles Pro, XL et Fold, la mémoire vive et le stockage.
- Des lecteurs directs supplémentaires consultent les fiches HONOR France, OnePlus France et realme Global. Les sources précisent le pays de la fiche.
- Pour les marques, variantes ou formats de page non pris en charge, OpenAI avec recherche web et les recherches publiques restent disponibles en complément. Leur succès dépend des sources, du réseau, des crédits API et du budget de l’application. La couverture de tous les téléphones du monde n’est pas garantie : un modèle exclusivement chinois, une variante ambiguë ou une fiche inaccessible peuvent rester introuvables.
- Le serveur distingue les variantes Pro, Pro Max, Plus/+, Ultra et 4G/5G. Les résultats comparatifs ou mélangeant plusieurs modèles ne servent pas à extraire des caractéristiques.
- Une capacité demandée doit être documentée. Une référence familiale comme `SM-S938B` ne permet pas de déduire le stockage de l’exemplaire vendu. Une capacité impossible pour un modèle du catalogue produit un message indiquant les options documentées.
- L’étiquette exige au moins trois caractéristiques. Le stockage peut rester **Non précisée** tant qu’aucune variante n’a été sélectionnée ; vérifie-le avant impression.

Les suggestions (`/api/phones/models`) fusionnent les catalogues locaux et les fiches vérifiées Supabase, avec un cache de cinq minutes, sans appel IA ni consommation du quota de recherche. Les stockages demandés mais non confirmés ne deviennent jamais des options documentées.

La recherche se déroule automatiquement : **base → sources constructeur → recherche web OpenAI si nécessaire → sauvegarde de la fiche vérifiée dans Supabase**. Une recherche utilisateur suffit ; la découverte immédiate n’attend pas la collecte hebdomadaire. Les noms de nouveaux modèles comportant une marque connue peuvent atteindre l’IA même si leur forme n’est pas encore prévue par les lecteurs natifs. L’IA doit confirmer le nom exact et au moins trois caractéristiques avec des sources consultables ; une variante voisine ou une réponse partielle est refusée.

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
3. Ajoute `OPENAI_API_KEY` depuis un [projet API OpenAI](https://platform.openai.com/api-keys) disposant de crédits. Configure `OPENAI_MODEL=gpt-5.4-mini`. Saisis la clé uniquement dans Render, jamais dans GitHub ou dans un message. L’utilisation de l’API est facturée séparément de l’abonnement ChatGPT.
4. Applique les variables via un déploiement validé. Une modification enregistrée sans déploiement n’est pas encore utilisée par le serveur.
5. Une fois connecté avec l’utilisateur `encre`, consulte `/api/status` pour la présence des configurations et `/api/admin/usage` pour le compteur mensuel. Ces deux lectures ne consomment aucun appel OpenAI.
6. Vérifie ensuite un modèle absent des catalogues et contrôle les sources affichées. Un résultat constructeur ne prouve pas que l’appel OpenAI fonctionne : le mode d’une fiche issue d’OpenAI est `openai_grounded`.

Le modèle par défaut est `gpt-5.4-mini`, avec **40 tentatives / 4 EUR de réserve par mois UTC**, utilisables librement au cours du mois dans Supabase. `gpt-5-mini` reste accepté pour une transition jusqu’à son [arrêt annoncé le 11 décembre 2026](https://developers.openai.com/api/docs/deprecations). Tout autre modèle est bloqué. Chaque tentative réserve 0,10 EUR, même en erreur. Cette réserve est une provision, pas la facture réelle ; les [tarifs OpenAI](https://developers.openai.com/api/docs/pricing) et les alertes du projet doivent aussi être suivis. Gemini n’est plus appelé par la recherche.

Un refus OpenAI `429` bloque le recours IA : contrôler les [limites du projet API](https://platform.openai.com/settings/organization/limits) et sa [facturation](https://platform.openai.com/settings/organization/billing/overview). Le code ne peut pas lever les restrictions du compte fournisseur. Une réponse constructeur déjà disponible reste utilisable dans ce cas.

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

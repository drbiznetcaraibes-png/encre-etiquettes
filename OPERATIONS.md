# Mise en service après validation

Aucune migration, import distant, activation planifiée ou opération Render n'a été effectuée par cette modification. La PR reste à valider avant fusion. Le nouveau visuel a été validé dans la conversation. Les dimensions 88 × 52 mm, la planche A4 et les coefficients 0,20748 / 0,10693 / 0,055885 restent inchangés.

## Supabase

1. Créer d'abord un projet de test. Exécuter **tout** `supabase_schema.sql` dans SQL Editor (réexécutable sur le schéma initial de la PR).
2. Les tables ont RLS activée, sans accès navigateur. La clé secrète `sb_secret_…` (recommandée) ou la clé legacy `service_role` contourne RLS : la conserver exclusivement comme secret serveur et secret GitHub Actions pour la maintenance. Ne jamais la copier dans le HTML, un catalogue, un log ou un message.
3. Configurer `SUPABASE_URL` et `SUPABASE_SERVICE_ROLE_KEY` dans l'environnement local ou de test. Faire `python maintenance.py import` (simulation sans réseau), puis `python maintenance.py import --apply` après validation du projet cible. L'import déduplique les quatre catalogues et conserve les capacités séparément. Il ne remplace pas les fiches déjà vérifiées ; les sources et dates sont celles des catalogues, sans prétendre à une nouvelle vérification réseau.
4. Vérifier une recherche connue dans `phone_specs`, puis une recherche nouvelle avec des réponses constructeur. Vérifier la persistance après redémarrage. Tester une indisponibilité de Supabase : les sources constructeur restent utilisables ; l'IA est bloquée si sa réservation échoue.

## Render

1. Dans le tableau de bord, **désactiver Auto-Deploy avant de fusionner** et vérifier la branche réellement surveillée. `autoDeployTrigger: off` prépare cette consigne dans le Blueprint, mais ne modifie pas à lui seul un service existant.
2. Créer un service de préproduction distinct, sur cette branche. Commandes habituelles : `pip install -r requirements.txt`, puis `uvicorn server:app --host 0.0.0.0 --port $PORT`. Health check : `/health`.
3. Définir les secrets `APP_ACCESS_TOKEN` (long mot de passe aléatoire), `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. Le navigateur demande l'utilisateur **encre** et ce mot de passe via HTTP Basic. HTTPS Render est indispensable. Sans mot de passe configuré, le site est fermé ; seul `/health` reste public. Ce compte partagé convient à une petite équipe, sans gestion individuelle des salariés.
4. Tester sans IA, puis ajouter `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5.4-mini`. Gemini n'est plus appelé dans le parcours de recherche, pour éviter une deuxième facturation hors budget.
5. Valider manuellement modèle exact, variantes, stockage et sources d'un téléphone absent des catalogues. Tester impression et panne/quota avant de demander l'autorisation de déployer en production. Aucun appel payant réel n'a été fait pendant les tests automatisés.

## Budget et suivi

La fonction SQL `reserve_ai_call` réserve atomiquement **0,10 EUR par tentative**, y compris erreurs et délais dépassés, au maximum **40 appels / 4 EUR par mois UTC**, partagés entre instances et redémarrages. Le serveur passe par cette fonction avant tout appel IA. La recherche utilise `gpt-5.4-mini` par défaut, au maximum un appel d'outil web, un contexte de recherche faible et 2 200 jetons de sortie, avec un raisonnement faible. `gpt-5-mini` reste accepté pour la transition ; tous les autres modèles sont bloqués. Il n’y a plus de quota IA quotidien : les tentatives disponibles peuvent être utilisées le même jour. Le compteur mensuel persiste après redémarrage et est partagé entre les instances. L’ancienne variable `OPENAI_DAILY_MAX` est ignorée et peut être supprimée de Render.

Révision du modèle du 12 novembre 2026 : [OpenAI annonce l’arrêt de gpt-5-mini le 11 décembre 2026](https://developers.openai.com/api/docs/deprecations). [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) prend en charge Responses et la recherche web ; ses tarifs consultés sont de 0,75 USD par million de jetons d’entrée et 4,50 USD par million de jetons de sortie, auxquels s’ajoutent les outils. Les 2 200 jetons de sortie représentent au plus 0,0099 USD de sortie par appel ; la recherche web est actuellement facturée 0,01 USD par appel, plus ses jetons d’entrée. La réserve reste prudente et ne garantit pas le montant de la facture ; vérifier les entrées, le change, les taxes et les tarifs avant activation. Aucun plafond de tentatives ni réservation n’est augmenté.

`GET /api/admin/usage`, protégé par le même accès salarié, expose les tentatives et réservations mensuelles. `ai_usage` est également consultable dans Supabase. Il s'agit d'une **provision prudente, pas de la facture réelle** : vérifier les tarifs OpenAI en vigueur, le change et les taxes avant activation. Configurer un projet OpenAI dédié, une alerte de budget à 4 EUR équivalents et contrôler la facturation OpenAI. Les alertes du fournisseur ne garantissent pas un arrêt strict ; ne pas réutiliser la clé dans d'autres applications. L'objectif de 5 EUR concerne uniquement l'IA, pas l'hébergement Render/Supabase ou un éventuel Cron Job payant.

## Découverte et vérification hebdomadaires

Le workflow `.github/workflows/weekly.yml` comprend désormais deux étapes : une collecte des index/listes officiels Apple, Xiaomi, Samsung et Google pour découvrir de nouveaux modèles, puis l'ancienne vérification ciblée des demandes enregistrées. Il ne crée aucun Cron Job Render, ne modifie pas les fichiers JSON du dépôt et ne déploie pas le site. Le déploiement du code reste manuel ; une fois la collecte activée, ses fiches vérifiées sont des données Supabase disponibles pour le site sans nouveau déploiement.

La commande de collecte est :

```sh
python catalogue_update.py --report work-report/catalogue-update.json
```

Par défaut, elle consulte les sources constructeur mais ne modifie pas Supabase ni les catalogues locaux. `--apply` autorise la sauvegarde dans la base configurée, après validation de sa cible. Un budget global de cinq minutes borne la collecte et ses écritures ; les collecteurs travaillent en parallèle avec leurs propres limites de pages et de concurrence. Chaque fiche doit confirmer le modèle exact, ses variantes et au moins trois caractéristiques avec une source constructeur. Un index indisponible, une page non reconnue ou une limite atteinte produit un bilan partiel et un échec signalé ; cela ne supprime pas les fiches déjà enregistrées. Cette découverte ne garantit pas que toutes les annonces régionales ou tous les modèles soient immédiatement publiés dans les sources consultées.

Après la collecte appliquée, `python maintenance.py weekly --apply` traite au plus dix fiches ciblées : demandes incomplètes et nouvelles fiches issues d'OpenAI arrivées à échéance. Les échecs sont reprogrammés à sept jours, sans dégrader une fiche vérifiée lors d'une panne transitoire. Chaque étape du workflow est limitée à six minutes, le job à quinze minutes et une seule exécution de maintenance est active à la fois. Une fiche peut aussi être ciblée en mettant `next_check_at` à une date échue dans Supabase.

Les deux scripts désactivent OpenAI et Gemini ; aucune clé IA ne leur est transmise par GitHub Actions. Le schéma Supabase et le plafond de **4 EUR par mois** restent inchangés. Le coût éventuel d'exécution ou de stockage GitHub Actions relève du forfait du compte ; aucun nouveau service payant n'est créé.

### Activation après validation

1. Vérifier le projet Supabase cible et son schéma existant, puis valider les changements de code et les sources. Conserver Auto-Deploy désactivé dans Render ; la fusion du code ne vaut pas autorisation de déployer en production.
2. Après fusion dans la branche par défaut, ouvrir **GitHub → dépôt → Settings → Secrets and variables → Actions → Secrets**. Ajouter `SUPABASE_URL` et une seule clé serveur : de préférence `SUPABASE_SECRET_KEY`, ou la variable compatible `SUPABASE_SERVICE_ROLE_KEY`. La clé moderne prend priorité si les deux existent ; supprimer une ancienne clé incorrecte plutôt que de laisser deux configurations contradictoires. Ne pas ajouter de clé OpenAI à ce workflow.
3. Dans **Actions → Collecte et vérification des smartphones → Run workflow**, laisser **dry_run** coché. Cette simulation manuelle est permise avant activation, ne reçoit pas les secrets Supabase et n'exécute pas la vérification ciblée de la base. Examiner le bilan et, si demandé, la sauvegarde des faits collectés.
4. Une fois la cible, la simulation et les données validées, ouvrir **Settings → Secrets and variables → Actions → Variables** et créer `ENABLE_WEEKLY_CHECK` avec la valeur exacte `true`. Toute écriture planifiée ou manuelle reste conditionnée à cette variable. Sans elle, le job planifié et un lancement manuel avec `dry_run` décoché sont ignorés.
5. Lancer une première fois **Run workflow** avec **dry_run** décoché et **backup** coché pour constituer la première archive, puis contrôler le bilan, les lignes Supabase et les suggestions du site déployé. Le planning suivant est chaque lundi à **10:00 UTC / 06:00 à La Paz**. GitHub exécute le planning uniquement depuis la branche par défaut et peut le retarder. Pour arrêter les mises à jour, retirer la variable ou la passer à `false`.

L'état GitHub « active » du fichier workflow ne prouve pas que sa condition d'activation est vraie ni qu'une collecte a réussi. La lecture des noms de variables et de secrets a été refusée par la connexion actuelle (HTTP 403) : aucune activation distante n'est confirmée. Contrôler le dernier run dans **Actions** ; un run rouge ou un bilan `discovery_ok=false` indique une couverture incomplète à traiter, pas une garantie de catalogue à jour.

### Sauvegarde mensuelle des faits

La première exécution planifiée de la première semaine du mois UTC (jour inférieur ou égal à 7) ajoute `--backup work-backup/phone-catalogue-AAAA-MM-JJ.json`. Un lancement manuel peut aussi cocher **backup**, en simulation ou en application. Le fichier contient uniquement les faits constructeur fusionnés et leur provenance : modèles, variantes, caractéristiques, sources, alias, références et dates. Il ne contient ni secrets, ni budget IA, ni prix, ni étiquettes, ni données de salariés.

Quand Supabase est configuré et joignable, ses fiches vérifiées Apple, Xiaomi/Redmi/POCO, Samsung et Google sont lues avant la collecte et fusionnées avec les catalogues locaux et les résultats réseau. Un modèle précédemment découvert reste ainsi sauvegardé même s'il disparaît d'un index courant. Le bilan indique `database_included` ; une lecture de base impossible suspend les écritures et signale un résultat partiel. La simulation GitHub sans secrets couvre seulement les catalogues locaux et les pages constructeur accessibles, et ne constitue pas un export de la base en production.

Les sauvegardes et bilans sont déposés dans les **Artifacts** du run GitHub, avec une rétention demandée de **90 jours**, avant la vérification ciblée afin de les conserver si cette dernière échoue. Vérifier que le dépôt autorise cette durée et télécharger les archives utiles avant expiration. Une interruption avant création du fichier ou un échec d'upload ne crée pas de sauvegarde : vérifier sa présence dans le run. Une sauvegarde issue d'une collecte partielle conserve les faits disponibles sans prétendre à une vérification exhaustive.

```sh
python catalogue_update.py --backup work-backup/phone-catalogue.json --report work-report/catalogue-update.json
# Après validation du projet Supabase uniquement :
python catalogue_update.py --apply --backup work-backup/phone-catalogue.json --report work-report/catalogue-update.json
```

L'archive n'est pas une sauvegarde complète de Supabase et ne remplace pas les exports de base nécessaires à une restauration globale. Le script ne déclenche pas de restauration automatique.

## Validation

`python -m unittest discover -s tests -v` et `python maintenance.py import`. Les tests n'utilisent ni secrets réels ni appels payants. Tests navigateur : recherche iPhone 16 128 Go, prix 1 000 EUR donnant 208 / 107 / 56 EUR, ajout à la planche et impression PDF A4. Le schéma a aussi été exécuté deux fois dans PostgreSQL embarqué (PGlite) : 40 réservations acceptées sur 60 demandes, plafond de 4 EUR et permissions vérifiés. Cela ne remplace pas une validation multi-connexions sur un vrai projet Supabase de test avant production. Le test reproductible est `tests/check_schema.cjs`, exécuté dans la CI.


## Gabarit graphique validé

Le modèle imprimé a été épuré à la demande du client : aucun bandeau de marque, police Inter locale, textes droits et chiffres tabulaires, caractéristiques à gauche sans photo, colonne marine de mensualités avec aplats jaunes et prix comptant sur fond blanc. Les halos, ombres et déformations sont retirés. Les quatre graisses Inter sont servies depuis assets/fonts, avec leur licence OFL, et intégrées au PDF. Le contrôle local avec pdffonts confirme leur incorporation ; pdfimages ne trouve aucune image matricielle dans le PDF des étiquettes. Le clic sur une étiquette ouvre un aperçu agrandi ; ce dialogue est exclu de l'impression. Aucun visuel de maquette n'est utilisé comme fiche technique.

Le test `tests/check_labels.cjs` vérifie les 113 modèles dédupliqués des catalogues : absence de débordement, format 88 × 52 mm, pagination (11 étiquettes sur deux pages A4), affichage mobile, modification d'une étiquette sauvegardée et coefficients. Il tourne également en CI avec Playwright/Chromium. Pour le lancer localement, démarrer le serveur avec `APP_ACCESS_TOKEN=browser-test`, installer Playwright, puis `APP_ACCESS_TOKEN=browser-test TEST_PORT=8002 node tests/check_labels.cjs` (définir `CHROMIUM_PATH` si Chromium est déjà installé). Le PDF de test et l'aperçu sont enregistrés dans `/tmp`, jamais envoyés en production.


## Saisie de capacité en GB

Le champ numérique « Capacité du téléphone (GB) » fonctionne avant ou après la recherche et lors de la modification d'une étiquette. Il accepte des entiers entre 1 et 2 048 ; 1 024 GB et 2 048 GB sont normalisés en 1 To et 2 To. Les variantes documentées restent prioritaires : une valeur absente de leur liste bloque l'ajout. Sans liste de capacités, la saisie du vendeur est conservée comme donnée manuelle de l'étiquette, sans être présentée ni enregistrée comme une preuve constructeur. Le test `tests/check_capacity.cjs` couvre ces cas dans Chromium.


Les nouvelles clés serveur Supabase (`sb_secret_…`) sont prises en charge dans `SUPABASE_SECRET_KEY` ou dans la variable existante `SUPABASE_SERVICE_ROLE_KEY`. Elles sont transmises uniquement dans l'en-tête `apikey`. Le mode JWT legacy conserve l'en-tête Bearer pour compatibilité. Préférer une clé dédiée au serveur Render et ne jamais la publier ; remplacer toute clé divulguée.


## Diagnostic OpenAI

Le serveur distingue désormais les crédits API insuffisants, les limites de débit, la clé refusée, un modèle indisponible et les paramètres incompatibles. Les messages sont construits localement : le corps d'erreur OpenAI et les clés ne sont jamais renvoyés au navigateur ni consignés dans ces logs. Une réponse incomplète n'est pas enregistrée comme fiche vérifiée. Les tentatives infructueuses restent réservées dans le quota mensuel par prudence.

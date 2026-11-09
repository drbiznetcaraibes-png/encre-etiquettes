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
4. Tester sans IA, puis ajouter `OPENAI_API_KEY`, `OPENAI_MODEL=gpt-5-mini`, `OPENAI_DAILY_MAX=3`. Gemini n'est plus appelé dans le parcours de recherche, pour éviter une deuxième facturation hors budget.
5. Valider manuellement modèle exact, variantes, stockage et sources d'un téléphone absent des catalogues. Tester impression et panne/quota avant de demander l'autorisation de déployer en production. Aucun appel payant réel n'a été fait pendant les tests automatisés.

## Budget et suivi

La fonction SQL `reserve_ai_call` réserve atomiquement **0,10 EUR par tentative**, y compris erreurs et délais dépassés, au maximum **40 appels / 4 EUR par mois UTC**, partagés entre instances et redémarrages. Seule la fonction peut modifier le compteur via l'API. La recherche utilise exclusivement `gpt-5-mini`, au maximum un appel d'outil web et 2 200 jetons de sortie. Un autre modèle est refusé jusqu'à révision du budget. Une limite locale de trois appels/jour complète cette protection ; elle seule se réinitialise au redémarrage.

`GET /api/admin/usage`, protégé par le même accès salarié, expose les tentatives et réservations mensuelles. `ai_usage` est également consultable dans Supabase. Il s'agit d'une **provision prudente, pas de la facture réelle** : vérifier les tarifs OpenAI en vigueur, le change et les taxes avant activation. Configurer un projet OpenAI dédié, une alerte de budget à 4 EUR équivalents et contrôler la facturation OpenAI. Les alertes du fournisseur ne garantissent pas un arrêt strict ; ne pas réutiliser la clé dans d'autres applications. L'objectif de 5 EUR concerne uniquement l'IA, pas l'hébergement Render/Supabase ou un éventuel Cron Job payant.

## Vérification hebdomadaire

Le workflow `.github/workflows/weekly.yml` est inactif par défaut. Après validation et fusion dans la branche par défaut, ajouter les secrets GitHub Actions `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, puis la variable `ENABLE_WEEKLY_CHECK=true`. Lancement chaque lundi à 10:00 UTC (06:00 à La Paz), ou manuel. GitHub n'exécute le planning que depuis la branche par défaut et peut le retarder.

Chaque passage traite au plus dix fiches : découvertes incomplètes et nouveaux modèles issus d'OpenAI arrivés à échéance. Les fiches stables des catalogues ne sont pas revérifiées systématiquement. Les échecs sont reprogrammés à sept jours ; les fiches vérifiées sont conservées en cas d'échec transitoire. Aucun appel IA dans cette tâche. Les nouveaux modèles sont découverts par les recherches utilisateurs et les imports, sans balayage global des annonces constructeur. Une fiche peut aussi être ciblée en mettant `next_check_at` à une date échue dans Supabase.

Alternative Render : Cron Job `python maintenance.py weekly --apply`, planning `0 10 * * 1`, avec les deux secrets Supabase, sans clé IA. Ne pas activer les deux ordonnanceurs. Vérifier le tarif Render avant création.

## Validation

`python -m unittest discover -s tests -v` et `python maintenance.py import`. Les tests n'utilisent ni secrets réels ni appels payants. Tests navigateur : recherche iPhone 16 128 Go, prix 1 000 EUR donnant 208 / 107 / 56 EUR, ajout à la planche et impression PDF A4. Le schéma a aussi été exécuté deux fois dans PostgreSQL embarqué (PGlite) : 40 réservations acceptées sur 60 demandes, plafond de 4 EUR et permissions vérifiés. Cela ne remplace pas une validation multi-connexions sur un vrai projet Supabase de test avant production. Le test reproductible est `tests/check_schema.cjs`, exécuté dans la CI.


## Gabarit graphique validé

Le modèle imprimé reprend le bandeau marine, le logo sans les trois rayons jaunes, les caractéristiques à gauche sans photo, les mensualités avec liseré bleu fin et les panneaux jaunes à droite, puis le prix comptant sur fond blanc. Les pictogrammes et les textes sont vectoriels/HTML : le rendu reste net et les données restent propres à chaque téléphone. Aucun visuel de maquette n'est utilisé comme fiche technique.

Le test `tests/check_labels.cjs` vérifie les 103 modèles dédupliqués des catalogues : absence de débordement, format 88 × 52 mm, pagination (11 étiquettes sur deux pages A4), affichage mobile, modification d'une étiquette sauvegardée et coefficients. Il tourne également en CI avec Playwright/Chromium. Pour le lancer localement, démarrer le serveur avec `APP_ACCESS_TOKEN=browser-test`, installer Playwright, puis `APP_ACCESS_TOKEN=browser-test TEST_PORT=8002 node tests/check_labels.cjs` (définir `CHROMIUM_PATH` si Chromium est déjà installé). Le PDF de test et l'aperçu sont enregistrés dans `/tmp`, jamais envoyés en production.


## Saisie de capacité en GB

Le champ numérique « Capacité du téléphone (GB) » fonctionne avant ou après la recherche et lors de la modification d'une étiquette. Il accepte des entiers entre 1 et 2 048 ; 1 024 GB et 2 048 GB sont normalisés en 1 To et 2 To. Les variantes documentées restent prioritaires : une valeur absente de leur liste bloque l'ajout. Sans liste de capacités, la saisie du vendeur est conservée comme donnée manuelle de l'étiquette, sans être présentée ni enregistrée comme une preuve constructeur. Le test `tests/check_capacity.cjs` couvre ces cas dans Chromium.


Les nouvelles clés serveur Supabase (`sb_secret_…`) sont prises en charge dans `SUPABASE_SECRET_KEY` ou dans la variable existante `SUPABASE_SERVICE_ROLE_KEY`. Elles sont transmises uniquement dans l'en-tête `apikey`. Le mode JWT legacy conserve l'en-tête Bearer pour compatibilité. Préférer une clé dédiée au serveur Render et ne jamais la publier ; remplacer toute clé divulguée.


## Diagnostic OpenAI

Le serveur distingue désormais les crédits API insuffisants, les limites de débit, la clé refusée, un modèle indisponible et les paramètres incompatibles. Les messages sont construits localement : le corps d'erreur OpenAI et les clés ne sont jamais renvoyés au navigateur ni consignés dans ces logs. Une réponse incomplète n'est pas enregistrée comme fiche vérifiée. Les tentatives infructueuses restent réservées dans le quota mensuel par prudence.

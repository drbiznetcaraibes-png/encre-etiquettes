# ENCRE POUR TOUS — Générateur d'étiquettes V12

Cette mise à jour se concentre sur la **fiabilité de l'identification des smartphones** sans modifier le modèle graphique V11.

## Installation sur Render + GitHub

1. Décompressez le ZIP.
2. Dans le dépôt GitHub `encre-etiquettes`, remplacez les fichiers présents à la racine par **tous les fichiers** du ZIP. Cliquez sur **Commit changes**.
3. Render > service `encre-etiquettes` > **Environment** : vérifiez la variable `GEMINI_API_KEY`. Ne la placez jamais dans le dépôt GitHub.
4. Pour permettre le choix automatique de modèle, supprimez l'ancienne variable `GEMINI_MODEL` si elle existe, ou affectez-lui la valeur **`auto`**.
5. Render > **Manual Deploy** > **Deploy latest commit** (ou attendez le redéploiement automatique). Attendez le statut **Live**, puis actualisez Chrome avec Ctrl+F5.
6. Dans l'application, cliquez sur « Tester la connexion IA » puis recherchez `Samsung A56 256 Go`, `iPhone 17 Pro Max 256 Go` et `Xiaomi 15T Pro`.

## Fonctionnement

- Recherche en priorité avec Gemini et les sources Google, avec repli sur les résultats de recherche publics et les fiches constructeur reconnues.
- Choix de modèle Gemini plus souple : en mode auto, essai de `gemini-3.5-flash-lite`, puis `gemini-3.8-flash`, puis `gemini-2.5-flash-lite` en cas de modèle indisponible. En cas de quota ou clé invalide, aucune autre requête Gemini n'est lancée inutilement.
- Ne valide une étiquette que si le modèle et au moins trois caractéristiques sont identifiés, avec sources vérifiables.
- Les fiches déjà vérifiées sont sauvegardées **dans le navigateur** utilisé (localStorage). Render Free ne garantit pas de stockage durable sur son disque.
- Saisie du seul prix TTC, puis paiements ×5 (0,20748), ×10 (0,10693), ×20 (0,055885), arrondis à l'euro supérieur.
- Gabarit commercial V11 inchangé : 88 × 52 mm, 2 × 5 par A4, sans photo, prix TTC seul dans un cartouche blanc.

## Limites et sécurité

- La recherche Internet/IA dépend de l'état du réseau, de la disponibilité du modèle Gemini, de la clé et des quotas. Certains appels peuvent être refusés ; la recherche n'est pas garantie à 100 %.
- **L'utilisation gratuite de Gemini dépend du forfait de votre compte Google et des quotas applicables.** Consultez la tarification officielle : https://ai.google.dev/gemini-api/docs/pricing
- Le moteur peut refuser une variante inconnue plutôt que d'imprimer des informations techniques incorrectes.
- Le diagnostic vérifie que la clé existe côté serveur, **pas** qu'elle est valide ou qu'elle dispose encore de quota.
- Ne rendez pas ce service public sans contrôle d'accès si vous activez une facturation Google ; les limites de requêtes en mémoire de ce petit serveur ne sont pas un dispositif anti-abus suffisant.
- Aucune vraie interrogation Gemini n'a été exécutée sans la clé API personnelle, qui ne doit pas être communiquée.

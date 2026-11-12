"""Recherche web OpenAI facultative pour les fiches smartphones.

Appels uniquement côté serveur : la clé OpenAI ne doit jamais arriver au navigateur.
Les résultats IA ne sont pas des sources officielles et sont revalidés dans server.py.
"""
from __future__ import annotations

import json
import ipaddress
import re
from urllib.parse import urlparse

import httpx


class OpenAIServiceError(RuntimeError):
    """Message public construit localement, sans corps d'erreur ni clé API."""


def _http_error(response):
    try:
        error = response.json().get('error', {})
        if not isinstance(error, dict):
            error = {}
    except (ValueError, AttributeError):
        error = {}
    code = error.get('code')
    quota_codes = {'insufficient_quota', 'billing_hard_limit_reached', 'billing_not_active'}
    if response.status_code in (402, 429) and (code in quota_codes or error.get('type') in quota_codes):
        return OpenAIServiceError('Crédits API OpenAI insuffisants : vérifie la facturation du projet OpenAI.')
    if response.status_code == 429:
        return OpenAIServiceError('Limite de requêtes OpenAI atteinte : vérifie les limites du projet OpenAI, puis réessaie plus tard.')
    if response.status_code in (401, 403):
        return OpenAIServiceError('Clé API OpenAI refusée : vérifie OPENAI_API_KEY sur Render.')
    if code == 'model_not_found':
        return OpenAIServiceError('Modèle OpenAI indisponible pour cette clé API.')
    param = error.get('param')
    safe_params = {'model', 'tools', 'tool_choice', 'max_tool_calls', 'max_output_tokens', 'include', 'store'}
    suffix = ' (paramètre '+param+')' if isinstance(param,str) and param in safe_params else ''
    return OpenAIServiceError(f'Erreur OpenAI HTTP {response.status_code}'+suffix+'.')


def _json_object(text: str):
    """Accepte du JSON brut ou un unique bloc JSON entouré de markdown."""
    value = (text or "").strip()
    value = re.sub(r"^\x60\x60\x60(?:json)?\s*", "", value, flags=re.I)
    value = re.sub(r"\s*\x60\x60\x60$", "", value)
    try:
        obj = json.loads(value)
        return obj if isinstance(obj, dict) else None
    except (ValueError, TypeError):
        match = re.search(r"\{[\s\S]*\}", value)
        if not match:
            return None
        try:
            obj = json.loads(match.group())
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None


def _safe_source(url, title="Source web"):
    if not isinstance(url, str) or len(url) > 2048 or re.search(r'[\x00-\x20\x7f]', url):
        return None
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip('.')
        port = parsed.port
    except ValueError:
        return None
    # Only external HTTPS sources. Reject local/internal addresses and userinfo.
    if parsed.scheme != "https" or not host or parsed.username or parsed.password or port not in (None, 443):
        return None
    if host == "localhost" or host.endswith((".local", ".localhost", ".internal", ".lan", ".home")):
        return None
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Reject single-label hosts and browser-interpreted numeric IP spellings.
        if "." not in host or re.fullmatch(r'(?:0x[0-9a-f]+|\d+)(?:\.(?:0x[0-9a-f]+|\d+))*', host, re.I):
            return None
    else:
        if not address.is_global or address.is_multicast or address.is_reserved:
            return None
    return {"title": str(title or host)[:150], "url": url}


def parse_openai_response(payload):
    """Extrait uniquement la réponse textuelle et des URL effectivement renvoyées par l'outil."""
    if not isinstance(payload, dict) or payload.get("status") != "completed":
        return None
    output = payload.get("output") or []
    if not isinstance(output, list) or not any(
        isinstance(part, dict) and part.get("type") == "web_search_call" and part.get("status") == "completed"
        for part in output
    ):
        return None

    texts, urls, seen = [], [], set()

    def add(url, title):
        item = _safe_source(url, title)
        if item and item["url"] not in seen and len(urls) < 6:
            seen.add(item["url"])
            urls.append(item)

    for part in output:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "message":
            if part.get("status") != "completed":
                continue
            for content in part.get("content") or []:
                if not isinstance(content, dict) or content.get("type") != "output_text":
                    continue
                if isinstance(content.get("text"), str):
                    texts.append(content["text"])
                for annotation in content.get("annotations") or []:
                    if isinstance(annotation, dict) and annotation.get("type") == "url_citation":
                        add(annotation.get("url"), annotation.get("title"))
        elif part.get("type") == "web_search_call" and part.get("status") == "completed":
            action = part.get("action") or {}
            if not isinstance(action, dict):
                continue
            for source in action.get("sources") or []:
                if isinstance(source, dict):
                    add(source.get("url"), source.get("title"))

    parsed = _json_object("\n".join(texts))
    if not parsed or not urls:
        return None
    return parsed, urls


async def search_openai_web(query: str, api_key: str, model: str = "gpt-5.4-mini"):
    """One grounded Responses API request (web_search) for an unknown model."""
    if not api_key:
        return None
    prompt = f"""Tu prépares une fiche technique commerciale française pour un smartphone.
Recherche réellement sur le Web la référence ou le modèle EXACT : {query!r}.
Priorité aux pages constructeur officielles (Apple, Samsung, Xiaomi, etc.).
N'utilise jamais une fiche d'une variante voisine, ni un comparatif confondant deux modèles.
Vérifie soigneusement 4G/5G, Plus/Pro/Ultra/Pro Max, année et génération.
Une référence constructeur familiale ne prouve jamais le stockage de l'exemplaire.
Ne confirme une capacité demandée que si les sources prouvent que le modèle existe en cette capacité.
Si la capacité n'est pas documentée, écris exactement "Non précisée".
Une donnée non confirmée doit être une chaîne vide. N'invente rien.
Retourne exclusivement un objet JSON valide, sans markdown et sans explication :
{{"verified":true,"brand":"SAMSUNG","model":"Galaxy A56 5G","capacity":"Non précisée",
"manufacturer_reference":"","specs":{{"ecran":"6,7 pouces AMOLED 120 Hz","photo":"50 Mpx",
"batterie":"5000 mAh","processeur":"Exynos 1580","reseau":"5G","ram":"8 Go"}}}}
Ce JSON est un EXEMPLE de format : n'en recopie aucune valeur sans preuve.
Si tu ne peux confirmer au moins 3 caractéristiques spécifiques du modèle exact,
retourne {{"verified":false}}.
"""
    body = {
        "model": model,
        "input": prompt,
        "tools": [{"type": "web_search", "search_context_size": "low"}],
        "tool_choice": "required",
        "include": ["web_search_call.action.sources"],
        "max_output_tokens": 2200,
        "max_tool_calls": 1,
        "reasoning": {"effort": "low"},
        "store": False,
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(19, connect=4)) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
            )
    except httpx.TimeoutException as exc:
        raise OpenAIServiceError("Délai OpenAI dépassé.") from exc
    except httpx.RequestError as exc:
        raise OpenAIServiceError("Connexion au service OpenAI impossible.") from exc

    if response.status_code != 200:
        raise _http_error(response)
    payload = response.json()
    if payload.get('status') == 'incomplete':
        raise OpenAIServiceError('Réponse OpenAI incomplète : aucune fiche IA enregistrée.')
    return parse_openai_response(payload)

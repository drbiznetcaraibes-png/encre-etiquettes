"""Encre Pour Tous – minimal web lookup server.
Optional Gemini API with grounded Google Search on the server; public search fallback.
The API key must only be stored as a Render environment variable.
"""
from __future__ import annotations
import asyncio
import secrets
import logging
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import html
import json
import os
import re
import time
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse

from official_phones import lookup_official_phone
from xiaomi_phones import lookup_xiaomi_phone
from samsung_phones import lookup_samsung_phone
from manufacturer_pages import lookup_manufacturer_phone
from openai_phones import search_openai_web, OpenAIServiceError
from storage import store

BASE = Path(__file__).resolve().parent
app = FastAPI(title='ENCRE POUR TOUS – recherche téléphones', version='1.16.2')
@app.middleware('http')
async def protect_access(request, call_next):
    from fastapi.responses import JSONResponse
    import base64
    token = os.getenv('APP_ACCESS_TOKEN', '')
    if request.url.path != '/health':
        supplied = request.headers.get('authorization', '')
        expected = 'Basic ' + base64.b64encode(('encre:' + token).encode()).decode()
        if not token or not secrets.compare_digest(supplied, expected):
            return JSONResponse({'detail': 'Authentification requise.'}, status_code=401,
                                headers={'WWW-Authenticate': 'Basic realm="ENCRE POUR TOUS"'})
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    return response

HEADERS = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36', 'Accept-Language':'fr-FR,fr;q=0.9,en;q=0.8'}
TIMEOUT = httpx.Timeout(9, connect=4)

BRAND_PATTERNS = [(r'\b(samsung|galaxy)\b','SAMSUNG'),(r'\b(apple|iphone)\b','APPLE'),(r'\b(xiaomi|redmi|poco)\b','XIAOMI'),(r'\b(oppo)\b','OPPO'),(r'\b(realme)\b','REALME'),(r'\b(honor)\b','HONOR'),(r'\b(huawei)\b','HUAWEI'),(r'\b(motorola|moto\s)\b','MOTOROLA'),(r'\b(google|pixel)\b','GOOGLE'),(r'\b(nothing phone|nothing)\b','NOTHING'),(r'\b(oneplus)\b','ONEPLUS'),(r'\b(nokia|hmd)\b','NOKIA'),(r'\b(vivo)\b','VIVO'),(r'\b(tecno)\b','TECNO'),(r'\b(infinix)\b','INFINIX')]
MODEL_PATTERNS = [
 (r'\bGalaxy\s+(?:S\s*\d{1,2}(?:\s*(?:Ultra|Plus|FE|\+))?(?:\s*5G)?|Z\s*(?:Fold|Flip)\s*\d+(?:\s*Ultra)?(?:\s*5G)?|A\s*\d{1,2}(?:\s*(?:4G|5G))?|M\d{1,2}(?:\s*(?:4G|5G))?|Note\s*\d{1,2}(?:\s*Ultra)?)(?![\w+])', 'SAMSUNG'),
 (r'\biPhone\s*(?:SE(?:\s*(?:20(?:16|20|22)|[123]|\([123](?:re|e|ère|ème)?\s*génération\)))?|XS(?:\s*Max)?|XR|X|(?:[12]\d|[5-9])(?:\s*(?:Pro\s*Max|Pro|Plus|mini|e))?|Air)(?![\w+])', 'APPLE'),
 (r'\b(?:Redmi\s*Note\s*\d{1,2}(?:\s*Pro\+?)?(?:\s*(?:4G|5G))?|Redmi\s*\d{1,2}(?:\s*[AC])?(?:\s*(?:4G|5G))?|POCO\s*[XFM]\d{1,2}(?:\s*Pro)?(?:\s*(?:4G|5G))?|Xiaomi\s*\d{1,2}(?:\s*(?:T\s*Pro|Ultra|Pro|T|Lite))?(?:\s*(?:4G|5G))?)(?![\w+])', 'XIAOMI'),
 (r'\b(?:Pixel\s*\d{1,2}(?:\s*(?:Pro\s*XL|Pro|a|XL|Fold))?)', 'GOOGLE'),
 (r'\b(?:HONOR\s*(?:Magic\s*\d{1,2}(?:\s*(?:Pro|Lite))?|\d{1,3}(?:\s*(?:Pro|Lite))?|X\d{1,2}))', 'HONOR'),
 (r'\b(?:OPPO\s*(?:Reno\s*\d{1,2}(?:\s*(?:Pro|F))?|A\d{2,3}|Find\s*[NX]\d{1,2}(?:\s*Pro)?))', 'OPPO'),
 (r'\b(?:Motorola\s*(?:Edge\s*\d{1,2}(?:\s*(?:Ultra|Pro|Fusion))?|Moto\s*G\d{1,2}))', 'MOTOROLA'),
 (r'\b(?:realme\s*(?:\d{1,2}(?:\s*Pro\+?)?|GT\s*\d{1,2}(?:\s*Pro)?|C\d{2,3}))', 'REALME'),
 (r'\b(?:OnePlus\s*\d{1,2}(?:\s*[RT])?|OnePlus\s*Nord\s*\d{1,2})', 'ONEPLUS'),
]
CAP_RE = re.compile(r'(?<![\w])(?:(16|32|64|128|256|512|1024|2048)\s*(?:Go|GB)|(1|2)\s*(?:To|TB))\b',re.I)
REF_OK = re.compile(r'^[A-Z0-9][A-Z0-9_./#\-+]{3,59}$')
MODEL_QUERY_OK = re.compile(r"^[\w\s()./# +\-]{4,60}$", re.UNICODE)

def _normalize_model_text(value):
 text=unicodedata.normalize('NFKC',str(value or '')).replace('\u00ad','')
 # Brands/subbrands and model words are frequently typed without spaces.
 text=re.sub(r'\bXiaomi\s*(?=Redmi|POCO)', '', text, flags=re.I)
 for word,following in [('iPhone',r'\d|SE|XS|XR|X|Air'),('Redmi',r'Note|\d'),
                        ('POCO',r'[XFM]\d'),('Xiaomi',r'\d'),('Samsung',r'Galaxy|[SAZM]\d'),
                        ('Galaxy',r'[SAZM]\d|Z\s*(?:Fold|Flip)'),('Nothing',r'Phone'),
                        ('Google',r'Pixel'),('OnePlus',r'\d|Nord'),('OPPO',r'Reno|Find|A\d')]:
  text=re.sub(r'\b'+word+r'(?='+following+r')',word+' ',text,flags=re.I)
 # Explicit storage can be attached to a model suffix (ProMax512Go, 17T256GB).
 # Require the unit and end of the option, so generation numbers stay intact.
 text=re.sub(r'(?<=[A-Za-z+])((?:16|32|64|128|256|512|1024|2048)\s*(?:Go|GB)|[12]\s*(?:To|TB))(?=\s|$)',r' \1',text,flags=re.I)
 return re.sub(r'\s+',' ',text).strip()

async def _search_bing(client, ref: str):
 url='https://www.bing.com/search'
 try:
  r=await client.get(url, params={'format':'rss','q':f'{ref} smartphone caractéristiques stockage capacité'},headers=HEADERS)
  r.raise_for_status()
  root=ET.fromstring(r.text)
  items=[]
  for itm in root.findall('.//item')[:10]:
   title=itm.findtext('title') or ''
   desc=BeautifulSoup(itm.findtext('description') or '', 'html.parser').get_text(' ',strip=True)
   link=itm.findtext('link') or ''
   items.append({'title':title,'snippet':desc,'url':link})
  return items
 except Exception: return []

async def _search_ddg(client, ref: str):
 try:
  r=await client.get('https://html.duckduckgo.com/html/',params={'q':f'{ref} téléphone caractéristiques capacité'},headers=HEADERS)
  r.raise_for_status()
  doc=BeautifulSoup(r.text,'html.parser')
  items=[]
  for result in doc.select('.result')[:12]:
   a=result.select_one('.result__a'); desc=result.select_one('.result__snippet')
   if not a:continue
   items.append({'title':a.get_text(' ',strip=True),'snippet':desc.get_text(' ',strip=True) if desc else '', 'url':a.get('href','')})
  return items
 except Exception: return []

def _normalize_ref(s): return re.sub(r'[^A-Z0-9]','',s.upper().replace('+','PLUS'))

def _model_identity(text, brand):
    """Normalise marque commerciale et modèle, sans confondre les versions."""
    text=_normalize_model_text(text)
    b=(brand or '').upper()
    if b=='SAMSUNG':
        text=re.sub(r'^(?:SAMSUNG\s+)?(?:GALAXY\s+)?', '', text, flags=re.I)
        text=re.sub(r'^SAMSUNG\s+', '', text, flags=re.I)
    elif b=='APPLE':
        text=re.sub(r'^APPLE\s+', '', text, flags=re.I)
        if re.match(r'^iPhone\s*SE',text,re.I):
            generation=re.sub(r'\D','',text)
            if generation in ('2020','2'):return 'IPHONESE2020'
            if generation in ('2022','3'):return 'IPHONESE2022'
            if generation in ('2016','1'):return 'IPHONESE2016'
    elif b=='XIAOMI':
        text=re.sub(r'^XIAOMI\s+', '', text, flags=re.I)
    else:
        text=re.sub(r'^'+re.escape(b)+r'\s+', '', text, flags=re.I)
    return _normalize_ref(text)

def _same_model(expected, received, brand):
    a=_model_identity(expected,brand)
    b=_model_identity(received,brand)
    if not a or not b:
        return False
    if a == b:
        return True
    # Galaxy A56 is sold as A56 5G; accept a shorter customer name only
    # for this exact generation, not for ambiguous A07/A16 variants.
    return brand.upper() == 'SAMSUNG' and a == 'A56' and b == 'A565G'


def _detect_brand(text):
 for pat,brand in BRAND_PATTERNS:
  if re.search(pat,text,re.I):return brand
 return None

def _parse_capacity(text):
 # Storage units are explicit; 8/12/16 GB RAM must never become storage.
 m=next((m for m in CAP_RE.finditer(text)
         if not re.match(r'\s*(?:de\s+)?RAM\b',text[m.end():],re.I)),None)
 if not m:return None
 if m.group(2):return m.group(2)+' To'
 return {'1024':'1 To','2048':'2 To'}.get(m.group(1),m.group(1)+' Go')

def _capacities_in(text):
 return {_parse_capacity(m.group(0)) for m in CAP_RE.finditer(text)
         if not re.match(r'\s*(?:de\s+)?RAM\b',text[m.end():],re.I)}

def _extract_model(text):
 text=_normalize_model_text(text)
 for pattern,brand in MODEL_PATTERNS:
  m=re.search(pattern,text,re.I)
  if m:
   out=re.sub(r'\s+',' ',m.group(0)).strip()
   if brand=='SAMSUNG' and not out.lower().startswith('galaxy'):out='Galaxy '+out
   return out,brand
 return None,None

def _query_model(query):
 # Allow familiar expressions such as "Samsung S26 Ultra" without pretending this is a manufacturer SKU.
 cleaned = _normalize_model_text(query)
 # Remove memory options, never a part of the model name or generation.
 cleaned=re.sub(r'(?<!\w)\d{1,2}\s*(?:Go|GB)\s*(?:de\s+)?RAM\b','',cleaned,flags=re.I)
 cleaned=CAP_RE.sub('',cleaned).strip()
 # A colour is not a different phone, but unknown suffixes remain significant.
 cleaned=re.sub(r'\s+(?:noir|blanc|bleu|rose|vert|violet|gris|argent|gold|black|white|blue|silver)$','',cleaned,flags=re.I).strip()
 # Every displayed suggestion is itself a valid model query, including names
 # with generation parentheses, written Plus, or newly introduced suffixes.
 for row in _known_phone_models():
  identity=_model_identity(cleaned,row['brand'])
  if any(identity==_model_identity(name,row['brand'])
         for name in [row['model'],*row['_aliases']]):
   return row['model'],row['brand']
 model, brand = _extract_model(cleaned)
 if not model:
  b = _detect_brand(cleaned)
  if b == 'SAMSUNG':
   m = re.search(r"\b(?:S\s?\d{2}|A\s?\d{2}|Z\s?(?:Fold|Flip)\s?\d+)(?:\s*(?:Ultra|Plus|FE|\+))?(?:\s*(?:4G|5G))?(?![\w+])",cleaned,re.I)
   if m: model='Galaxy '+m.group(0);brand='SAMSUNG'
 if not model:
  # Allow new models without changing regex on every new release. Explicit brand
  # plus an alphanumeric generation is mandatory; avoid overly vague strings.
  b=_detect_brand(cleaned)
  if b=='APPLE':return None
  stripped=CAP_RE.sub('',cleaned)
  stripped=re.sub(r'^(?:samsung|apple|xiaomi)\s+', '',stripped,flags=re.I).strip()
  if b and re.search(r'\d',stripped) and len(stripped)>=4:
   if re.match(r'^(?:SM-|MZB|[A-Z]\d{4,}[A-Z0-9/.-]*$)',stripped,re.I):return None
   model=('Galaxy '+re.sub(r'^Galaxy\s+', '',stripped,flags=re.I)) if b=='SAMSUNG' else stripped
   brand=b
 if not model: return None
 if _model_identity(cleaned,brand)!=_model_identity(model,brand):
  if brand=='APPLE':return None
  # Preserve a new variant's full name. Its manufacturer provider must verify
  # that full identity; never silently search for a shorter base model.
  return cleaned,brand
 return model,brand

def _item_matches_model(item, model, brand):
 """A comparison or another variant is not evidence for this phone's specs."""
 text=html.unescape((item.get('title') or '')+' '+(item.get('snippet') or ''))
 if re.search(r'\b(?:vs\.?|versus|comparatif|comparaison)\b',text,re.I):return False
 detected,b=_extract_model(text)
 if b!=brand or not detected or not _same_model(model,detected,brand):return False
 for pattern,item_brand in MODEL_PATTERNS:
  for match in re.finditer(pattern,text,re.I):
   if item_brand!=brand or not _same_model(model,match.group(0),brand):return False
 return True

def _analyze_model_query(ref, items):
 expected=_query_model(ref)
 if not expected:return None
 query_model,query_brand=expected
 candidates=[]; links=[]
 for item in items:
  title=html.unescape(item.get('title','')); snippet=html.unescape(item.get('snippet',''))
  combined=title+' — '+snippet
  model,brand=_extract_model(combined)
  if not _item_matches_model(item,query_model,query_brand):continue
  candidates.append((model,brand))
  url=_clean_link(item.get('url',''))
  if url and len(links)<5 and url not in [u['url'] for u in links]: links.append({'title':title[:160],'url':url})
 if not candidates:return None
 # Results may show many variants; model-name queries cannot identify a specific storage capacity.
 supplied_cap=_parse_capacity(ref)
 # The customer's requested option must also occur in matching evidence.
 supported=set().union(*(_capacities_in((i.get('title') or '')+' '+(i.get('snippet') or ''))
                         for i in items if _item_matches_model(i,query_model,query_brand)))
 confirmed=bool(supplied_cap and supplied_cap in supported)
 specs=_specs_from_public(ref,items)
 return {'brand':query_brand,'model':query_model,'capacity':supplied_cap if confirmed else 'Non précisée',
         'feature':_spec_feature(specs),'specs':specs,
         'sources':links,'matching_results':len(candidates),
         'capacity_confirmed':confirmed,'input_type':'model_name',
         'status':'verified_online'}

async def _search_google_news(client, ref: str):
 # RSS public, utilisé comme source supplémentaire si les moteurs bloquent les robots.
 try:
  r=await client.get('https://news.google.com/rss/search',params={'q':f'"{ref}" smartphone','hl':'fr','gl':'FR','ceid':'FR:fr'},headers=HEADERS)
  r.raise_for_status()
  root=ET.fromstring(r.text)
  out=[]
  for itm in root.findall('.//item')[:10]:
   out.append({'title':itm.findtext('title') or '', 'snippet':BeautifulSoup(itm.findtext('description') or '', 'html.parser').get_text(' ',strip=True), 'url':itm.findtext('link') or ''})
  return out
 except Exception: return []

async def _search_bing_html(client, ref: str):
 try:
  r=await client.get('https://www.bing.com/search',params={'q':f'"{ref}" smartphone'},headers=HEADERS)
  r.raise_for_status()
  soup=BeautifulSoup(r.text,'html.parser'); out=[]
  for item in soup.select('li.b_algo')[:10]:
   a=item.select_one('h2 a'); desc=item.select_one('.b_caption')
   if a:out.append({'title':a.get_text(' ',strip=True),'snippet':desc.get_text(' ',strip=True) if desc else '', 'url':a.get('href','')})
  return out
 except Exception: return []

async def _search_official_samsung(client, ref: str):
 # A manufacturer web page is much more reliable than scraped search snippets.
 found=_query_model(ref)
 if not found or found[1]!='SAMSUNG':return []
 model=found[0]
 slug=re.sub(r'[^a-z0-9]+','-',model.lower()).strip('-')
 url=f'https://www.samsung.com/latin_en/smartphones/{slug}/'
 try:
  r=await client.get(url,headers=HEADERS)
  r.raise_for_status()
  soup=BeautifulSoup(r.text,'html.parser')
  title=(soup.title.get_text(' ',strip=True) if soup.title else '')
  h1=' '.join(x.get_text(' ',strip=True) for x in soup.select('h1')[:4])
  # Validate manufacturer page title/heading, not just a generic page mentioning the name.
  check=_normalize_ref(title+' '+h1)
  if _normalize_ref(model) not in check:return []
  description=' '.join(x.get('content','') for x in soup.select('meta[name="description"],meta[property="og:description"]'))
  return [{'title':title,'snippet':h1+' '+description,'url':str(r.url)}]
 except Exception:return []

OPENAI_KEY = os.environ.get('OPENAI_API_KEY', '').strip()
OPENAI_MODEL = os.environ.get('OPENAI_MODEL', 'gpt-5-mini').strip() or 'gpt-5-mini'
OPENAI_DAILY_MAX = max(0, min(100, int(os.environ.get('OPENAI_DAILY_MAX', '3'))))
OPENAI_CALL_LOG = []
GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
# Explicit model override is supported; 'auto' tries current models compatible with a free API key.
# 2.5 Flash-Lite can be unavailable for newer API projects, so prefer current Flash-Lite.
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'auto').strip()
GEMINI_CANDIDATES = ('gemini-3.5-flash-lite', 'gemini-3.8-flash', 'gemini-2.5-flash-lite')



def _gemini_json(text):
    # JSON output may be fenced by the model despite the requested format.
    text = text.strip()
    if text.startswith('```'):
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.I)
        text = re.sub(r'\s*```$', '', text)
    try:
        obj = json.loads(text)
    except (ValueError, TypeError):
        match = re.search(r'\{[\s\S]*\}', text)
        if not match:
            return None
        try:
            obj = json.loads(match.group())
        except ValueError:
            return None
    return obj if isinstance(obj, dict) else None


def _grounding_sources(payload):
    urls = []
    candidates = payload.get('candidates') or []
    for c in candidates:
        meta = c.get('groundingMetadata') or {}
        for chunk in meta.get('groundingChunks') or []:
            web = chunk.get('web') or {}
            url = web.get('uri', '')
            clean = _clean_link(url) if isinstance(url, str) else None
            if clean and clean not in [i['url'] for i in urls]:
                urls.append({'title': (web.get('title') or 'Source web')[:150], 'url': clean})
    return urls[:6]


def _model_candidates():
    name = GEMINI_MODEL
    if name and name.lower() != 'auto':
        return (name,)
    return GEMINI_CANDIDATES


def _gemini_error_message(response):
    code = response.status_code
    try:
        parsed = response.json().get('error', {})
        message = str(parsed.get('message') or '')
    except Exception:
        message = ''
    if code in (400, 404) and ('not found' in message.lower() or 'not supported' in message.lower() or 'model' in message.lower()):
        return 'modele_indisponible'
    if code == 429:
        return 'quota_atteint'
    if code in (401,403):
        return 'cle_non_autorisee'
    return f'HTTP_{code}'


def _first_fenced_json(text):
    result = _gemini_json(text)
    if result is not None:
        return result
    # Sometimes the model replies with several JSON objects; choose the first valid one.
    decoder = json.JSONDecoder()
    for match in re.finditer(r'\{', text):
        try:
            obj, _ = decoder.raw_decode(text[match.start():])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            continue
    return None


async def gemini_lookup(query, is_model_query=False):
    """Grounded Gemini lookup with model availability fallback and explicit diagnostics.

    One successful grounded call per lookup; no extra AI generation for parsing.
    """
    if not GEMINI_KEY:
        return None
    prompt = f'''Tu vérifies les caractéristiques exactes des smartphones pour une étiquette commerciale.
REQUÊTE DU VENDEUR : {query!r}

Cherche en priorité les pages constructeur officielles Samsung, Apple, Xiaomi/Redmi/POCO,
puis compare éventuellement à deux autres sources. Vérifie le NOM EXACT et la génération.
Le suffixe 4G/5G, Plus/Pro/Ultra/FE, la capacité, et le code constructeur sont essentiels.
Si l'utilisateur n'indique PAS de capacité, mets exactement "Non précisée" : ne la devine pas.
Si l'utilisateur indique un stockage, indique-le seulement s'il existe pour ce modèle.
Retourne des faits précis, chacun vérifiable dans les sources : écran (taille et type),
photo (Mpx), batterie (mAh), processeur (nom), réseau (4G/5G), RAM (Go).
Les champs incertains sont des chaînes vides. Ne retourne pas d'informations génériques.
Si le modèle exact n'est pas confirmé et que moins de trois caractéristiques
spécifiques sont documentées, définis "verified": false.
Réponds avec UN SEUL objet JSON sans markdown, schéma :
{{"verified":true,"brand":"SAMSUNG","model":"Galaxy A56 5G","capacity":"Non précisée","manufacturer_reference":"","specs":{{"ecran":"6,7 pouces Super AMOLED 120 Hz","photo":"50 Mpx","batterie":"5000 mAh","processeur":"Exynos 1580","reseau":"5G","ram":"8 Go"}}}}
Les valeurs du schéma sont UNIQUEMENT des exemples : ne les copie pas sans vérification.
'''
    body = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
            'tools': [{'google_search': {}}],
            'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 2600}}
    payload = None
    tried = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(23, connect=5)) as client:
        for model in _model_candidates():
            endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{quote(model, safe="")}:generateContent'
            try:
                response = await client.post(endpoint, headers={'x-goog-api-key': GEMINI_KEY, 'Content-Type': 'application/json'}, json=body)
            except httpx.TimeoutException:
                raise RuntimeError('Delai Gemini depasse. Reessaie.')
            except httpx.RequestError:
                raise RuntimeError('Connexion au service Gemini impossible.')
            if response.status_code == 200:
                payload = response.json()
                break
            error = _gemini_error_message(response)
            tried.append(f'{model}:{error}')
            # If the model is missing, try another one. Rate limits or bad keys
            # must stop immediately to avoid needless additional requests.
            if error == 'modele_indisponible':
                continue
            if error == 'quota_atteint':
                raise RuntimeError('Quota gratuit Gemini atteint ou limité : vérifie AI Studio.')
            if error == 'cle_non_autorisee':
                raise RuntimeError('Clé Gemini refusée : vérifie GEMINI_API_KEY dans Render.')
            raise RuntimeError(f'Erreur Gemini {error} avec {model}.')
    if payload is None:
        raise RuntimeError('Aucun modèle Gemini disponible sur cette clé (' + ', '.join(tried) + ').')

    sources = _grounding_sources(payload)
    candidates = payload.get('candidates') or []
    texts = [part['text'] for candidate in candidates
             for part in (((candidate.get('content') or {}).get('parts')) or [])
             if isinstance(part.get('text'), str)]
    parsed = _first_fenced_json('\n'.join(texts))
    if not parsed or parsed.get('verified') is not True or not sources:
        return None
    brand = str(parsed.get('brand') or '').strip().upper()[:35]
    model = str(parsed.get('model') or '').strip()[:105]
    capacity = str(parsed.get('capacity') or 'Non précisée').strip()[:32]
    specs = _clean_specs(parsed.get('specs'))
    if not brand or not model or len(specs) < 3:
        return None
    # Match the model from a name query, but accept cosmetic punctuation or
    # 'Samsung Galaxy' prefix changes, not a different Plus/Ultra/FE/5G variant.
    if is_model_query:
        expected = _query_model(query)
        if not expected or expected[1] != brand or not _same_model(expected[0], model, brand):
            return None
        requested=_parse_capacity(query)
        if not requested or _parse_capacity(capacity)!=requested:
            capacity = 'Non précisée'
        else:
            capacity=requested
    else:
        sku = str(parsed.get('manufacturer_reference') or '').strip()
        if not sku or _normalize_ref(query.split('/')[0]) not in _normalize_ref(sku):
            return None
    return {'brand':brand,'model':model,'capacity':capacity,
            'feature':_spec_feature(specs),'specs':specs,
            'capacity_confirmed':capacity!='Non précisée', 'sources':sources,
            'status':'verified_online',
            'input_type':'model_name' if is_model_query else 'manufacturer_sku',
            'search_mode':'gemini_grounded','matching_results':len(sources)}

async def openai_lookup(query, is_model_query=False):
    """Fallback OpenAI with strict identity and storage checks."""
    if not OPENAI_KEY or OPENAI_DAILY_MAX == 0:
        return None
    if OPENAI_MODEL != 'gpt-5-mini':
        raise RuntimeError('Modèle IA non autorisé par le budget configuré.')
    now = time.time()
    OPENAI_CALL_LOG[:] = [t for t in OPENAI_CALL_LOG if now-t < 86400]
    if len(OPENAI_CALL_LOG) >= OPENAI_DAILY_MAX:
        raise RuntimeError("Plafond quotidien des recherches OpenAI atteint.")
    if not store.configured or not await store.reserve():
        raise RuntimeError("Budget IA indisponible ou plafond mensuel atteint.")
    OPENAI_CALL_LOG.append(now)
    evidence = await search_openai_web(query, OPENAI_KEY, OPENAI_MODEL)
    if not evidence:
        return None
    parsed, sources = evidence
    if parsed.get('verified') is not True or not sources:
        return None
    brand = str(parsed.get('brand') or '').strip().upper()[:35]
    model = str(parsed.get('model') or '').strip()[:105]
    capacity = str(parsed.get('capacity') or 'Non précisée').strip()[:32]
    specs = _clean_specs(parsed.get('specs'))
    if not brand or not model or len(specs) < 3:
        return None
    if is_model_query:
        expected = _query_model(query)
        if not expected or expected[1] != brand or not _same_model(expected[0], model, brand):
            return None
    else:
        sku = str(parsed.get('manufacturer_reference') or '').strip()
        if not sku or _normalize_ref(query.split('/')[0]) not in _normalize_ref(sku):
            return None
    requested = _parse_capacity(query)
    if not requested or _parse_capacity(capacity) != requested:
        capacity = 'Non précisée'
    else:
        capacity = requested
    return {'brand':brand,'model':model,'capacity':capacity,
            'capacity_confirmed':capacity!='Non précisée',
            'feature':_spec_feature(specs),'specs':specs,'sources':sources,
            'status':'verified_online',
            'input_type':'model_name' if is_model_query else 'manufacturer_sku',
            'search_mode':'openai_grounded','matching_results':len(sources)}

# Compact structured facts for reliable typography on 88 x 52 mm labels.
SPEC_LABELS = {'ecran':'Écran','photo':'Photo','batterie':'Batterie','processeur':'Puce','reseau':'Réseau','ram':'RAM'}
def _clean_specs(obj):
    if not isinstance(obj, dict):return {}
    out={}
    for key in SPEC_LABELS:
        if not isinstance(obj.get(key),str):continue
        v=obj[key].strip()
        if not v or len(v)>75 or re.search(r'\b(?:inconnu|unknown|vérifier|confirmer|non\s+précis|n/?a)\b',v,re.I):continue
        if key in ('ram','processeur') and re.search(r'\bou\b|selon|suivant',v,re.I):continue
        out[key]=v
    return out

def _spec_feature(specs):
    return ' · '.join(f'{SPEC_LABELS[k]} : {v}' for k,v in list(specs.items())[:4])

def _specs_from_public(query, items):
    """Only match values actually present in search snippets that explicitly name this model.
    No guesses from another generation or variant."""
    expected=_query_model(query)
    if not expected:return {}
    model,brand=expected
    found={}
    for item in items:
        text=html.unescape((item.get('title') or '')+' '+(item.get('snippet') or ''))
        if not _item_matches_model(item,model,brand):continue
        checks=[
         ('ecran',r'(?<!\d)([5-8][,.]\d\s*(?:pouces|[″“”]|inches|\")\s*(?:AMOLED|OLED|LTPO|120\s*Hz|90\s*Hz)?)'),
         ('photo',r'(?<!\d)(\d{2,3}\s*(?:MP|Mpx|mégapixels))\b'),
         ('batterie',r'(?<!\d)([3-9]\s?\d{3}\s*mAh)\b'),
         ('processeur',r'\b((?:Snapdragon|Dimensity|Exynos|Tensor|Apple A)\s*[\w\-+ ]{2,28})'),
         ('reseau',r'\b(5G(?:\s*[/·,+]\s*Wi-?Fi\s*[67])?)\b'),
         ('ram',r'\b(\d{1,2}\s*(?:Go|GB)\s*(?:RAM|de RAM|LPDDR5[X]?))\b')]
        for field,pattern in checks:
            if field in found:continue
            m=re.search(pattern,text,re.I)
            if m:
                val=m.group(1).strip().rstrip(' .:,;')
                if field=='processeur':
                    val=re.split(r'\s+(?:avec|et|pour|sur|batterie|stockage|écran|camera|caméra|RAM|5G)\b',val,flags=re.I)[0][:35].strip()
                if val:found[field]=val
    return found

def _clean_link(url):
 from urllib.parse import parse_qs, urlsplit, unquote
 if url.startswith('//'):url='https:'+url
 if url.startswith('/'):
  qs=parse_qs(urlsplit(url).query)
  if 'uddg' in qs:url=qs['uddg'][0]
 if not url.startswith('https://'):return None
 h=urlparse(url).hostname or ''
 if not h or h.endswith('duckduckgo.com'):return None
 return url

def _analyze(ref, items):
 wanted=_normalize_ref(ref)
 candidates=[]
 used=[]
 for item in items:
  title=html.unescape(item['title']); snippet=html.unescape(item['snippet'])
  combined=title+' — '+snippet
  # a shared SKU core, e.g. SM-S938B vs SM-S938B/DS, is acceptable but explicit.
  wordref=ref.split('/')[0]
  if _normalize_ref(wordref) not in _normalize_ref(combined):continue
  model,brand=_extract_model(combined)
  if not model or not brand or not _item_matches_model(item,model,brand):continue
  # Determine capacity by title first; a spec line may contain RAM then storage.
  candidates.append((model,brand,_capacities_in(combined)))
  url=_clean_link(item['url'])
  if url and len(used)<5:used.append({'title':title[:160],'url':url})
 if not candidates:return None
 name,brand=Counter((model,b) for model,b,c in candidates).most_common(1)[0][0]
 vals=set().union(*(c for model,b,c in candidates if model==name and b==brand))
 capacity=next(iter(vals)) if len(vals)==1 else 'Non précisée'
 specs=_specs_from_public(name,items)
 return {'brand':brand,'model':name,'capacity':capacity,'capacity_confirmed':capacity!='Non précisée',
         'input_type':'manufacturer_sku','feature':_spec_feature(specs),'specs':specs,'sources':used,'matching_results':len(candidates)}

CACHE = {}
# Simple per-process safeguard for accidental or automated repeated public requests.
# Render may restart the process; use an authenticated site or external quota controls
# for a production deployment with paid Gemini requests.
REQUEST_LOG = {}
GLOBAL_REQUEST_LOG = []

def _check_rate_limit(request):
    now = time.time()
    ip = request.client.host if request.client else 'unknown'
    recent = [t for t in REQUEST_LOG.get(ip, []) if now - t < 3600]
    if len(recent) >= 50:
        raise HTTPException(429, 'Trop de recherches depuis cette connexion. Réessaie plus tard.')
    recent.append(now)
    REQUEST_LOG[ip] = recent
    GLOBAL_REQUEST_LOG[:] = [t for t in GLOBAL_REQUEST_LOG if now - t < 86400]
    if len(GLOBAL_REQUEST_LOG) >= 200:
        raise HTTPException(429, 'Limite quotidienne du serveur atteinte.')
    GLOBAL_REQUEST_LOG.append(now)

async def _online_lookup(ref, is_model_query=False):
 key='V14:'+('MODEL:' if is_model_query else 'SKU:')+ref.upper()+':'+('OPENAI' if OPENAI_KEY else 'GEMINI' if GEMINI_KEY else 'PUBLIC')
 now=time.time()
 if key in CACHE:
  saved_at,saved=CACHE[key]
  ttl=21600 if saved.get('status')=='verified_online' else 60
  if now-saved_at<ttl:return saved
 # Manufacturer facts and exact variant options work without an AI key/quota.
 async with httpx.AsyncClient(timeout=httpx.Timeout(6,connect=3),follow_redirects=True,headers=HEADERS) as client:
  try:
   async def manufacturer_lookup():
    # Each provider returns immediately for other brands, keeping one shared budget.
    for provider in (lookup_official_phone,lookup_xiaomi_phone,lookup_samsung_phone,lookup_manufacturer_phone):
     result=await provider(client,ref)
     if not result:continue
     if result.get('status')=='verified_online':
      result['specs']=_clean_specs(result.get('specs'))
      if len(result['specs'])<3:continue
      result['feature']=_spec_feature(result['specs'])
     return result
    return None
   official=await asyncio.wait_for(manufacturer_lookup(),timeout=12)
  except (httpx.HTTPError,asyncio.TimeoutError):
   official=None
 if official:
  CACHE[key]=(now,official)
  return official
 openai_problem = None
 if OPENAI_KEY:
  try:
   openai_result = await asyncio.wait_for(openai_lookup(ref, is_model_query=is_model_query), timeout=22)
   if openai_result:
    CACHE[key]=(now,openai_result)
    return openai_result
  except asyncio.TimeoutError:
   openai_problem='Délai OpenAI dépassé.'
  except OpenAIServiceError as exc:
   openai_problem=str(exc)
   logging.getLogger('uvicorn.error').warning('%s', openai_problem)
  except Exception as exc:
   openai_problem='Recherche IA indisponible ou budget atteint.'
   logging.getLogger('uvicorn.error').warning('Recherche IA indisponible (%s)', type(exc).__name__)
 async with httpx.AsyncClient(timeout=TIMEOUT,follow_redirects=True) as client:
  bing,ddg,news,binghtml,official=await asyncio.gather(
   _search_bing(client,ref), _search_ddg(client,ref),
   _search_google_news(client,ref), _search_bing_html(client,ref),
   _search_official_samsung(client,ref) if is_model_query else asyncio.sleep(0,result=[]))
  items=official+bing+ddg+news+binghtml
 # Seconde passe ciblée : interrogations constructeur/fiches techniques si la
 # première recherche renvoie seulement des annonces marchandes génériques.
 preliminary=_analyze_model_query(ref,items) if is_model_query else _analyze(ref,items)
 if not preliminary or len(preliminary.get('specs') or {}) < 3:
  model_hint=_query_model(ref)[0] if is_model_query and _query_model(ref) else ref
  targeted_query=f'{model_hint} fiche technique écran batterie appareil photo processeur site:samsung.com OR site:apple.com OR site:mi.com'
  async with httpx.AsyncClient(timeout=TIMEOUT,follow_redirects=True) as detail_client:
   extra_bing, extra_ddg=await asyncio.gather(_search_bing(detail_client,targeted_query),_search_ddg(detail_client,targeted_query))
  items+=extra_bing+extra_ddg
 result=_analyze_model_query(ref,items) if is_model_query else _analyze(ref,items)
 counts={'bing':len(bing),'duckduckgo':len(ddg),'google_news':len(news),'bing_html':len(binghtml),'official':len(official),'all_results':len(items)}
 # Public search fallback if Gemini has no grounded/verified result.
 ai_problem = openai_problem
 if result and len(result.get('specs') or {}) < 3:
  result={'status':'insufficient_specs','sources':result.get('sources',[]),'search_engines':counts,
          'ai_warning':ai_problem or 'Moins de 3 caractéristiques techniques vérifiables.'}
 if result and result.get('status')!='insufficient_specs':
  if result.get('status')!='manufacturer_reference':
   result['status']='verified_online' if result['capacity'] else 'missing_capacity'
  result['search_engines']=counts
  result['search_mode']='public_search'
  if ai_problem: result['ai_warning']=ai_problem
 else:
  if not result or result.get('status')!='insufficient_specs':
   result={'status':'not_found','sources':[],'search_engines':counts}
  if ai_problem: result['ai_warning']=ai_problem
  if not OPENAI_KEY: result['ai_warning']='IA non configurée sur Render (OPENAI_API_KEY manquante).'
 CACHE[key]=(now,result)
 # Bound process memory without turning an old quota failure into a long outage.
 if len(CACHE)>500:
  for expired in list(CACHE)[:100]:CACHE.pop(expired,None)
 return result

def database_key(ref):
 query=_query_model(ref)
 capacity=_parse_capacity(ref) or 'Non précisée'
 if query:
  return query[1]+'|'+_model_identity(query[0],query[1])+'|'+capacity
 return 'SKU|'+_normalize_ref(ref)

async def online_lookup(ref, is_model_query=False, refresh=False):
 key=database_key(ref)
 if store.configured and not refresh:
  try:
   row=await store.get(key)
   if row and row.get('result',{}).get('status')=='verified_online':
    return row['result']
  except RuntimeError:
   pass  # Official facts remain available; paid calls fail closed independently.
 result=await _online_lookup(ref,is_model_query)
 if store.configured and result.get('status')=='verified_online' and len(result.get('specs',{}))>=3 and result.get('sources'):
  now=datetime.now(timezone.utc)
  from datetime import timedelta
  try:
   await store.save({'model_key':key,'brand':result['brand'],'model':result['model'],
    'capacity':_parse_capacity(ref) or 'Non précisée','specs':result['specs'],
    'sources':result['sources'],'search_mode':result.get('search_mode',''),
    'verification_status':'vérifié','verified_at':now.isoformat(),
    'updated_at':now.isoformat(),'next_check_at':(now+timedelta(days=7)).isoformat() if result.get('search_mode')=='openai_grounded' else None,'result':result})
  except RuntimeError:
   result={**result,'database_warning':'Fiche vérifiée mais non enregistrée : base indisponible.'}
 if store.configured and result.get('status') in ('not_found','insufficient_specs') and _query_model(ref):
  model,brand=_query_model(ref)
  from datetime import timedelta
  now=datetime.now(timezone.utc)
  try:
   # Never downgrade a verified record after a transient refresh failure.
   if await store.get(key):
    return result
   await store.save({'model_key':key,'brand':brand,'model':model,
    'capacity':_parse_capacity(ref) or 'Non précisée','specs':{},'sources':[],
    'verification_status':'incomplet','next_check_at':(now+timedelta(days=7)).isoformat(),
    'updated_at':now.isoformat(),'result':{}})
  except RuntimeError:
   pass
 return result

@app.get('/api/admin/usage')
async def usage():
 return {'monthly_ceiling_eur':4,'reservation_per_call_eur':0.10,
         'usage':await store.request('GET','ai_usage',params={'order':'month.desc','limit':'12'})}

@app.get('/')
async def home():return FileResponse(BASE/'index.html')

@app.get('/health')
async def health():return {'ok':True,'service':'encre-phones','version':app.version}

@app.get('/api/status')
async def private_status():
 return {'ai_configured':bool(OPENAI_KEY),'openai_configured':bool(OPENAI_KEY),
         'openai_model':OPENAI_MODEL if OPENAI_KEY else None,'database_configured':store.configured}

@lru_cache(maxsize=1)
def _known_phone_models():
 """Suggestions use the shipped manufacturer facts and never consume an AI call."""
 models={}
 for filename in ('phone_catalog.json','apple_catalog.json','xiaomi_catalog.json','samsung_catalog.json'):
  path=BASE/filename
  if not path.is_file():continue
  data=json.loads(path.read_text(encoding='utf-8'))
  for phone in data.get('phones',[]):
   brand=phone.get('brand',''); model=phone.get('model','')
   if not brand or not model:continue
   key=(brand,_model_identity(model,brand))
   models[key]={'brand':brand,'model':model,'available_capacities':list(phone.get('capacities',[])),
                '_aliases':phone.get('aliases',[])}
 return list(models.values())

@app.get('/api/phones/models')
async def phone_models(query:str=Query(default='',max_length=60),limit:int=Query(default=8,ge=1,le=20)):
 needle=_normalize_ref(CAP_RE.sub('',_normalize_model_text(query)))
 def names(row):
  return [_normalize_ref(row['brand']+' '+row['model']),_normalize_ref(row['model']),
          *(_normalize_ref(alias) for alias in row['_aliases'])]
 matches=[row for row in _known_phone_models() if not needle or any(needle in name for name in names(row))]
 def sort_key(row):
  generation=re.search(r'\d+',row['model'])
  recent=int(generation.group()) if generation and 'SE' not in row['model'].upper() else 0
  return (0 if any(name.startswith(needle) for name in names(row)) else 1,-recent,row['model'].upper())
 matches.sort(key=sort_key)
 return {'models':[{key:value for key,value in row.items() if not key.startswith('_')}
                   for row in matches[:limit]],'total':len(matches),'limit':limit}

@app.get('/api/phones/lookup')
async def lookup(request:Request, reference:str=Query(min_length=4,max_length=60)):
 ref=_normalize_model_text(reference).upper()
 _check_rate_limit(request)
 if not MODEL_QUERY_OK.fullmatch(ref): raise HTTPException(400,'Recherche invalide : entre un modèle ou une référence constructeur.')
 if _normalize_ref(CAP_RE.sub('',ref)) in ('IPHONESE','APPLEIPHONESE'):
  raise HTTPException(400,{'message':'Précise la génération : iPhone SE 2016, iPhone SE 2020 ou iPhone SE 2022.'})
 is_model_query=bool(_query_model(ref))
 if not is_model_query and re.match(r'^(?:APPLE\s*)?IPHONE',_normalize_model_text(ref),re.I):
  raise HTTPException(400,{'message':'Nom d’iPhone non reconnu. Choisis le modèle exact dans les suggestions, par exemple iPhone 17 Pro Max ou iPhone SE 2022.'})
 if not is_model_query and not REF_OK.fullmatch(CAP_RE.sub('',ref).strip()): raise HTTPException(400,'Nom de modèle non reconnu. Exemple : Samsung Galaxy A56 256 Go, iPhone 16 ou SM-S938B.')
 try:
  result=await asyncio.wait_for(online_lookup(ref,is_model_query=is_model_query),timeout=52)
 except asyncio.TimeoutError:
  raise HTTPException(504,{'message':'La recherche a pris trop de temps. Réessaie avec le nom exact du modèle et son stockage.'})
 if result['status']=='invalid_capacity':
  options=', '.join(result.get('capacities',[]))
  raise HTTPException(422,{'message':f"Stockage non proposé pour ce modèle. Capacités constructeur : {options}.",'capacities':result.get('capacities',[]),'sources':result.get('sources',[])})
 if result['status']=='insufficient_specs':
  raise HTTPException(422,{'message':'Téléphone identifié, mais moins de 3 caractéristiques ont pu être vérifiées. Précise le nom complet ou la référence constructeur.','ai_warning':result.get('ai_warning'),'sources':result.get('sources',[])})
 if result['status']=='not_found':
  raise HTTPException(404,{'message':'Modèle ou référence non identifié dans les résultats de recherche publics. Aucun produit inventé.','search_engines':result['search_engines'],'ai_warning':result.get('ai_warning')})
 if not result.get('capacity'):
  raise HTTPException(422,{'message':'Modèle trouvé, mais capacité de stockage non confirmée pour cette référence constructeur.','model':result['model'],'brand':result['brand'],'sources':result['sources']})
 return {'reference':ref,**result}


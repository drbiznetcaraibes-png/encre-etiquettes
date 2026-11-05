"""Encre Pour Tous – minimal web lookup server.
Optional Gemini API with grounded Google Search on the server; public search fallback.
The API key must only be stored as a Render environment variable.
"""
from __future__ import annotations
import asyncio
from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
import html
import json
import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

BASE = Path(__file__).resolve().parent
app = FastAPI(title='ENCRE POUR TOUS – recherche téléphones', version='1.4.0')
HEADERS = {'User-Agent':'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36', 'Accept-Language':'fr-FR,fr;q=0.9,en;q=0.8'}
TIMEOUT = httpx.Timeout(9, connect=4)

BRAND_PATTERNS = [(r'\b(samsung|galaxy)\b','SAMSUNG'),(r'\b(apple|iphone)\b','APPLE'),(r'\b(xiaomi|redmi|poco)\b','XIAOMI'),(r'\b(oppo)\b','OPPO'),(r'\b(realme)\b','REALME'),(r'\b(honor)\b','HONOR'),(r'\b(huawei)\b','HUAWEI'),(r'\b(motorola|moto\s)\b','MOTOROLA'),(r'\b(google|pixel)\b','GOOGLE'),(r'\b(nothing phone|nothing)\b','NOTHING'),(r'\b(oneplus)\b','ONEPLUS'),(r'\b(nokia|hmd)\b','NOKIA'),(r'\b(vivo)\b','VIVO'),(r'\b(tecno)\b','TECNO'),(r'\b(infinix)\b','INFINIX')]
MODEL_PATTERNS = [
 (r'\bGalaxy\s+(?:S\d{1,2}(?:\s*(?:Ultra|Plus|FE|\+))?|Z\s*(?:Fold|Flip)\s*\d+|A\d{1,2}(?:\s*5G)?|M\d{1,2}|Note\s*\d{1,2}(?:\s*Ultra)?)', 'SAMSUNG'),
 (r'\biPhone\s+(?:1[1-9]|[5-9]|SE)(?:\s*(?:Pro\s*Max|Pro|Plus|mini|e))?', 'APPLE'),
 (r'\b(?:Redmi\s*Note\s*\d{1,2}(?:\s*Pro\+?|\s*5G)?|Redmi\s*\d{1,2}(?:\s*[AC])?|POCO\s*[XFM]\d{1,2}(?:\s*Pro)?|Xiaomi\s*\d{1,2}(?:\s*(?:Ultra|Pro|T\s*Pro|T|Lite))?)', 'XIAOMI'),
 (r'\b(?:Pixel\s*\d{1,2}(?:\s*(?:Pro\s*XL|Pro|a|XL|Fold))?)', 'GOOGLE'),
 (r'\b(?:HONOR\s*(?:Magic\s*\d{1,2}(?:\s*(?:Pro|Lite))?|\d{1,3}(?:\s*(?:Pro|Lite))?|X\d{1,2}))', 'HONOR'),
 (r'\b(?:OPPO\s*(?:Reno\s*\d{1,2}(?:\s*(?:Pro|F))?|A\d{2,3}|Find\s*[NX]\d{1,2}(?:\s*Pro)?))', 'OPPO'),
 (r'\b(?:Motorola\s*(?:Edge\s*\d{1,2}(?:\s*(?:Ultra|Pro|Fusion))?|Moto\s*G\d{1,2}))', 'MOTOROLA'),
 (r'\b(?:realme\s*(?:\d{1,2}(?:\s*Pro\+?)?|GT\s*\d{1,2}(?:\s*Pro)?|C\d{2,3}))', 'REALME'),
 (r'\b(?:OnePlus\s*\d{1,2}(?:\s*[RT])?|OnePlus\s*Nord\s*\d{1,2})', 'ONEPLUS'),
]
CAP_RE = re.compile(r'(?<![\d])(?:64|128|256|512|1024)\s*(?:Go|GB|G[Bb]|To|TB)(?![a-z])',re.I)
REF_OK = re.compile(r'^[A-Z0-9][A-Z0-9_./#\-+]{3,59}$')
MODEL_QUERY_OK = re.compile(r"^[\w\s./#+\-]{4,60}$", re.UNICODE)

# Well-known references from prior approved examples. These act as a fallback only;
# searches are attempted first. Capacity is verified only if SKU mapping provides it.
CATALOG = {
 'SM-S938B':{'brand':'SAMSUNG','model':'Galaxy S25 Ultra','capacity':None,'feature':'Galaxy AI · 200 Mpx · 5G'},
 'SM-S938B/DS':{'brand':'SAMSUNG','model':'Galaxy S25 Ultra','capacity':None,'feature':'Galaxy AI · 200 Mpx · 5G'},
 'SM-F946B':{'brand':'SAMSUNG','model':'Galaxy Z Fold5','capacity':None,'feature':'Écran pliable · AMOLED · 5G'},
 'SM-A115F':{'brand':'SAMSUNG','model':'Galaxy A11','capacity':None,'feature':'Écran 6,4″ · Triple capteur · 4G'},
}

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

def _normalize_ref(s): return re.sub(r'[^A-Z0-9]','',s.upper())

def _detect_brand(text):
 for pat,brand in BRAND_PATTERNS:
  if re.search(pat,text,re.I):return brand
 return None

def _parse_capacity(text):
 # Product capacity needs explicit storage context or recognizably branded listing, NOT RAM numbers.
 m=CAP_RE.search(text)
 if not m:return None
 unit=m.group(0).upper().replace(' ','')
 if unit.endswith(('TO','TB')):return '1 To'
 num=re.match(r'\d+',unit).group()
 return num+' Go'

def _extract_model(text):
 for pattern,brand in MODEL_PATTERNS:
  m=re.search(pattern,text,re.I)
  if m:
   out=re.sub(r'\s+',' ',m.group(0)).strip()
   if brand=='SAMSUNG' and not out.lower().startswith('galaxy'):out='Galaxy '+out
   return out,brand
 return None,None

def _query_model(query):
 # Allow familiar expressions such as "Samsung S26 Ultra" without pretending this is a manufacturer SKU.
 cleaned = re.sub(r"\s+", " ", query).strip()
 model, brand = _extract_model(cleaned)
 if not model:
  b = _detect_brand(cleaned)
  if b == 'SAMSUNG':
   m = re.search(r"\b(?:S\s?\d{2}|A\s?\d{2}|Z\s?(?:Fold|Flip)\s?\d+)\s*(?:Ultra|Plus|FE|5G|\+)?\b",cleaned,re.I)
   if m: model='Galaxy '+m.group(0);brand='SAMSUNG'
 if not model: return None
 return model,brand

def _analyze_model_query(ref, items):
 expected=_query_model(ref)
 if not expected:return None
 query_model,query_brand=expected
 expected_token=_normalize_ref(query_model)
 candidates=[]; links=[]
 for item in items:
  title=html.unescape(item.get('title','')); snippet=html.unescape(item.get('snippet',''))
  combined=title+' — '+snippet
  model,brand=_extract_model(combined)
  if not model or brand!=query_brand or _normalize_ref(model)!=expected_token:continue
  candidates.append((model,brand))
  url=_clean_link(item.get('url',''))
  if url and len(links)<5 and url not in [u['url'] for u in links]: links.append({'title':title[:160],'url':url})
 if not candidates:return None
 # Results may show many variants; model-name queries cannot identify a specific storage capacity.
 supplied_cap=_parse_capacity(ref)
 return {'brand':query_brand,'model':query_model,'capacity':supplied_cap or 'Non précisée',
         'feature':'Caractéristiques détaillées à vérifier sur la fiche fabricant',
         'sources':links,'matching_results':len(candidates),
         'capacity_confirmed':bool(supplied_cap),'input_type':'model_name',
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

# No artificial manufacturer fallback: no live evidence means no automatic label.
def _official_fallback(ref):
 return None

GEMINI_KEY = os.environ.get('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.environ.get('GEMINI_MODEL', 'gemini-3.8-flash').strip()


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


async def gemini_lookup(query, is_model_query=False):
    if not GEMINI_KEY:
        return None
    prompt = f'''Tu es un vérificateur de fiches smartphones pour une étiquette de magasin français.
Recherche avec Google Search le téléphone EXACT demandé : {query!r}.
Règles impératives :
- La marque et le modèle doivent correspondre strictement à la demande (ex. S26 n'est pas S25, Ultra n'est pas Plus).
- Ne jamais déduire ou inventer une capacité de stockage : si la demande ne précise pas 128 Go / 256 Go / 512 Go / 1 To, indique "Non précisée", même si le modèle existe en plusieurs variantes.
- Si l'entrée est un SKU fabricant, n'indique la capacité que si ce SKU précis est associé à une capacité dans une source consultée.
- Les caractéristiques techniques doivent être confirmées par les pages consultées, sinon écrire "Caractéristiques à confirmer".
- Si impossible de vérifier le modèle précis sur une page web pertinente, verified=false.
Réponds UNIQUEMENT avec un objet JSON (sans markdown) :
{{"verified":true ou false,"brand":"SAMSUNG","model":"Galaxy S26 Ultra","capacity":"Non précisée","feature":"Écran ... · ... · 5G","manufacturer_reference":"identifiant constructeur ou chaîne vide"}}.
Dans feature, donne au plus 3 caractéristiques distinctes, courtes, et confirmées. Aucune information de prix.'''
    body = {
       'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
       'tools': [{'google_search': {}}],
       'generationConfig': {'temperature': 0.1, 'maxOutputTokens': 1200},
    }
    endpoint = f'https://generativelanguage.googleapis.com/v1beta/models/{quote(GEMINI_MODEL, safe="")}:generateContent'
    async with httpx.AsyncClient(timeout=httpx.Timeout(42, connect=7)) as client:
        res = await client.post(endpoint, headers={'x-goog-api-key': GEMINI_KEY, 'Content-Type': 'application/json'}, json=body)
        if res.status_code != 200:
            # No API token, keys or response bodies in public errors/logs.
            raise RuntimeError(f'API Gemini HTTP {res.status_code}; vérifie la clé, le modèle et les quotas.')
        payload = res.json()
    sources = _grounding_sources(payload)
    if not sources:
        return None  # No web grounding: do not present an unsupported model as verified.
    candidates = payload.get('candidates') or []
    texts = []
    for candidate in candidates:
        for part in ((candidate.get('content') or {}).get('parts') or []):
            if isinstance(part.get('text'), str): texts.append(part['text'])
    parsed = _gemini_json('\n'.join(texts))
    if not parsed or parsed.get('verified') is not True:
        return None
    brand = str(parsed.get('brand') or '').strip().upper()[:35]
    model = str(parsed.get('model') or '').strip()[:100]
    capacity = str(parsed.get('capacity') or 'Non précisée').strip()[:32]
    feature = str(parsed.get('feature') or 'Caractéristiques à confirmer').strip()[:175]
    if not brand or not model:
        return None
    if is_model_query:
        expected = _query_model(query)
        if not expected or expected[1] != brand or _normalize_ref(expected[0]) != _normalize_ref(model):
            return None
        # A brand+model input alone does not determine the phone's storage.
        if not _parse_capacity(query): capacity = 'Non précisée'
    else:
        sku = str(parsed.get('manufacturer_reference') or '').strip()
        if not sku or _normalize_ref(query.split('/')[0]) not in _normalize_ref(sku):
            return None
    # The model must be visible in at least the model's grounded answer; do not use inference from brand alone.
    return {'brand':brand,'model':model,'capacity':capacity,'feature':feature,
            'capacity_confirmed':capacity!='Non précisée',
            'sources':sources,'status':'verified_online','input_type':'model_name' if is_model_query else 'manufacturer_sku',
            'search_mode':'gemini_grounded','matching_results':len(sources)}

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
  if not model or not brand:continue
  # Determine capacity by title first; a spec line may contain RAM then storage.
  caps=re.findall(r'(?:64|128|256|512|1024)\s*(?:Go|GB|To|TB)\b',combined,re.I)
  cap=None
  if caps:
   cap=_parse_capacity(' '.join(caps))
  candidates.append((model,brand,cap))
  url=_clean_link(item['url'])
  if url and len(used)<5:used.append({'title':title[:160],'url':url})
 if not candidates:return None
 name,brand=Counter((model,b) for model,b,c in candidates).most_common(1)[0][0]
 vals=[c for model,b,c in candidates if model==name and b==brand and c]
 capacity=Counter(vals).most_common(1)[0][0] if vals else None
 return {'brand':brand,'model':name,'capacity':capacity,'feature':'Smartphone · caractéristiques à vérifier auprès du fabricant','sources':used,'matching_results':len(candidates)}

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

async def online_lookup(ref, is_model_query=False):
 key=('MODEL:' if is_model_query else 'SKU:')+ref.upper()+':'+('GEMINI' if GEMINI_KEY else 'PUBLIC')
 now=time.time()
 if key in CACHE and now-CACHE[key][0]<21600: return CACHE[key][1]
 gemini_problem = None
 if GEMINI_KEY:
  try:
   ai_result = await gemini_lookup(ref, is_model_query=is_model_query)
   if ai_result:
    CACHE[key]=(now, ai_result)
    return ai_result
  except Exception as exc:
   gemini_problem = str(exc)
 async with httpx.AsyncClient(timeout=TIMEOUT,follow_redirects=True) as client:
  bing,ddg,news,binghtml,official=await asyncio.gather(
   _search_bing(client,ref), _search_ddg(client,ref),
   _search_google_news(client,ref), _search_bing_html(client,ref),
   _search_official_samsung(client,ref) if is_model_query else asyncio.sleep(0,result=[]))
 items=official+bing+ddg+news+binghtml
 result=_analyze_model_query(ref,items) if is_model_query else _analyze(ref,items)
 counts={'bing':len(bing),'duckduckgo':len(ddg),'google_news':len(news),'bing_html':len(binghtml),'official':len(official)}
 # Public search fallback if Gemini has no grounded/verified result.
 if result:
  if result.get('status')!='manufacturer_reference':
   result['status']='verified_online' if result['capacity'] else 'missing_capacity'
  result['search_engines']=counts
  result['search_mode']='public_search'
  if gemini_problem: result['ai_warning']=gemini_problem
 else:
  result={'status':'not_found','sources':[],'search_engines':counts}
  if gemini_problem: result['ai_warning']=gemini_problem
  if not GEMINI_KEY: result['ai_warning']='IA non configurée sur Render (GEMINI_API_KEY manquante).' 
 CACHE[key]=(now,result)
 return result

@app.get('/')
async def home():return FileResponse(BASE/'index.html')

@app.get('/health')
async def health():return {'ok':True,'service':'encre-phones','ai_configured':bool(GEMINI_KEY),'gemini_model':GEMINI_MODEL if GEMINI_KEY else None,'timestamp':datetime.now(timezone.utc).isoformat()}

@app.get('/api/phones/lookup')
async def lookup(request:Request, reference:str=Query(min_length=4,max_length=60)):
 ref=reference.strip().upper()
 _check_rate_limit(request)
 if not MODEL_QUERY_OK.fullmatch(ref): raise HTTPException(400,'Recherche invalide : entre un modèle ou une référence constructeur.')
 is_model_query=not bool(REF_OK.fullmatch(ref))
 if is_model_query and not _query_model(ref): raise HTTPException(400,'Nom de modèle non reconnu. Exemple : Samsung Galaxy S26 Ultra ou SM-S938B.')
 result=await online_lookup(ref,is_model_query=is_model_query)
 if result['status']=='not_found':
  raise HTTPException(404,{'message':'Modèle ou référence non identifié dans les résultats de recherche publics. Aucun produit inventé.','search_engines':result['search_engines'],'ai_warning':result.get('ai_warning')})
 if not result.get('capacity'):
  raise HTTPException(422,{'message':'Modèle trouvé, mais capacité de stockage non confirmée pour cette référence constructeur.','model':result['model'],'brand':result['brand'],'sources':result['sources']})
 return {'reference':ref,**result}


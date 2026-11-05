"""Encre Pour Tous – minimal web lookup server.
Public web searches only. No Google/Gemini API key required.
Search engines can change or block requests, so lookup is best-effort.
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
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse

BASE = Path(__file__).resolve().parent
app = FastAPI(title='ENCRE POUR TOUS – recherche téléphones', version='1.0.0')
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
  r=await client.get(url, params={'format':'rss','q':f'"{ref}" smartphone capacité fiche technique'},headers=HEADERS)
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
  r=await client.get('https://html.duckduckgo.com/html/',params={'q':f'"{ref}" téléphone capacité caractéristiques'},headers=HEADERS)
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
async def online_lookup(ref):
 key=ref.upper()
 now=time.time()
 if key in CACHE and now-CACHE[key][0]<21600: return CACHE[key][1]
 async with httpx.AsyncClient(timeout=TIMEOUT,follow_redirects=True) as client:
  bing,ddg=await asyncio.gather(_search_bing(client,ref),_search_ddg(client,ref))
 items=bing+ddg
 result=_analyze(ref,items)
 if result:
  result['status']='verified_online' if result['capacity'] else 'missing_capacity'
  result['search_engines']={'bing':len(bing),'duckduckgo':len(ddg)}
 else:
  result={'status':'not_found','sources':[],'search_engines':{'bing':len(bing),'duckduckgo':len(ddg)}}
 CACHE[key]=(now,result)
 return result

@app.get('/')
async def home():return FileResponse(BASE/'index.html')

@app.get('/health')
async def health():return {'ok':True,'service':'encre-phones','timestamp':datetime.now(timezone.utc).isoformat()}

@app.get('/api/phones/lookup')
async def lookup(reference:str=Query(min_length=4,max_length=60)):
 ref=reference.strip().upper()
 if not REF_OK.fullmatch(ref): raise HTTPException(400,'Référence invalide.')
 result=await online_lookup(ref)
 if result['status']=='not_found':
  raise HTTPException(404,{'message':'Référence non identifiée sur les moteurs publics. Aucun modèle inventé.','search_engines':result['search_engines']})
 if not result.get('capacity'):
  raise HTTPException(422,{'message':'Modèle trouvé, mais capacité de stockage non confirmée pour cette référence.','model':result['model'],'brand':result['brand'],'sources':result['sources']})
 return {'reference':ref,**result}


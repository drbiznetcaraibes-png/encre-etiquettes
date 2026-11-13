"""Exact Xiaomi/Redmi/POCO facts from official specification pages.

Recent models can be looked up by their official product slug without waiting for
a catalogue update. The checked local snapshots keep common models available
when the manufacturer website is temporarily unavailable.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import time
import unicodedata

import httpx
from bs4 import BeautifulSoup

_CATALOG_PATH = Path(__file__).with_name('xiaomi_catalog.json')
CATALOG = json.loads(_CATALOG_PATH.read_text(encoding='utf-8')) if _CATALOG_PATH.exists() else {'phones': []}
_PAGE_CACHE = {}
_STORAGE = re.compile(r'(?<![\w.])(\d+)\s*(GO|GB|TO|TB)\b', re.I)
_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie', 'processeur': 'Puce', 'reseau': 'Réseau', 'ram': 'RAM'}
_IMPLICIT_5G = {'POCOX6', 'POCOX6PRO', 'POCOX7', 'POCOX7PRO', 'POCOF6', 'POCOF6PRO', 'POCOF7', 'POCOF7PRO', 'POCOF7ULTRA'}


def _plain(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or '')).replace('\u00ad', '')).strip()


def _identity(value):
    value = _plain(value).upper().replace('PLUS', '+')
    value = re.sub(r'^XIAOMI\s*(?=REDMI|POCO)', '', value)
    return re.sub(r'[^A-Z0-9+]', '', value)


def _capacity(number, unit):
    size = int(number) * (1024 if unit.upper() in ('TB', 'TO') else 1)
    return f'{size // 1024} To' if size >= 1024 and size % 1024 == 0 else f'{size} Go'


def _parse_query(query):
    text = _plain(query)
    requested = []
    combo = re.compile(r'\b(?:4|6|8|12|16|24)\s*(?:GO|GB)?\s*[+/]\s*(\d{2,4})\s*(GO|GB)\b', re.I)
    for match in combo.finditer(text):
        requested.append(_capacity(match.group(1), match.group(2)))
    text = combo.sub(' ', text)
    for match in _STORAGE.finditer(text):
        if not re.match(r'\s*(?:DE\s+)?RAM\b', text[match.end():], re.I):
            requested.append(_capacity(match.group(1), match.group(2)))
    if len(set(requested)) > 1:
        return None, None
    text = _STORAGE.sub('', text)
    text = re.sub(r'\b(?:DE\s+)?RAM\b', '', text, flags=re.I)
    text = re.sub(r'\b(?:NOIR|BLACK|BLANC|WHITE|BLEU|BLUE|VERT|GREEN|GRIS|GRAY|GREY|SILVER)\s*$', '', text, flags=re.I)
    return _identity(text), requested[0] if requested else None


def _slug(identity):
    match = re.fullmatch(r'REDMIA(\d{1,2})(PRO|PLUS)?(4G|5G)?', identity)
    if match:
        generation, version, network = match.groups()
        return '-'.join(x.lower() for x in ('redmi', 'a' + generation, version, network) if x)
    match = re.fullmatch(r'XIAOMI(\d{1,2})(T?)(PROMAX|PRO|ULTRA|LITE)?(4G|5G)?', identity)
    if match:
        generation, t, version, network = match.groups()
        version = 'PRO-MAX' if version == 'PROMAX' else version
        return '-'.join(x.lower() for x in ('xiaomi', generation + t, version, network) if x)
    match = re.fullmatch(r'REDMI(NOTE)?(\d{1,2})(PRO\+|PRO|S|C|A|T)?(4G|5G)?', identity)
    if match:
        note, generation, version, network = match.groups()
        version = (version or '').replace('+', '-plus')
        return '-'.join(x.lower() for x in ('redmi', note, generation + (version if version in ('S', 'C', 'A', 'T') else ''), version if version not in ('S', 'C', 'A', 'T') else '', network) if x)
    match = re.fullmatch(r'POCO([XFMC])(\d{1,2}S?)(PROMAX|PRO|ULTRA|GT)?(4G|5G)?', identity)
    if match:
        family, generation, version, network = match.groups()
        version = 'pro-max' if version == 'PROMAX' else version
        return '-'.join(x.lower() for x in ('poco', family + generation, version, network) if x)
    return None


def _section(soup, labels):
    """Read the value column beside a specification label, not the whole page."""
    for node in soup.find_all(['span', 'h2', 'h3', 'h4', 'div']):
        if _plain(node.get_text(' ', strip=True)).casefold().rstrip(':') not in labels:
            continue
        current = node
        for _ in range(3):
            for sibling in current.next_siblings:
                if not hasattr(sibling, 'get_text') or getattr(sibling, 'name', '') in ('script', 'style'):
                    continue
                value = _plain(sibling.get_text(' ', strip=True))
                if value:
                    return value
            current = current.parent
            if current is None:
                break
    return ''


def parse_xiaomi_sheet(document, expected_identity, source_url):
    soup = BeautifulSoup(document, 'html.parser')
    heading = soup.find('h1')
    if not heading:
        return None
    for hidden in heading.select('[style*="display:none"], [style*="display: none"]'):
        hidden.decompose()
    model = _plain(heading.get_text(' ', strip=True))
    model = re.split(r'\s+(?:Caractéristiques|Specifications?|Specs)\b', model, flags=re.I)[0]
    actual = _identity(model)
    # Network suffixes on Redmi are real distinct products, not cosmetic aliases.
    exact = actual == expected_identity
    implicit_5g = expected_identity in _IMPLICIT_5G and actual == expected_identity + '5G'
    explicit_4g = expected_identity.endswith('4G') and actual == expected_identity[:-2]
    explicit_5g = expected_identity.endswith('5G') and actual == expected_identity[:-2]
    if not (exact or implicit_5g or explicit_4g or explicit_5g):
        return None
    sections = {
        'processor': _section(soup, {'processeur', 'processor', 'plateforme mobile', 'mobile platform', 'chipset'}),
        'storage': _section(soup, {'stockage et ram', 'stockage et mémoire ram', 'storage & ram', 'storage and ram', 'mémoire et stockage', 'ram et stockage', 'ram & stockage', 'mémoire', 'memory'}),
        'screen': _section(soup, {'écran', 'display', 'screen', 'affichage'}),
        'camera': _section(soup, {'caméra arrière', 'appareil photo arrière', 'rear camera', 'rear cameras', 'camera', 'caméra'}),
        'battery': _section(soup, {'batterie et charge', 'batterie et chargement', 'batterie & charge', 'batterie', 'battery & charging', 'battery and charging', 'battery & charger', 'battery'}),
        'network': _section(soup, {'réseau et connectivité', 'réseaux et connectivité', 'network & connectivity', 'network and connectivity', 'connectivité', 'connectivity'}),
    }
    specs = {}
    processor_text = re.sub(r'génération', 'Gen', sections['processor'], flags=re.I)
    processor = re.search(r'\b((?:(?:MediaTek|Qualcomm)[®™]?\s+)?(?:Dimensity|Helio|Snapdragon)[®™]?\s*[A-Za-z0-9]+(?:[ -]+(?:Gen\s*\d+[s+]?|Elite|Ultra|for Galaxy|[A-Z]\d{2,3}))+\+?|(?:(?:MediaTek|Qualcomm)[®™]?\s+)?(?:Dimensity|Helio|Snapdragon)[®™]?\s*[A-Za-z0-9]+\+?)', processor_text, re.I)
    if processor:
        specs['processeur'] = processor.group(1).replace('®', '').replace('™', '').strip()[:60]
    else:
        unisoc = re.search(r'\bUNISOC\s+[A-Z]\d{3,5}\b', processor_text, re.I)
        if unisoc:
            specs['processeur'] = unisoc.group(0)
    screen = sections['screen']
    diagonal = re.search(r'(?<!\d)([5-8][,.]\d{1,2})\s*(?:pouces|inches|inch|[\"″”]|\'\'|′′)', screen, re.I)
    panel = 'AMOLED' if re.search(r'AMOLED', screen, re.I) else 'OLED' if re.search(r'OLED', screen, re.I) else 'LCD' if re.search(r'LCD', screen, re.I) else ''
    refresh = re.search(r'(?<!\d)(60|90|120|144|165)\s*Hz\b', screen, re.I)
    if diagonal:
        specs['ecran'] = diagonal.group(1).replace('.', ',') + '″' + (' ' + panel if panel else '') + (' ' + refresh.group(1) + ' Hz' if refresh else '')
    elif panel and refresh:
        specs['ecran'] = panel + ' ' + refresh.group(1) + ' Hz'
    photo = re.search(r'(?<!\d)(\d{1,3})\s*(?:MP|Mpx|mégapixels)\b', sections['camera'], re.I)
    if photo:
        specs['photo'] = photo.group(1) + ' Mpx (principal)'
    battery = re.search(r'(?<!\d)([3-9]\s*\d{3})\s*mAh\b', sections['battery'], re.I)
    if battery:
        number = re.sub(r'\s', '', battery.group(1))
        specs['batterie'] = number[0] + ' ' + number[1:] + ' mAh'
    network = sections['network']
    if re.search(r'\b5G\b', network):
        specs['reseau'] = '5G'
    elif re.search(r'\b4G\b|\bLTE\b', network):
        specs['reseau'] = '4G'
    if explicit_4g and specs.get('reseau') != '4G':
        return None
    if explicit_5g and specs.get('reseau') != '5G':
        return None
    capacities = []
    # Only factory storage choices from the storage section. Ignore expandable
    # microSD limits and RAM values, which are not internal storage variants.
    memory = re.split(r'(?:\*|stockage extensible|expandable|extension|microSD)', sections['storage'], maxsplit=1, flags=re.I)[0]
    memory = re.sub(r'\+\s*\d+\s*(?:Go|GB)\s+d[’\']espace supplémentaire', '', memory, flags=re.I)
    # Compact manufacturer options share the unit: 6+128/8+256GB.
    for number in re.findall(r'(?:^|[/|,\s])(?:4|6|8|12|16|24)\s*\+\s*(\d{2,4})(?=\s*[/|]|\s*(?:GB|GO)\b)', memory, re.I):
        value=_capacity(number,'GB')
        if value not in capacities:
            capacities.append(value)
    for match in _STORAGE.finditer(memory):
        size = int(match.group(1))
        if (match.group(2).upper() in ('GO', 'GB') and size < 32):
            continue
        value = _capacity(match.group(1), match.group(2))
        if value not in capacities:
            capacities.append(value)
    if len(specs) < 3 or not capacities:
        return None
    return {'brand': 'XIAOMI', 'model': model, 'aliases': [model], 'manufacturer_references': [],
            'capacities': capacities, 'specs': specs,
            'sources': [{'title': 'Xiaomi — ' + model + ' : caractéristiques', 'url': source_url}]}


def _result(item, requested, mode):
    capacities = list(item['capacities'])
    if requested and requested not in capacities:
        return {'status': 'invalid_capacity', 'brand': 'XIAOMI', 'model': item['model'],
                'capacities': capacities, 'available_capacities': capacities, 'requested_capacity': requested,
                'sources': deepcopy(item['sources'])}
    specs = dict(item['specs'])
    return {'brand': 'XIAOMI', 'model': item['model'], 'capacity': requested or 'Non précisée',
            'capacity_confirmed': bool(requested), 'available_capacities': capacities,
            'specs': specs, 'feature': ' · '.join(f'{_LABELS[k]} : {v}' for k, v in list(specs.items())[:4]),
            'sources': deepcopy(item['sources']), 'status': 'verified_online', 'input_type': 'model_name',
            'search_mode': mode, 'matching_results': len(item['sources'])}


async def _fetch_sheet(client, url, identity):
    try:
        from urllib.parse import urljoin, urlparse
        for _ in range(4):
            parsed = urlparse(url)
            if (parsed.scheme != 'https' or parsed.hostname not in ('www.mi.com', 'mi.com', 'www.po.co', 'po.co')
                    or parsed.username or parsed.password or parsed.port not in (None, 443)):
                return None
            response = await client.get(url, timeout=httpx.Timeout(4, connect=2), follow_redirects=False)
            if response.is_redirect:
                location = response.headers.get('location')
                if not location:
                    return None
                url = urljoin(url, location)
                continue
            response.raise_for_status()
            if response.url.scheme != 'https' or response.url.host not in ('www.mi.com', 'mi.com', 'www.po.co', 'po.co') or len(response.content) > 2000000:
                return None
            return parse_xiaomi_sheet(response.text, identity, str(response.url))
        return None
    except (httpx.HTTPError, ValueError):
        return None


async def lookup_xiaomi_phone(client, query):
    identity, requested = _parse_query(query)
    if not identity:
        return None
    for item in CATALOG['phones']:
        if identity in {_identity(a) for a in item.get('aliases', []) + [item['model']]}:
            return _result(item, requested, 'official_manufacturer_catalog')
    cached = _PAGE_CACHE.get(identity)
    if cached and time.monotonic() - cached[0] < 21600:
        return _result(cached[1], requested, 'official_manufacturer_page')
    slug = _slug(identity)
    if not slug:
        return None
    slugs = [slug]
    if identity.endswith('4G'):
        slugs.append(slug.removesuffix('-4g'))
    if re.fullmatch(r'REDMI\d{1,2}C', identity):
        slugs.append(re.sub(r'(\d)c$', r'\1-c', slug))
    if slug.endswith('-5g'):
        slugs.append(slug.removesuffix('-5g'))
    locales = ('fr', 'global', 'in') if identity.startswith('REDMIA') and identity.endswith('5G') else ('fr', 'global')
    urls = [f'https://www.mi.com/{locale}/product/{candidate}/specs/' for locale in locales for candidate in slugs]
    try:
        results = await asyncio.wait_for(asyncio.gather(*(_fetch_sheet(client, url, identity) for url in urls)), timeout=6.5)
    except asyncio.TimeoutError:
        return None
    item = next((result for result in results if result), None)
    if not item:
        return None
    _PAGE_CACHE[identity] = (time.monotonic(), item)
    return _result(item, requested, 'official_manufacturer_page')

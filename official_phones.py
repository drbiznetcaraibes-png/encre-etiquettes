"""Manufacturer-sourced phone facts, available without an AI subscription.

The local catalogues contain facts checked against the linked manufacturer pages.
Known iPhones work offline; new models can use Apple's own technical sheets.
Storage is chosen explicitly and validated independently of RAM.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

CATALOG = json.loads((Path(__file__).with_name('phone_catalog.json')).read_text(encoding='utf-8'))
APPLE_CATALOG = json.loads((Path(__file__).with_name('apple_catalog.json')).read_text(encoding='utf-8'))
# The expanded Apple catalogue replaces the former single-model fallback, keeping
# one exact definition per phone. Other manufacturer catalogues can join here.
CATALOG['phones'] = APPLE_CATALOG['phones'] + [item for item in CATALOG['phones'] if item['brand'] != 'APPLE']
APPLE_INDEX = 'https://support.apple.com/fr-fr/108044'
APPLE_ARTICLES = {
    'iphone14': '111850', 'iphone15': '111831',
    'iphone15promax': '111828', 'iphone16': '121029',
    'iphone16plus': '121030', 'iphone16pro': '121031',
    'iphone16promax': '121032', 'iphone16e': '122208',
    'iphone17': '125089', 'iphone17promax': '125091',
}
_PAGE_CACHE: dict[str, tuple[float, dict]] = {}
_INDEX_CACHE: tuple[float, dict] | None = None
_STORAGE = re.compile(r'(?<![\w.])(\d+(?:[,.]\d+)?)\s*(GO|GB|TO|TB)\b', re.I)
_SPEC_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie',
                'processeur': 'Puce', 'reseau': 'Réseau', 'ram': 'RAM'}


def _plain(text):
    text = unicodedata.normalize('NFKC', str(text or '')).replace('\u00ad', '')
    return re.sub(r'\s+', ' ', text).strip()


def _identity(text):
    return re.sub(r'[^A-Z0-9+]', '', _plain(text).upper())


def _capacity(number, unit):
    value = float(number.replace(',', '.'))
    if unit.upper() in ('TB', 'TO'):
        value *= 1024
    if not value.is_integer() or value <= 0:
        return None
    size = int(value)
    return f'{size // 1024} To' if size >= 1024 and size % 1024 == 0 else f'{size} Go'


def _parse_query(query):
    """Return the exact remaining model and one unambiguous storage choice."""
    text = _plain(query)
    requested = []
    for match in _STORAGE.finditer(text):
        # Explicit RAM is not a storage choice. Ambiguous multiple memories fail closed.
        tail = text[match.end():]
        if re.match(r'\s*(?:DE\s+)?RAM\b', tail, re.I):
            continue
        requested.append(_capacity(match.group(1), match.group(2)))
    if None in requested or len(set(requested)) > 1:
        return None, None
    model = _STORAGE.sub('', text)
    model = re.sub(r'\s*(?:DE\s+)?RAM\b', '', model, flags=re.I).strip()
    model = re.sub(r'^(?:APPLE|SAMSUNG)\s+', '', model, flags=re.I).strip()
    return _identity(model), requested[0] if requested else None


def _result(item, requested, is_sku=False, search_mode='official_manufacturer_catalog'):
    capacities = list(item.get('capacities', []))
    sources = deepcopy(item['sources'])
    if requested and capacities and requested not in capacities:
        return {'status': 'invalid_capacity', 'brand': item['brand'], 'model': item['model'],
                'capacities': capacities, 'available_capacities': capacities, 'sources': sources,
                'requested_capacity': requested}
    confirmed = bool(requested and requested in capacities)
    specs = dict(item['specs'])
    return {'brand': item['brand'], 'model': item['model'],
            'capacity': requested if confirmed else 'Non précisée',
            'capacity_confirmed': confirmed, 'available_capacities': capacities,
            'specs': specs,
            'feature': ' · '.join(f'{_SPEC_LABELS[k]} : {v}' for k, v in list(specs.items())[:4]),
            'sources': sources, 'status': 'verified_online',
            'input_type': 'manufacturer_sku' if is_sku else 'model_name',
            'search_mode': search_mode, 'matching_results': len(sources)}


def _section(soup, prefix, exact=False):
    for heading in soup.find_all(['h2', 'h3']):
        name = _plain(heading.get_text(' ', strip=True)).casefold()
        matches = name == prefix.casefold() if exact else name.startswith(prefix.casefold())
        if matches:
            parts = []
            for sibling in heading.next_siblings:
                if getattr(sibling, 'name', None) in ('h1', 'h2', 'h3'):
                    break
                parts.append(sibling.get_text(' ', strip=True) if hasattr(sibling, 'get_text') else str(sibling))
            return _plain(' '.join(parts))
    return ''


def parse_apple_sheet(document, expected_model, source_url):
    """Read only the named phone's sections, ignoring recommendations and footer."""
    soup = BeautifulSoup(document, 'html.parser')
    heading = soup.find('h1')
    if not heading:
        return None
    title = _plain(heading.get_text(' ', strip=True))
    title_model = re.split(r'\s+[-–—]\s+', title, maxsplit=1)[0]
    if _identity(title_model) != expected_model.upper():
        return None
    capacities = []
    for match in _STORAGE.finditer(_section(soup, 'Capacité')):
        capacity = _capacity(match.group(1), match.group(2))
        if capacity and capacity not in capacities:
            capacities.append(capacity)
    screen = _section(soup, 'Écran')
    # Older sheets put the rear cameras under "Appareil photo" and separately
    # list the front "Caméra TrueDepth". Never label the selfie sensor principal.
    camera = _section(soup, 'Appareil photo') or _section(soup, 'Caméra', exact=True)
    chip = _section(soup, 'Puce')
    battery = _section(soup, 'Alimentation et batterie')
    network = _section(soup, 'Connectivité cellulaire') or _section(soup, 'Réseaux cellulaires')
    specs = {}
    diagonal = re.search(r'(?<!\d)(\d(?:[,.]\d+)?)\s*pouces', screen, re.I)
    if diagonal:
        panel = ' OLED' if re.search(r'\bOLED\b', screen, re.I) else ' LCD' if re.search(r'\bLCD\b', screen, re.I) else ''
        refresh = ' 120 Hz' if re.search(r'120\s*Hz', screen, re.I) else ''
        specs['ecran'] = diagonal.group(1).replace('.', ',') + '″' + panel + refresh
    photo = re.search(r'(?<!\d)(\d{1,3})\s*(?:MP|Mpx)\b', camera, re.I)
    if photo:
        specs['photo'] = photo.group(1) + ' Mpx (principal)'
    processor = re.search(r'\bPuce\s+(A\d{1,2}(?:\s+(?:Pro|Bionic|Fusion))?)\b', chip, re.I)
    if processor:
        specs['processeur'] = 'Apple ' + processor.group(1)
    endurance = re.search(r'Lecture vidéo(?:\s+(?:sans fil|\(sans fil\)))?\s*:?\s*Jusqu[’\']à\s*(\d+)\s*heures', battery, re.I)
    if endurance:
        specs['batterie'] = 'Jusqu’à ' + endurance.group(1) + ' h de vidéo'
    if re.search(r'\b5G\b', network):
        specs['reseau'] = '5G'
    elif re.search(r'\bLTE\b', network):
        specs['reseau'] = '4G LTE'
    if len(specs) < 3 or not capacities:
        return None
    return {'brand': 'APPLE', 'model': title_model, 'capacities': capacities, 'specs': specs,
            'sources': [{'title': title, 'url': source_url}]}


async def _apple_index(client):
    global _INDEX_CACHE
    if _INDEX_CACHE and time.monotonic() - _INDEX_CACHE[0] < 21600:
        return _INDEX_CACHE[1]
    response = await client.get(APPLE_INDEX, timeout=httpx.Timeout(4, connect=2))
    response.raise_for_status()
    soup = BeautifulSoup(response.text, 'html.parser')
    links = {}
    for anchor in soup.find_all('a', href=True):
        label = _plain(anchor.get_text(' ', strip=True))
        if 'caractéristiques techniques' not in label.lower():
            continue
        name = re.search(r'iPhone\s*(?:\d{1,2}(?:e)?(?:\s*(?:Pro\s*Max|Pro|Plus|mini))?|X(?:R|S(?:\s*Max)?)?|Air|Duo)\b', label, re.I)
        if not name:
            continue
        url = urljoin(APPLE_INDEX, anchor['href'])
        parsed = urlparse(url)
        if parsed.scheme != 'https' or parsed.hostname != 'support.apple.com':
            continue
        article = re.fullmatch(r'/(?:fr-fr/)?(\d{5,6})', parsed.path)
        if article:
            # Apple's identification page uses locale-free numeric links.
            url = 'https://support.apple.com/fr-fr/' + article.group(1)
        elif not re.fullmatch(r'/kb/SP\d{1,4}', parsed.path):
            continue
        links[_identity(name.group(0))] = url
    _INDEX_CACHE = (time.monotonic(), links)
    return links


async def _lookup_apple(client, identity, requested):
    model_key = identity.lower()
    if identity in _PAGE_CACHE and time.monotonic() - _PAGE_CACHE[identity][0] < 21600:
        return _result(_PAGE_CACHE[identity][1], requested, search_mode='official_manufacturer_page')
    article = APPLE_ARTICLES.get(model_key)
    if article:
        url = f'https://support.apple.com/fr-fr/{article}'
    else:
        url = (await _apple_index(client)).get(identity)
    if not url:
        return None
    response = await client.get(url, timeout=httpx.Timeout(4, connect=2))
    response.raise_for_status()
    # Redirects must remain on the manufacturer's support domain.
    if response.url.host != 'support.apple.com':
        return None
    item = parse_apple_sheet(response.text, identity, str(response.url))
    if not item:
        return None
    _PAGE_CACHE[identity] = (time.monotonic(), item)
    return _result(item, requested, search_mode='official_manufacturer_page')


async def lookup_official_phone(client, query: str):
    """Return a verified manufacturer result, invalid_capacity, or None.

    Callers may use any other search provider when None is returned. The complete
    live Apple request is bounded to 7 seconds and never sends user data off-domain.
    """
    identity, requested = _parse_query(query)
    if not identity:
        return None
    for item in CATALOG['phones']:
        aliases = {_identity(a) for a in item.get('aliases', [])}
        skus = {_identity(a) for a in item.get('manufacturer_references', [])}
        if identity in aliases or identity in skus:
            return _result(item, requested, is_sku=identity in skus)
    if re.fullmatch(r'IPHONE(?:\d{1,2}E?(?:PROMAX|PRO|PLUS|MINI)?|X(?:R|S(?:MAX)?)?|AIR|DUO)', identity):
        try:
            return await asyncio.wait_for(_lookup_apple(client, identity, requested), timeout=7)
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
            return None
    return None

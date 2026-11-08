"""Samsung facts from exact manufacturer model tables, with a sourced fallback.

The fallback is deliberately bounded: an unknown variant is never replaced by
its base model, and storage is never inferred from a Samsung family reference.
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

CATALOG = json.loads(Path(__file__).with_name('samsung_catalog.json').read_text(encoding='utf-8'))
_CACHE = {}
_INDEX_CACHE = {}
_MEMORY = re.compile(r'(?<![\w.])(\d+(?:[,.]\d+)?)\s*(GO|GB|TO|TB)\b', re.I)
_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie', 'processeur': 'Puce', 'reseau': 'Réseau'}


def _plain(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip()


def _identity(value):
    text = re.sub(r'\bPLUS\b', '+', _plain(value).upper())
    return re.sub(r'[^A-Z0-9+]', '', text)


def _capacity(number, unit):
    value = float(number.replace(',', '.')) * (1024 if unit.upper() in ('TO', 'TB') else 1)
    if not value.is_integer() or value <= 0:
        return None
    value = int(value)
    return f'{value // 1024} To' if value >= 1024 and value % 1024 == 0 else f'{value} Go'


def _query(value):
    text = _plain(value)
    capacities = []
    for match in _MEMORY.finditer(text):
        if not re.match(r'\s*(?:DE\s+)?RAM\b', text[match.end():], re.I):
            capacities.append(_capacity(*match.groups()))
    if None in capacities or len(set(capacities)) > 1:
        return None, None
    text = _MEMORY.sub('', text)
    text = re.sub(r'\b(?:DE\s+)?RAM\b', '', text, flags=re.I)
    text = re.sub(r'^(?:SAMSUNG\s*)?(?:GALAXY\s*)?', '', text, flags=re.I)
    identity = _identity(text)
    if not re.fullmatch(r'(?:S\d{2}(?:\+|ULTRA|FE|EDGE)?(?:5G)?|A\d{2}(?:5G)?|ZFOLD\d{1,2}(?:ULTRA)?(?:5G)?|ZFLIP\d{1,2}(?:FE)?(?:5G)?)', identity):
        return None, None
    return identity, capacities[0] if capacities else None


def _result(item, requested, search_mode='official_manufacturer_catalog'):
    capacities = list(item['capacities'])
    sources = deepcopy(item['sources'])
    if requested and requested not in capacities:
        return {'status': 'invalid_capacity', 'brand': 'SAMSUNG', 'model': item['model'],
                'capacities': capacities, 'available_capacities': capacities,
                'sources': sources, 'requested_capacity': requested}
    specs = dict(item['specs'])
    return {'brand': 'SAMSUNG', 'model': item['model'],
            'capacity': requested or 'Non précisée', 'capacity_confirmed': bool(requested),
            'available_capacities': capacities, 'specs': specs,
            'feature': ' · '.join(f'{_LABELS[key]} : {value}' for key, value in list(specs.items())[:4]),
            'sources': sources, 'status': 'verified_online', 'input_type': 'model_name',
            'search_mode': search_mode, 'matching_results': len(sources)}


def _allowed(url):
    parsed = urlparse(str(url))
    return parsed.scheme == 'https' and parsed.hostname in ('samsungmobilepress.com', 'www.samsungmobilepress.com', 'api.samsungmobilepress.com')


def _grid(table):
    """Expand row/column spans so shared cells keep the correct model column."""
    occupied = {}
    rows = []
    for row_number, row in enumerate(table.find_all('tr')):
        column = 0
        for cell in row.find_all(['td', 'th'], recursive=False):
            while (row_number, column) in occupied:
                column += 1
            colspan = min(int(cell.get('colspan', 1)), 20)
            rowspan = min(int(cell.get('rowspan', 1)), 30)
            for y in range(row_number, row_number + rowspan):
                for x in range(column, column + colspan):
                    occupied[y, x] = cell
            column += colspan
        width = max((x + 1 for y, x in occupied if y == row_number), default=0)
        rows.append([occupied.get((row_number, x)) for x in range(width)])
    return rows


def _table_sections(table, identity):
    rows = _grid(table)
    if not rows:
        return None
    headers = []
    for cell in table.find('tr').find_all(['td', 'th'], recursive=False):
        title = _plain(cell.get_text(' ', strip=True))
        if re.fullmatch(r'Galaxy\s+(?:S\d{2}(?:\+|\s+(?:Ultra|FE|Edge))?|A\d{2}(?:\s+5G)?|Z\s*(?:Fold|Flip)\s*\d{1,2}(?:\s+(?:Ultra|FE))?)', title, re.I):
            headers.append(title)
    identities = [_identity(re.sub(r'^Galaxy\s+', '', title, flags=re.I)) for title in headers]
    if identity not in identities:
        return None
    # Several official tables have an oversized title colspan (five columns
    # over a three-column body). Determine data columns from the body itself.
    first_body = next((row for row in rows[1:] if len(row) > 1 and row[0] is not row[-1]), None)
    if not first_body:
        return None
    column = len(first_body) - len(headers) + identities.index(identity)
    sections = {}
    for row in rows[1:]:
        if len(row) <= column or row[column] is None or not row:
            continue
        label = _plain(row[0].get_text(' ', strip=True)) if row[0] else ''
        label = re.sub(r'\s+\d+$', '', label)
        value = _plain(row[column].get_text(' ', strip=True))
        if not value or value.startswith('*') or value == label:
            continue
        # Foldable tables put Main/Cover/Rear Camera labels in a second column.
        sublabels = []
        for cell in row[1:column] if len(headers) == 1 else []:
            if cell is not None and cell is not row[0] and cell is not row[column]:
                sublabel = _plain(cell.get_text(' ', strip=True))
                if sublabel and sublabel not in sublabels:
                    sublabels.append(sublabel)
        value = ' '.join(sublabels + [value])
        sections.setdefault(label.casefold(), []).append(value)
    return headers[identities.index(identity)], {key: ' '.join(dict.fromkeys(values)) for key, values in sections.items()}


def parse_samsung_sheet(document, expected_model, source_url):
    """Accept only a table headed by the exact requested Galaxy variant."""
    if not _allowed(source_url):
        return None
    identity = _identity(re.sub(r'^(?:Samsung\s*)?(?:Galaxy\s*)?', '', expected_model, flags=re.I))
    soup = BeautifulSoup(document, 'html.parser')
    for table in soup.find_all('table'):
        parsed = _table_sections(table, identity)
        if not parsed:
            continue
        model, sections = parsed
        storage = sections.get('memory & storage', sections.get('storage', ''))
        storage = re.split(r'micro\s*sd|expandable', storage, flags=re.I)[0]
        capacities = []
        # Samsung writes RAM + storage. Only the right side of the pair is ROM.
        pairs = re.findall(r'\d+\s*(?:GB|GO)?\s*\+\s*(\d+)\s*(GB|GO|TB|TO)\b', storage, re.I)
        pairs += re.findall(r'(\d+)\s*(GB|GO|TB|TO)\s+internal\s+storage', storage, re.I)
        for number, unit in pairs:
            capacity = _capacity(number, unit)
            if capacity and capacity not in capacities:
                capacities.append(capacity)
        if not pairs and 'storage' in sections:
            for match in _MEMORY.finditer(storage):
                capacity = _capacity(*match.groups())
                if capacity and capacity not in capacities:
                    capacities.append(capacity)
        specs = {}
        display = sections.get('display', '')
        diagonal = re.search(r'(\d+(?:[,.]\d+)?)\s*(?:[- ]?inch|[”″\"])', display, re.I)
        if diagonal:
            panel = re.search(r'(Dynamic\s+AMOLED\s+2X|Super\s+AMOLED(?:\s*\+)?|AMOLED)', display, re.I)
            refresh = re.search(r'(?<!\d)(120|90|60)\s*Hz', display, re.I)
            panel_name = re.sub(r'\s*\+', '+', panel.group(1)) if panel else ''
            specs['ecran'] = diagonal.group(1).replace('.', ',') + '″' + (' ' + panel_name if panel_name else '') + (' ' + refresh.group(1) + ' Hz' if refresh else '')
        camera = sections.get('camera', '')
        rear = re.split(r'Rear\s+(?:(?:Dual|Triple|Quad)\s+)?Camera', camera, maxsplit=1, flags=re.I)
        camera = rear[-1] if len(rear) > 1 else re.split(r'\d+\s*MP\s*(?:Front|Selfie|Main Screen)', camera, flags=re.I)[0]
        sensors = re.findall(r'(\d+)\s*MP\b', camera, re.I)
        if sensors:
            specs['photo'] = ' + '.join(sensors) + ' Mpx (arrière)'
        battery = re.search(r'(\d[\d, ]{2,6})\s*mAh', sections.get('battery', ''), re.I)
        if battery:
            number = int(re.sub(r'\D', '', battery.group(1)))
            specs['batterie'] = f'{number:,}'.replace(',', ' ') + ' mAh'
        processor = sections.get('ap', sections.get('processor', ''))
        processor = _plain(re.split(r'\s*\*', processor)[0].replace('®', ''))
        processor = re.sub(r'\bExynos\s*(\d+)', r'Exynos \1', processor)
        if processor and len(processor) < 160:
            specs['processeur'] = processor + (' (selon marché)' if 'Exynos' in processor and 'Snapdragon' in processor else '')
        network = sections.get('network and connectivity', sections.get('network & connectivity', sections.get('network', sections.get('connectivity', ''))))
        if re.search(r'\b5G\b', network) or model.endswith('5G'):
            specs['reseau'] = '5G'
        if len(specs) < 3 or not capacities:
            continue
        capacities.sort(key=lambda value: int(value.split()[0]) * (1024 if value.endswith('To') else 1))
        return {'brand': 'SAMSUNG', 'model': model, 'capacities': capacities, 'specs': specs,
                'sources': [{'title': 'Samsung — ' + model + ' : caractéristiques constructeur', 'url': source_url}]}
    return None


async def _discover(client, identity, explicit_5g=False):
    family = 'galaxy-z' if identity.startswith('Z') else 'galaxy-a' if identity.startswith('A') else 'galaxy-s'
    cache = _INDEX_CACHE.get(family)
    if cache and time.monotonic() - cache[0] < 21600:
        links = cache[1]
    else:
        index_url = 'https://www.samsungmobilepress.com/products/' + family
        response = await client.get(index_url, timeout=httpx.Timeout(4, connect=2))
        response.raise_for_status()
        if not _allowed(response.url):
            return None
        soup = BeautifulSoup(response.text, 'html.parser')
        links = []
        for anchor in soup.find_all('a', href=True):
            url = urljoin(index_url, anchor['href'])
            if _allowed(url) and '/articles/' in urlparse(url).path:
                links.append((_identity(anchor.get_text(' ', strip=True) + ' ' + urlparse(url).path), url))
        links = list(dict.fromkeys(links))
        _INDEX_CACHE[family] = time.monotonic(), links
    base = re.sub(r'(?:ULTRA|FE|EDGE|\+|5G)$', '', identity)
    candidates = list(dict.fromkeys(url for text, url in links if base in text))[:3]
    async def read(url):
        response = await client.get(url, timeout=httpx.Timeout(4, connect=2))
        response.raise_for_status()
        if not _allowed(response.url):
            return None
        item = parse_samsung_sheet(response.text, identity, str(response.url))
        if not item and explicit_5g:
            item = parse_samsung_sheet(response.text, identity + '5G', str(response.url))
        if explicit_5g and item and item['specs'].get('reseau') != '5G':
            return None
        return item
    if not candidates:
        return None
    results = await asyncio.gather(*(read(url) for url in candidates), return_exceptions=True)
    return next((item for item in results if isinstance(item, dict)), None)


async def lookup_samsung_phone(client, query):
    """Return exact verified model facts, invalid_capacity, or None in <= 8 s."""
    identity, requested = _query(query)
    if not identity:
        return None
    explicit_5g = identity.endswith('5G')
    model_identity = identity[:-2] if explicit_5g else identity
    for item in CATALOG['phones']:
        aliases = {_identity(alias) for alias in item['aliases']}
        matches = identity in aliases or (explicit_5g and model_identity in aliases)
        if matches and (not explicit_5g or item['specs'].get('reseau') == '5G'):
            return _result(item, requested)
    cache = _CACHE.get(identity)
    if cache and time.monotonic() - cache[0] < 21600:
        return _result(cache[1], requested, 'official_manufacturer_page')
    try:
        item = await asyncio.wait_for(_discover(client, model_identity, explicit_5g=explicit_5g), timeout=8)
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError):
        return None
    if item:
        _CACHE[identity] = time.monotonic(), item
        return _result(item, requested, 'official_manufacturer_page')
    return None

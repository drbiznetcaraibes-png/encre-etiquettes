"""Read structured specification sections from Honor, OnePlus and realme.

These are live manufacturer pages, not a catalogue of inferred product facts.
Unsupported URLs, layouts or model variants return None for the next provider.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

_CACHE = {}
_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie',
           'processeur': 'Puce', 'reseau': 'Réseau'}
_STORAGE = re.compile(r'(?<![\w.])(\d+)\s*(Go|GB|To|TB)\b', re.I)


def _plain(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or ''))).strip()


def _key(value):
    value = unicodedata.normalize('NFKD', _plain(value)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^A-Z0-9]', '', value.upper().replace('+', 'PLUS'))


def _capacity(value, unit):
    size = int(value) * (1024 if unit.upper() in ('TO', 'TB') else 1)
    if size < 32 or size > 8192:
        return None
    return f'{size // 1024} To' if size % 1024 == 0 else f'{size} Go'


def _capacities(text):
    out = []
    for match in _STORAGE.finditer(text):
        value = _capacity(*match.groups())
        if value and value not in out:
            out.append(value)
    return out


def _request(query):
    text = _plain(query)
    requested = []
    for match in _STORAGE.finditer(text):
        if re.match(r'\s*(?:de\s+)?RAM\b', text[match.end():], re.I):
            continue
        requested.append(_capacity(*match.groups()))
    if None in requested or len(set(requested)) > 1:
        return None
    text = _STORAGE.sub('', text)
    text = re.sub(r'\b(?:de\s+)?RAM\b', '', text, flags=re.I).strip()
    compact = _key(text)
    if re.fullmatch(r'HONOR(?:MAGIC\d{1,2}(?:PRO|LITE)?|\d{2,3}(?:PRO|LITE)?|X\d{1,2}[ABC]?)', compact):
        body = re.sub(r'^HONOR', '', compact)
        body = re.sub(r'(PRO|LITE)$', r'-\1', body).lower()
        display = 'HONOR ' + body.replace('-', ' ').title()
        url = f'https://www.honor.com/fr/phones/honor-{body}/spec/'
        brand, parser = 'HONOR', _parse_honor
    elif re.fullmatch(r'ONEPLUS(?:\d{1,2}[RT]?|NORD(?:CE)?\d{1,2}(?:LITE)?)', compact):
        body = compact[len('ONEPLUS'):].lower()
        slug = re.sub(r'^(nord)(ce)?(\d+)(lite)?$', lambda m: '-'.join(v for v in m.groups() if v), body)
        display = 'OnePlus ' + slug.replace('-', ' ').upper().replace('NORD', 'Nord').replace('LITE', 'Lite')
        url = f'https://www.oneplus.com/fr/{slug}/specs'
        brand, parser = 'ONEPLUS', _parse_oneplus
    elif re.fullmatch(r'REALME(?:\d{1,2}(?:PROPLUS|PRO|PLUS|T|X)?(?:4G|5G)?|GT\d{1,2}(?:PRO|T)?|C\d{1,3}(?:5G)?)', compact):
        body = compact[len('REALME'):].lower()
        match = re.fullmatch(r'(gt|c)?(\d{1,3})(proplus|pro|plus|t|x)?(4g|5g)?', body)
        if not match:
            return None
        family, number, variant, network = match.groups()
        words = [family, number, variant.replace('proplus', 'pro-plus') if variant else None, network]
        slug = '-'.join(v for v in words if v)
        # C-series use c75, while GT-series use gt-7 in the manufacturer's URLs.
        if family == 'c':
            slug = slug.replace('c-', 'c', 1)
        display = 'realme ' + slug.replace('-', ' ').upper().replace('PRO', 'Pro').replace('PLUS', 'Plus')
        url = f'https://www.realme.com/global/realme-{slug}/specs'
        brand, parser = 'REALME', _parse_realme
    else:
        return None
    return brand, compact, display, url, parser, requested[0] if requested else None


def _screen(text):
    match = re.search(r'(?<!\d)([4-8][,.]\d{1,2})\s*(?:pouces|inches|inch|[″"])', text, re.I)
    if not match:
        return None
    value = match.group(1).replace('.', ',') + '″'
    panel = re.search(r'\b(?:AMOLED|OLED|LCD|LTPO)\b', text, re.I)
    if panel:
        value += ' ' + panel.group().upper()
    refresh = re.search(r'(?<!\d)(90|120|144|165)\s*Hz\b', text, re.I)
    if refresh:
        value += ' ' + refresh.group(1) + ' Hz'
    return value


def _battery(text):
    match = re.search(r'(?<!\d)([3-9](?:[ \u00a0]?\d){3})\s*mAh\b', text, re.I)
    return re.sub(r'\s', '', match.group(1)) + ' mAh' if match else None


def _photo(text):
    # A camera group may contain front cameras after its explicitly rear entries.
    rear = True
    for line in text.splitlines():
        if re.search(r'\b(?:selfie|front camera|appareil photo avant|TrueDepth)\b', line, re.I):
            rear = False
            continue
        if re.search(r'\b(?:rear|main camera|principal|grand.angle)\b', line, re.I):
            rear = True
        if not rear:
            continue
        match = re.search(r'(?<!\d)(\d{1,3})\s*(?:MP|Mpx)\b', line, re.I)
        if not match:
            match = re.search(r'M[ée]gapixels\s*:\s*(\d{1,3})\b', line, re.I)
        if match:
            return match.group(1) + ' Mpx (principal)'
    return None


def _processor(text):
    text = text.replace('®', '').replace('™', '')
    match = re.search(r'\b((?:Snapdragon|Dimensity|Kirin)\s*[A-Za-z0-9]+(?:\s+(?:Gen\s*\d|Elite|Energy|Plus))?)\b', text, re.I)
    return _plain(match.group(1)) if match else None


def _network(text):
    if re.search(r'\b5G\b', text, re.I):
        return '5G'
    if re.search(r'\b4G\b|\bLTE\b', text, re.I):
        return '4G LTE'
    return None


def _parse_honor(soup, expected):
    heading = soup.select_one('h1.product-name')
    if not heading or _key(heading.get_text(' ', strip=True)) != expected:
        return None
    groups = {}
    for heading in soup.select('h2.products-spec-component-title-text'):
        block = heading.find_parent(class_='products-spec-component-level-1')
        if block:
            groups[_key(heading.get_text(' ', strip=True))] = '\n'.join(
                value['data-value'] for value in block.select('[data-class="p2"][data-value]'))
    memory = groups.get('MEMOIRE', '')
    # The right side of RAM+storage combinations alone is storage evidence.
    options = re.findall(r'\d+\s*(?:Go|GB)\s*\+\s*(\d+\s*(?:Go|GB|To|TB))', memory, re.I)
    capacities = _capacities(' '.join(options))
    specs = {'ecran': _screen(groups.get('ECRAN', '')),
             'photo': _photo(groups.get('APPAREILPHOTOARRIERE', '')),
             'batterie': _battery(groups.get('BATTERIE', '')),
             'processeur': _processor(groups.get('PROCESSEUR', '')),
             'reseau': _network(groups.get('RESEAUCELLULAIRE', ''))}
    return specs, capacities


def _parse_oneplus(soup, expected):
    heading = soup.find('h1')
    if not heading or _key(re.sub(r'\s+(?:Specs|Specifications)$', '', heading.get_text(' ', strip=True), flags=re.I)) != expected:
        return None
    components = []
    for script in soup.find_all('script'):
        text = script.string or ''
        match = re.search(r'window\.pageDsl\s*=\s*', text)
        if not match:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(text[match.end():])
        except (ValueError, TypeError):
            continue
        if not isinstance(data, dict) or not isinstance(data.get('byId'), dict):
            continue
        for component in data.get('byId', {}).values():
            if not isinstance(component, dict):
                continue
            groups = component.get('attr', {}).get('spuData', {}).get('parameterData', [])
            names = [entry.get('content', '') for group in groups for entry in group.get('nameDetailList', []) if entry.get('name') == 'SPU_NAME']
            if names and all(_key(name) == expected for name in names):
                components.append(groups)
    if len(components) != 1:
        return None
    groups = {_key(group.get('GroupName')): group.get('nameDetailList', []) for group in components[0]}
    content = lambda key: '\n'.join(entry.get('content', '') for entry in groups.get(key, []))
    performance = content('PERFORMANCES') or content('PERFORMANCE')
    storage = re.search(r'(?:Stockage|Storage)\s*:\s*([^\r\n]+)', performance, re.I)
    rear = '\n'.join(entry.get('content', '') for entry in groups.get('APPAREILPHOTO', [])
                     if re.search(r'grand.angle|main camera', entry.get('name', ''), re.I)
                     and not re.search(r'ultra|avant|front', entry.get('name', ''), re.I))
    specs = {'ecran': _screen(content('ECRAN') or content('DISPLAY')),
             'photo': _photo(rear), 'batterie': _battery(performance),
             'processeur': _processor(performance),
             'reseau': _network(content('CONNECTIVITE') or content('CONNECTIVITY'))}
    return specs, _capacities(storage.group(1)) if storage else []


def _parse_realme(soup, expected):
    if not soup.title:
        return None
    title = soup.title.get_text(' ', strip=True)
    match = re.fullmatch(r'(.+?)\s+Specifications\s*[-–]\s*realme\s*\(Global\)', title, re.I)
    if not match or _key(match.group(1)) != expected:
        return None
    groups = {}
    for heading in soup.select('h2.sec-params-label'):
        block = heading.find_parent(class_='sec-params-unit')
        if block:
            groups[_key(heading.get_text(' ', strip=True))] = '\n'.join(
                element.get_text('\n', strip=True) for element in block.select('.sec-params-title,.sec-params-description'))
    memory = groups.get('MEMORYSTORAGE', '')
    storage = re.search(r'ROM\s*[:：]\s*([^\r\n]+)', memory, re.I)
    # realme uses both 128G and 256GB on the same ROM line.
    storage_text = re.sub(r'\b(\d+)G\b', r'\1GB', storage.group(1)) if storage else ''
    specs = {'ecran': _screen(groups.get('DISPLAY', '')),
             'photo': _photo(groups.get('CAMERA', '')),
             'batterie': _battery(groups.get('CHARGINGBATTERY', '')),
             'processeur': _processor(groups.get('PROCESSOR', '')),
             'reseau': _network(groups.get('CELLULARWIRELESS', ''))}
    return specs, _capacities(storage_text)


async def _read_page(client, url):
    host = urlparse(url).hostname
    for _ in range(3):
        response = await client.get(url, timeout=httpx.Timeout(5, connect=2), follow_redirects=False)
        if response.is_redirect:
            target = urljoin(url, response.headers.get('location', ''))
            parsed = urlparse(target)
            if parsed.scheme != 'https' or parsed.hostname != host or parsed.username or parsed.password:
                return None
            url = target
            continue
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            return None
        return response.text, str(response.url)
    return None


async def lookup_manufacturer_phone(client, query):
    """Lookup supported Honor/OnePlus/realme layouts, with a seven-second budget."""
    parsed = _request(query)
    if not parsed:
        return None
    brand, expected, model, url, parser, requested = parsed
    saved = _CACHE.get(url)
    try:
        if saved and time.monotonic() - saved[0] < 21600:
            specs, capacities, source_url = deepcopy(saved[1])
        else:
            page = await asyncio.wait_for(_read_page(client, url), timeout=7)
            if not page:
                return None
            document, source_url = page
            result = parser(BeautifulSoup(document, 'html.parser'), expected)
            if not result:
                return None
            specs, capacities = result
            specs = {key: value for key, value in specs.items() if value}
            if len(specs) < 3:
                return None
            _CACHE[url] = (time.monotonic(), (specs, capacities, source_url))
            if len(_CACHE) > 128:
                _CACHE.pop(next(iter(_CACHE)))
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError, KeyError, AttributeError):
        return None
    sources = [{'title': f'{model} — caractéristiques constructeur' + (' (Global)' if brand == 'REALME' else ' (France)'), 'url': source_url}]
    if requested and capacities and requested not in capacities:
        return {'status': 'invalid_capacity', 'brand': brand, 'model': model,
                'requested_capacity': requested, 'capacities': capacities,
                'available_capacities': capacities, 'sources': sources}
    confirmed = bool(requested and requested in capacities)
    return {'brand': brand, 'model': model, 'capacity': requested if confirmed else 'Non précisée',
            'capacity_confirmed': confirmed, 'available_capacities': capacities,
            'specs': specs, 'feature': ' · '.join(f'{_LABELS[key]} : {value}' for key, value in list(specs.items())[:4]),
            'sources': sources, 'status': 'verified_online', 'input_type': 'model_name',
            'search_mode': 'official_manufacturer_page', 'matching_results': 1}

"""Pixel facts from Google's per-model specification tables and a local snapshot."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

SOURCE = 'https://support.google.com/pixelphone/answer/7158570?hl=en'
_PATH = Path(__file__).with_name('google_catalog.json')
CATALOG = json.loads(_PATH.read_text()) if _PATH.exists() else {'phones': []}
_MEMORY = re.compile(r'(?<![\w.])(\d+)\s*(GB|GO|TB|TO)\b', re.I)


def _plain(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def _identity(value):
    value = re.sub(r'^GOOGLE\s*', '', _plain(value), flags=re.I)
    return re.sub(r'[^A-Z0-9]', '', value.upper())


def parse_google_specs(document, *, minimum_year=None, maximum_year=None):
    """Each table caption identifies its own variant; never mix Pro/XL/Fold."""
    current_year = datetime.now(timezone.utc).year
    minimum_year = current_year - 1 if minimum_year is None else minimum_year
    maximum_year = current_year if maximum_year is None else maximum_year
    soup = BeautifulSoup(document, 'html.parser')
    phones, year = [], None
    for node in soup.find_all(['h2', 'table']):
        if node.name == 'h2':
            match = re.search(r'\((20\d{2})\)', node.get_text(' ', strip=True))
            year = int(match[1]) if match else None
            continue
        caption = node.find('caption')
        if not caption or year is None or not minimum_year <= year <= maximum_year:
            continue
        title = _plain(caption.get_text(' ', strip=True))
        match = re.fullmatch(r'(Pixel\s+\d{1,2}(?:a|\s+Pro(?:\s+(?:XL|Fold))?|\s+Fold)?)\s+tech specs', title, re.I)
        if not match:
            continue
        model = match[1]
        sections = {}
        for row in node.find_all('tr'):
            cells = row.find_all(['td', 'th'], recursive=False)
            if len(cells) == 2:
                for mark in cells[0].find_all('sup'):
                    mark.decompose()
                sections[_plain(cells[0].get_text(' ', strip=True)).casefold()] = _plain(cells[1].get_text(' ', strip=True))
        capacities = []
        memory = sections.get('memory & storage', '')
        for option in _MEMORY.finditer(memory):
            if re.match(r'\s*RAM\b', memory[option.end():], re.I):
                continue
            size = int(option[1]) * (1024 if option[2].upper() in ('TB', 'TO') else 1)
            if size < 64 or size > 4096:
                continue
            capacity = f'{size // 1024} To' if size >= 1024 and size % 1024 == 0 else f'{size} Go'
            if capacity not in capacities:
                capacities.append(capacity)
        specs = {}
        display = sections.get('display', '')
        sizes = re.findall(r'(\d+(?:\.\d+)?)\s*-\s*inch', display, re.I)
        if sizes and 'OLED' in display:
            if 'Fold' in model and len(sizes) >= 2:
                specs['ecran'] = sizes[1].replace('.', ',') + '″ OLED interne / ' + sizes[0].replace('.', ',') + '″ externe'
            else:
                specs['ecran'] = sizes[0].replace('.', ',') + '″ OLED'
            if re.search(r'120\s*Hz', display, re.I):
                specs['ecran'] += ' 120 Hz'
        processor = re.search(r'Google Tensor G\d+', sections.get('processor', ''), re.I)
        if processor:
            specs['processeur'] = processor[0]
        rear = re.search(r'(\d+(?:\.\d+)?)\s*MP', sections.get('rear camera', ''), re.I)
        if rear:
            specs['photo'] = rear[1].replace('.', ',') + ' Mpx (principal)'
        battery = re.search(r'Typical\s+(\d[\d, ]*)\s*mAh', sections.get('battery & charging', ''), re.I)
        if battery:
            specs['batterie'] = f'{int(re.sub(r"\D", "", battery[1])):,}'.replace(',', ' ') + ' mAh'
        network = ' '.join(value for key, value in sections.items() if 'network' in key)
        if re.search(r'\b5G\b', network):
            specs['reseau'] = '5G'
        if len(specs) < 3 or not capacities:
            continue
        phones.append({'brand': 'GOOGLE', 'model': model, 'aliases': [model],
                       'capacities': capacities, 'manufacturer_references': [], 'specs': specs,
                       'release_year': year, 'sources': [{'title': 'Google — ' + model + ' : caractéristiques', 'url': SOURCE}]})
    return phones


async def collect(client, existing):
    result = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': False, 'errors': []}
    url = SOURCE
    try:
        for _ in range(4):
            parsed = urlparse(url)
            if parsed.scheme != 'https' or parsed.hostname != 'support.google.com' or parsed.username or parsed.password or parsed.port not in (None, 443):
                raise ValueError('Google source rejected')
            response = await client.get(url, timeout=httpx.Timeout(15, connect=4), follow_redirects=False)
            if response.is_redirect:
                target = response.headers.get('location')
                if not target:
                    raise ValueError('Missing Google redirect target')
                url = urljoin(url, target)
                continue
            response.raise_for_status()
            if len(response.content) > 6_000_000:
                raise ValueError('Google document too large')
            result['phones'] = parse_google_specs(response.text)
            result['checked'] = len(result['phones'])
            result['discovery_ok'] = bool(result['phones'])
            previous = {_identity(phone.get('model')) for phone in existing}
            result['discovered'] = sum(_identity(phone['model']) not in previous for phone in result['phones'])
            if not result['phones']:
                result['errors'].append('Google specification tables not recognized')
            return result
        raise ValueError('Too many Google redirects')
    except (httpx.HTTPError, ValueError, TypeError):
        result['errors'].append('Google specification source unavailable; previous facts retained')
        return result


async def lookup_google_phone(client, query):
    text = _plain(query)
    requested = []
    for option in _MEMORY.finditer(text):
        if not re.match(r'\s*RAM\b', text[option.end():], re.I):
            size = int(option[1]) * (1024 if option[2].upper() in ('TB', 'TO') else 1)
            requested.append(f'{size // 1024} To' if size >= 1024 and size % 1024 == 0 else f'{size} Go')
    if len(set(requested)) > 1:
        return None
    identity = _identity(re.sub(r'\bRAM\b', '', _MEMORY.sub('', text), flags=re.I))
    phone = next((item for item in CATALOG['phones'] if _identity(item['model']) == identity), None)
    if not phone:
        return None
    capacity = requested[0] if requested else None
    if capacity and capacity not in phone['capacities']:
        return {'status': 'invalid_capacity', 'brand': 'GOOGLE', 'model': phone['model'],
                'capacities': phone['capacities'], 'available_capacities': phone['capacities'], 'sources': phone['sources']}
    return {**phone, 'capacity': capacity or 'Non précisée', 'capacity_confirmed': bool(capacity),
            'available_capacities': phone['capacities'], 'status': 'verified_online',
            'input_type': 'model_name', 'search_mode': 'official_manufacturer_catalog'}

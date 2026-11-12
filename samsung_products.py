"""Samsung's public product finder and specification API, without model lists.

The storefront uses these same unauthenticated endpoints. Product names and
regional SKUs establish identity; labelled specification attributes supply facts.
Storage is read separately from RAM, free space and removable memory.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx

API = 'https://searchapi.samsung.com/v6/front/b2c/product/'
SITES = ('fr', 'uk', 'de')
_INDEX_CACHE = {}
_INDEX_PENDING = {}
_SPEC_CACHE = {}
_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie', 'processeur': 'Puce', 'reseau': 'Réseau'}
_MEMORY = re.compile(r'(?<!\d)(\d+(?:[,.]\d+)?)\s*(GO|GB|TO|TB)\b', re.I)
_ATTR_KEYS = {
    'storage': 'F0F7647C-2B93-E0A0-E043-CBFEDFDDC465',
    'size': 'PSP-198CE175-4E7A-43AB-9CD1-2C5CDB821F49',
    'panel': 'F0F7647C-2B68-E0A0-E043-CBFEDFDDC465',
    'refresh': 'PSP-9E6DA643-C8C8-4760-9447-DBFB816F4F1E',
    'rear_camera': 'PSP-18B2C1F7-07F8-4D83-8A29-DE59809DFB30',
    'battery': 'PSP-D59D72A5-D70A-4D00-A5B7-C77487173564',
    'network': 'F1CCA4B5-5FF6-F026-E043-CBFEDFDDC006',
}


def _plain(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value or '')).replace('\\"', '"')).strip()


def _key(value):
    text = re.sub(r'^\s*(?:SAMSUNG\s*)?(?:GALAXY\s*)?', '', _plain(value), flags=re.I)
    text = re.sub(r'\bPLUS\b', '+', text, flags=re.I)
    return re.sub(r'[^A-Z0-9+]', '', text.upper())


def _allowed(url):
    try:
        parsed = urlparse(str(url))
        return (parsed.scheme == 'https' and parsed.hostname in ('searchapi.samsung.com', 'www.samsung.com')
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def _capacity(value, default_unit='GB'):
    match = _MEMORY.search(_plain(value))
    if not match:
        match = _MEMORY.fullmatch(_plain(value) + ' ' + default_unit)
    if not match:
        return None
    amount = float(match[1].replace(',', '.')) * (1024 if match[2].upper() in ('TO', 'TB') else 1)
    if not amount.is_integer() or amount < 16:
        return None
    number = int(amount)
    return f'{number // 1024} To' if number % 1024 == 0 else f'{number} Go'


def _query(query):
    text = _plain(query)
    requested = []
    for match in _MEMORY.finditer(text):
        if not re.match(r'\s*(?:DE\s+)?RAM\b', text[match.end():], re.I):
            requested.append(_capacity(match[0]))
    if None in requested or len(set(requested)) > 1:
        return None
    text = _MEMORY.sub('', text)
    text = re.sub(r'\b(?:DE\s+)?RAM\b', '', text, flags=re.I)
    identity = _key(text)
    sku = bool(re.fullmatch(r'SM[A-Z0-9]{4,18}(?:DSB?)?', identity))
    if not sku and not re.fullmatch(r'(?:[SAMF]\d{1,3}(?:\+|ULTRA|FE|EDGE)?|Z(?:FOLD|FLIP)\d{1,2}(?:ULTRA|FE)?|XCOVER\d{1,2}(?:PRO)?)(?:[45]G)?', identity):
        return None
    network = '5G' if identity.endswith('5G') else '4G' if identity.endswith('4G') else None
    return identity[:-2] if network else identity, network, requested[0] if requested else None, sku


async def _json(client, url, params=None):
    if not _allowed(url):
        raise ValueError('Samsung source domain rejected')
    for _ in range(4):
        response = await client.get(url, params=params, timeout=httpx.Timeout(3.5, connect=1.5), follow_redirects=False)
        if response.is_redirect:
            target = urljoin(str(response.url), response.headers.get('location', ''))
            if not response.headers.get('location') or not _allowed(target):
                raise ValueError('Samsung redirect domain rejected')
            url, params = target, None
            continue
        response.raise_for_status()
        if not _allowed(response.url) or len(response.content) > 6_000_000:
            raise ValueError('Invalid Samsung response')
        data = response.json().get('response', {})
        if str(data.get('statusCode')) != '200':
            raise ValueError('Samsung API did not return success')
        return data.get('resultData', {})
    raise ValueError('Too many Samsung redirects')


def _families(data, site):
    rows = []
    for family in data.get('productList', []):
        name = _plain(family.get('fmyMarketingName') or family.get('fmyEngName'))
        if not name.lower().startswith('galaxy ') or not _query(name):
            continue
        models = []
        for model in family.get('modelList', []):
            sku = _plain(model.get('modelCode')).upper()
            display = _plain(model.get('displayName'))
            url = urljoin('https://www.samsung.com', model.get('pdpUrl') or '')
            if not re.fullmatch(r'SM-[A-Z0-9]{5,18}', sku) or _key(display) != _key(name) or not _allowed(url):
                continue
            if not urlparse(url).path.startswith('/' + site + '/smartphones/'):
                continue
            capacity = next((_capacity(chip.get('fmyChipCode') or chip.get('fmyChipLocalName'))
                             for chip in model.get('fmyChipList', [])
                             if chip.get('fmyChipType') == 'MOBILE MEMORY'), None)
            models.append({'sku': sku, 'family_ref': _plain(model.get('modelName')).upper(),
                           'name': display, 'url': url, 'capacity': capacity})
        if models:
            rows.append({'site': site, 'name': name, 'english_name': _plain(family.get('fmyEngName')), 'models': models})
    return rows


async def _load_listing(client, site):
    try:
        data = await _json(client, API + 'finder/global', {'type': '01010000', 'siteCode': site,
                           'start': 1, 'num': 100, 'sort': 'newest', 'onlyFilterInfoYN': 'N', 'keySummaryYN': 'Y'})
        rows = _families(data, site)
        if not rows:
            raise ValueError('Samsung finder contained no phone families')
        _INDEX_CACHE[site] = time.monotonic(), 21600, rows
        return rows
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        _INDEX_CACHE[site] = time.monotonic(), 60, None
        return None


async def _listing(client, site, force=False):
    cached = _INDEX_CACHE.get(site)
    age = time.monotonic() - cached[0] if cached else None
    if cached and age < cached[1] and (not force or age <= 60):
        return cached[2]
    key = asyncio.get_running_loop(), site
    pending = _INDEX_PENDING.get(key)
    if pending is None:
        pending = asyncio.create_task(_load_listing(client, site))
        _INDEX_PENDING[key] = pending
        def cleanup(task):
            if _INDEX_PENDING.get(key) is task:
                _INDEX_PENDING.pop(key, None)
        pending.add_done_callback(cleanup)
    return await asyncio.shield(pending)


async def list_product_families(client, force=False):
    """Public discovery helper: newest 100 phone families per FR/UK/DE storefront."""
    groups = await asyncio.gather(*(_listing(client, site, force=force) for site in SITES))
    return [family for group in groups if group for family in group]


def _attrs(nodes):
    values = {}
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        name = _plain(node.get('attrName')).casefold()
        value = _plain(node.get('attrValue'))
        if name and value:
            values[name] = value
        if node.get('attrKey') and value:
            values[str(node['attrKey']).casefold()] = value
        values.update(_attrs(node.get('attrs')))
    return values


def parse_product_specs(data, model):
    """Read only the spec record bearing the exact discovered regional SKU."""
    record = next((row for row in data.get('modelList', []) if row.get('modelCode') == model['sku']), None)
    if not record:
        return None
    attrs = _attrs(record.get('spec', {}).get('specItems'))
    def value(*labels):
        return next((attrs[label.casefold()] for label in labels if attrs.get(label.casefold())), '')
    storage = value(_ATTR_KEYS['storage'], 'Stockage (Go)', 'Storage (GB)', 'ROM Size (GB)', 'ROM Size (GB/MB)', 'Storage (GB/MB)')
    capacity = _capacity(storage)
    if not capacity or model.get('capacity') and model['capacity'] != capacity:
        return None
    specs = {}
    size = value(_ATTR_KEYS['size'], 'Taille (Écran principal)', 'Size (Main_Display)', 'Size (Main Display)')
    diagonal = re.search(r'(\d+(?:[,.]\d+)?)\s*["”″]', size)
    if diagonal:
        panel = value(_ATTR_KEYS['panel'], 'Technologie (Écran principal)', 'Technology (Main Display)', 'Technology (Main_Display)')
        refresh = value(_ATTR_KEYS['refresh'], 'Fréquence de rafraîchissement maximale (Écran principal)', 'Max Refresh Rate (Main Display)', 'Max Refresh Rate (Main_Display)')
        specs['ecran'] = diagonal[1].replace('.', ',') + '″' + (' ' + panel if panel else '') + (' ' + refresh if refresh else '')
    camera = value(_ATTR_KEYS['rear_camera'], 'Résolution de l’appareil photo arrière (Multiple)', 'Résolution de l\'appareil photo arrière (Multiple)',
                   'Rear Camera - Resolution (Multiple)', 'Rear Camera - Resolution')
    sensors = re.findall(r'(?<![\d,.])(\d+(?:[,.]\d+)?)\s*MP', camera, re.I)
    if sensors:
        specs['photo'] = ' + '.join(format(float(sensor.replace(',', '.')), 'g') for sensor in sensors) + ' Mpx (arrière)'
    battery = value(_ATTR_KEYS['battery'], 'Capacité de la batterie (mAh, Typique)', 'Battery Capacity (mAh, Typical)', 'Battery Capacity (mAh)')
    if re.fullmatch(r'\d[\d,. ]*', battery):
        specs['batterie'] = f'{int(re.sub(r"\D", "", battery)):,}'.replace(',', ' ') + ' mAh'
    network = value(_ATTR_KEYS['network'], 'Infra', 'Network/Bearer', 'Network')
    if re.search(r'\b5G\b', network):
        specs['reseau'] = '5G'
    elif re.search(r'\b(?:4G|LTE)\b', network):
        specs['reseau'] = '4G LTE'
    cpu = value('Chipset', 'Processor Name', 'Nom du processeur')
    if cpu:
        specs['processeur'] = cpu
    if len(specs) < 3 or 'reseau' not in specs:
        return None
    return {'capacity': capacity, 'specs': specs}


async def _read_model(client, site, model):
    key = site, model['sku']
    cache = _SPEC_CACHE.get(key)
    if cache and time.monotonic() - cache[0] < cache[1]:
        return cache[2]
    try:
        data = await _json(client, API + 'spec/detail/', {'modelList': model['sku'], 'siteCode': site})
        parsed = parse_product_specs(data, model)
        _SPEC_CACHE[key] = time.monotonic(), 21600 if parsed else 60, parsed
        return parsed
    except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError):
        _SPEC_CACHE[key] = time.monotonic(), 60, None
        return None


def _family_network(family):
    names = family['name'] + ' ' + family['english_name']
    return '5G' if re.search(r'\b5G\b', names, re.I) else '4G' if re.search(r'\b(?:4G|LTE)\b', names, re.I) else None


def _base_name(family):
    return re.sub(r'(?:4G|5G)$', '', _key(family['name']))


def _item(family, model, parsed, families, exact_sku=False):
    network = '5G' if parsed['specs']['reseau'] == '5G' else '4G'
    named_network = _family_network(family)
    if named_network and network != named_network:
        return None
    capacities = sorted({entry['capacity'] for entry in family['models'] if entry.get('capacity')},
                        key=lambda text: int(text.split()[0]) * (1024 if text.endswith('To') else 1))
    if parsed['capacity'] not in capacities:
        capacities.append(parsed['capacity'])
    siblings = [other for other in families if _base_name(other) == _base_name(family)]
    complete_index = all(_INDEX_CACHE.get(site, (None, None, None))[2] is not None for site in SITES)
    has_4g = (not complete_index or
              any(_family_network(other) == '4G' or not _key(other['name']).endswith('5G') for other in siblings))
    name = family['name']
    bare = re.sub(r'\s*(?:4G|5G)$', '', name, flags=re.I)
    aliases = [name, re.sub(r'^Galaxy\s+', '', name, flags=re.I)]
    if network == '4G':
        aliases += [bare + ' 4G', re.sub(r'^Galaxy\s+', '', bare, flags=re.I) + ' 4G']
    elif not has_4g:
        aliases += [bare, re.sub(r'^Galaxy\s+', '', bare, flags=re.I)]
    references = sorted({ref for entry in family['models'] for ref in (entry['sku'], entry['family_ref']) if ref})
    # One exact regional product URL per documented capacity, rather than
    # presenting a 128 GB product page as the sole proof for a 256 GB option.
    source_models = [model]
    source_capacities = {parsed['capacity']}
    if not exact_sku:
        for entry in family['models']:
            if entry.get('capacity') and entry['capacity'] not in source_capacities:
                source_models.append(entry)
                source_capacities.add(entry['capacity'])
    sources = [{'title': 'Samsung — ' + name + ' ' + (entry.get('capacity') or parsed['capacity']) + ' : caractéristiques',
                'url': entry['url']} for entry in source_models]
    return {'brand': 'SAMSUNG', 'model': name, 'aliases': list(dict.fromkeys(aliases)),
            'manufacturer_references': references, 'capacities': [parsed['capacity']] if exact_sku else capacities,
            'sku_capacities': {entry['sku']: entry['capacity'] for entry in family['models'] if entry.get('capacity')},
            'specs': parsed['specs'], 'sources': sources,
            'source_region': family['site']}


async def lookup_product_phone(client, query):
    """Return a catalogue item or None; identity never comes from a search snippet."""
    parsed_query = _query(query)
    if not parsed_query:
        return None
    identity, network, requested, is_sku = parsed_query
    async def lookup():
        def candidates_for(families):
            candidates = []
            for family in families:
                exact = _key(family['name']) == identity
                base = _base_name(family) == identity
                if is_sku:
                    if any(identity in (_key(entry['sku']), _key(entry['family_ref'])) for entry in family['models']):
                        candidates.append(family)
                elif base and (network is not None or exact):
                    candidates.append(family)
            if not candidates and not network and not is_sku:
                if any(_INDEX_CACHE.get(site, (None, None, None))[2] is None for site in SITES):
                    return []
                candidates = [family for family in families if _base_name(family) == identity]
            candidates = [family for family in candidates if not network or not _family_network(family) or _family_network(family) == network]
            if requested and not is_sku:
                candidates.sort(key=lambda family: not any(model.get('capacity') == requested for model in family['models']))
            return candidates

        families = await list_product_families(client)
        for attempt in range(2):
            candidates = candidates_for(families)
            stale = any(time.monotonic() - entry[0] > 60 for site in SITES if (entry := _INDEX_CACHE.get(site)))
            missing_capacity = requested and not any(model.get('capacity') == requested for family in candidates for model in family['models'])
            if attempt == 0 and stale and (not candidates or missing_capacity):
                families = await list_product_families(client, force=True)
                continue
            for family in candidates:
                models = family['models']
                if is_sku:
                    models = [model for model in models if identity in (_key(model['sku']), _key(model['family_ref']))]
                elif requested:
                    models = sorted(models, key=lambda model: model.get('capacity') != requested)
                if not models:
                    continue
                model = models[0]
                spec = await _read_model(client, family['site'], model)
                if not spec or network and not spec['specs']['reseau'].startswith(network):
                    continue
                exact_sku = is_sku and identity == _key(model['sku'])
                return _item(family, model, spec, families, exact_sku=exact_sku)
            if attempt == 0 and stale:
                families = await list_product_families(client, force=True)
            else:
                break
        return None
    try:
        return await asyncio.wait_for(lookup(), timeout=6)
    except (asyncio.TimeoutError, httpx.HTTPError, ValueError, TypeError):
        return None


async def collect_product_phones(client, existing: list[dict]):
    """Read up to 100 exact models, deduplicating regional families first."""
    result = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': True, 'errors': []}
    old = {_key(item.get('model', '')) for item in existing if isinstance(item, dict)}
    found = {}
    semaphore = asyncio.Semaphore(4)
    async def collect():
        groups = await asyncio.gather(*(_listing(client, site) for site in SITES))
        result['discovery_ok'] = all(group is not None for group in groups)
        for site, group in zip(SITES, groups):
            if group is None:
                result['errors'].append('Samsung finder unavailable: ' + site)
        families = [family for group in groups if group for family in group]
        regional = {}
        for family in families:
            regional.setdefault(_key(family['name']), []).append(family)
        unique = list(regional.values())[:100]
        async def read(options):
            async with semaphore:
                result['checked'] += 1
                for family in options:
                    model = family['models'][0]
                    parsed = await _read_model(client, family['site'], model)
                    item = _item(family, model, parsed, families) if parsed else None
                    if not item:
                        result['errors'].append('Samsung specifications unavailable: ' + model['sku'])
                        continue
                    key = _key(item['model'])
                    found[key] = item
                    return
        await asyncio.gather(*(read(options) for options in unique))
    try:
        await asyncio.wait_for(collect(), timeout=180)
    except asyncio.TimeoutError:
        result['discovery_ok'] = False
        result['errors'].append('Samsung product collection timed out; partial results retained')
    result['phones'] = [found[key] for key in sorted(found)]
    result['discovered'] = len(set(found) - old)
    return result

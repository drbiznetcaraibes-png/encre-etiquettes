"""Read Apple catalogue updates without writing files or inventing product facts."""
from __future__ import annotations

import asyncio
import re
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from official_phones import APPLE_INDEX, _identity, _plain, parse_apple_sheet

MAX_MODELS = 128
MAX_CONCURRENCY = 4
MAX_DOCUMENT_BYTES = 2_000_000
COLLECTION_TIMEOUT = 180
PAGE_TIMEOUT = 15


def _source_url(value):
    """Only Apple support article IDs are fetchable; always request French."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlparse(value)
        if (parsed.scheme != 'https' or parsed.hostname != 'support.apple.com'
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            return None
    except ValueError:
        return None
    article = re.fullmatch(r'/(?:fr-fr/)?(\d{1,8})/?', parsed.path)
    if article:
        return 'https://support.apple.com/fr-fr/' + article.group(1)
    legacy = re.fullmatch(r'/kb/(SP\d{1,6})/?', parsed.path)
    if legacy:
        return 'https://support.apple.com/kb/' + legacy.group(1) + '?locale=fr_FR'
    return None


def _ascii(value):
    return unicodedata.normalize('NFKD', _plain(value)).encode('ascii', 'ignore').decode()


def _se_year(name, url=''):
    text = _ascii(name).upper()
    if not re.match(r'^IPHONE\s*SE\b', text):
        return None
    year = re.search(r'\b(2016|2020|2022)\b', text)
    if year:
        return year.group(1)
    generation = re.search(r'\b([123])(?:RE|E|ERE|EME)?\s*GENERATION\b', text)
    if generation:
        return {'1': '2016', '2': '2020', '3': '2022'}[generation.group(1)]
    # Apple's original SE sheet has no generation in its H1.
    if re.search(r'/(?:112005|SP738)(?:[/?]|$)', url):
        return '2016'
    return None


def _model_key(name, url=''):
    year = _se_year(name, url)
    return 'IPHONESE' + year if year else _identity(name)


def _expected_name(phone):
    name = _plain(phone.get('model', ''))
    if re.fullmatch(r'iPhone\s*SE\s*\((?:2016|2020|2022)\)', name, re.I):
        year = _se_year(name)
        return {'2016': 'iPhone SE', '2020': 'iPhone SE (2e génération)',
                '2022': 'iPhone SE (3e génération)'}[year]
    return name


def _discover(document):
    """Discover all named iPhones, including names absent from the local catalogue."""
    soup = BeautifulSoup(document, 'html.parser')
    found = {}
    for anchor in soup.find_all('a', href=True):
        label = _plain(anchor.get_text(' ', strip=True))
        if 'caracteristiques techniques' not in _ascii(label).casefold():
            continue
        match = re.search(r'\biPhone(?:\s|$)', label, re.I)
        if not match:
            continue
        name = _plain(label[match.start():]).rstrip('. :')
        if len(name) > 100 or not re.fullmatch(r'iPhone(?:\s+[\w\s()+.-]+)?', name, re.I):
            continue
        url = _source_url(urljoin(APPLE_INDEX, anchor['href']))
        if url:
            found.setdefault(_model_key(name, url), {'name': name, 'url': url})
    return found


async def _read(client, url):
    url = _source_url(url)
    if not url:
        raise ValueError('URL de fiche Apple non autorisée')
    for _ in range(4):
        response = await client.get(url, timeout=httpx.Timeout(8, connect=3), follow_redirects=False)
        if response.is_redirect:
            target = _source_url(urljoin(url, response.headers.get('location', '')))
            if not target:
                raise ValueError('Redirection hors des fiches Apple autorisées')
            url = target
            continue
        response.raise_for_status()
        if len(response.content) > MAX_DOCUMENT_BYTES:
            raise ValueError('Fiche Apple trop volumineuse')
        return response.text, url
    raise ValueError('Trop de redirections Apple')


def _catalogue_phone(document, expected_name, url):
    phone = parse_apple_sheet(document, _identity(expected_name), url)
    if not phone:
        raise ValueError('Titre/modèle différent ou caractéristiques insuffisantes')
    if (len(phone.get('specs', {})) < 3 or not phone.get('capacities')
            or not all(re.fullmatch(r'[1-9]\d* (?:Go|To)', value) for value in phone['capacities'])):
        raise ValueError('Caractéristiques ou capacités Apple invalides')
    original_name = phone['model']
    year = _se_year(original_name, url)
    if year:
        generation = {'2016': '1', '2020': '2', '2022': '3'}[year]
        suffix = '1re' if generation == '1' else generation + 'e'
        phone['model'] = f'iPhone SE ({year})'
        aliases = [f'iPhone SE {year}', f'iPhone SE {generation}', f'iPhone SE ({suffix} génération)']
    else:
        aliases = [original_name]
    if phone['specs'].get('reseau') == '5G':
        aliases += [alias + ' 5G' for alias in list(aliases)]
    phone['aliases'] = aliases
    phone['manufacturer_references'] = []
    return phone


async def collect(client, existing: list[dict]) -> dict:
    """Return validated updates only; the caller merges and preserves old records.

    ``checked`` counts attempted product sheets, ``discovered`` counts new index
    entries relative to existing records. An unavailable/truncated/empty index
    always returns discovery_ok=False, even when known sheets refresh correctly.
    """
    deadline = time.monotonic() + COLLECTION_TIMEOUT
    result = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': False, 'errors': []}
    targets = {}
    for phone in existing:
        if not isinstance(phone, dict) or phone.get('brand') != 'APPLE' or not phone.get('model'):
            continue
        urls = [_source_url(source.get('url')) for source in phone.get('sources', []) if isinstance(source, dict)]
        url = next((url for url in urls if url), None)
        if url:
            targets.setdefault(_model_key(phone['model'], url), {'name': _expected_name(phone), 'url': url})
        else:
            result['errors'].append(f"{phone['model']} : aucune URL Apple officielle utilisable")
    known = set(targets)
    try:
        document, _ = await asyncio.wait_for(_read(client, APPLE_INDEX), timeout=min(PAGE_TIMEOUT, COLLECTION_TIMEOUT))
        found = _discover(document)
        if not found:
            raise ValueError('Index Apple sans lien de caractéristiques iPhone reconnu')
        result['discovery_ok'] = True
        result['discovered'] = len(set(found) - known)
        targets.update(found)
    except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError) as exc:
        result['errors'].append(f'Index Apple : {type(exc).__name__}: {exc}')
    if len(targets) > MAX_MODELS:
        result['discovery_ok'] = False
        result['errors'].append(f'Apple : limite de {MAX_MODELS} fiches atteinte ({len(targets)} disponibles)')
        targets = dict(list(targets.items())[:MAX_MODELS])

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def read_target(target):
        async with semaphore:
            result['checked'] += 1
            try:
                document, url = await asyncio.wait_for(_read(client, target['url']), timeout=PAGE_TIMEOUT)
                return _catalogue_phone(document, target['name'], url), None
            except (httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError, AttributeError) as exc:
                return None, f"{target['name']} : {type(exc).__name__}: {exc}"

    tasks = [(target, asyncio.create_task(read_target(target))) for target in targets.values()]
    if not tasks:
        return result
    _, pending = await asyncio.wait([task for _, task in tasks], timeout=max(0, deadline - time.monotonic()))
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
        result['errors'].append('Apple : délai global de collecte dépassé ; anciennes fiches à conserver')
    phones = {}
    for target, task in tasks:
        if task.cancelled():
            result['errors'].append(target['name'] + ' : collecte interrompue avant validation')
            continue
        phone, error = task.result()
        if error:
            result['errors'].append(error)
        elif phone:
            phones[_model_key(phone['model'], target['url'])] = phone
    result['phones'] = list(phones.values())
    return result

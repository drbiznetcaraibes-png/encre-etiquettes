"""Discover and refresh Xiaomi phones from official France/Global listings.

This collector returns newly fetched facts only. The caller owns merging,
validation, persistence and backup; a failed discovery never erases old records.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

from xiaomi_phones import _capacity, _identity, _plain, _section, parse_xiaomi_sheet

INDEX_URLS = (
    'https://www.mi.com/fr/phone/',
    'https://www.mi.com/fr/',
    'https://www.mi.com/global/',
    'https://www.mi.com/global/phone/',
    'https://www.mi.com/fr/product-list/phone/xiaomi/',
    'https://www.mi.com/fr/product-list/phone/redmi/',
    'https://www.mi.com/fr/product-list/phone/poco/',
    'https://www.mi.com/global/phone/redmi/redmi-list/',
)
MAX_PAGES = 150
CONCURRENCY = 4
TOTAL_TIMEOUT = 180
MAX_BYTES = 2_000_000
_HOSTS = {'www.mi.com', 'mi.com'}
_PHONE_SLUG = re.compile(
    r'(?:xiaomi-(?:\d{1,2}[a-z]?|mix-(?:fold|flip)(?:-?\d{1,2})?)(?:-[a-z0-9]+)*'
    r'|redmi-(?:note-)?(?:\d{1,2}[a-z]?|a\d{1,2}[a-z]?)(?:-[a-z0-9]+)*'
    r'|poco-[xfmc]\d{1,2}[a-z]?(?:-[a-z0-9]+)*)'
)
_PRODUCT_PATH = re.compile(r'/(fr|global)/product/([a-z0-9-]+)/(?:specs/?)?$')


def _safe_url(url):
    """Never send requests to credentials, another host, port, or region."""
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != 'https' or parsed.hostname not in _HOSTS
                or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not re.match(r'^/(?:fr|global)(?:/|$)', parsed.path)):
            return None
        return urlunsplit(('https', 'www.mi.com', parsed.path, '', ''))
    except (ValueError, TypeError):
        return None


def _product_url(url):
    url = _safe_url(url)
    if not url:
        return None
    path = urlsplit(url).path
    match = _PRODUCT_PATH.fullmatch(path if path.endswith('/') else path + '/')
    if not match or not _PHONE_SLUG.fullmatch(match.group(2)):
        return None
    return f'https://www.mi.com/{match.group(1)}/product/{match.group(2)}/specs/'


def _slug_identity(url):
    slug = _PRODUCT_PATH.fullmatch(urlsplit(url).path).group(2)
    # Xiaomi's older Leica campaign URLs identify the same named phone.
    slug = re.sub(r'-leica$', '', slug)
    return _identity(slug.replace('-plus', '+').replace('-', ' '))


def _listing_links(document, source_url):
    soup = BeautifulSoup(document, 'html.parser')
    title = _plain(soup.title.get_text(' ', strip=True)) if soup.title else ''
    if re.search(r'\b404\b|access denied|captcha|page introuvable', title, re.I):
        return []
    links = []
    for anchor in soup.find_all('a', href=True):
        url = _product_url(urljoin(source_url, anchor['href']))
        if url and url not in links:
            links.append(url)
    return links


def _parse(document, requested_url, final_url):
    soup = BeautifulSoup(document, 'html.parser')
    heading = soup.find('h1')
    if heading is None:
        return None
    for hidden in heading.select('[style*="display:none"], [style*="display: none"]'):
        hidden.decompose()
    model = re.split(r'\s+(?:Caractéristiques|Specifications?|Specs)\b',
                     _plain(heading.get_text(' ', strip=True)), flags=re.I)[0]
    actual = _identity(model)
    wanted = _slug_identity(requested_url)
    # The document names its own exact variant. A marketing URL can omit the
    # network suffix, but never a generation or Pro/Plus/Max/Ultra distinction.
    compatible = (actual == wanted or actual == wanted + '5G'
                  or (wanted.endswith(('4G', '5G')) and actual == wanted[:-2]))
    if not compatible:
        return None
    item = parse_xiaomi_sheet(document, actual, final_url)
    if not item or len(item.get('specs', {})) < 3 or not item.get('capacities'):
        return None
    requested_network = wanted[-2:] if wanted.endswith(('4G', '5G')) else None
    if requested_network and item['specs'].get('reseau') != requested_network:
        return None
    if actual == wanted + '5G' and item['specs'].get('reseau') != '5G':
        return None
    # Prefer explicit RAM+storage combinations over secondary protocol notes
    # such as "UFS 2.2 (128 Go)" that do not identify an offered phone variant.
    memory = _section(soup, {'stockage et ram', 'stockage et mémoire ram', 'storage & ram',
                            'storage and ram', 'mémoire et stockage', 'ram et stockage',
                            'ram & stockage', 'mémoire', 'memory'})
    paired = []
    for match in re.finditer(r'\b(?:2|3|4|6|8|12|16|24|32)\s*(?:Go|GB)?\s*[+/,]\s*(\d+)\s*(Go|GB|To|TB)\b', memory, re.I):
        value = _capacity(match.group(1), match.group(2))
        if value in item['capacities'] and value not in paired:
            paired.append(value)
    if paired:
        item['capacities'] = paired
    item['aliases'] = [item['model']]
    if item['specs'].get('reseau') in ('4G', '5G') and not actual.endswith(('4G', '5G')):
        item['aliases'].append(item['model'] + ' ' + item['specs']['reseau'])
    return item


async def collect(client, existing):
    """Fetch official listings, discover new models and refresh known sources.

    ``discovered`` counts valid returned phones not present in ``existing``.
    ``checked`` counts attempted specification pages, including failed fetches.
    ``discovery_ok`` requires a fetched official listing with phone links; merely
    refreshing the old catalogue cannot make discovery successful.
    """
    result = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': False, 'errors': []}
    semaphore = asyncio.Semaphore(CONCURRENCY)
    start = time.monotonic()
    requests_used = 0
    old_by_identity = {_identity(phone.get('model')): phone for phone in existing
                       if isinstance(phone, dict) and phone.get('model')}
    found = {}

    def error(message):
        if len(result['errors']) < 100:
            result['errors'].append(message)

    async def fetch(url):
        nonlocal requests_used
        async with semaphore:
            current = _safe_url(url)
            if not current:
                error('URL constructeur refusée : ' + str(url)[:180])
                return None
            for _ in range(4):
                if requests_used >= MAX_PAGES or time.monotonic() - start >= TOTAL_TIMEOUT:
                    error('Budget de collecte Xiaomi atteint.')
                    return None
                requests_used += 1
                try:
                    # Do not follow a redirect until its destination is checked.
                    response = await client.get(current, follow_redirects=False,
                                                timeout=httpx.Timeout(8, connect=3))
                    if response.status_code in (301, 302, 303, 307, 308):
                        target = _safe_url(urljoin(current, response.headers.get('location', '')))
                        if not target:
                            error('Redirection constructeur refusée : ' + current)
                            return None
                        current = target
                        continue
                    response.raise_for_status()
                    if len(response.content) > MAX_BYTES:
                        error('Page constructeur trop volumineuse : ' + current)
                        return None
                    if not _safe_url(str(response.url)):
                        error('Réponse hors domaine constructeur refusée : ' + current)
                        return None
                    if '/errors/' in urlsplit(current).path:
                        error('Page constructeur introuvable : ' + current)
                        return None
                    return response.text, current
                except (httpx.HTTPError, ValueError) as exc:
                    error(type(exc).__name__ + ' : ' + current)
                    return None
            error('Trop de redirections : ' + url)
            return None

    async def run():
        discovered_urls = []
        listings = await asyncio.gather(*(fetch(url) for url in INDEX_URLS))
        for listing in listings:
            if listing:
                links = _listing_links(*listing)
                if links:
                    result['discovery_ok'] = True
                for url in links:
                    if url not in discovered_urls:
                        discovered_urls.append(url)
        if not result['discovery_ok']:
            error('Découverte Xiaomi indisponible : aucun index officiel exploitable.')
        known_urls = []
        for phone in existing:
            if not isinstance(phone, dict):
                continue
            for source in phone.get('sources', []):
                url = _product_url(source.get('url', '')) if isinstance(source, dict) else None
                if url and url not in known_urls:
                    known_urls.append(url)
        # New model URLs lead the queue so a bounded run is not monopolised by
        # the historical catalogue. Prefer France before Global per identity.
        urls = list(dict.fromkeys(discovered_urls + known_urls))
        urls.sort(key=lambda url: (_slug_identity(url) in old_by_identity,
                                  '/fr/' not in url))
        urls = urls[:max(0, MAX_PAGES - requests_used)]

        async def phone(url):
            result['checked'] += 1
            page = await fetch(url)
            if not page:
                return
            item = _parse(page[0], url, page[1])
            if not item:
                error('Fiche Xiaomi non validée : ' + url)
                return
            identity = _identity(item['model'])
            previous = old_by_identity.get(identity)
            if previous:
                item['aliases'] = list(dict.fromkeys(item['aliases'] + deepcopy(previous.get('aliases', []))))
            saved = found.get(identity)
            if saved is None or ('/fr/' in item['sources'][0]['url'] and '/fr/' not in saved['sources'][0]['url']):
                found[identity] = item

        await asyncio.gather(*(phone(url) for url in urls))

    try:
        await asyncio.wait_for(run(), timeout=TOTAL_TIMEOUT)
    except asyncio.TimeoutError:
        error('Délai global de collecte Xiaomi dépassé.')
    result['phones'] = list(found.values())
    result['discovered'] = sum(_identity(phone['model']) not in old_by_identity for phone in result['phones'])
    return result

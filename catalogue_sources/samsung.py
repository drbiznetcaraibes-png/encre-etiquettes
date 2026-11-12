"""Bounded weekly Samsung discovery and refresh, with no catalogue writes.

Read the public sitemap and current Galaxy article indexes, then extract exact
model columns from Samsung's own specification tables. The caller owns merging
these validated facts with its previous catalogue when a source is unavailable.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import re
from urllib.parse import urljoin, urlparse, urlunparse
import xml.etree.ElementTree as ET

import httpx
from bs4 import BeautifulSoup

from samsung_phones import _plain, _identity, _query, parse_samsung_sheet

SITEMAP = 'https://www.samsungmobilepress.com/sitemap.xml'
INDEXES = tuple('https://www.samsungmobilepress.com/products/galaxy-' + family for family in ('s', 'a', 'z'))
HOSTS = frozenset(('www.samsungmobilepress.com', 'samsungmobilepress.com', 'api.samsungmobilepress.com'))
MAX_PHONES = 120
MAX_DOCUMENTS = 120
CONCURRENCY = 4
TIME_LIMIT = 180
MAX_BYTES = 4_000_000


def _allowed(url):
    try:
        parsed = urlparse(str(url))
        return (parsed.scheme == 'https' and parsed.hostname in HOSTS
                and parsed.port in (None, 443) and parsed.username is None and parsed.password is None)
    except ValueError:
        return False


def _article(url, base):
    try:
        absolute = urljoin(base, str(url))
        parsed = urlparse(absolute)
    except ValueError:
        return None
    if not _allowed(absolute) or not re.fullmatch(r'/(?:articles|press-releases|techspecs|tech-specs)/[^/]+/?', parsed.path):
        return None
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip('/'), '', '', ''))


def _model_key(model):
    return _identity(re.sub(r'^(?:Samsung\s*)?Galaxy\s*', '', str(model), flags=re.I))


def _headers(document):
    """Names come from table headings, never article prose or search snippets."""
    soup = BeautifulSoup(document, 'html.parser')
    names = []
    for table in soup.find_all('table'):
        first = table.find('tr')
        if first is None:
            continue
        for cell in first.find_all(['th', 'td'], recursive=False):
            name = _plain(cell.get_text(' ', strip=True))
            if name.lower().startswith('galaxy ') and _query(name)[0] and name not in names:
                names.append(name)
    return names


def _priority(url):
    path = urlparse(url).path.lower()
    # Model numbers are for ordering downloads only. Facts and variants still
    # require an exact manufacturer's table header and a successful parser.
    generations = [int(number) for pair in re.findall(r'galaxy-(?:s|a)(\d{2})|galaxy-z-(?:fold|flip)(\d{1,2})', path)
                   for number in pair if number]
    launch = any(word in path for word in ('unveil', 'introduc', 'launch', 'meet-', 'perfected', 'specification'))
    return (0 if launch else 1, -max(generations, default=0), path)


async def _collect_press(client, existing: list[dict]):
    """Return verified phones and collection diagnostics within 180 seconds.

    ``checked`` counts exact model tables attempted, capped at 120;
    ``discovered`` counts successfully read models absent from ``existing``.
    Index failures make ``discovery_ok`` false even if old sources still refresh.
    """
    result = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': True, 'errors': []}
    semaphore = asyncio.Semaphore(CONCURRENCY)
    old_by_key = {_model_key(item.get('model', '')): item for item in existing
                  if isinstance(item, dict) and str(item.get('brand', '')).upper() == 'SAMSUNG'}
    old_sources = {}
    for item in old_by_key.values():
        for source in item.get('sources', []):
            url = _article(source.get('url', ''), SITEMAP) if isinstance(source, dict) else None
            if url:
                old_sources.setdefault(url, []).append(item['model'])
    found = {}
    found_rank = {}
    ranks = {}

    async def fetch(url):
        async with semaphore:
            for _ in range(4):
                if not _allowed(url):
                    raise ValueError('source domain rejected')
                response = await client.get(url, timeout=httpx.Timeout(12, connect=4), follow_redirects=False)
                if response.is_redirect:
                    location = response.headers.get('location')
                    if not location:
                        raise ValueError('redirect without location')
                    url = urljoin(url, location)
                    if not _allowed(url):
                        raise ValueError('redirect domain rejected')
                    continue
                response.raise_for_status()
                if not _allowed(response.url):
                    raise ValueError('response domain rejected')
                if len(response.content) > MAX_BYTES:
                    raise ValueError('document exceeds size limit')
                return response.text, str(response.url)
            raise ValueError('too many redirects')

    async def index(url, sitemap=False):
        try:
            document, final_url = await fetch(url)
            links = []
            if sitemap:
                root = ET.fromstring(document)
                if root.tag.rsplit('}', 1)[-1] != 'urlset':
                    raise ValueError('expected sitemap urlset')
                raw_links = [node.text or '' for node in root.iter() if node.tag.rsplit('}', 1)[-1] == 'loc']
                for value in raw_links:
                    article = _article(value, final_url)
                    if article and re.search(r'galaxy-(?:[sa]\d{2}|z-(?:fold|flip)\d{1,2})', article, re.I):
                        links.append(article)
            else:
                soup = BeautifulSoup(document, 'html.parser')
                for anchor in soup.find_all('a', href=True):
                    value = urljoin(final_url, anchor['href'])
                    article = _article(value, final_url)
                    if article:
                        links.append(article)
                    elif '/articles/' in urlparse(value).path and not _allowed(value):
                        result['errors'].append('Rejected article domain from ' + url)
            if not links:
                raise ValueError('index contains no usable official article links')
            return list(dict.fromkeys(links))
        except (httpx.HTTPError, ValueError, TypeError, ET.ParseError) as exc:
            result['discovery_ok'] = False
            result['errors'].append(f'Index unavailable: {url} ({type(exc).__name__}: {str(exc)[:160]})')
            return []

    async def read(url):
        if result['checked'] >= MAX_PHONES:
            return
        try:
            document, final_url = await fetch(url)
            names = list(dict.fromkeys(_headers(document) + old_sources.get(url, [])))
            for name in names:
                if result['checked'] >= MAX_PHONES:
                    break
                result['checked'] += 1
                item = parse_samsung_sheet(document, name, final_url)
                if not item:
                    result['errors'].append(f'Unparseable exact Galaxy table: {name} ({url})')
                    continue
                key = _model_key(item['model'])
                if not key or not item.get('capacities') or len(item.get('specs', {})) < 3:
                    result['errors'].append(f'Incomplete Galaxy specifications: {name} ({url})')
                    continue
                previous = old_by_key.get(key, {})
                merged = deepcopy(previous)
                merged.update(item)
                aliases = list(previous.get('aliases', [])) + [item['model'], re.sub(r'^Galaxy\s+', '', item['model'], flags=re.I)]
                merged['aliases'] = list(dict.fromkeys(aliases))
                # Prefer the earliest selected specification source over later
                # marketing pages with fewer facts for the same exact model.
                rank = ranks[url]
                if (key not in found or len(merged['specs']) > len(found[key]['specs'])
                        or (len(merged['specs']) == len(found[key]['specs']) and rank < found_rank[key])):
                    found[key] = merged
                    found_rank[key] = rank
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError) as exc:
            result['errors'].append(f'Article unavailable: {url} ({type(exc).__name__}: {str(exc)[:160]})')

    async def run():
        indexes = await asyncio.gather(index(SITEMAP, sitemap=True), *(index(url) for url in INDEXES))
        # Current category lists catch newly named releases. The sitemap reaches
        # beyond the six articles shown on a category's first page.
        current = [url for links in indexes[1:] for url in links]
        sitemap_links = sorted(indexes[0], key=_priority)
        urls = list(dict.fromkeys(current + list(old_sources) + sitemap_links))[:MAX_DOCUMENTS]
        ranks.update({url: index for index, url in enumerate(urls)})
        await asyncio.gather(*(read(url) for url in urls))

    try:
        await asyncio.wait_for(run(), timeout=TIME_LIMIT)
    except asyncio.TimeoutError:
        result['discovery_ok'] = False
        result['errors'].append('Samsung collection exceeded the 180-second time limit; partial verified results retained')
    result['phones'] = [found[key] for key in sorted(found)]
    result['discovered'] = len(set(found) - set(old_by_key))
    return result


async def collect(client, existing: list[dict]):
    """Combine storefront discovery with the retained official press archive."""
    from samsung_products import collect_product_phones
    results = await asyncio.gather(
        collect_product_phones(client, existing), _collect_press(client, existing),
        return_exceptions=True)
    merged = {'phones': [], 'checked': 0, 'discovered': 0, 'discovery_ok': True, 'errors': []}
    phones = {}
    # Products have regional SKU and storage evidence; prefer them when both
    # official sources describe the same exact variant.
    for result in reversed(results):
        if isinstance(result, BaseException):
            merged['discovery_ok'] = False
            merged['errors'].append('Samsung source collection unavailable')
            continue
        merged['checked'] += result['checked']
        merged['discovery_ok'] = merged['discovery_ok'] and result['discovery_ok']
        merged['errors'].extend(result['errors'])
        for phone in result['phones']:
            phones[_model_key(phone['model'])] = phone
    # A 5G phone's bare alias must not hide a separately documented LTE phone.
    four_g = {_model_key(phone['model']).removesuffix('4G') for phone in phones.values()
              if phone.get('specs', {}).get('reseau', '').startswith('4G')}
    for key, phone in phones.items():
        if phone.get('specs', {}).get('reseau') == '5G' and key.removesuffix('5G') in four_g:
            phone['aliases'] = [alias for alias in phone.get('aliases', [])
                                if _model_key(alias) != key.removesuffix('5G')]
    old_keys = {_model_key(phone.get('model', '')) for phone in existing}
    merged['phones'] = [phones[key] for key in sorted(phones)]
    merged['discovered'] = len(set(phones) - old_keys)
    return merged

"""Bounded, cached suggestions from verified phone records in the shared database.

Only model names and storage evidence cross the database boundary. This module
neither reads credentials nor calls an AI provider; ``Store`` owns access.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field
import re
from time import monotonic
import unicodedata
from weakref import WeakKeyDictionary


SUCCESS_TTL_SECONDS = 300
FAILURE_TTL_SECONDS = 60
FETCH_TIMEOUT_SECONDS = 2
PAGE_SIZE = 1000
MAX_PAGES = 3

_CAPACITY = re.compile(r'(16|32|64|128|256|512|1024|2048)\s*(GO|GB)|(1|2)\s*(TO|TB)', re.I)


@dataclass
class _CacheEntry:
    models: list[dict] = field(default_factory=list)
    expires_at: float = 0.0
    generation: int = 0
    task: asyncio.Task | None = None


_CACHE: WeakKeyDictionary = WeakKeyDictionary()


def _text(value, max_length):
    if not isinstance(value, str):
        return None
    text = re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value)).strip()
    if not text or len(text) > max_length or not text.isprintable() or any(c in text for c in '<>'):
        return None
    return text


def _capacity(value):
    text = _text(value, 20)
    match = _CAPACITY.fullmatch(text or '')
    if not match:
        return None
    size = int(match.group(1)) if match.group(1) else int(match.group(3)) * 1024
    return f'{size // 1024} To' if size >= 1024 else f'{size} Go'


def _capacity_size(value):
    number, unit = value.split()
    return int(number) * (1024 if unit == 'To' else 1)


def _models_from_rows(rows):
    grouped = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        brand = _text(row.get('brand'), 35)
        model = _text(row.get('model'), 105)
        if not brand or not model:
            continue
        brand = brand.upper()
        # Exact model names are deliberate: neither radio variants nor suffixes
        # such as Pro, Pro Max and '+' may be collapsed in a suggestion list.
        key = (brand, model)
        grouped.setdefault(key, set())
        documented = row.get('available_capacities')
        if isinstance(documented, list):
            for value in documented:
                capacity = _capacity(value)
                if capacity:
                    grouped[key].add(capacity)
        # ``capacity`` stores the user's requested variant, even when lookup
        # could not confirm it. Only a JSON true in the verified result permits
        # treating that value as a documented storage option.
        if row.get('confirmed') is True:
            capacity = _capacity(row.get('capacity'))
            if capacity:
                grouped[key].add(capacity)
    return [
        {'brand': brand, 'model': model,
         'available_capacities': sorted(capacities, key=_capacity_size), '_aliases': []}
        for (brand, model), capacities in sorted(grouped.items())
    ]


async def _fetch_models(store):
    rows = []
    for page_number in range(MAX_PAGES):
        page = await store.request('GET', 'phone_specs', params={
            'select': ('brand,model,capacity,confirmed:result->capacity_confirmed,'
                       'available_capacities:result->available_capacities'),
            'verification_status': 'eq.vérifié',
            'order': 'brand.asc,model.asc,id.asc',
            'limit': str(PAGE_SIZE),
            'offset': str(page_number * PAGE_SIZE),
        })
        if not isinstance(page, list):
            raise ValueError('Invalid model list')
        rows.extend(page[:PAGE_SIZE])
        if len(page) < PAGE_SIZE:
            break
    return _models_from_rows(rows)


async def _refresh(store, entry, generation):
    try:
        try:
            # This is a total budget, including every page and normalization.
            async with asyncio.timeout(FETCH_TIMEOUT_SECONDS):
                models = await _fetch_models(store)
        except Exception:
            # The existing catalogue stays useful during an outage. Do not log
            # raw upstream errors, and avoid repeating a failing read per keypress.
            if generation == entry.generation:
                entry.expires_at = monotonic() + FAILURE_TTL_SECONDS
        else:
            if generation == entry.generation:
                entry.models = models
                entry.expires_at = monotonic() + SUCCESS_TTL_SECONDS
    finally:
        if entry.task is asyncio.current_task():
            entry.task = None


async def get_database_models(store) -> list[dict]:
    """Return verified model suggestions; unavailable storage yields stale data.

    Concurrent callers for the same configured Store share one read. A cancelled
    caller does not cancel that shared read for the remaining callers.
    """
    if not store.configured:
        return []
    entry = _CACHE.get(store)
    if entry is None:
        entry = _CacheEntry()
        _CACHE[store] = entry
    if monotonic() >= entry.expires_at:
        if entry.task is None:
            entry.task = asyncio.create_task(_refresh(store, entry, entry.generation))
        await asyncio.shield(entry.task)
    # Filtering/merging suggestions in the route cannot mutate the shared cache.
    return deepcopy(entry.models)


def invalidate_database_models_cache(store=None):
    """Expire one Store's suggestions (or all), preserving data for fallback.

    An in-flight read may finish, but cannot mark data fresh after invalidation.
    The next call after that read will load the newly saved records.
    """
    entries = list(_CACHE.values()) if store is None else [_CACHE.get(store)]
    for entry in entries:
        if entry is not None:
            entry.generation += 1
            entry.expires_at = 0.0

"""Read-only adapter from Supabase product_catalog to the label and poster forms."""
from typing import Literal
import re
import unicodedata

from storage import store

Category = Literal['tablet', 'console']
CATEGORY_FILTERS = {
    'tablet': 'in.(tablet,tablets,tablette,tablettes)',
    'console': 'in.(console,consoles)',
}

TABLET_SPECS = {
    'processeur': ('processeur', 'processor', 'chip', 'cpu'),
    'ecran': ('ecran', 'display', 'screen'),
    'photo': ('photo', 'camera', 'cameras'),
    'batterie': ('batterie', 'battery'),
    'reseau': ('reseau', 'network', 'connectivity', 'connectivite'),
    'ram': ('ram', 'memory', 'memoire'),
}
CONSOLE_SPECS = {
    'storage': ('storage', 'stockage', 'capacity', 'capacite'),
    'video': ('video', 'image', 'resolution', 'display'),
    'technology': ('technology', 'technologie', 'features'),
    'included': ('included', 'inclus', 'controller', 'manette'),
}


def clean_text(value, limit=180):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def search_key(value):
    ascii_text = unicodedata.normalize('NFKD', value.casefold())
    return re.sub(r'[^a-z0-9]', '', ascii_text.replace('+', 'plus'))


def first_text(row, *fields):
    return next((value for field in fields if (value := clean_text(row.get(field)))), '')


def tablet_capacities(raw):
    values = raw if isinstance(raw, list) else re.split(r'[,;/|]', clean_text(raw))
    capacities = set()
    for value in values:
        if type(value) is int:
            gb = value
        else:
            match = re.fullmatch(r'(\d{1,5})\s*(go|gb|to|tb)', clean_text(value), re.IGNORECASE)
            if not match:
                continue
            gb = int(match[1]) * (1024 if match[2].lower() in ('to', 'tb') else 1)
        if gb > 0:
            capacities.add(gb)
    return [f'{gb // 1024} To' if gb % 1024 == 0 else f'{gb} Go' for gb in sorted(capacities)]


def public_product(row, category):
    brand = first_text(row, 'brand', 'marque')
    model = first_text(row, 'model', 'name', 'modele', 'nom')
    if not brand or not model:
        return None
    reference = first_text(row, 'reference', 'manufacturer_reference', 'model_number', 'sku')
    variant = first_text(row, 'variant', 'edition')
    raw_specs = row.get('specs') or row.get('specifications') or row.get('characteristics')
    specs = dict(row)
    if isinstance(raw_specs, dict):
        specs.update(raw_specs)
    specs = {search_key(key): value for key, value in specs.items()}
    fields = TABLET_SPECS if category == 'tablet' else CONSOLE_SPECS
    facts = {key: first_text(specs, *names) for key, names in fields.items()}
    if category == 'tablet':
        raw_storage = (row.get('capacities') or row.get('available_capacities') or row.get('storage_options')
                       or row.get('storage_gb') or specs.get('storage') or specs.get('stockage')
                       or specs.get('capacity') or specs.get('capacite'))
        capacities = tablet_capacities(raw_storage)
        facts = {key: value for key, value in facts.items() if value}
    else:
        capacities = []
    return {
        'brand': brand, 'model': model, 'reference': reference, 'variant': variant,
        'specs': facts, 'available_capacities': capacities,
    }


async def search_products(category: Category, query: str):
    rows = await store.request('GET', 'product_catalog', params={'category': CATEGORY_FILTERS[category]})
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('Réponse du catalogue invalide.')
    needle = search_key(query)
    products = []
    for row in rows:
        product = public_product(row, category)
        if product is None:
            continue
        keys = ('brand', 'model', 'variant', 'reference')
        candidates = [*(product[key] for key in keys), f"{product['brand']} {product['model']} {product['variant']}"]
        if any(needle in search_key(candidate) for candidate in candidates):
            products.append(product)
    return sorted(products, key=lambda product: tuple(search_key(product[key]) for key in keys))

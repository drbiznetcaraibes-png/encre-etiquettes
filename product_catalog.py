"""Read-only adapter from Supabase product_catalog to the label and poster forms."""
import re
import unicodedata
from typing import Literal

from storage import store

Category = Literal['tablet', 'console']
CATALOG_COLUMNS = (
    'brand,model,manufacturer_references,available_capacities,connectivity_options,'
    'specs,variant_note,verification_status'
)

TABLET_SPECS = {
    'processeur': ('puce', 'processeur', 'processor', 'cpu'),
    'ecran': ('ecran', 'display', 'screen'),
    'photo': ('photo', 'appareilphoto', 'camera'),
    'batterie': ('batterie', 'battery'),
    'reseau': ('reseau', 'connectivite', 'connectivity'),
    'ram': ('ram', 'memoirevive'),
}
CONSOLE_PRIORITY = (
    ('stockage', 'storage'),
    ('image', 'resolution', 'affichage', 'video'),
    ('technologie', 'technologies', 'performance'),
    ('inclus', 'lecteur', 'manette', 'accessoires'),
)


def clean_text(value, limit=180):
    return ' '.join(value.split())[:limit] if isinstance(value, str) else ''


def search_key(value):
    text = unicodedata.normalize('NFKD', value.casefold()).replace('+', 'plus')
    return re.sub(r'[^a-z0-9]', '', text)


def text_array(values):
    return list(dict.fromkeys(value for item in values if (value := clean_text(item)))) if isinstance(values, list) else []


def tablet_capacities(values):
    capacities = set()
    for value in text_array(values):
        match = re.fullmatch(r'(\d{1,5})\s*(go|gb|to|tb)', value, re.IGNORECASE)
        if match and (size := int(match[1])) > 0:
            capacities.add(size * (1024 if match[2].lower() in ('to', 'tb') else 1))
    return [f'{gb // 1024} To' if gb % 1024 == 0 else f'{gb} Go' for gb in sorted(capacities)]


def tablet_specs(facts, row):
    specs = {}
    for field, aliases in TABLET_SPECS.items():
        if key := next((alias for alias in aliases if alias in facts), None):
            specs[field] = facts[key]['value']
    if 'reseau' not in specs and (options := text_array(row.get('connectivity_options'))):
        specs['reseau'] = ' / '.join(options)
    known_aliases = {alias for aliases in TABLET_SPECS.values() for alias in aliases}
    specs.update({fact['label']: fact['value'] for key, fact in facts.items() if key not in known_aliases})
    return specs


def console_specs(facts):
    # Prefer the four fields of the existing poster, then use other recorded facts.
    priority = [key for aliases in CONSOLE_PRIORITY if (key := next((a for a in aliases if a in facts), None))]
    return [facts[key] for key in dict.fromkeys([*priority, *facts])][:4]


def public_product(row, category, needle):
    brand, model, variant = (clean_text(row.get(key)) for key in ('brand', 'model', 'variant_note'))
    if not brand or not model:
        return None
    references = text_array(row.get('manufacturer_references'))
    matching = [ref for ref in references if needle in search_key(ref)] if needle else []
    candidates = (brand, model, variant, f'{brand} {model} {variant}')
    if not matching and not any(needle in search_key(text) for text in candidates):
        return None
    reference = next((ref for ref in matching if needle == search_key(ref)), '')
    if not reference and len(matching or references) == 1:
        reference = (matching or references)[0]

    raw_specs = row.get('specs')
    facts = {}
    if isinstance(raw_specs, dict):
        for label, raw_value in raw_specs.items():
            if (label := clean_text(label, 50)) and (value := clean_text(raw_value)):
                facts[search_key(label)] = {'label': label, 'value': value}

    return {
        'brand': brand,
        'model': model,
        'reference': reference,
        'variant': variant,
        'specs': tablet_specs(facts, row) if category == 'tablet' else console_specs(facts),
        'available_capacities': tablet_capacities(row.get('available_capacities')),
        'verification_status': clean_text(row.get('verification_status'), 30),
    }


async def search_products(category: Category, query: str):
    rows = await store.request('GET', 'product_catalog', params={
        'category': f'eq.{category}',
        'select': CATALOG_COLUMNS,
        'order': 'id.asc',
    })
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise RuntimeError('Réponse du catalogue invalide.')
    needle = search_key(query)
    products = [product for row in rows if (product := public_product(row, category, needle))]
    return sorted(products, key=lambda product: tuple(
        search_key(product[key]) for key in ('brand', 'model', 'variant', 'reference')
    ))

"""Manufacturer-only catalogue maintenance; dry run unless --apply is explicit.

No web-app import, AI call, schema change, catalogue JSON rewrite, or deployment.
Only successful collector outputs receive a new per-phone verification date.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from storage import Store

BASE = Path(__file__).resolve().parent
CATALOGUE_FILES = ('phone_catalog.json', 'apple_catalog.json', 'xiaomi_catalog.json', 'samsung_catalog.json', 'google_catalog.json')
BRANDS = ('APPLE', 'XIAOMI', 'SAMSUNG', 'GOOGLE')
SPEC_LABELS = {'ecran': 'Écran', 'photo': 'Photo', 'batterie': 'Batterie',
               'processeur': 'Puce', 'reseau': 'Réseau', 'ram': 'RAM'}
OFFICIAL_DOMAINS = {'APPLE': ('apple.com',), 'XIAOMI': ('mi.com', 'po.co'),
                    'SAMSUNG': ('samsung.com', 'samsungmobilepress.com'), 'GOOGLE': ('google.com', 'blog.google')}
BATCH_SIZE = 50
MAX_DATABASE_ROWS = 5000
DATABASE_PAGE_SIZE = 500


def _text(value, limit=160):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFKC', value).replace('\u00ad', '')
    value = re.sub(r'\s+', ' ', value).strip()
    if re.search(r'\b(?:sb_secret_|sb_publishable_|sk-proj-|sk-svcacct-|SUPABASE_(?:SECRET|SERVICE_ROLE)_KEY|OPENAI_API_KEY)', value, re.I):
        return ''
    return value if len(value) <= limit and not any(ord(char) < 32 for char in value) else ''


def model_identity(brand, model):
    """Match the application's canonical model identity, without importing server."""
    brand = _text(brand, 30).upper()
    model = _text(model, 100)
    if brand == 'SAMSUNG':
        model = re.sub(r'^(?:SAMSUNG\s*)?(?:GALAXY\s*)?', '', model, flags=re.I)
    elif brand == 'APPLE':
        model = re.sub(r'^APPLE\s*', '', model, flags=re.I)
        if re.match(r'^iPhone\s*SE', model, re.I):
            generation = re.sub(r'\D', '', model)
            years = {'1': '2016', '2': '2020', '3': '2022', '2016': '2016', '2020': '2020', '2022': '2022'}
            if generation in years:
                return 'IPHONESE' + years[generation]
    elif brand == 'XIAOMI':
        model = re.sub(r'^XIAOMI\s*', '', model, flags=re.I)
    return re.sub(r'[^A-Z0-9]', '', model.upper().replace('+', 'PLUS'))


def phone_key(phone):
    return phone['brand'] + '|' + model_identity(phone['brand'], phone['model'])


def _capacity(value):
    match = re.fullmatch(r'([1-9]\d{0,3})\s*(Go|GB|To|TB)', _text(value, 20), re.I)
    if not match:
        return None
    size = int(match.group(1)) * (1024 if match.group(2).upper() in ('TO', 'TB') else 1)
    if not 1 <= size <= 8192:
        return None
    return f'{size // 1024} To' if size >= 1024 and size % 1024 == 0 else f'{size} Go'


def _source(source, brand):
    if not isinstance(source, dict) or not isinstance(source.get('url'), str):
        return None
    try:
        parsed = urlparse(source['url'])
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
                or not any(host == domain or host.endswith('.' + domain) for domain in OFFICIAL_DOMAINS[brand])):
            return None
    except ValueError:
        return None
    # Manufacturer facts do not require arbitrary query strings or bearer URLs.
    query = [(key, value) for key, value in parse_qsl(parsed.query)
             if (key, value) in (('tab', 'specs'), ('locale', 'fr_FR'), ('lang', 'FR'), ('hl', 'en'))]
    url = urlunparse(('https', host, parsed.path or '/', '', urlencode(query), ''))
    return {'title': _text(source.get('title'), 220) or host, 'url': url}


def _date(value):
    if not isinstance(value, str):
        return None
    try:
        datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return value


def validate_phone(value, expected_brand=None, *, require_capacities=True):
    """Return the facts-only schema or None. Drop non-fact fields and sources."""
    if not isinstance(value, dict):
        return None
    brand = _text(value.get('brand'), 30).upper()
    model = _text(value.get('model'), 100)
    if brand not in BRANDS or (expected_brand and brand != expected_brand) or not model:
        return None
    starts = {'APPLE': r'^(?:Apple\s+)?iPhone(?:\b|(?=\d))', 'XIAOMI': r'^(?:Xiaomi|Redmi|POCO)\b',
              'SAMSUNG': r'^(?:Samsung\s+)?Galaxy\b', 'GOOGLE': r'^(?:Google\s+)?Pixel\b'}
    if not re.match(starts[brand], model, re.I) or not model_identity(brand, model):
        return None
    raw_specs = value.get('specs')
    if not isinstance(raw_specs, dict):
        return None
    specs = {}
    for key in SPEC_LABELS:
        text = _text(raw_specs.get(key), 160)
        if text and not re.search(r'\b(?:inconnu|unknown|vérifier|confirmer|non\s+précis|non\s+disponible|n/?a)\b', text, re.I):
            specs[key] = text
    if len(specs) < 3:
        return None
    capacities = value.get('capacities', [])
    if not isinstance(capacities, list) or any(_capacity(capacity) is None for capacity in capacities):
        return None
    capacities = list(dict.fromkeys(_capacity(capacity) for capacity in capacities))
    if require_capacities and not capacities:
        return None
    sources = value.get('sources')
    if not isinstance(sources, list):
        return None
    sources = [clean for source in sources if (clean := _source(source, brand))]
    sources = list({source['url']: source for source in sources}.values())[:8]
    if not sources:
        return None
    phone = {'brand': brand, 'model': model, 'specs': specs, 'capacities': capacities, 'sources': sources}
    for key, limit in (('aliases', 100), ('manufacturer_references', 80)):
        raw = value.get(key, [])
        phone[key] = list(dict.fromkeys(text for item in raw if (text := _text(item, limit))))[:100] if isinstance(raw, list) else []
    if model not in phone['aliases']:
        phone['aliases'].insert(0, model)
    if (verified := _date(value.get('verified_at'))):
        phone['verified_at'] = verified
    if isinstance(value.get('release_year'), int) and 2000 <= value['release_year'] <= datetime.now(timezone.utc).year:
        phone['release_year'] = value['release_year']
    return phone


def _merge(previous, current):
    merged = deepcopy(current)
    if previous:
        for key in ('aliases', 'manufacturer_references'):
            merged[key] = list(dict.fromkeys(previous.get(key, []) + current.get(key, [])))[:100]
    return merged


def _prune_samsung_radio_aliases(phones):
    """Keep an old bare alias from redirecting a documented 4G model to 5G.

    This is an alias correction only. It neither combines radio variants nor
    changes a phone's verification date or makes an old phone a fresh update.
    """
    def radio(phone):
        identity = model_identity('SAMSUNG', phone['model'])
        network = phone.get('specs', {}).get('reseau', '')
        is_5g = identity.endswith('5G') or bool(re.search(r'\b5G\b', network, re.I))
        is_4g = not is_5g and (identity.endswith(('4G', 'LTE')) or bool(re.search(r'\b(?:4G|LTE)\b', network, re.I)))
        base = re.sub(r'(?:4G|5G|LTE)$', '', identity)
        return base, is_4g, is_5g

    documented_4g = {radio(phone)[0] for phone in phones.values()
                     if phone['brand'] == 'SAMSUNG' and radio(phone)[1]}
    cleaned = dict(phones)
    removed = 0
    for key, phone in phones.items():
        if phone['brand'] != 'SAMSUNG':
            continue
        base, _, is_5g = radio(phone)
        if not is_5g or base not in documented_4g:
            continue
        ambiguous = {base, base + '4G', base + 'LTE', base + '4GLTE'}
        aliases = [alias for alias in phone.get('aliases', [])
                   if model_identity('SAMSUNG', alias) not in ambiguous]
        removed += len(phone.get('aliases', [])) - len(aliases)
        if aliases != phone.get('aliases', []):
            cleaned[key] = {**phone, 'aliases': aliases}
    return cleaned, removed


def load_catalogues(directory=BASE):
    phones = {}
    for filename in CATALOGUE_FILES:
        data = json.loads((Path(directory) / filename).read_text(encoding='utf-8'))
        for raw in data.get('phones', []):
            if not isinstance(raw, dict):
                continue
            candidate = {**raw, 'verified_at': raw.get('verified_at', data.get('verified_at'))}
            phone = validate_phone(candidate, require_capacities=False)
            if phone:
                key = phone_key(phone)
                phones[key] = _merge(phones.get(key), phone)
    return phones


def rows_for_phone(phone):
    """One durable row per exact model and capacity, matching database_key()."""
    verified = _date(phone.get('verified_at'))
    if not verified:
        raise ValueError('Une vraie date de vérification est requise.')
    rows = []
    for capacity in ['Non précisée', *phone['capacities']]:
        key = phone_key(phone) + '|' + capacity
        result = {'brand': phone['brand'], 'model': phone['model'], 'capacity': capacity,
                  'capacity_confirmed': capacity != 'Non précisée', 'available_capacities': phone['capacities'],
                  'specs': phone['specs'], 'sources': phone['sources'], 'aliases': phone['aliases'],
                  'manufacturer_references': phone['manufacturer_references'],
                  'feature': ' · '.join(f'{SPEC_LABELS[k]} : {v}' for k, v in list(phone['specs'].items())[:4]),
                  'status': 'verified_online', 'input_type': 'model_name',
                  'search_mode': 'official_manufacturer_catalogue', 'lastVerified': verified}
        if 'release_year' in phone:
            result['release_year'] = phone['release_year']
        rows.append({'model_key': key, 'brand': phone['brand'], 'model': phone['model'], 'capacity': capacity,
                     'specs': phone['specs'], 'sources': phone['sources'], 'result': result,
                     'search_mode': 'official_manufacturer_catalogue', 'verification_status': 'vérifié',
                     'verified_at': verified, 'updated_at': verified, 'last_attempt_at': verified,
                     'next_check_at': None})
    return rows


def _phone_from_row(row):
    if not isinstance(row, dict) or row.get('verification_status') != 'vérifié':
        return None
    result = row.get('result') if isinstance(row.get('result'), dict) else {}
    capacities = result.get('available_capacities', [])
    if not isinstance(capacities, list):
        capacities = []
    if result.get('capacity_confirmed') is True and (capacity := _capacity(row.get('capacity'))):
        capacities = [*capacities, capacity]
    return validate_phone({'brand': row.get('brand'), 'model': row.get('model'), 'specs': row.get('specs'),
                           'sources': row.get('sources'), 'capacities': list(dict.fromkeys(capacities)),
                           'aliases': result.get('aliases', []), 'manufacturer_references': result.get('manufacturer_references', []),
                           'verified_at': row.get('verified_at')}, require_capacities=False)


def default_collectors():
    from catalogue_sources import apple, xiaomi, samsung
    from google_phones import collect as collect_google
    return {'APPLE': apple.collect, 'XIAOMI': xiaomi.collect, 'SAMSUNG': samsung.collect, 'GOOGLE': collect_google}


def _confirmed(row, response):
    if not isinstance(response, dict):
        return False
    try:
        same_date = datetime.fromisoformat(response.get('verified_at', '').replace('Z', '+00:00')) == datetime.fromisoformat(row['verified_at'])
    except (TypeError, ValueError):
        return False
    return (response.get('model_key') == row['model_key'] and response.get('capacity') == row['capacity']
            and response.get('brand') == row['brand'] and response.get('model') == row['model']
            and response.get('verification_status') == 'vérifié' and same_date
            and response.get('specs') == row['specs'] and response.get('sources') == row['sources']
            and response.get('result') == row['result'])


async def refresh_catalogues(collectors=None, *, apply=False, store=None, client=None,
                             catalogue_dir=None, existing=None, timeout=300, now=None):
    """Collect, validate and merge; optionally persist only confirmed fresh rows.

    Existing records are never deleted or downgraded. Returned ``catalogues`` are
    a facts-only snapshot; a run date is never used as a global verification date.
    Collector/store/client injection keeps tests independent of live services.
    """
    deadline = time.monotonic() + max(0, timeout)
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
    report = {'mode': 'apply' if apply else 'dry_run', 'status': 'failed', 'ok': False,
              'attempted_at': timestamp, 'brands': {}, 'totals': {}, 'errors': [],
              'database': {'configured': False, 'read_complete': False, 'rows_written': 0}, 'catalogues': {}}
    phones = load_catalogues(catalogue_dir or BASE) if existing is None else {}
    if existing is not None:
        values = [phone for group in existing.values() for phone in group] if isinstance(existing, dict) else existing
        for raw in values:
            phone = validate_phone(raw, require_capacities=False)
            if phone:
                key = phone_key(phone)
                phones[key] = _merge(phones.get(key), phone)
    db = store if store is not None else Store()
    report['database']['configured'] = bool(db.configured)

    async def bounded(awaitable, maximum=15):
        return await asyncio.wait_for(awaitable, timeout=max(0.001, min(maximum, deadline - time.monotonic())))

    storage_read_failed = False
    if db.configured:
        try:
            for offset in range(0, MAX_DATABASE_ROWS, DATABASE_PAGE_SIZE):
                rows = await bounded(db.request('GET', 'phone_specs', params={
                    'verification_status': 'eq.vérifié', 'order': 'id.asc',
                    'select': 'model_key,brand,model,capacity,specs,sources,verified_at,result,verification_status',
                    'limit': str(DATABASE_PAGE_SIZE), 'offset': str(offset)}))
                if not isinstance(rows, list):
                    raise RuntimeError('Invalid database page')
                for row in rows:
                    phone = _phone_from_row(row)
                    if phone:
                        key = phone_key(phone)
                        old = phones.get(key)
                        if old:
                            phone['capacities'] = list(dict.fromkeys(old['capacities'] + phone['capacities']))
                        if not old or (phone.get('verified_at') or '') >= (old.get('verified_at') or ''):
                            phones[key] = _merge(old, phone)
                        else:
                            phones[key] = _merge(phone, {**old, 'capacities': phone['capacities']})
                if len(rows) < DATABASE_PAGE_SIZE:
                    report['database']['read_complete'] = True
                    break
            if not report['database']['read_complete']:
                raise RuntimeError('Database export limit reached')
        except (RuntimeError, httpx.HTTPError, asyncio.TimeoutError, ValueError, TypeError):
            storage_read_failed = True
            report['errors'].append('Lecture Supabase incomplète ; anciennes fiches conservées et écritures suspendues.')
    elif apply:
        report['errors'].append('Supabase non configuré ; aucune mise à jour durable effectuée.')

    before = set(phones)
    # Preserve the previous facts and their dates separately: an old 256 Go
    # variant must not vanish from the backup when only 128 Go was rechecked.
    report['previous_catalogues'] = {
        brand: [deepcopy(phone) for phone in phones.values() if phone['brand'] == brand]
        for brand in BRANDS}
    fresh = {}
    selected = default_collectors() if collectors is None else collectors
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=12, follow_redirects=False, headers={
            'User-Agent': 'EncreCatalogueMaintenance/1.0', 'Accept-Language': 'fr-FR,fr;q=0.9,en;q=0.8'})
    try:
        async def run(brand, collector):
            old = [deepcopy(phone) for phone in phones.values() if phone['brand'] == brand]
            try:
                remaining = max(0.001, deadline - time.monotonic() - 30)
                outcome = await asyncio.wait_for(collector(client, old), timeout=min(185, remaining))
                if (not isinstance(outcome, dict) or not isinstance(outcome.get('phones'), list)
                        or not isinstance(outcome.get('discovery_ok'), bool)
                        or not isinstance(outcome.get('errors'), list)):
                    raise ValueError('Invalid collector contract')
                stats = {'checked': max(0, int(outcome.get('checked', 0))), 'discovered': max(0, int(outcome.get('discovered', 0))),
                         'discovery_ok': outcome['discovery_ok'], 'validated': 0, 'invalid': 0,
                         'collector_errors': len(outcome['errors'])}
                updates = {}
                verified_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
                for raw in outcome['phones']:
                    phone = validate_phone(raw, brand)
                    if not phone:
                        stats['invalid'] += 1
                        continue
                    phone['verified_at'] = verified_at
                    key = phone_key(phone)
                    updates[key] = _merge(phones.get(key), phone)
                stats['validated'] = len(updates)
                return brand, stats, updates
            except (Exception, asyncio.TimeoutError):
                return brand, {'checked': 0, 'discovered': 0, 'discovery_ok': False, 'validated': 0,
                               'invalid': 0, 'collector_errors': 1}, {}

        outputs = await asyncio.gather(*(run(brand, collector) for brand, collector in selected.items() if brand in BRANDS))
        for brand, stats, updates in outputs:
            report['brands'][brand] = stats
            phones.update(updates)
            fresh.update(updates)
            if not stats['discovery_ok'] or stats['collector_errors'] or stats['invalid']:
                report['errors'].append(f"{brand} : découverte ou actualisation partielle ; anciennes fiches conservées.")
    finally:
        if owns_client:
            await client.aclose()

    phones, aliases_pruned = _prune_samsung_radio_aliases(phones)
    # Newly read 4G facts can disambiguate an old 5G alias. Only the records
    # already marked fresh are persisted; a cleanup never fabricates recency.
    fresh = {key: phones[key] for key in fresh}
    planned = [row for phone in fresh.values() for row in rows_for_phone(phone)]
    written = 0
    consecutive_failures = 0
    if apply and db.configured and not storage_read_failed:
        for start in range(0, len(planned), BATCH_SIZE):
            batch = planned[start:start + BATCH_SIZE]
            try:
                response = await bounded(db.save(batch))
                acknowledged = response if isinstance(response, list) else []
                confirmed = sum(any(_confirmed(row, saved) for saved in acknowledged) for row in batch)
                written += confirmed
                if confirmed != len(batch):
                    consecutive_failures += 1
                    report['errors'].append('Écriture Supabase non confirmée pour certaines fiches ; aucune réussite déclarée pour celles-ci.')
                else:
                    consecutive_failures = 0
            except (Exception, asyncio.TimeoutError):
                consecutive_failures += 1
                report['errors'].append('Écriture Supabase indisponible ; seules les écritures déjà confirmées sont comptées.')
            if consecutive_failures >= 2:
                report['errors'].append('Persistance interrompue après deux lots non confirmés ; anciennes fiches conservées.')
                break
            if time.monotonic() >= deadline:
                report['errors'].append('Délai global atteint ; mise à jour partielle.')
                break
    report['database']['rows_written'] = written
    report['totals'] = {'existing_models': len(before), 'models': len(phones), 'validated_models': len(fresh),
                        'new_models': len(set(fresh) - before), 'retained_models': len(before - set(fresh)),
                        'aliases_pruned': aliases_pruned,
                        'rows_planned': len(planned), 'rows_written': written,
                        'rows_unconfirmed': len(planned) - written if apply else 0}
    report['catalogues'] = {brand: sorted((phone for phone in phones.values() if phone['brand'] == brand), key=lambda phone: phone['model'])
                            for brand in BRANDS}
    if not fresh:
        report['errors'].append('Aucune fiche fraîchement vérifiée ; aucune actualisation complète déclarée.')
    complete = bool(report['brands']) and not report['errors']
    report['ok'] = complete and (not apply or written == len(planned))
    report['status'] = ('updated' if apply else 'dry_run') if report['ok'] else ('partial' if fresh else 'failed')
    return report


def write_backup(report, path):
    """Explicitly requested facts export, never a database/secret/environment dump."""
    payload = {'format': 'encre-manufacturer-facts-v1', 'exported_at': report['attempted_at'],
               'scope': 'catalogue_facts_only', 'collection_complete': report['ok'],
               'database_included': report['database']['read_complete'], 'catalogues': report['catalogues'],
               'previous_catalogues': report.get('previous_catalogues', {})}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def public_report(report):
    return {key: value for key, value in report.items() if key not in ('catalogues', 'previous_catalogues')}


async def main(argv=None):
    parser = argparse.ArgumentParser(description='Actualisation constructeur sans IA ; simulation par défaut.')
    parser.add_argument('--apply', action='store_true', help='Écrire les fiches fraîchement vérifiées dans Supabase.')
    parser.add_argument('--backup', type=Path, help='Exporter explicitement les faits catalogue uniquement.')
    parser.add_argument('--report', type=Path, help='Enregistrer le résumé JSON, sans données de connexion.')
    args = parser.parse_args(argv)
    try:
        report = await refresh_catalogues(apply=args.apply)
    except Exception:
        report = {'mode': 'apply' if args.apply else 'dry_run', 'status': 'failed', 'ok': False,
                  'attempted_at': datetime.now(timezone.utc).isoformat(), 'brands': {}, 'totals': {},
                  'errors': ['Actualisation interrompue avant validation ; aucune réussite déclarée.'],
                  'database': {'configured': False, 'read_complete': False, 'rows_written': 0}, 'catalogues': {}}
    if args.backup:
        try:
            write_backup(report, args.backup)
        except (OSError, ValueError, TypeError):
            report['errors'].append('Export de sauvegarde impossible ; aucune sauvegarde réussie déclarée.')
            report['ok'] = False
            report['status'] = 'partial' if report['database']['rows_written'] else 'failed'
    summary = public_report(report)
    if args.report:
        try:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        except (OSError, ValueError, TypeError):
            report['errors'].append('Enregistrement du rapport impossible.')
            report['ok'] = False
            report['status'] = 'partial' if report['database']['rows_written'] else 'failed'
            summary = public_report(report)
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))

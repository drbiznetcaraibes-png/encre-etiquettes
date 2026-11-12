"""Explicit offline dry run by default. No production writes without --apply."""
import argparse
import asyncio
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from storage import store
import server


def catalogue_rows():
    rows = {}
    for filename in ('phone_catalog.json', 'apple_catalog.json', 'xiaomi_catalog.json', 'samsung_catalog.json', 'google_catalog.json'):
        data = json.loads((Path(__file__).parent / filename).read_text())
        for phone in data['phones']:
            capacities = phone.get('capacities', [])
            for capacity in ['Non précisée', *capacities]:
                query = phone['brand'] + ' ' + phone['model']
                if capacity != 'Non précisée':
                    query += ' ' + capacity
                key = server.database_key(query)
                specs = server._clean_specs(phone.get('specs'))
                complete = len(specs) >= 3 and bool(phone.get('sources'))
                result = {**phone, 'capacity': capacity, 'capacity_confirmed': capacity != 'Non précisée',
                          'available_capacities': capacities, 'specs': specs,
                          'feature': server._spec_feature(specs), 'status': 'verified_online',
                          'search_mode': 'official_manufacturer_catalogue'}
                rows[key] = dict(model_key=key, brand=phone['brand'], model=phone['model'],
                    capacity=capacity, specs=specs, sources=phone.get('sources', []),
                    verification_status='vérifié' if complete else 'incomplet',
                    search_mode='catalogue', verified_at=data.get('verified_at'), result=result,
                    next_check_at=None if complete else datetime.now(timezone.utc).isoformat())
    return list(rows.values())


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['import', 'weekly'])
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.command == 'import':
        rows = catalogue_rows()
        print(f'{len(rows)} fiches dédupliquées; appliquer={args.apply}')
        if args.apply:
            for row in rows:
                # Never replace an existing verified online record on re-import.
                if not await store.get(row['model_key']):
                    await store.save(row)
        return
    # Deliberately no AI in scheduled maintenance: prevents background spending.
    server.OPENAI_KEY = ''
    server.GEMINI_KEY = ''
    rows = await store.request('GET', 'phone_specs', params={
        'or': '(and(verification_status.in.(à vérifier,incomplet),next_check_at.is.null),next_check_at.lte.' + datetime.now(timezone.utc).isoformat() + ')',
        'order': 'last_attempt_at.asc.nullsfirst,id.asc', 'limit': '10'})
    print(f'{len(rows)} fiches ciblées; appliquer={args.apply}; IA désactivée')
    if not args.apply:
        return
    for row in rows:
        now = datetime.now(timezone.utc)
        query = row['brand'] + ' ' + row['model']
        if row['capacity'] != 'Non précisée':
            query += ' ' + row['capacity']
        result = await server.online_lookup(query, True, refresh=True)
        await store.request('PATCH', 'phone_specs', params={'id': 'eq.' + str(row['id'])}, json={
            'last_attempt_at': now.isoformat(),
            'next_check_at': (now + timedelta(days=7)).isoformat() if result.get('status') != 'verified_online' else None})


if __name__ == '__main__':
    asyncio.run(main())

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import catalogue_update as update
from storage import Store


NOW = datetime(2026, 11, 12, 10, 0, tzinfo=timezone.utc)
OLD_DATE = '2026-10-01T00:00:00+00:00'


def phone(brand='APPLE', model='iPhone 16', capacities=None):
    domains = {'APPLE': 'https://support.apple.com/fr-fr/121029',
               'SAMSUNG': 'https://www.samsung.com/fr/smartphones/galaxy-a/galaxy-a17-5g/',
               'XIAOMI': 'https://www.mi.com/fr/product/xiaomi-17t/specs/'}
    return {'brand': brand, 'model': model, 'capacities': capacities if capacities is not None else ['128 Go'],
            'specs': {'ecran': '6,1″ OLED', 'photo': '48 Mpx', 'batterie': '5 000 mAh', 'reseau': '5G'},
            'sources': [{'title': model + ' : caractéristiques', 'url': domains[brand]}],
            'aliases': [model], 'manufacturer_references': [], 'verified_at': OLD_DATE}


def collector(phones, *, errors=None, discovery_ok=True, checked=None):
    async def run(client, existing):
        return {'phones': deepcopy(phones), 'checked': checked if checked is not None else len(phones),
                'discovered': len(phones), 'discovery_ok': discovery_ok, 'errors': errors or []}
    return run


class FakeStore:
    def __init__(self, rows=None, configured=True):
        self.configured = configured
        self.rows = rows or []
        self.saved = []
        self.requests = []

    async def request(self, method, path, **kwargs):
        self.requests.append((method, path, kwargs))
        return deepcopy(self.rows)

    async def save(self, batch):
        self.saved.append(deepcopy(batch))
        return deepcopy(batch)


class CatalogueUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def refresh(self, collectors, **kwargs):
        kwargs.setdefault('existing', [])
        kwargs.setdefault('now', NOW)
        kwargs.setdefault('store', FakeStore(configured=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: self.fail('Unexpected network call'))) as client:
            return await update.refresh_catalogues(collectors, client=client, **kwargs)

    async def test_dry_run_merges_valid_updates_preserving_old_failure_dates_and_aliases(self):
        old = phone()
        old['aliases'].append('saisie ancienne')
        retained = phone('SAMSUNG', 'Galaxy A17 (4G)')
        existing = [deepcopy(old), deepcopy(retained)]
        fresh = phone()
        fresh['specs']['photo'] = '48 Mpx (principal)'
        report = await self.refresh({'APPLE': collector([fresh]),
                                     'SAMSUNG': collector([], errors=['network failed'], discovery_ok=False)}, existing=existing)
        self.assertEqual(report['status'], 'partial')
        self.assertFalse(report['ok'])
        self.assertEqual(report['database']['rows_written'], 0)
        self.assertEqual(report['catalogues']['APPLE'][0]['verified_at'], NOW.isoformat())
        self.assertIn('saisie ancienne', report['catalogues']['APPLE'][0]['aliases'])
        self.assertEqual(report['catalogues']['SAMSUNG'][0]['verified_at'], OLD_DATE)
        self.assertEqual(existing, [old, retained])

    async def test_backup_keeps_old_capacity_and_date_separately_from_fresh_options(self):
        old = phone(capacities=['128 Go', '256 Go'])
        fresh = phone(capacities=['128 Go'])
        report = await self.refresh({'APPLE': collector([fresh])}, existing=[old])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'facts.json'
            update.write_backup(report, target)
            backup = json.loads(target.read_text())
        self.assertEqual(backup['catalogues']['APPLE'][0]['capacities'], ['128 Go'])
        self.assertEqual(backup['catalogues']['APPLE'][0]['verified_at'], NOW.isoformat())
        self.assertEqual(backup['previous_catalogues']['APPLE'][0]['capacities'], ['128 Go', '256 Go'])
        self.assertEqual(backup['previous_catalogues']['APPLE'][0]['verified_at'], OLD_DATE)
        self.assertNotIn('previous_catalogues', update.public_report(report))

    async def test_documented_4g_prunes_old_bare_alias_after_5g_merge_before_backup_and_rows(self):
        previous = phone('SAMSUNG', 'Galaxy A17 5G')
        previous['aliases'] = ['Galaxy A17 5G', 'A17', 'Samsung A17', 'A17 4G', 'A17 4G LTE', 'SM-A176B']
        fresh_5g = phone('SAMSUNG', 'Galaxy A17 5G')
        fresh_5g['aliases'] = ['A17 5G', 'SM-A176B']
        fresh_4g = phone('SAMSUNG', 'Galaxy A17 (4G)')
        fresh_4g['specs']['reseau'] = '4G LTE'
        fresh_4g['aliases'] = ['A17', 'A17 4G']
        db = FakeStore()
        report = await self.refresh({'SAMSUNG': collector([fresh_5g, fresh_4g])},
                                    existing=[previous], apply=True, store=db)
        self.assertTrue(report['ok'])
        five_g = next(item for item in report['catalogues']['SAMSUNG'] if item['model'] == 'Galaxy A17 5G')
        self.assertEqual(set(five_g['aliases']), {'Galaxy A17 5G', 'A17 5G', 'SM-A176B'})
        self.assertGreater(report['totals']['aliases_pruned'], 0)
        for row in [row for batch in db.saved for row in batch if row['model'] == 'Galaxy A17 5G']:
            self.assertNotIn('A17', row['result']['aliases'])
            self.assertNotIn('A17 4G', row['result']['aliases'])
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'facts.json'
            update.write_backup(report, target)
            backup = json.loads(target.read_text())
        archived = next(item for item in backup['catalogues']['SAMSUNG'] if item['model'] == 'Galaxy A17 5G')
        self.assertNotIn('A17', archived['aliases'])
        self.assertEqual(previous['aliases'], ['Galaxy A17 5G', 'A17', 'Samsung A17', 'A17 4G', 'A17 4G LTE', 'SM-A176B'])

    async def test_alias_cleanup_of_old_5g_record_does_not_claim_new_verification_or_write_it(self):
        old_5g = phone('SAMSUNG', 'Galaxy A17 5G')
        old_5g['aliases'].append('A17')
        fresh_4g = phone('SAMSUNG', 'Galaxy A17 (4G)')
        fresh_4g['specs']['reseau'] = 'LTE'
        db = FakeStore()
        report = await self.refresh({'SAMSUNG': collector([fresh_4g])}, existing=[old_5g], apply=True, store=db)
        retained = next(item for item in report['catalogues']['SAMSUNG'] if item['model'] == 'Galaxy A17 5G')
        self.assertEqual(retained['verified_at'], OLD_DATE)
        self.assertNotIn('A17', retained['aliases'])
        self.assertTrue(all(row['model'] != 'Galaxy A17 5G' for batch in db.saved for row in batch))

    async def test_bare_5g_alias_is_retained_without_a_documented_4g_variant(self):
        old = phone('SAMSUNG', 'Galaxy A37 5G')
        old['sources'][0]['url'] = 'https://www.samsung.com/fr/smartphones/galaxy-a/galaxy-a37-5g/'
        old['aliases'].append('A37')
        current = deepcopy(old)
        current['aliases'] = ['A37 5G']
        report = await self.refresh({'SAMSUNG': collector([current])}, existing=[old])
        self.assertIn('A37', report['catalogues']['SAMSUNG'][0]['aliases'])
        self.assertEqual(report['totals']['aliases_pruned'], 0)

    async def test_invalid_specs_capacity_source_and_brand_cannot_replace_verified_model(self):
        old = phone('SAMSUNG', 'Galaxy A17 5G')
        candidates = []
        for field, value in [('specs', {'photo': '50 Mpx'}), ('capacities', ['512 To']),
                             ('sources', [{'url': 'https://samsung.com.attacker.example/fake'}]), ('brand', 'APPLE')]:
            invalid = deepcopy(old)
            invalid[field] = value
            candidates.append(invalid)
        report = await self.refresh({'SAMSUNG': collector(candidates)}, existing=[old])
        self.assertFalse(report['ok'])
        self.assertEqual(report['brands']['SAMSUNG']['invalid'], 4)
        self.assertEqual(report['catalogues']['SAMSUNG'][0], update.validate_phone(old))
        self.assertEqual(report['totals']['rows_planned'], 0)

    async def test_apply_confirms_real_store_rest_upsert_and_never_calls_ai_budget(self):
        db = Store()
        db.url = 'https://test.supabase.co'
        db.key = 'sb_secret_test_only'
        requests = []
        async def rest(client, method, url, **kwargs):
            requests.append((method, url, kwargs))
            self.assertNotIn('reserve_ai_call', url)
            self.assertNotIn('openai', url)
            return httpx.Response(200, json=kwargs['json'] if method == 'POST' else [])
        with patch.object(httpx.AsyncClient, 'request', new=rest):
            report = await self.refresh({'APPLE': collector([phone(capacities=['128 Go', '256 Go'])])}, apply=True, store=db)
        self.assertTrue(report['ok'])
        self.assertEqual(report['status'], 'updated')
        self.assertEqual(report['database']['rows_written'], 3)
        post = next(request for request in requests if request[0] == 'POST')
        self.assertEqual(post[2]['params']['on_conflict'], 'model_key,capacity')
        self.assertEqual({row['capacity'] for row in post[2]['json']}, {'Non précisée', '128 Go', '256 Go'})
        self.assertNotIn('Authorization', post[2]['headers'])
        self.assertNotIn(db.key, json.dumps(report))

    async def test_empty_or_mismatched_write_acknowledgement_is_not_success(self):
        for response in (None, [], [{'model_key': 'wrong', 'capacity': '128 Go'}]):
            with self.subTest(response=response):
                db = FakeStore()
                db.save = AsyncMock(return_value=response)
                report = await self.refresh({'APPLE': collector([phone()])}, apply=True, store=db)
                self.assertFalse(report['ok'])
                self.assertEqual(report['database']['rows_written'], 0)
                self.assertEqual(report['totals']['rows_unconfirmed'], 2)

    async def test_partial_write_keeps_successful_batches_without_downgrading_existing_rows(self):
        db = FakeStore()
        original_save = db.save
        async def save(batch):
            if db.saved:
                raise RuntimeError('private upstream token sb_secret_should_not_escape')
            return await original_save(batch)
        db.save = save
        with patch.object(update, 'BATCH_SIZE', 2):
            report = await self.refresh({'APPLE': collector([phone(), phone(model='iPhone 17')])}, apply=True, store=db)
        self.assertFalse(report['ok'])
        self.assertEqual(report['database']['rows_written'], 2)
        self.assertEqual(report['totals']['rows_unconfirmed'], 2)
        self.assertTrue(all(method == 'GET' for method, _, _ in db.requests))
        self.assertNotIn('sb_secret_should_not_escape', json.dumps(report))

    async def test_database_failure_blocks_writes_but_preserves_fresh_reviewable_facts(self):
        db = FakeStore()
        db.request = AsyncMock(side_effect=RuntimeError('secret database error'))
        report = await self.refresh({'APPLE': collector([phone()])}, apply=True, store=db)
        self.assertFalse(report['ok'])
        self.assertFalse(report['database']['read_complete'])
        self.assertEqual(db.saved, [])
        self.assertEqual(report['catalogues']['APPLE'][0]['model'], 'iPhone 16')
        self.assertNotIn('secret database error', json.dumps(report))

    async def test_unconfigured_apply_never_reports_durable_success(self):
        db = FakeStore(configured=False)
        report = await self.refresh({'APPLE': collector([phone()])}, apply=True, store=db)
        self.assertFalse(report['ok'])
        self.assertNotEqual(report['status'], 'updated')
        self.assertEqual(db.saved, [])

    async def test_cli_defaults_to_dry_run_and_explicit_exports_are_written(self):
        report = await self.refresh({'APPLE': collector([phone()])})
        with tempfile.TemporaryDirectory() as directory:
            backup = Path(directory) / 'facts.json'
            summary = Path(directory) / 'report.json'
            with patch.object(update, 'refresh_catalogues', new=AsyncMock(return_value=report)) as refresh:
                with redirect_stdout(io.StringIO()):
                    code = await update.main(['--backup', str(backup), '--report', str(summary)])
            refresh.assert_awaited_once_with(apply=False)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(backup.read_text())['scope'], 'catalogue_facts_only')
            self.assertNotIn('catalogues', json.loads(summary.read_text()))

    async def test_verified_database_only_models_are_retained_in_backup_and_collector_input(self):
        durable = phone(model='iPhone 19')
        db = FakeStore(update.rows_for_phone(durable))
        seen = []
        async def check(client, existing):
            seen.extend(deepcopy(existing))
            return {'phones': [], 'checked': 1, 'discovered': 0, 'discovery_ok': False, 'errors': ['source unavailable']}
        report = await self.refresh({'APPLE': check}, store=db)
        self.assertEqual(seen[0]['model'], 'iPhone 19')
        self.assertEqual(report['catalogues']['APPLE'][0]['verified_at'], OLD_DATE)
        self.assertFalse(report['ok'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'backup.json'
            update.write_backup(report, path)
            backup = json.loads(path.read_text())
        self.assertTrue(backup['database_included'])
        self.assertEqual(backup['catalogues']['APPLE'][0]['model'], 'iPhone 19')

    async def test_backup_contains_facts_only_and_strips_secret_like_fields_and_url_queries(self):
        raw = phone()
        raw.update(cash=999, labels=[{'customer': 'private'}], ai_usage={'calls': 99}, api_key='sb_secret_hidden')
        raw['aliases'].append('sb_secret_hidden_alias')
        raw['sources'][0]['url'] += '?api_key=sb_secret_hidden_url&tab=specs'
        report = await self.refresh({'APPLE': collector([raw])})
        self.assertTrue(report['ok'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'backup.json'
            update.write_backup(report, path)
            content = path.read_text()
            backup = json.loads(content)
        for forbidden in ('cash', 'labels', 'customer', 'ai_usage', 'api_key', 'sb_secret_'):
            self.assertNotIn(forbidden, content)
        self.assertNotIn('result', backup['catalogues']['APPLE'][0])
        self.assertTrue(backup['catalogues']['APPLE'][0]['sources'][0]['url'].endswith('?tab=specs'))

    async def test_collector_timeout_or_exception_is_honest_and_does_not_refresh_old_dates(self):
        async def fail(client, existing):
            raise RuntimeError('sk-proj-private-key')
        report = await self.refresh({'APPLE': fail}, existing=[phone()])
        self.assertFalse(report['ok'])
        self.assertEqual(report['catalogues']['APPLE'][0]['verified_at'], OLD_DATE)
        self.assertNotIn('sk-proj-private-key', json.dumps(report))
        async def slow(client, existing):
            await asyncio.sleep(1)
        report = await self.refresh({'APPLE': slow}, existing=[phone()], timeout=0.01)
        self.assertFalse(report['brands']['APPLE']['discovery_ok'])

    def test_canonical_database_keys_preserve_radio_plus_se_and_capacity(self):
        cases = [
            ('APPLE', 'iPhone SE (2020)', ['128 Go'], 'APPLE|IPHONESE2020|128 Go'),
            ('SAMSUNG', 'Galaxy A17 (4G)', ['128 Go'], 'SAMSUNG|A174G|128 Go'),
            ('SAMSUNG', 'Galaxy A17 5G', ['128 Go'], 'SAMSUNG|A175G|128 Go'),
            ('SAMSUNG', 'Galaxy S26+', ['512 Go'], 'SAMSUNG|S26PLUS|512 Go'),
            ('XIAOMI', 'Redmi Note 14 Pro+ 5G', ['256 Go'], 'XIAOMI|REDMINOTE14PROPLUS5G|256 Go'),
            ('XIAOMI', 'Xiaomi 17T Pro', ['1024 GB'], 'XIAOMI|17TPRO|1 To'),
        ]
        for brand, model, capacities, expected in cases:
            with self.subTest(model=model):
                checked = update.validate_phone(phone(brand, model, capacities))
                self.assertEqual(update.rows_for_phone(checked)[1]['model_key'], expected)

    def test_local_catalogue_load_order_deduplicates_without_claiming_new_verification(self):
        old = phone()
        newer = phone()
        newer['specs']['photo'] = '48 Mpx (principal)'
        with tempfile.TemporaryDirectory() as directory:
            for filename in update.CATALOGUE_FILES:
                values = [old] if filename == 'phone_catalog.json' else [newer] if filename == 'apple_catalog.json' else []
                Path(directory, filename).write_text(json.dumps({'phones': values, 'verified_at': '2026-11-01'}))
            loaded = update.load_catalogues(directory)
        self.assertEqual(len(loaded), 1)
        self.assertEqual(next(iter(loaded.values()))['verified_at'], OLD_DATE)
        self.assertEqual(next(iter(loaded.values()))['specs']['photo'], '48 Mpx (principal)')

    def test_module_does_not_import_web_app_or_ai_clients(self):
        command = [sys.executable, '-c', 'import sys; import catalogue_update; assert "server" not in sys.modules; assert "openai_phones" not in sys.modules']
        result = subprocess.run(command, cwd=update.BASE, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()

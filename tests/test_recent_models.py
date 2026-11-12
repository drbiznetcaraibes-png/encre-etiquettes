import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server
import samsung_phones
import samsung_products
from catalogue_sources import samsung
from database_models import invalidate_database_models_cache


def phone(model='Galaxy A19', network='4G LTE'):
    return {'brand': 'SAMSUNG', 'model': model, 'aliases': [model],
            'capacities': ['128 Go'],
            'specs': {'ecran': '6,7″ AMOLED', 'photo': '50 Mpx', 'batterie': '5 000 mAh', 'reseau': network},
            'sources': [{'url': 'https://www.samsung.com/fr/smartphones/galaxy-a/example/'}]}


class RecentModelTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server.CACHE.clear()
        server.REQUEST_LOG.clear()
        server.GLOBAL_REQUEST_LOG.clear()
        samsung_phones._CACHE.clear()
        samsung_phones._INDEX_CACHE.clear()
        invalidate_database_models_cache()
        self.registry = patch.object(server, '_DATABASE_MODEL_NAMES', [])
        self.registry.start()
        self.addCleanup(self.registry.stop)

    async def test_unknown_catalogue_model_uses_product_discovery_without_paid_search(self):
        self.assertNotIn('Galaxy A19', [p['model'] for p in samsung_phones.CATALOG['phones']])
        with patch.object(samsung_products, 'lookup_product_phone', new=AsyncMock(return_value=phone())) as product, \
                patch.object(server, 'OPENAI_KEY', 'test-only'), \
                patch.object(server, 'openai_lookup', new_callable=AsyncMock) as ai:
            found = await server._online_lookup('GALAXY A19 128 GO', True)
        self.assertEqual(found['model'], 'Galaxy A19')
        self.assertEqual(found['capacity'], '128 Go')
        self.assertEqual(found['search_mode'], 'official_manufacturer_page')
        product.assert_awaited_once()
        ai.assert_not_awaited()

    async def test_press_archive_finds_a_model_missing_from_current_index(self):
        url = 'https://www.samsungmobilepress.com/articles/galaxy-a26-5g-specifications'
        sheet = '''<table><tr><td colspan="3">Galaxy A26 5G</td></tr>
        <tr><td>Display</td><td colspan="2">6.7-inch AMOLED 120 Hz</td></tr>
        <tr><td>Memory &amp; Storage</td><td colspan="2">6 + 128 GB</td></tr>
        <tr><td>Battery</td><td colspan="2">5000 mAh</td></tr>
        <tr><td>Network</td><td colspan="2">5G LTE</td></tr></table>'''
        def transport(request):
            if request.url.path == '/sitemap.xml':
                return httpx.Response(200, text='<urlset><url><loc>' + url + '</loc></url></urlset>')
            if '/products/' in request.url.path:
                return httpx.Response(200, text='<h1>Current models</h1>')
            self.assertEqual(str(request.url), url)
            return httpx.Response(200, text=sheet)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            item = await samsung_phones._discover(client, 'A26', explicit_5g=True)
        self.assertEqual(item['model'], 'Galaxy A26 5G')
        self.assertEqual(item['capacities'], ['128 Go'])

    async def test_press_redirect_is_rejected_before_leaving_samsung(self):
        calls = []
        def transport(request):
            calls.append(request.url.host)
            return httpx.Response(302, headers={'location': 'https://example.com/specs'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            with self.assertRaises(ValueError):
                await samsung_phones._get_document(client, 'https://www.samsungmobilepress.com/products/galaxy-a')
        self.assertEqual(calls, ['www.samsungmobilepress.com'])

    async def test_combined_sources_keep_lte_and_5g_distinct(self):
        lte, five_g = phone('Galaxy A17'), phone('Galaxy A17 5G', '5G')
        five_g['aliases'] = ['A17', 'A17 5G']
        outcome = lambda phones: {'phones': phones, 'checked': len(phones), 'discovered': len(phones),
                                  'discovery_ok': True, 'errors': []}
        with patch.object(samsung_products, 'collect_product_phones', new=AsyncMock(return_value=outcome([lte]))), \
                patch.object(samsung, '_collect_press', new=AsyncMock(return_value=outcome([five_g]))):
            result = await samsung.collect(None, [])
        self.assertEqual(result['discovered'], 2)
        self.assertNotIn('A17', next(row for row in result['phones'] if row['model'].endswith('5G'))['aliases'])

    async def test_database_models_reach_suggestions_and_unknown_name_lookup(self):
        rows = [{'brand': 'SAMSUNG', 'model': 'Galaxy A26 5G', 'capacity': '256 Go',
                 'confirmed': True, 'available_capacities': ['128 Go', '256 Go']},
                {'brand': 'APPLE', 'model': 'iPhone Fold', 'capacity': '256 Go',
                 'confirmed': True, 'available_capacities': ['256 Go']}]
        result = {'brand': 'APPLE', 'model': 'iPhone Fold', 'capacity': '256 Go',
                  'status': 'verified_online', 'sources': []}
        with patch.dict(os.environ, {'APP_ACCESS_TOKEN': 'test-access'}), \
                patch.object(server.store, 'url', 'https://test.supabase.co'), \
                patch.object(server.store, 'key', 'test-only'), \
                patch.object(server.store, 'request', new=AsyncMock(return_value=rows)) as read, \
                patch.object(server, 'online_lookup', new=AsyncMock(return_value=result)) as lookup, \
                patch.object(server, 'openai_lookup', new_callable=AsyncMock) as ai:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),
                                         base_url='https://test', auth=('encre', 'test-access')) as client:
                # The database record is resolved even without visiting suggestions first.
                found = await client.get('/api/phones/lookup', params={'reference': 'iPhone Fold 256 Go'})
                self.assertEqual(found.status_code, 200, found.text)
                self.assertTrue(lookup.await_args.kwargs['is_model_query'])
                self.assertEqual(server.database_key('iPhone Fold 256 Go'), 'APPLE|IPHONEFOLD|256 Go')
                suggested = await client.get('/api/phones/models', params={'query': 'Samsung A26'})
                self.assertEqual(suggested.status_code, 200)
                self.assertEqual(suggested.json()['models'][0]['model'], 'Galaxy A26 5G')
                self.assertEqual(suggested.json()['models'][0]['available_capacities'], ['128 Go', '256 Go'])
                lookup.return_value = {'status': 'not_found', 'search_engines': {}}
                invalid = await client.get('/api/phones/lookup', params={'reference': 'iPhone Fold Deluxe 256 Go'})
                self.assertEqual(invalid.status_code, 404)
                self.assertIn('DELUXE', lookup.await_args.args[0])
        read.assert_awaited_once()
        ai.assert_not_awaited()


if __name__ == '__main__':
    unittest.main()

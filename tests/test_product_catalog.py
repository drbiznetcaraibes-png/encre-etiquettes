import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server


TABLET = {
    'id': 123, 'category': 'tablet', 'brand': 'Samsung', 'model': 'Galaxy Tab S9+',
    'reference': 'SM-X810', 'capacities': ['256 GB', '512 Go', '256 Go', 'inconnu'],
    'specs': {'CPU': 'Snapdragon 8 Gen 2', 'Écran': 'AMOLED 12,4″', 'RAM': '12 Go'},
    'private_note': 'should not leave server',
}
CONSOLE = {
    'id': 456, 'category': 'console', 'brand': 'Sony', 'model': 'PlayStation 5 Pro',
    'reference': 'CFI-7000', 'variant': 'Édition numérique',
    'specifications': {'stockage': 'SSD 2 To', 'image': '4K', 'technologie': 'PSSR', 'inclus': 'DualSense'},
}


class ProductCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        for context in (
            patch.dict(os.environ, {'APP_ACCESS_TOKEN': 'local-only'}),
            patch.object(server.store, 'url', 'https://test.supabase.co'),
            patch.object(server.store, 'key', 'sb_secret_test_only'),
        ):
            self.enterContext(context)
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url='http://test',
        ))
        self.auth = ('encre', 'local-only')

    async def test_catalog_requires_authentication(self):
        with patch.object(server.store, 'request', new_callable=AsyncMock) as upstream:
            response = await self.client.get('/api/products', params={'category': 'tablet'})
            self.assertEqual(response.status_code, 401)
            upstream.assert_not_awaited()

    async def test_tablet_search_by_brand_model_and_reference(self):
        with patch.object(server.store, 'request', new=AsyncMock(return_value=[TABLET])) as upstream:
            for query in ['samsung', 'Samsung galaxy tab S9+', 'sm x810', '']:
                response = await self.client.get('/api/products', auth=self.auth, params={
                    'category': 'tablet', 'query': query,
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'products': [{
                    'brand': 'Samsung', 'model': 'Galaxy Tab S9+', 'reference': 'SM-X810', 'variant': '',
                    'available_capacities': ['256 Go', '512 Go'],
                    'specs': {'processeur': 'Snapdragon 8 Gen 2', 'ecran': 'AMOLED 12,4″', 'ram': '12 Go'},
                }]})
            upstream.assert_awaited_with('GET', 'product_catalog', params={
                'category': 'in.(tablet,tablets,tablette,tablettes)',
            })
            response = await self.client.get('/api/products', auth=self.auth, params={
                'category': 'tablet', 'query': 'Galaxy Tab S11',
            })
            self.assertEqual(response.json(), {'products': []})

    async def test_console_features_and_variant(self):
        with patch.object(server.store, 'request', new=AsyncMock(return_value=[CONSOLE])) as upstream:
            response = await self.client.get('/api/products', auth=self.auth, params={
                'category': 'console', 'query': 'edition numerique',
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()['products'][0]['specs'], {
                'storage': 'SSD 2 To', 'video': '4K', 'technology': 'PSSR', 'included': 'DualSense',
            })
            upstream.assert_awaited_once_with('GET', 'product_catalog', params={'category': 'in.(console,consoles)'})

    async def test_validation_and_unavailable_catalog(self):
        with patch.object(server.store, 'request', new=AsyncMock(side_effect=RuntimeError('private upstream detail'))) as upstream:
            for params in ({'category': 'phone'}, {'category': 'console', 'query': 'x' * 121}):
                response = await self.client.get('/api/products', auth=self.auth, params=params)
                self.assertEqual(response.status_code, 422)
            upstream.assert_not_awaited()
            response = await self.client.get('/api/products?category=console', auth=self.auth)
            self.assertEqual(response.status_code, 502)
            self.assertNotIn('private upstream detail', response.text)
        with patch.object(server.store, 'key', ''):
            response = await self.client.get('/api/products?category=console', auth=self.auth)
            self.assertEqual(response.status_code, 503)

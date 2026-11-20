import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server
from product_catalog import CATALOG_COLUMNS


TABLET = {
    'id': 123, 'category': 'tablet', 'brand': 'APPLE',
    'model': 'iPad Air 11 pouces (M4)', 'model_key': 'TABLET|APPLE|IPADAIR11POUCESM4',
    'manufacturer_references': ['A1234', 'A5678'],
    'available_capacities': ['128 Go', '256 GB', '512 Go', '1 To', '256 Go'],
    'connectivity_options': ['Wi-Fi', 'Cellulaire'],
    'specs': {'Écran': '11 pouces Liquid Retina', 'Puce': 'Apple M4', 'Réseau': 'Wi-Fi 7'},
    'variant_note': None, 'verification_status': 'vérifié',
}
CONSOLE = {
    'id': 456, 'category': 'console', 'brand': 'PLAYSTATION',
    'model': 'PS5 Pro — 2 To Digital', 'model_key': 'CONSOLE|PLAYSTATION|PS5PRODIGITAL',
    'manufacturer_references': ['CFI-7000'], 'available_capacities': ['2 To'],
    'connectivity_options': [],
    'specs': {'Stockage': 'SSD 2 To', 'Image': "4K ; jusqu'à 120 i/s selon jeu/écran",
              'Technologie': 'PSSR et ray tracing avancé', 'Lecteur': 'Absent de série ; accessoire optionnel'},
    'variant_note': 'Édition numérique', 'verification_status': 'vérifié',
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

    async def test_tablet_search_by_brand_model_and_second_reference(self):
        with patch.object(server.store, 'request', new=AsyncMock(return_value=[TABLET])) as upstream:
            for query, reference in [('apple', ''), ('Apple iPad Air 11 pouces M4', ''), ('a-5678', 'A5678'), ('', '')]:
                response = await self.client.get('/api/products', auth=self.auth, params={
                    'category': 'tablet', 'query': query,
                })
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'products': [{
                    'brand': 'APPLE', 'model': 'iPad Air 11 pouces (M4)', 'reference': reference, 'variant': '',
                    'available_capacities': ['128 Go', '256 Go', '512 Go', '1 To'],
                    'verification_status': 'vérifié',
                    'specs': {'processeur': 'Apple M4', 'ecran': '11 pouces Liquid Retina', 'reseau': 'Wi-Fi 7'},
                }]})
            upstream.assert_awaited_with('GET', 'product_catalog', params={
                'category': 'eq.tablet', 'select': CATALOG_COLUMNS, 'order': 'id.asc',
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
            product = response.json()['products'][0]
            self.assertEqual(product['variant'], 'Édition numérique')
            self.assertEqual(product['reference'], 'CFI-7000')
            self.assertEqual(product['specs'], [
                {'label': label, 'value': value} for label, value in CONSOLE['specs'].items()
            ])
            upstream.assert_awaited_once_with('GET', 'product_catalog', params={
                'category': 'eq.console', 'select': CATALOG_COLUMNS, 'order': 'id.asc',
            })

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

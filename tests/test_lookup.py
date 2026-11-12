import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server


def item(title, snippet='Écran 6,1 pouces OLED, photo 48 MP, batterie 5000 mAh, 5G'):
    return {'title': title, 'snippet': snippet, 'url': 'https://example.com/phone'}


class ModelEvidenceTests(unittest.TestCase):
    def test_complete_bare_samsung_models_normalize_without_changing_skus(self):
        cases = [('A17', 'Galaxy A17'), ('A18', 'Galaxy A18'), ('A26', 'Galaxy A26'),
                 ('A37', 'Galaxy A37'), ('A17 128Go', 'Galaxy A17 128Go'),
                 ('A175G', 'Galaxy A17 5G'), ('A174G 128Go', 'Galaxy A17 4G 128Go'),
                 ('S26Ultra', 'Galaxy S26 Ultra'), ('ZFold7', 'Galaxy Z Fold7')]
        for query, normalized in cases:
            with self.subTest(query=query):
                self.assertEqual(server._normalize_model_text(query), normalized)
                self.assertEqual(server._query_model(query)[1], 'SAMSUNG')
        for reference in ['SM-A175F/DS', 'SM-A176BZKAEUB', 'A175F', 'MZB12345', '1234567890123']:
            with self.subTest(reference=reference):
                self.assertEqual(server._normalize_model_text(reference), reference)
                self.assertIsNone(server._query_model(reference))

    def test_bare_samsung_radio_and_capacity_keep_distinct_identities(self):
        four_g = server._query_model('A174G 128Go')[0]
        five_g = server._query_model('A175G 128Go')[0]
        self.assertTrue(server._same_model(four_g, 'Galaxy A17 (4G)', 'SAMSUNG'))
        self.assertTrue(server._same_model(five_g, 'Galaxy A17 5G', 'SAMSUNG'))
        self.assertFalse(server._same_model(four_g, five_g, 'SAMSUNG'))
        self.assertEqual(server._parse_capacity(server._normalize_model_text('A17 4G 4 Go RAM 128Go')), '128 Go')
        for name in ['A17', 'A18', 'A26', 'A37', 'S26Ultra', 'ZFold7']:
            with self.subTest(name=name):
                normalized = server._normalize_model_text(name)
                self.assertEqual(server.database_key(name + ' 128Go'), server.database_key('Samsung ' + normalized + ' 128Go'))
        self.assertEqual(server.database_key('S26Ultra512Go'), server.database_key('Samsung Galaxy S26 Ultra 512 Go'))

    def test_variants_remain_distinct(self):
        self.assertFalse(server._same_model('Redmi Note 14 Pro', 'Redmi Note 14 Pro+', 'XIAOMI'))
        self.assertFalse(server._same_model('Redmi Note 14 Pro 4G', 'Redmi Note 14 Pro 5G', 'XIAOMI'))
        self.assertFalse(server._same_model('iPhone 16', 'iPhone 16 Pro Max', 'APPLE'))
        self.assertEqual(server._query_model('Samsung S25+ 256 Go')[0], 'Galaxy S25+')
        self.assertEqual(server._query_model('Redmi Note 14 Pro+ 5G')[0], 'Redmi Note 14 Pro+ 5G')
        self.assertEqual(server._query_model('iPhone16 128Go')[0], 'iPhone 16')

    def test_storage_units_and_ram(self):
        for value, expected in [('1 To', '1 To'), ('2 TB', '2 To'), ('1024 GB', '1 To'),
                                ('32 Go', '32 Go'), ('8 Go RAM', None), ('256 To', None)]:
            with self.subTest(value=value):
                self.assertEqual(server._parse_capacity(value), expected)
        self.assertEqual(server._parse_capacity('16 Go RAM 512 Go'), '512 Go')
        self.assertEqual(server._parse_capacity('iPhone SE 2016 16 Go'), '16 Go')

    def test_older_iphones_and_compact_subbrand_names(self):
        for query in ['iPhone X', 'iPhone XR 64 Go', 'iPhone XS Max 256 Go',
                      'iPhone SE 2020 128 Go', 'iPhone SE 2022',
                      'XiaomiRedmiNote13Pro+5G 256 Go', 'Xiaomi17T 512Go']:
            with self.subTest(query=query):
                self.assertIsNotNone(server._query_model(query))
        self.assertTrue(server._same_model('iPhone SE 2022', 'iPhone SE (3e génération)', 'APPLE'))
        self.assertFalse(server._same_model('iPhone SE 2020', 'iPhone SE (3e génération)', 'APPLE'))
        expected = server._query_model('iPhone 16 Pro Max Deluxe 256 Go')
        self.assertIsNotNone(expected)
        self.assertFalse(server._same_model(expected[0], 'iPhone 16 Pro Max', 'APPLE'))

    def test_comparisons_and_other_phones_are_not_evidence(self):
        for title in ['iPhone 16 vs iPhone 16 Pro Max', 'iPhone 16 et iPhone 15', 'iPhone 16 Pro Max']:
            with self.subTest(title=title):
                self.assertEqual(server._specs_from_public('iPhone 16', [item(title)]), {})

    def test_requested_capacity_requires_evidence(self):
        data = server._analyze_model_query('iPhone 16 512 Go', [item('iPhone 16 128 Go')])
        self.assertEqual(data['capacity'], 'Non précisée')
        self.assertFalse(data['capacity_confirmed'])
        data = server._analyze_model_query('iPhone 16 128 Go', [item('iPhone 16 128 Go')])
        self.assertTrue(data['capacity_confirmed'])

    def test_family_sku_with_multiple_options_has_no_invented_storage(self):
        data = server._analyze('SM-S938B', [item('Samsung Galaxy S25 Ultra SM-S938B 256 Go 512 Go 1 To')])
        self.assertEqual(data['capacity'], 'Non précisée')
        self.assertFalse(data['capacity_confirmed'])


class LookupAPITests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.access = patch.dict(os.environ, {'APP_ACCESS_TOKEN':'test-access'})
        self.access.start()
        self.addCleanup(self.access.stop)
        server.CACHE.clear()
        server.REQUEST_LOG.clear()
        server.GLOBAL_REQUEST_LOG.clear()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app), base_url='http://test', auth=('encre','test-access'))

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_bare_and_compact_samsung_names_reach_lookup_as_models(self):
        result = {'status': 'verified_online', 'brand': 'SAMSUNG', 'model': 'Galaxy A17',
                  'capacity': 'Non précisée', 'specs': {}, 'sources': []}
        with patch.object(server, 'online_lookup', new=AsyncMock(return_value=result)) as lookup:
            for query in ['A17', 'A18', 'A26', 'A37', 'A17 128Go', 'A175G',
                          'Galaxy A17 4G', 'Galaxy A17 5G', 'S26Ultra', 'ZFold7',
                          'SamsungA26', 'SamsungA37', 'Samsung A17']:
                with self.subTest(query=query):
                    response = await self.client.get('/api/phones/lookup', params={'reference': query})
                    self.assertEqual(response.status_code, 200, response.text)
                    normalized = server._normalize_model_text(query).upper()
                    lookup.assert_awaited_with(normalized, is_model_query=True)
                    self.assertEqual(response.json()['reference'], normalized)

    async def test_manufacturer_reference_keeps_sku_classification(self):
        result = {'status': 'verified_online', 'brand': 'SAMSUNG', 'model': 'Galaxy A17',
                  'capacity': '128 Go', 'specs': {}, 'sources': []}
        with patch.object(server, 'online_lookup', new=AsyncMock(return_value=result)) as lookup:
            for reference in ['SM-A175F/DS', 'SM-A176BZKAEUB', 'A175F']:
                with self.subTest(reference=reference):
                    response = await self.client.get('/api/phones/lookup', params={'reference': reference})
                    self.assertEqual(response.status_code, 200, response.text)
                    lookup.assert_awaited_with(reference, is_model_query=False)
                    self.assertEqual(response.json()['reference'], reference)

    async def test_bare_samsung_suggestions_use_the_same_canonical_query(self):
        models = [{'brand': 'SAMSUNG', 'model': 'Galaxy A26 5G', 'capacities': ['128 Go'], '_aliases': []},
                  {'brand': 'SAMSUNG', 'model': 'Galaxy A37 5G', 'capacities': ['256 Go'], '_aliases': []}]
        with patch.object(server, '_known_phone_models', return_value=models), patch.object(server, 'online_lookup', new_callable=AsyncMock) as lookup:
            for query, model in [('A26', 'Galaxy A26 5G'), ('A37', 'Galaxy A37 5G')]:
                with self.subTest(query=query):
                    response = await self.client.get('/api/phones/models', params={'query': query})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertIn(model, [row['model'] for row in response.json()['models']])
            lookup.assert_not_awaited()

    async def test_official_lookups_do_not_use_gemini(self):
        with patch.object(server, 'GEMINI_KEY', 'configured-but-no-quota'), patch.object(server, 'gemini_lookup', new_callable=AsyncMock) as ai:
            for query, model in [('iPhone16 128 Go', 'iPhone 16'), ('SM-S938B', 'Galaxy S25 Ultra'),
                                 ('SM-S938B/DS 512 Go', 'Galaxy S25 Ultra'), ('Samsung S25+', 'Galaxy S25+'),
                                 ('Samsung A56 256 Go', 'Galaxy A56 5G')]:
                with self.subTest(query=query):
                    response = await self.client.get('/api/phones/lookup', params={'reference': query})
                    self.assertEqual(response.status_code, 200, response.text)
                    data = response.json()
                    self.assertEqual(data['model'], model)
                    self.assertGreaterEqual(len(data['specs']), 3)
                    self.assertTrue(data['sources'])
            ai.assert_not_awaited()

    async def test_recent_models_from_each_catalogue_do_not_use_ai(self):
        with patch.object(server, 'GEMINI_KEY', 'configured-but-no-quota'), patch.object(server, 'gemini_lookup', new_callable=AsyncMock) as ai:
            for query, brand in [('Xiaomi 17T 256 Go', 'XIAOMI'), ('Xiaomi 17T Pro 512 Go', 'XIAOMI'),
                                 ('POCO X7 Pro', 'XIAOMI'), ('Samsung Galaxy S26 Ultra', 'SAMSUNG'),
                                 ('Samsung Galaxy A57 5G', 'SAMSUNG'), ('iPhone 17 Pro Max', 'APPLE'),
                                 ('iPhone XR', 'APPLE'), ('iPhone SE 2022 128 Go', 'APPLE')]:
                with self.subTest(query=query):
                    response = await self.client.get('/api/phones/lookup', params={'reference': query})
                    self.assertEqual(response.status_code, 200, response.text)
                    data=response.json()
                    self.assertEqual(data['brand'], brand)
                    self.assertGreaterEqual(len(data['specs']), 3)
                    self.assertTrue(data['sources'])
            ai.assert_not_awaited()

    async def test_storage_attached_to_the_model_is_normalized_before_providers(self):
        with patch.object(server,'gemini_lookup',new_callable=AsyncMock) as ai:
            for query,capacity in [('iPhone17ProMax512Go','512 Go'),
                                   ('iPhone17ProMax1To','1 To'),('Xiaomi17T256GB','256 Go')]:
                with self.subTest(query=query):
                    response=await self.client.get('/api/phones/lookup',params={'reference':query})
                    self.assertEqual(response.status_code,200,response.text)
                    self.assertEqual(response.json()['capacity'],capacity)
                    self.assertTrue(response.json()['capacity_confirmed'])
            ai.assert_not_awaited()

    async def test_suggestions_are_fast_and_do_not_consume_lookup_quota(self):
        with patch.object(server, 'online_lookup', new_callable=AsyncMock) as lookup:
            response=await self.client.get('/api/phones/models',params={'query':'xiaomi 17t'})
            self.assertEqual(response.status_code,200)
            data=response.json()
            self.assertIn('Xiaomi 17T',[row['model'] for row in data['models']])
            self.assertIn('Xiaomi 17T Pro',[row['model'] for row in data['models']])
            self.assertTrue(all('_aliases' not in row for row in data['models']))
            lookup.assert_not_awaited()
            self.assertEqual(server.GLOBAL_REQUEST_LOG,[])

    async def test_ambiguous_se_is_explained_and_unknown_name_can_be_searched(self):
        response=await self.client.get('/api/phones/lookup',params={'reference':'iPhone SE'})
        self.assertEqual(response.status_code,400)
        with patch.object(server, 'online_lookup', new=AsyncMock(return_value={'status':'not_found','search_engines':{}})) as lookup:
            response=await self.client.get('/api/phones/lookup',params={'reference':'iPhone16Ultra'})
            self.assertEqual(response.status_code,404)
            self.assertTrue(lookup.await_args.kwargs['is_model_query'])

    async def test_impossible_capacity_is_explained(self):
        response = await self.client.get('/api/phones/lookup', params={'reference': 'iPhone 16 1 To'})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['detail']['capacities'], ['128 Go', '256 Go', '512 Go'])

    async def test_unknown_family_sku_exposes_storage_choices(self):
        data = (await self.client.get('/api/phones/lookup', params={'reference': 'SM-S938B'})).json()
        self.assertEqual(data['capacity'], 'Non précisée')
        self.assertFalse(data['capacity_confirmed'])
        self.assertEqual(data['available_capacities'], ['256 Go', '512 Go', '1 To'])

    async def test_timeout_is_actionable(self):
        with patch.object(server, 'online_lookup', new=AsyncMock(side_effect=asyncio.TimeoutError)):
            response = await self.client.get('/api/phones/lookup', params={'reference': 'Google Pixel 9'})
        self.assertEqual(response.status_code, 504)
        self.assertIn('nom exact', response.json()['detail']['message'])

    async def test_failed_search_cache_expires_after_one_minute(self):
        key = 'V14:MODEL:GOOGLE PIXEL 9:PUBLIC'
        server.CACHE[key] = (0, {'status': 'not_found'})
        result = {'status': 'verified_online', 'model': 'Pixel 9',
                  'specs': {'ecran':'6,3″ OLED','photo':'50 Mpx','batterie':'4700 mAh'}}
        with patch.object(server, 'GEMINI_KEY', ''), patch.object(server.time, 'time', return_value=61), \
             patch.object(server, 'lookup_official_phone', new=AsyncMock(return_value=result)) as lookup:
            self.assertEqual(await server.online_lookup('GOOGLE PIXEL 9', True), result)
            lookup.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()

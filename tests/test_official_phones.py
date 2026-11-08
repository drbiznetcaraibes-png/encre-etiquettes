import unittest
from unittest.mock import patch

import httpx

import official_phones


APPLE_SHEET = '''<!doctype html><h1>iPhone 16 Pro - Caractéristiques techniques</h1>
<div><h3>Capacité<sup>1</sup></h3><ul><li>128 Go</li><li>256 Go</li><li>512 Go</li><li>1 To</li></ul></div>
<div><h3>Écran</h3><p>OLED tout écran de 6,3 pouces. ProMotion 120 Hz.</p></div>
<div><h3>Puce</h3><p>Puce A18 Pro</p></div>
<div><h3>Caméra</h3><p>Caméra principale Fusion 48 MP</p></div>
<div><h3>Alimentation et batterie</h3><p>Lecture vidéo : Jusqu’à 27 heures</p></div>
<div><h3>Connectivité cellulaire et sans fil</h3><p>5G NR</p></div>'''


class OfficialLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        official_phones._PAGE_CACHE.clear()
        official_phones._INDEX_CACHE = None
        self.requests = []

    def transport(self, request):
        self.requests.append(str(request.url))
        return httpx.Response(200, text=APPLE_SHEET)

    async def test_failed_public_queries_are_resolved_without_ai_or_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            iphone = await official_phones.lookup_official_phone(client, 'Apple iPhone 16 128 Go')
            samsung = await official_phones.lookup_official_phone(client, 'SM-S938B/DS')
        self.assertEqual(iphone['model'], 'iPhone 16')
        self.assertEqual(iphone['capacity'], '128 Go')
        self.assertTrue(iphone['capacity_confirmed'])
        self.assertEqual(samsung['model'], 'Galaxy S25 Ultra')
        self.assertEqual(samsung['capacity'], 'Non précisée')
        self.assertFalse(samsung['capacity_confirmed'])
        self.assertEqual(samsung['available_capacities'], ['256 Go', '512 Go', '1 To'])
        self.assertEqual(self.requests, [])

    async def test_different_suffix_is_never_replaced_with_base_model(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            wrong = await official_phones.lookup_official_phone(client, 'Samsung Galaxy S25 Ultra Pro 256 Go')
            plus = await official_phones.lookup_official_phone(client, 'Samsung S25+ 256Go')
            ultra_5g = await official_phones.lookup_official_phone(client, 'Samsung S25 Ultra 5G 256 Go')
            wrong_4g = await official_phones.lookup_official_phone(client, 'Samsung S25 Ultra 4G 256 Go')
        self.assertIsNone(wrong)
        self.assertEqual(plus['model'], 'Galaxy S25+')
        self.assertEqual(plus['specs']['batterie'], '4 900 mAh')
        self.assertEqual(ultra_5g['model'], 'Galaxy S25 Ultra')
        self.assertIsNone(wrong_4g)

    async def test_invalid_capacity_cannot_silently_select_another_variant(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            result = await official_phones.lookup_official_phone(client, 'iPhone16 1 To')
        self.assertEqual(result['status'], 'invalid_capacity')
        self.assertEqual(result['capacities'], ['128 Go', '256 Go', '512 Go'])
        self.assertEqual(result['requested_capacity'], '1 To')

    async def test_ram_is_not_used_as_storage_and_gigabytes_normalize(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            result = await official_phones.lookup_official_phone(client, 'Samsung A56 8 Go RAM 256 Go')
            terabyte = await official_phones.lookup_official_phone(client, 'Samsung S25 Ultra 1024 GB')
        self.assertEqual(result['capacity'], '256 Go')
        self.assertNotIn('ram', result['specs'])
        self.assertEqual(terabyte['capacity'], '1 To')

    async def test_live_apple_sheet_uses_exact_heading_and_explicit_sections(self):
        with patch.object(official_phones, 'CATALOG', {'phones': []}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
                result = await official_phones.lookup_official_phone(client, 'iPhone16 Pro 1 To')
                invalid = await official_phones.lookup_official_phone(client, 'iPhone 16 Pro 2 To')
        self.assertEqual(result['model'], 'iPhone 16 Pro')
        self.assertEqual(result['capacity'], '1 To')
        self.assertEqual(result['specs']['processeur'], 'Apple A18 Pro')
        self.assertEqual(result['specs']['batterie'], 'Jusqu’à 27 h de vidéo')
        self.assertEqual(invalid['status'], 'invalid_capacity')
        self.assertEqual(len(self.requests), 1)

    async def test_apple_redirect_or_page_for_another_model_is_rejected(self):
        with patch.object(official_phones, 'CATALOG', {'phones': []}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
                result = await official_phones.lookup_official_phone(client, 'iPhone 16 Pro Max 256 Go')
        self.assertIsNone(result)

    async def test_older_apple_sheet_uses_rear_camera_not_truedepth(self):
        document = APPLE_SHEET.replace('iPhone 16 Pro', 'iPhone 14 Pro')
        document = document.replace('<h3>Caméra</h3>', '<h3>Appareil photo</h3>')
        document += '<div><h3>Caméra TrueDepth</h3><p>Caméra 12 MP</p></div>'
        result = official_phones.parse_apple_sheet(document, 'IPHONE14PRO', 'https://support.apple.com/fr-fr/111849')
        self.assertEqual(result['specs']['photo'], '48 Mpx (principal)')
        without_rear_section = document.replace('<h3>Appareil photo</h3>', '<h3>Autres fonctions</h3>')
        result = official_phones.parse_apple_sheet(without_rear_section, 'IPHONE14PRO', 'https://support.apple.com/fr-fr/111849')
        self.assertNotIn('photo', result['specs'])

    async def test_manufacturer_unavailable_allows_next_provider(self):
        def failing(request):
            return httpx.Response(503)
        with patch.object(official_phones, 'CATALOG', {'phones': []}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
                result = await official_phones.lookup_official_phone(client, 'iPhone 16 Pro 256 Go')
        self.assertIsNone(result)

    async def test_all_common_iphone_families_work_when_apple_and_ai_are_unreachable(self):
        def offline(request):
            self.fail('A known iPhone must not need a network request')
        cases = [
            ('iPhone 7 32 Go', 'iPhone 7', '32 Go', '4G LTE'),
            ('iPhone 7 Plus 128 Go', 'iPhone 7 Plus', '128 Go', '4G LTE'),
            ('iPhone 8 64 Go', 'iPhone 8', '64 Go', '4G LTE'),
            ('iPhone 8 Plus 256 Go', 'iPhone 8 Plus', '256 Go', '4G LTE'),
            ('iPhone X 64 Go', 'iPhone X', '64 Go', '4G LTE'),
            ('iPhone XR 128 Go', 'iPhone XR', '128 Go', '4G LTE'),
            ('iPhone XS 512 Go', 'iPhone XS', '512 Go', '4G LTE'),
            ('iPhone XS Max 256 Go', 'iPhone XS Max', '256 Go', '4G LTE'),
            ('iPhone 11 128 Go', 'iPhone 11', '128 Go', '4G LTE'),
            ('iPhone 11 Pro 64 Go', 'iPhone 11 Pro', '64 Go', '4G LTE'),
            ('iPhone 11 Pro Max 512 Go', 'iPhone 11 Pro Max', '512 Go', '4G LTE'),
            ('iPhone 12 64 Go', 'iPhone 12', '64 Go', '5G'),
            ('iPhone 12 mini 128 Go', 'iPhone 12 mini', '128 Go', '5G'),
            ('iPhone 12 Pro 512 Go', 'iPhone 12 Pro', '512 Go', '5G'),
            ('iPhone 12 Pro Max 256 Go', 'iPhone 12 Pro Max', '256 Go', '5G'),
            ('iPhone 13 128 Go', 'iPhone 13', '128 Go', '5G'),
            ('iPhone 13 mini 256 Go', 'iPhone 13 mini', '256 Go', '5G'),
            ('iPhone 13 Pro 1 To', 'iPhone 13 Pro', '1 To', '5G'),
            ('iPhone 13 Pro Max 512 Go', 'iPhone 13 Pro Max', '512 Go', '5G'),
            ('iPhone 14 128 Go', 'iPhone 14', '128 Go', '5G'),
            ('iPhone 14 Plus 256 Go', 'iPhone 14 Plus', '256 Go', '5G'),
            ('iPhone 14 Pro 1 To', 'iPhone 14 Pro', '1 To', '5G'),
            ('iPhone 14 Pro Max 512 Go', 'iPhone 14 Pro Max', '512 Go', '5G'),
            ('iPhone 15 128 Go', 'iPhone 15', '128 Go', '5G'),
            ('iPhone 15 Plus 256 Go', 'iPhone 15 Plus', '256 Go', '5G'),
            ('iPhone 15 Pro 1 To', 'iPhone 15 Pro', '1 To', '5G'),
            ('iPhone 15 Pro Max 512 Go', 'iPhone 15 Pro Max', '512 Go', '5G'),
            ('iPhone 16 128 Go', 'iPhone 16', '128 Go', '5G'),
            ('iPhone 16 Plus 256 Go', 'iPhone 16 Plus', '256 Go', '5G'),
            ('iPhone 16 Pro 1 To', 'iPhone 16 Pro', '1 To', '5G'),
            ('iPhone 16 Pro Max 512 Go', 'iPhone 16 Pro Max', '512 Go', '5G'),
            ('iPhone 16e 128 Go', 'iPhone 16e', '128 Go', '5G'),
            ('iPhone 17 256 Go', 'iPhone 17', '256 Go', '5G'),
            ('iPhone 17 Pro 1 To', 'iPhone 17 Pro', '1 To', '5G'),
            ('iPhone 17 Pro Max 2 To', 'iPhone 17 Pro Max', '2 To', '5G'),
            ('iPhone 17e 256 Go', 'iPhone 17e', '256 Go', '5G'),
            ('iPhone Air 512 Go', 'iPhone Air', '512 Go', '5G'),
            ('iPhone SE 2016 16 Go', 'iPhone SE (2016)', '16 Go', '4G LTE'),
            ('iPhone SE 2020 64 Go', 'iPhone SE (2020)', '64 Go', '4G LTE'),
            ('iPhone SE 2022 128 Go', 'iPhone SE (2022)', '128 Go', '5G'),
        ]
        async with httpx.AsyncClient(transport=httpx.MockTransport(offline)) as client:
            for query, model, capacity, network in cases:
                with self.subTest(query=query):
                    result = await official_phones.lookup_official_phone(client, query)
                    self.assertIsNotNone(result)
                    self.assertEqual(result['model'], model)
                    self.assertEqual(result['capacity'], capacity)
                    self.assertTrue(result['capacity_confirmed'])
                    self.assertEqual(result['specs']['reseau'], network)
                    self.assertGreaterEqual(len(result['specs']), 4)
                    self.assertTrue(all(source['url'].startswith('https://support.apple.com/fr-fr/') for source in result['sources']))

    async def test_se_requires_generation_and_capacities_remain_model_specific(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            self.assertIsNone(await official_phones.lookup_official_phone(client, 'iPhone SE 128 Go'))
            se2 = await official_phones.lookup_official_phone(client, 'iPhone SE 2 128 Go')
            se3 = await official_phones.lookup_official_phone(client, 'iPhone SE (3e génération) 128 Go')
            self.assertEqual(se2['specs']['processeur'], 'Apple A13 Bionic')
            self.assertEqual(se3['specs']['processeur'], 'Apple A15 Bionic')
            for query in ['iPhone 7 64 Go', 'iPhone 11 Pro 128 Go', 'iPhone 17 128 Go', 'iPhone X 512 Go']:
                with self.subTest(query=query):
                    result = await official_phones.lookup_official_phone(client, query)
                    self.assertEqual(result['status'], 'invalid_capacity')
        self.assertEqual(self.requests, [])

    async def test_legacy_apple_sections_preserve_radio_battery_and_fusion_chip(self):
        document = '''<h1>iPhone 7 - Caractéristiques techniques</h1>
        <div><h3>Capacité</h3><p>32 Go 128 Go 256 Go</p></div>
        <div><h3>Écran</h3><p>LCD panoramique de 4,7 pouces</p></div>
        <div><h3>Appareil photo</h3><p>Appareil photo 12 Mpx</p></div>
        <div><h3>Puce</h3><p>Puce A10 Fusion</p></div>
        <div><h3>Alimentation et batterie</h3><p>Lecture vidéo sans fil : Jusqu’à 13 heures</p></div>
        <div><h3>Réseaux cellulaires et sans fil</h3><p>FDD-LTE</p></div>'''
        result = official_phones.parse_apple_sheet(document, 'IPHONE7', 'https://support.apple.com/fr-fr/111943')
        self.assertEqual(result['specs']['processeur'], 'Apple A10 Fusion')
        self.assertEqual(result['specs']['reseau'], '4G LTE')
        self.assertEqual(result['specs']['batterie'], 'Jusqu’à 13 h de vidéo')
        document = document.replace('iPhone 7', 'iPhone SE').replace('4,7 pouces', '4 pouces')
        document = document.replace('Puce A10 Fusion', 'Puce A9')
        document = document.replace('Lecture vidéo sans fil :', 'Lecture vidéo')
        result = official_phones.parse_apple_sheet(document, 'IPHONESE', 'https://support.apple.com/fr-fr/112005')
        self.assertEqual(result['specs']['ecran'], '4″ LCD')
        self.assertEqual(result['specs']['batterie'], 'Jusqu’à 13 h de vidéo')

    async def test_index_supports_locale_free_and_legacy_apple_links(self):
        index = '''<a href="https://support.apple.com/125090">Consultez les caractéristiques techniques de l’iPhone 17 Pro</a>
        <a href="https://support.apple.com/kb/SP851?locale=fr_FR">caractéristiques techniques de l’iPhone 13</a>
        <a href="https://example.com/123456">caractéristiques techniques de l’iPhone 99</a>'''
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=index))) as client:
            links = await official_phones._apple_index(client)
        self.assertEqual(links['IPHONE17PRO'], 'https://support.apple.com/fr-fr/125090')
        self.assertEqual(links['IPHONE13'], 'https://support.apple.com/kb/SP851?locale=fr_FR')
        self.assertNotIn('IPHONE99', links)


if __name__ == '__main__':
    unittest.main()

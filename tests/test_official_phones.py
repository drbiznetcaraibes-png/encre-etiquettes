import unittest

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
        async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
            result = await official_phones.lookup_official_phone(client, 'iPhone 16 Pro 256 Go')
        self.assertIsNone(result)

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

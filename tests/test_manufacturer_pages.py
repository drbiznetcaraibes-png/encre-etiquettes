import json
import unittest

import httpx

import manufacturer_pages as provider


def honor_group(title, *values):
    fields = ''.join(f'<div data-class="p2" data-value="{value}"></div>' for value in values)
    return f'<div class="products-spec-component-level-1"><h2 class="products-spec-component-title-text">{title}</h2>{fields}</div>'


HONOR = ('<h1 class="product-name">HONOR 400</h1>'
         + honor_group('Écran', '6,55 pouces', 'AMOLED')
         + honor_group('Processeur', 'Snapdragon 7 Gen 3')
         + honor_group('Mémoire', '8Go + 256Go\n8Go + 512Go')
         + honor_group('Appareil photo arrière', 'Caméra Ultra HD 200MP + 12MP')
         + honor_group('Appareil photo avant', '50 MP')
         + honor_group('Batterie', '5300 mAh (valeur nominale)')
         + honor_group('Réseau cellulaire', '5G NR 4G LTE')
         + '<footer>HONOR Magic7 Pro 200MP 6000mAh 1To</footer>')


def oneplus_document(model='OnePlus 13'):
    groups = [
        {'GroupName': 'General', 'nameDetailList': [{'name': 'SPU_NAME', 'content': model}]},
        {'GroupName': 'Écran', 'nameDetailList': [{'content': '6,82 pouces LTPO 120 Hz'}]},
        {'GroupName': 'Performances', 'nameDetailList': [{'content': 'Snapdragon® 8 Elite\nRAM : 12 Go/16 Go\nStockage : 256 Go/512 Go\nBatterie : 6 000 mAh (double cellule 3 000 mAh)'}]},
        {'GroupName': 'Appareil photo', 'nameDetailList': [
            {'name': 'Appareil photo grand angle', 'content': 'Mégapixels : 50'},
            {'name': 'Appareil photo avant', 'content': 'Mégapixels : 32'}]},
        {'GroupName': 'Connectivité', 'nameDetailList': [{'content': '5G SA'}]},
    ]
    payload = {'byId': {'specs': {'attr': {'spuData': {'parameterData': groups}}}}}
    return '<h1>OnePlus 13 Specs</h1><script>window.pageDsl=' + json.dumps(payload) + ';</script><footer>OnePlus 12 5400mAh</footer>'


def realme_group(title, text):
    return f'<div class="sec-params-unit"><h2 class="sec-params-label">{title}</h2><div class="sec-params-description">{text}</div></div>'


REALME = ('<title>realme 14 Pro 5G Specifications - realme (Global)</title>'
          + realme_group('Processor', 'Dimensity 7300 Energy')
          + realme_group('Memory &amp; Storage', 'RAM：8GB/12GB\nROM：128G/256GB/512GB\nUp to 14GB Dynamic RAM')
          + realme_group('Display', 'Size: 6.77 inches\nMaterial: OLED\nRefresh rate: 120Hz')
          + realme_group('Charging &amp; Battery', '6000mAh (typ)\n5860mAh (min)')
          + realme_group('Camera', '50MP Sony IMX882 OIS Camera\n16MP Selfie Camera')
          + realme_group('Cellular &amp; Wireless', '5G SA/NSA')
          + '<footer>realme 15 Pro 7000mAh 50MP</footer>')


class ManufacturerPageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        provider._CACHE.clear()
        self.urls = []

    def transport(self, request):
        self.urls.append(str(request.url))
        documents = {'www.honor.com': HONOR, 'www.oneplus.com': oneplus_document(), 'www.realme.com': REALME}
        return httpx.Response(200, text=documents.get(request.url.host, ''))

    async def lookup(self, query):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            return await provider.lookup_manufacturer_phone(client, query)

    async def test_honor_reads_scoped_data_values_and_storage_pairs(self):
        result = await self.lookup('HONOR400 256Go')
        self.assertEqual(result['model'], 'HONOR 400')
        self.assertEqual(result['specs']['photo'], '200 Mpx (principal)')
        self.assertEqual(result['specs']['batterie'], '5300 mAh')
        self.assertEqual(result['available_capacities'], ['256 Go', '512 Go'])
        self.assertTrue(result['capacity_confirmed'])
        self.assertEqual(self.urls, ['https://www.honor.com/fr/phones/honor-400/spec/'])

    async def test_oneplus_requires_product_component_and_ignores_front_camera(self):
        result = await self.lookup('OnePlus13 512GB')
        self.assertEqual(result['specs']['photo'], '50 Mpx (principal)')
        self.assertEqual(result['specs']['batterie'], '6000 mAh')
        self.assertEqual(result['specs']['processeur'], 'Snapdragon 8 Elite')
        self.assertEqual(result['available_capacities'], ['256 Go', '512 Go'])
        self.assertEqual(result['capacity'], '512 Go')
        self.assertNotIn('ram', result['specs'])

    async def test_realme_reads_rom_instead_of_ram_and_notes_source_region(self):
        result = await self.lookup('realme14Pro5G 128Go')
        self.assertEqual(result['available_capacities'], ['128 Go', '256 Go', '512 Go'])
        self.assertEqual(result['specs']['photo'], '50 Mpx (principal)')
        self.assertEqual(result['specs']['batterie'], '6000 mAh')
        self.assertIn('(Global)', result['sources'][0]['title'])

    async def test_exact_model_heading_rejects_neighboring_variants(self):
        self.assertIsNone(await self.lookup('HONOR400Pro 256Go'))
        self.assertIsNone(await self.lookup('realme14Pro 256Go'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=oneplus_document('OnePlus 13R')))) as client:
            self.assertIsNone(await provider.lookup_manufacturer_phone(client, 'OnePlus 13'))

    async def test_unsupported_and_invalid_suffixes_skip_network(self):
        for query in ['iPhone 16', 'Samsung A56', 'Xiaomi 15T Pro', 'HONOR 400 Pro Deluxe', 'OnePlus 13 Ultra', 'realme 14 Pro 5G Pro']:
            with self.subTest(query=query):
                self.assertIsNone(await self.lookup(query))
        self.assertEqual(self.urls, [])

    async def test_invalid_storage_is_actionable_and_no_storage_is_invented(self):
        invalid = await self.lookup('HONOR400 1To')
        self.assertEqual(invalid['status'], 'invalid_capacity')
        self.assertEqual(invalid['capacities'], ['256 Go', '512 Go'])
        unspecified = await self.lookup('HONOR400')
        self.assertEqual(unspecified['capacity'], 'Non précisée')
        self.assertFalse(unspecified['capacity_confirmed'])
        self.assertEqual(len(self.urls), 1)

    async def test_foreign_redirect_is_never_followed_even_if_client_follows_redirects(self):
        def redirect(request):
            self.urls.append(str(request.url))
            return httpx.Response(302, headers={'location': 'https://example.com/fake'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
            self.assertIsNone(await provider.lookup_manufacturer_phone(client, 'HONOR400'))
        self.assertEqual(len(self.urls), 1)

    async def test_manufacturer_failure_allows_other_providers(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            self.assertIsNone(await provider.lookup_manufacturer_phone(client, 'OnePlus13'))

    def test_front_sensor_is_not_principal_when_listed_before_rear(self):
        self.assertEqual(provider._photo('16MP Selfie Camera\nRear Camera 50MP OIS'), '50 Mpx (principal)')
        self.assertIsNone(provider._photo('16MP Selfie Camera\nNo rear sensor documented'))


if __name__ == '__main__':
    unittest.main()

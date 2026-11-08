import unittest
from unittest.mock import patch

import httpx

import xiaomi_phones


# Condensed manufacturer-style DOM retaining the difficult labels and variants.
SHEET = '''<html><h1><span>Xiaomi 17T</span><span style="display:none"> Caractéristiques</span></h1>
<div><div><span>RAM &amp; Stockage</span></div><div>12 Go + 256 Go / 12 Go + 512 Go<br>RAM : LPDDR5X</div></div>
<div><div><span>Processeur</span></div><div>MediaTek Dimensity 8500-Ultra</div></div>
<div><div><span>Écran</span></div><div>Écran AMOLED de 6,59 pouces à 120 Hz</div></div>
<div><div><span>Caméra arrière</span></div><div>Caméra principale Leica 50 MP, ultra grand-angle 12 MP</div></div>
<div><div><span>Caméra avant</span></div><div>Caméra selfie 32 MP</div></div>
<div><div><span>Batterie &amp; Charge</span></div><div>6 500 mAh (typique)</div></div>
<div><div><span>Réseau et connectivité</span></div><div>5G SA / 4G LTE</div></div></html>'''


class XiaomiLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        xiaomi_phones._PAGE_CACHE.clear()
        self.requests = []

    def transport(self, request):
        self.requests.append(str(request.url))
        return httpx.Response(200, text=SHEET)

    async def test_xiaomi_17t_works_without_ai_or_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            result = await xiaomi_phones.lookup_xiaomi_phone(client, 'xiaomi 17t 256go')
        self.assertEqual(result['model'], 'Xiaomi 17T')
        self.assertEqual(result['capacity'], '256 Go')
        self.assertEqual(result['specs']['processeur'], 'MediaTek Dimensity 8500-Ultra')
        self.assertEqual(self.requests, [])

    async def test_new_model_discovery_does_not_require_catalogue_entry(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            with patch.object(xiaomi_phones, 'CATALOG', {'phones': []}):
                result = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T 512 Go')
        self.assertEqual(result['search_mode'], 'official_manufacturer_page')
        self.assertEqual(result['capacity'], '512 Go')
        self.assertEqual(result['specs']['photo'], '50 Mpx (principal)')
        self.assertTrue(all(url.startswith('https://www.mi.com/') for url in self.requests))

    async def test_future_generation_uses_same_dynamic_reader(self):
        def future(request):
            self.requests.append(str(request.url))
            return httpx.Response(200, text=SHEET.replace('Xiaomi 17T', 'Xiaomi 18T'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(future)) as client:
            result = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 18T 512 Go')
        self.assertEqual(result['model'], 'Xiaomi 18T')
        self.assertEqual(result['capacity'], '512 Go')
        self.assertTrue(any('/xiaomi-18t/specs/' in url for url in self.requests))

    async def test_unknown_and_impossible_storage_are_distinct(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            model = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T')
            wrong = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T 1 To')
        self.assertFalse(model['capacity_confirmed'])
        self.assertEqual(model['capacity'], 'Non précisée')
        self.assertEqual(wrong['status'], 'invalid_capacity')
        self.assertEqual(wrong['capacities'], ['256 Go', '512 Go'])

    async def test_ram_and_combined_memory_do_not_change_storage(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            first = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T 12 Go RAM 256 Go')
            second = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T 12/256 Go')
        self.assertEqual(first['capacity'], '256 Go')
        self.assertEqual(second['capacity'], '256 Go')
        self.assertNotIn('ram', first['specs'])

    async def test_pro_plus_and_radio_variants_are_distinct(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            base = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi Redmi Note 14 Pro 256 Go')
            pro5g = await xiaomi_phones.lookup_xiaomi_phone(client, 'Redmi Note 14 Pro 5G 256 Go')
            plus5g = await xiaomi_phones.lookup_xiaomi_phone(client, 'Redmi Note 14 Pro+ 5G 256 Go')
        self.assertEqual(base['specs']['reseau'], '4G')
        self.assertEqual(pro5g['specs']['reseau'], '5G')
        self.assertNotEqual(pro5g['model'], plus5g['model'])
        self.assertNotEqual(pro5g['specs']['processeur'], plus5g['specs']['processeur'])

    async def test_wrong_model_page_is_not_accepted(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            with patch.object(xiaomi_phones, 'CATALOG', {'phones': []}):
                result = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T Pro')
        self.assertIsNone(result)

    async def test_redirect_outside_manufacturer_is_not_evidence(self):
        def redirect(request):
            if request.url.host == 'www.mi.com':
                return httpx.Response(302, headers={'Location': 'https://example.com/pretend-xiaomi'})
            return httpx.Response(200, text=SHEET)
        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
            with patch.object(xiaomi_phones, 'CATALOG', {'phones': []}):
                result = await xiaomi_phones.lookup_xiaomi_phone(client, 'Xiaomi 17T')
        self.assertIsNone(result)

    def test_virtual_ram_and_sd_card_limits_are_not_factory_storage(self):
        document = SHEET.replace('12 Go + 256 Go / 12 Go + 512 Go', "12 Go, 256 Go + 8 Go d'espace supplémentaire | 12 Go, 1 To + 32 Go d'espace supplémentaire. Stockage extensible jusqu’à 2 To")
        result = xiaomi_phones.parse_xiaomi_sheet(document, 'XIAOMI17T', 'https://www.mi.com/fr/product/xiaomi-17t/specs/')
        self.assertEqual(result['capacities'], ['256 Go', '1 To'])

    def test_catalogue_has_only_sourced_complete_unique_models(self):
        phones = xiaomi_phones.CATALOG['phones']
        self.assertGreaterEqual(len(phones), 30)
        self.assertEqual(len({xiaomi_phones._identity(p['model']) for p in phones}), len(phones))
        for phone in phones:
            self.assertGreaterEqual(len(phone['specs']), 3)
            self.assertTrue(phone['capacities'])
            self.assertTrue(phone['sources'][0]['url'].startswith('https://www.mi.com/'))


if __name__ == '__main__':
    unittest.main()

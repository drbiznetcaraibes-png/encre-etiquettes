import unittest

import httpx

import samsung_phones


URL = 'https://www.samsungmobilepress.com/articles/new-galaxy-s24-fe'
SHEET = '''<h1>Samsung introduces Galaxy S24 FE</h1><table>
<tr><td colspan="5">Galaxy S24 FE</td></tr>
<tr><td>Display</td><td colspan="2">6.7-inch Dynamic AMOLED 2X Display 120 Hz</td></tr>
<tr><td>Camera</td><td colspan="2">12MP Ultra-Wide Camera 50MP Wide Camera 8MP Telephoto Camera 10MP Front Camera</td></tr>
<tr><td>Memory &amp; Storage</td><td colspan="2">8 GB + 128 GB 8 + 256 GB MicroSD up to 2TB</td></tr>
<tr><td>Battery</td><td colspan="2">4,700 mAh</td></tr>
<tr><td>Processor</td><td colspan="2">Exynos 2400e</td></tr>
<tr><td>Network and Connectivity</td><td colspan="2">5G LTE</td></tr></table>'''


class SamsungLookupTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        samsung_phones._CACHE.clear()
        samsung_phones._INDEX_CACHE.clear()
        self.requests = []

    async def test_recent_exact_models_work_without_network(self):
        def transport(request):
            self.requests.append(str(request.url))
            return httpx.Response(500)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            for query, model in [('Samsung Galaxy S26 Ultra 512 Go', 'Galaxy S26 Ultra'),
                                 ('Samsung S26 Plus 256 GB', 'Galaxy S26+'),
                                 ('Galaxy A57 256 Go', 'Galaxy A57 5G'),
                                 ('Samsung Galaxy Z Fold 8 Ultra 1 To', 'Galaxy Z Fold8 Ultra'),
                                 ('Galaxy S25 FE 128 Go', 'Galaxy S25 FE')]:
                result = await samsung_phones.lookup_samsung_phone(client, query)
                self.assertEqual(result['model'], model)
                self.assertTrue(result['capacity_confirmed'])
        self.assertEqual(self.requests, [])
        self.assertGreaterEqual(len(samsung_phones.CATALOG['phones']), 10)

    async def test_storage_is_explicit_and_ram_is_separate(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            unspecified = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 Ultra')
            ram = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 Ultra 12 Go RAM 256 Go')
            invalid = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 128 Go')
            ambiguous = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 256 Go 512 Go')
        self.assertEqual(unspecified['capacity'], 'Non précisée')
        self.assertFalse(unspecified['capacity_confirmed'])
        self.assertNotIn('ram', unspecified['specs'])
        self.assertEqual(ram['capacity'], '256 Go')
        self.assertEqual(invalid['status'], 'invalid_capacity')
        self.assertEqual(invalid['available_capacities'], ['256 Go', '512 Go'])
        self.assertIsNone(ambiguous)

    async def test_unsupported_variant_and_family_sku_do_not_guess(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as client:
            for query in ['Samsung S26 Ultra Pro 256 Go', 'Samsung A57 4G 256 Go', 'SM-S948B', 'Galaxy S26 FE Plus 128 Go']:
                self.assertIsNone(await samsung_phones.lookup_samsung_phone(client, query))

    async def test_explicit_5g_alias_requires_exact_model_and_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            result = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 Ultra 5G 512 Go')
            plus = await samsung_phones.lookup_samsung_phone(client, 'Galaxy S26 Plus 5G 256 Go')
            wrong = await samsung_phones.lookup_samsung_phone(client, 'Samsung S26 Ultra 4G 512 Go')
        self.assertEqual(result['model'], 'Galaxy S26 Ultra')
        self.assertEqual(result['capacity'], '512 Go')
        self.assertEqual(result['specs']['reseau'], '5G')
        self.assertEqual(plus['model'], 'Galaxy S26+')
        self.assertIsNone(wrong)

    async def test_dynamic_explicit_5g_requires_manufacturer_confirmation(self):
        def transport(request):
            if '/products/' in request.url.path:
                return httpx.Response(200, text='<a href="' + URL + '">Samsung introduces Galaxy S24 FE</a>')
            return httpx.Response(200, text=SHEET)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung_phones.lookup_samsung_phone(client, 'Galaxy S24 FE 5G 256 Go')
        self.assertEqual(result['model'], 'Galaxy S24 FE')
        self.assertEqual(result['specs']['reseau'], '5G')
        samsung_phones._CACHE.clear()
        def lte_transport(request):
            response = transport(request)
            return httpx.Response(200, text=response.text.replace('5G LTE', 'LTE'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lte_transport)) as client:
            self.assertIsNone(await samsung_phones.lookup_samsung_phone(client, 'Galaxy S24 FE 5G 256 Go'))

    def test_exact_table_ignores_ram_sd_and_front_camera(self):
        item = samsung_phones.parse_samsung_sheet(SHEET, 'S24 FE', URL)
        self.assertEqual(item['capacities'], ['128 Go', '256 Go'])
        self.assertEqual(item['specs']['photo'], '12 + 50 + 8 Mpx (arrière)')
        self.assertEqual(item['specs']['batterie'], '4 700 mAh')
        self.assertIsNone(samsung_phones.parse_samsung_sheet(SHEET, 'S24', URL))
        self.assertIsNone(samsung_phones.parse_samsung_sheet(SHEET, 'S24 FE', 'https://example.com/article'))

    def test_shared_columns_keep_plus_and_base_batteries_distinct(self):
        table = '''<table><tr><td colspan="3">Galaxy S24</td><td>Galaxy S24+</td></tr>
        <tr><td rowspan="2" colspan="2">Display</td><td>6.2-inch</td><td>6.7-inch</td></tr>
        <tr><td colspan="2">Dynamic AMOLED 2X 120 Hz</td></tr>
        <tr><td colspan="2">Memory &amp; Storage</td><td>8 + 128 GB</td><td>12 + 256 GB</td></tr>
        <tr><td colspan="2">Battery</td><td>4,000 mAh</td><td>4,900 mAh</td></tr>
        <tr><td colspan="2">Network and Connectivity</td><td colspan="2">5G</td></tr></table>'''
        base = samsung_phones.parse_samsung_sheet(table, 'S24', URL)
        plus = samsung_phones.parse_samsung_sheet(table, 'S24+', URL)
        self.assertEqual(base['specs']['batterie'], '4 000 mAh')
        self.assertEqual(plus['specs']['batterie'], '4 900 mAh')
        self.assertEqual(base['capacities'], ['128 Go'])
        self.assertEqual(plus['capacities'], ['256 Go'])

    def test_foldable_camera_labels_select_rear_only(self):
        table = '''<table><tr><td colspan="3">Galaxy Z Fold7</td></tr>
        <tr><td>Display</td><td>Main Screen</td><td>8.0-inch Dynamic AMOLED 2X 120 Hz</td></tr>
        <tr><td rowspan="3">Camera</td><td>Cover Camera</td><td>10MP Selfie Camera</td></tr>
        <tr><td>Main Camera</td><td>10MP Front Camera</td></tr>
        <tr><td>Rear Triple Camera</td><td>200MP Wide Camera 50MP Ultra-Wide Camera 10MP Telephoto Camera</td></tr>
        <tr><td colspan="2">Memory &amp; Storage</td><td>12 + 256 GB 16 GB + 1 TB</td></tr>
        <tr><td colspan="2">Battery</td><td>4,400 mAh</td></tr></table>'''
        item = samsung_phones.parse_samsung_sheet(table, 'Z Fold7', URL)
        self.assertEqual(item['specs']['photo'], '200 + 50 + 10 Mpx (arrière)')
        self.assertEqual(item['capacities'], ['256 Go', '1 To'])

    def test_official_footnote_labels_and_written_storage_are_supported(self):
        table = '''<table><tr><td colspan="3">Galaxy A58 5G</td></tr>
        <tr><td colspan="2">Display <sup>24</sup></td><td>6.7” FHD+ Super AMOLED + Display Up to 120Hz</td></tr>
        <tr><td colspan="2">Memory &amp; Storage <sup>25</sup></td><td>12 GB Memory with 256 GB internal storage 16 GB Memory with 1 TB internal storage</td></tr>
        <tr><td colspan="2">Battery</td><td>5000mAh (typical)</td></tr>
        </table>'''
        item = samsung_phones.parse_samsung_sheet(table, 'A58 5G', URL)
        self.assertEqual(item['capacities'], ['256 Go', '1 To'])
        self.assertEqual(item['specs']['ecran'], '6,7″ Super AMOLED+ 120 Hz')

    async def test_dynamic_manufacturer_discovery_is_exact_and_cached(self):
        def transport(request):
            self.requests.append(str(request.url))
            if '/products/' in request.url.path:
                return httpx.Response(200, text='<a href="' + URL + '">Samsung introduces Galaxy S24 FE</a>')
            return httpx.Response(200, text=SHEET)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung_phones.lookup_samsung_phone(client, 'Samsung S24 FE 256 Go')
            invalid = await samsung_phones.lookup_samsung_phone(client, 'Samsung S24 FE 512 Go')
        self.assertEqual(result['capacity'], '256 Go')
        self.assertEqual(result['search_mode'], 'official_manufacturer_page')
        self.assertEqual(invalid['status'], 'invalid_capacity')
        self.assertEqual(len(self.requests), 2)

    async def test_unavailable_manufacturer_returns_none(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            self.assertIsNone(await samsung_phones.lookup_samsung_phone(client, 'Samsung S24 FE 256 Go'))


if __name__ == '__main__':
    unittest.main()

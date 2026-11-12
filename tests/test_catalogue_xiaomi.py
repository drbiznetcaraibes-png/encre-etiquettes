import unittest
from unittest.mock import patch

import httpx

from catalogue_sources import xiaomi


SHEET = '''<h1>Xiaomi 19T<span style="display:none"> Caractéristiques</span></h1>
<div><div><span>RAM &amp; Stockage</span></div><div>12 Go + 256 Go / 12 Go + 512 Go</div></div>
<div><div><span>Processeur</span></div><div>MediaTek Dimensity 8500-Ultra</div></div>
<div><div><span>Écran</span></div><div>Écran AMOLED 6,59 pouces 120 Hz</div></div>
<div><div><span>Caméra arrière</span></div><div>Caméra principale 50 MP</div></div>
<div><div><span>Batterie &amp; Charge</span></div><div>6 500 mAh</div></div>
<div><div><span>Réseau et connectivité</span></div><div>5G / 4G</div></div>'''
INDEX = 'https://www.mi.com/fr/phone/'
PRODUCT = 'https://www.mi.com/fr/product/xiaomi-19t/specs/'


class XiaomiCollectionTests(unittest.IsolatedAsyncioTestCase):
    def test_sitemap_filters_accessories_and_accepts_official_poco(self):
        self.assertIsNone(xiaomi._product_url('https://www.mi.com/global/product/xiaomi-18w-power-bank-30000mah/'))
        self.assertIsNone(xiaomi._product_url('https://www.mi.com/global/product/xiaomi-14-ultra-photography-kit/'))
        self.assertEqual(xiaomi._product_url('https://www.po.co/global/product/poco-x8-pro-max/'),
                         'https://www.po.co/global/product/poco-x8-pro-max/specs/')

    async def collect(self, handler, existing=None, **limits):
        with patch.object(xiaomi, 'INDEX_URLS', (INDEX,)):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                if limits:
                    with patch.multiple(xiaomi, **limits):
                        return await xiaomi.collect(client, existing or [])
                return await xiaomi.collect(client, existing or [])

    async def test_discovers_new_generation_from_official_listing(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if str(request.url) == INDEX:
                return httpx.Response(200, text='<title>Téléphones Xiaomi</title><a href="/fr/product/xiaomi-19t/">Nouveau Xiaomi 19T</a>')
            return httpx.Response(200, text=SHEET)
        data = await self.collect(handler)
        self.assertTrue(data['discovery_ok'])
        self.assertEqual(data['checked'], 1)
        self.assertEqual(data['discovered'], 1)
        self.assertEqual(data['phones'][0]['model'], 'Xiaomi 19T')
        self.assertEqual(data['phones'][0]['capacities'], ['256 Go', '512 Go'])
        self.assertEqual(requests, [INDEX, PRODUCT])

    async def test_invalid_specs_and_wrong_model_are_not_returned(self):
        for document in [SHEET.replace('Xiaomi 19T', 'Xiaomi 18T'), '<h1>Xiaomi 19T</h1><p>256 Go</p>']:
            def handler(request):
                return httpx.Response(200, text=('<a href="/fr/product/xiaomi-19t/">19T</a>' if str(request.url) == INDEX else document))
            data = await self.collect(handler)
            self.assertTrue(data['discovery_ok'])
            self.assertEqual(data['phones'], [])
            self.assertTrue(data['errors'])

    async def test_protocol_note_does_not_invent_an_extra_factory_variant(self):
        document = SHEET.replace('12 Go + 256 Go / 12 Go + 512 Go',
                                 '6 Go + 256 Go | 8 Go + 512 Go LPDDR4X + UFS 3.1 (256 Go/512 Go) / UFS 2.2 (128 Go)')
        def handler(request):
            return httpx.Response(200, text=('<a href="/fr/product/xiaomi-19t/">19T</a>' if str(request.url) == INDEX else document))
        data = await self.collect(handler)
        self.assertEqual(data['phones'][0]['capacities'], ['256 Go', '512 Go'])

    async def test_foreign_links_and_redirects_are_never_requested(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if str(request.url) == INDEX:
                return httpx.Response(200, text='<a href="https://evil.test/fr/product/xiaomi-20/">bad</a><a href="/fr/product/xiaomi-19t/">good</a>')
            return httpx.Response(302, headers={'Location': 'https://evil.test/fr/product/xiaomi-19t/specs/'})
        data = await self.collect(handler)
        self.assertEqual(requests, [INDEX, PRODUCT])
        self.assertEqual(data['phones'], [])
        self.assertTrue(any('Redirection' in error for error in data['errors']))

    async def test_failed_discovery_can_refresh_existing_without_claiming_discovery(self):
        old = {'model': 'Xiaomi 19T', 'aliases': ['Xiaomi 19T 5G'], 'sources': [{'url': PRODUCT}]}
        def handler(request):
            if str(request.url) == INDEX:
                raise httpx.ReadTimeout('unavailable', request=request)
            return httpx.Response(200, text=SHEET)
        data = await self.collect(handler, [old])
        self.assertFalse(data['discovery_ok'])
        self.assertEqual(len(data['phones']), 1)
        self.assertEqual(data['discovered'], 0)
        self.assertEqual(old['aliases'], ['Xiaomi 19T 5G'])
        self.assertTrue(data['errors'])

    async def test_soft_404_does_not_count_as_a_working_index(self):
        def handler(request):
            return httpx.Response(200, text='<title>404 - Xiaomi</title><a href="/fr/product/xiaomi-19t/">navigation</a>')
        data = await self.collect(handler)
        self.assertFalse(data['discovery_ok'])
        self.assertEqual(data['checked'], 0)

    async def test_budget_is_bounded(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if str(request.url) == INDEX:
                return httpx.Response(200, text=''.join(f'<a href="/fr/product/xiaomi-{number}t/">new</a>' for number in range(18, 28)))
            return httpx.Response(200, text=SHEET)
        await self.collect(handler, MAX_PAGES=3)
        self.assertLessEqual(len(requests), 3)


if __name__ == '__main__':
    unittest.main()

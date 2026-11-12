import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import httpx

from catalogue_sources import samsung


ARTICLE = 'https://www.samsungmobilepress.com/articles/samsung-unveils-galaxy-s27-series'
OLD_ARTICLE = 'https://www.samsungmobilepress.com/articles/meet-galaxy-s26-fe'
INDEX = '<a href="' + ARTICLE + '">Samsung unveils Galaxy S27 series</a>'
SITEMAP = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>' + ARTICLE + '</loc></url></urlset>'


def sheet(model='Galaxy S27', battery='4,500'):
    return '''<h1>Samsung introduces new Galaxy phones</h1><table>
    <tr><td colspan="3">''' + model + '''</td></tr>
    <tr><td colspan="2">Display</td><td>6.3-inch Dynamic AMOLED 2X 120 Hz</td></tr>
    <tr><td colspan="2">Camera</td><td>50MP Wide Camera 12MP Ultra-Wide Camera 12MP Front Camera</td></tr>
    <tr><td colspan="2">Memory &amp; Storage</td><td>12 + 256 GB 12 + 512 GB</td></tr>
    <tr><td colspan="2">Battery</td><td>''' + battery + ''' mAh</td></tr>
    <tr><td colspan="2">Network and Connectivity</td><td>5G LTE</td></tr></table>'''


class SamsungCollectorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.requests = []

    def transport(self, request):
        url = str(request.url)
        self.requests.append(url)
        if url == samsung.SITEMAP:
            return httpx.Response(200, text=SITEMAP)
        if url in samsung.INDEXES:
            return httpx.Response(200, text=INDEX)
        return httpx.Response(200, text=sheet())

    async def test_discovers_generation_missing_from_existing_catalogue(self):
        previous = [{'brand': 'SAMSUNG', 'model': 'Galaxy S26', 'aliases': ['S26'],
                     'capacities': ['256 Go'], 'specs': {}, 'sources': []}]
        original = deepcopy(previous)
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            result = await samsung._collect_press(client, previous)
        self.assertEqual([row['model'] for row in result['phones']], ['Galaxy S27'])
        self.assertEqual(result['phones'][0]['capacities'], ['256 Go', '512 Go'])
        self.assertIn('S27', result['phones'][0]['aliases'])
        self.assertEqual(result['discovered'], 1)
        self.assertEqual(result['checked'], 1)
        self.assertTrue(result['discovery_ok'])
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.requests.count(ARTICLE), 1)
        self.assertEqual(previous, original)

    async def test_sitemap_reaches_release_not_on_current_category_pages(self):
        def transport(request):
            if '/products/' in request.url.path:
                return httpx.Response(200, text='<a href="/articles/galaxy-ai-news">Galaxy news</a>')
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung._collect_press(client, [])
        self.assertEqual(result['discovered'], 1)
        self.assertEqual(result['phones'][0]['model'], 'Galaxy S27')

    async def test_triple_model_columns_are_independent(self):
        table = '''<table><tr><td colspan="3">Galaxy S27</td><td>Galaxy S27+</td><td>Galaxy S27 Ultra</td></tr>
        <tr><td colspan="2">Display</td><td>6.3-inch AMOLED 120 Hz</td><td>6.7-inch AMOLED 120 Hz</td><td>6.9-inch AMOLED 120 Hz</td></tr>
        <tr><td colspan="2">Memory &amp; Storage</td><td>12 + 256 GB</td><td>12 + 512 GB</td><td>16 GB + 1 TB</td></tr>
        <tr><td colspan="2">Battery</td><td>4,500 mAh</td><td>4,900 mAh</td><td>5,200 mAh</td></tr>
        <tr><td colspan="2">Network and Connectivity</td><td colspan="3">5G LTE</td></tr></table>'''
        def transport(request):
            if '/articles/' in request.url.path:
                return httpx.Response(200, text=table)
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung._collect_press(client, [])
        models = {row['model']: row for row in result['phones']}
        self.assertEqual(result['discovered'], 3)
        self.assertEqual(models['Galaxy S27']['capacities'], ['256 Go'])
        self.assertEqual(models['Galaxy S27+']['specs']['batterie'], '4 900 mAh')
        self.assertEqual(models['Galaxy S27 Ultra']['capacities'], ['1 To'])

    async def test_incomplete_table_is_reported_without_reusing_old_facts(self):
        def transport(request):
            if '/articles/' in request.url.path:
                return httpx.Response(200, text='<table><tr><td>Galaxy S27</td></tr><tr><td>Battery</td><td>5000mAh</td></tr></table>')
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung._collect_press(client, [])
        self.assertEqual(result['phones'], [])
        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['discovered'], 0)
        self.assertTrue(any('Unparseable' in error for error in result['errors']))

    async def test_blocked_index_marks_discovery_failed_but_refreshes_known_source(self):
        old = {'brand': 'SAMSUNG', 'model': 'Galaxy S27', 'aliases': ['S27', 'custom alias'],
               'manufacturer_references': ['TEST-SKU'], 'sources': [{'url': ARTICLE}]}
        def transport(request):
            if request.url.path == '/sitemap.xml' or '/products/' in request.url.path:
                return httpx.Response(403)
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await samsung._collect_press(client, [old])
        self.assertFalse(result['discovery_ok'])
        self.assertEqual(result['discovered'], 0)
        self.assertEqual(result['phones'][0]['manufacturer_references'], ['TEST-SKU'])
        self.assertIn('custom alias', result['phones'][0]['aliases'])
        self.assertEqual(len(result['errors']), 4)

    async def test_external_link_and_redirect_never_send_off_domain_requests(self):
        def transport(request):
            if request.url.host == 'example.com':
                self.fail('collector followed an external URL')
            if request.url.path == '/sitemap.xml':
                return httpx.Response(200, text=SITEMAP)
            if '/products/' in request.url.path:
                return httpx.Response(200, text=INDEX + '<a href="https://example.com/articles/galaxy-s99">External</a>')
            return httpx.Response(302, headers={'location': 'https://example.com/specs'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            result = await samsung._collect_press(client, [])
        self.assertEqual(result['phones'], [])
        self.assertTrue(any('domain' in error for error in result['errors']))

    async def test_limits_concurrency_and_number_of_model_tables(self):
        active = 0
        maximum = 0
        async def transport(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.001)
            active -= 1
            if request.url.path == '/sitemap.xml':
                return httpx.Response(200, text=SITEMAP)
            if '/products/' in request.url.path:
                return httpx.Response(200, text=''.join('<a href="/articles/galaxy-s27-' + str(i) + '">Galaxy S27</a>' for i in range(20)))
            return httpx.Response(200, text=sheet())
        with patch.object(samsung, 'MAX_PHONES', 3), patch.object(samsung, 'MAX_DOCUMENTS', 10):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                result = await samsung._collect_press(client, [])
        self.assertLessEqual(maximum, 4)
        self.assertEqual(result['checked'], 3)
        self.assertEqual(len(result['phones']), 1)

    async def test_timeout_returns_diagnostics(self):
        async def transport(request):
            await asyncio.sleep(1)
            return httpx.Response(200, text='')
        with patch.object(samsung, 'TIME_LIMIT', 0.01):
            async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
                result = await samsung._collect_press(client, [])
        self.assertFalse(result['discovery_ok'])
        self.assertTrue(any('time limit' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()

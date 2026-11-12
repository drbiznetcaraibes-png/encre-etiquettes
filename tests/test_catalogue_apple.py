import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import httpx

from catalogue_sources import apple


def sheet(model, chip='A18', storage='128 Go 256 Go', screen='6,1 pouces OLED', network='5G'):
    return f'''<h1>{model} - Caractéristiques techniques</h1>
    <div><h3>Capacité</h3><p>{storage}</p></div>
    <div><h3>Écran</h3><p>{screen}</p></div>
    <div><h3>Puce</h3><p>Puce {chip}</p></div>
    <div><h3>Appareil photo</h3><p>48 MP</p></div>
    <div><h3>Connectivité cellulaire et sans fil</h3><p>{network}</p></div>'''


def index(*entries):
    return ''.join(f'<a href="{url}">Consultez les caractéristiques techniques de l’{name}</a>'
                   for name, url in entries)


OLD = {'brand': 'APPLE', 'model': 'iPhone 16', 'aliases': ['iPhone 16', 'ancienne saisie conservée par la fusion'],
       'manufacturer_references': [], 'capacities': ['128 Go', '256 Go'],
       'specs': {'ecran': '6,1″ OLED', 'photo': '48 Mpx', 'processeur': 'Apple A18'},
       'sources': [{'title': 'iPhone 16 - Caractéristiques techniques',
                    'url': 'https://support.apple.com/fr-fr/121029'}]}


class AppleCollectorTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, handler, existing=None):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await apple.collect(client, existing if existing is not None else [deepcopy(OLD)])

    async def test_index_discovers_new_named_model_and_refreshes_known_without_writing(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            if request.url.path.endswith('/108044'):
                text = index(('iPhone 16', '/121029'), ('iPhone Duo', '/148588'))
            elif request.url.path.endswith('/148588'):
                text = sheet('iPhone Duo', chip='A20 Pro', storage='256 Go 512 Go')
            else:
                text = sheet('iPhone 16')
            return httpx.Response(200, text=text)
        existing = [deepcopy(OLD)]
        before = deepcopy(existing)
        result = await self.collect(handler, existing)
        self.assertTrue(result['discovery_ok'])
        self.assertEqual(result['checked'], 2)
        self.assertEqual(result['discovered'], 1)
        self.assertEqual({phone['model'] for phone in result['phones']}, {'iPhone 16', 'iPhone Duo'})
        self.assertEqual(result['errors'], [])
        self.assertEqual(existing, before)
        self.assertTrue(all(url.startswith('https://support.apple.com/fr-fr/') for url in requests))

    async def test_partial_bad_heading_returns_only_valid_updates_leaving_old_for_root_merge(self):
        def handler(request):
            if request.url.path.endswith('/108044'):
                return httpx.Response(200, text=index(('iPhone 16', '/121029'), ('iPhone 20 Pro', '/222222')))
            model = 'iPhone 16 Pro' if request.url.path.endswith('/121029') else 'iPhone 20 Pro'
            return httpx.Response(200, text=sheet(model))
        existing = [deepcopy(OLD)]
        result = await self.collect(handler, existing)
        self.assertEqual([phone['model'] for phone in result['phones']], ['iPhone 20 Pro'])
        self.assertTrue(any('iPhone 16' in error and 'différent' in error for error in result['errors']))
        self.assertEqual(existing[0], OLD)

    async def test_failed_discovery_is_reported_even_if_known_sheet_refreshes(self):
        def handler(request):
            if request.url.path.endswith('/108044'):
                return httpx.Response(503)
            return httpx.Response(200, text=sheet('iPhone 16'))
        result = await self.collect(handler)
        self.assertFalse(result['discovery_ok'])
        self.assertEqual(result['checked'], 1)
        self.assertEqual(result['discovered'], 0)
        self.assertEqual(result['phones'][0]['model'], 'iPhone 16')
        self.assertTrue(any('Index Apple' in error for error in result['errors']))

    async def test_empty_index_is_not_reported_as_successful_discovery(self):
        result = await self.collect(lambda request: httpx.Response(200, text='<h1>Page indisponible</h1>'), [])
        self.assertFalse(result['discovery_ok'])
        self.assertEqual(result['phones'], [])
        self.assertTrue(result['errors'])

    async def test_se_generation_uses_unambiguous_year_and_legacy_french_redirect(self):
        def handler(request):
            if request.url.path.endswith('/108044'):
                return httpx.Response(200, text=index(('iPhone SE (3e génération)', '/kb/SP867?locale=en_US'),
                                                     ('iPhone SE', '/112005')))
            if request.url.path.startswith('/kb/'):
                self.assertEqual(request.url.params['locale'], 'fr_FR')
                return httpx.Response(302, headers={'location': '/fr-fr/111866'})
            if request.url.path.endswith('/111866'):
                return httpx.Response(200, text=sheet('iPhone SE (3e génération)', chip='A15 Bionic',
                                                      screen='4,7 pouces LCD', storage='64 Go 128 Go 256 Go'))
            return httpx.Response(200, text=sheet('iPhone SE', chip='A9', screen='4 pouces LCD',
                                                  storage='16 Go 32 Go 64 Go 128 Go', network='LTE'))
        result = await self.collect(handler, [])
        self.assertEqual(result['errors'], [])
        phones = {phone['model']: phone for phone in result['phones']}
        self.assertIn('iPhone SE (2022)', phones)
        self.assertIn('iPhone SE (2016)', phones)
        self.assertIn('iPhone SE 2022', phones['iPhone SE (2022)']['aliases'])
        self.assertNotIn('iPhone SE', phones['iPhone SE (2016)']['aliases'])

    async def test_non_manufacturer_links_and_redirects_are_never_followed(self):
        requests = []
        def handler(request):
            requests.append(str(request.url))
            self.assertEqual(request.url.host, 'support.apple.com')
            if request.url.path.endswith('/108044'):
                return httpx.Response(200, text=index(('iPhone 20 Pro', '/222222'),
                                                     ('iPhone 99', 'https://attacker.example/333333')))
            return httpx.Response(302, headers={'location': 'https://attacker.example/333333'})
        result = await self.collect(handler, [])
        self.assertEqual(len(requests), 2)
        self.assertEqual(result['phones'], [])
        self.assertTrue(any('Redirection' in error for error in result['errors']))

    async def test_concurrency_and_total_number_of_sheets_are_bounded(self):
        active = 0
        highest = 0
        async def handler(request):
            nonlocal active, highest
            if request.url.path.endswith('/108044'):
                entries = [(f'iPhone {number}', f'/{200000 + number}') for number in range(20, 30)]
                return httpx.Response(200, text=index(*entries))
            active += 1
            highest = max(highest, active)
            await asyncio.sleep(0.005)
            active -= 1
            return httpx.Response(200, text=sheet(f'iPhone {int(request.url.path.rsplit("/", 1)[1]) - 200000}'))
        result = await self.collect(handler, [])
        self.assertEqual(result['checked'], 10)
        self.assertLessEqual(highest, 4)
        with patch.object(apple, 'MAX_MODELS', 3):
            limited = await self.collect(handler, [])
        self.assertEqual(limited['checked'], 3)
        self.assertFalse(limited['discovery_ok'])
        self.assertTrue(any('limite' in error for error in limited['errors']))

    async def test_global_deadline_is_reported_and_pending_sheets_are_cancelled(self):
        async def handler(request):
            if request.url.path.endswith('/108044'):
                return httpx.Response(200, text=index(('iPhone 20', '/222222')))
            await asyncio.sleep(1)
            return httpx.Response(200, text=sheet('iPhone 20'))
        with patch.object(apple, 'COLLECTION_TIMEOUT', 0.01):
            result = await self.collect(handler, [])
        self.assertEqual(result['phones'], [])
        self.assertTrue(any('délai global' in error for error in result['errors']))


if __name__ == '__main__':
    unittest.main()

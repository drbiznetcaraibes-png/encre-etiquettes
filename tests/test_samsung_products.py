import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

import samsung_products as products


def family(name, code, storage='128 GB', site='fr', english=None):
    return {'fmyMarketingName': name, 'fmyEngName': english or name,
            'modelList': [{'modelCode': code, 'modelName': code[:8] + '/DS', 'displayName': name,
                           'pdpUrl': '/' + site + '/smartphones/galaxy-a/' + name.lower().replace(' ', '-') + '-' + code.lower() + '/',
                           'fmyChipList': [{'fmyChipType': 'MOBILE MEMORY', 'fmyChipCode': storage}]}]}


def specs(code, storage='128 Go', network='2G GSM, 3G WCDMA, 4G LTE FDD', battery='5000'):
    attrs = [('Taille (Écran principal)', '169.1mm (6.7" rectangle complet) / 164.5mm (6.5" bords arrondis)'),
             ('Technologie (Écran principal)', 'Super AMOLED'),
             ('Résolution de l’appareil photo arrière (Multiple)', '50.0 MP + 5.0 MP + 2.0 MP'),
             ('Selfie', '13.0 MP'), ('Mémoire (Go)', '4 Go'), ('Stockage (Go)', storage),
             ('Stockage disponible (Go)', '109.5'), ('Stockage externe support', 'MicroSD (Jusqu’à 2To)'),
             ('Capacité de la batterie (mAh, Typique)', battery), ('Infra', network)]
    return {'modelList': [{'modelCode': code, 'spec': {'specItems': [
        {'attrName': 'Group', 'attrs': [{'attrName': label, 'attrValue': value} for label, value in attrs]}]}}]}


def response(data):
    return httpx.Response(200, json={'response': {'statusCode': 200, 'resultData': data}})


def german_specs(code, network='GSM (2G), FDD-LTE (4G), 5G Sub6 FDD'):
    attrs = [
        ('PSP-198CE175-4E7A-43AB-9CD1-2C5CDB821F49', 'Displaygröße (Hauptdisplay)', r'16,91 cm / 6,7\" (volles Rechteck)'),
        ('F0F7647C-2B68-E0A0-E043-CBFEDFDDC465', 'Displaytechnologie (Hauptdisplay)', 'Super AMOLED'),
        ('PSP-9E6DA643-C8C8-4760-9447-DBFB816F4F1E', 'Maximale Bildwiederholrate (Hauptdisplay)', '120 Hz'),
        ('PSP-18B2C1F7-07F8-4D83-8A29-DE59809DFB30', 'Auflösung Mehrfachkamera (multipel)', '50,0 MP + 8,0 MP + 2,0 MP'),
        ('F0F7647C-2B4F-E0A0-E043-CBFEDFDDC465', 'Auflösung Frontkamera', '13 MP'),
        ('PSP-590C3DF2-06D3-4644-BB83-F0B4D338F40C', 'Arbeitsspeicher (GB)', '6'),
        ('F0F7647C-2B93-E0A0-E043-CBFEDFDDC465', 'Speicher (GB)', '128'),
        ('PSP-714187A5-5D35-4538-ACCE-CE33B5283519', 'Verfügbarer Speicher (GB)', '106,5'),
        ('F0F7647C-2B90-E0A0-E043-CBFEDFDDC465', 'Erweiterbarer Speicher', 'microSD (bis zu 2 TB)'),
        ('PSP-D59D72A5-D70A-4D00-A5B7-C77487173564', 'Akku-Kapazität (mAh, typisch)', '5.000'),
        ('F1CCA4B5-5FF6-F026-E043-CBFEDFDDC006', 'Infrastruktur', network),
    ]
    return {'modelList': [{'modelCode': code, 'spec': {'specItems': [
        {'attrKey': key, 'attrName': label, 'attrValue': value} for key, label, value in attrs]}}]}


class SamsungProductsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        products._INDEX_CACHE.clear()
        products._SPEC_CACHE.clear()
        self.requests = []
        self.fr = [family('Galaxy A18', 'SM-A185FZKDEUB', english='Galaxy A18 4G'),
                   family('Galaxy A17 5G', 'SM-A176BZBAEUB'),
                   family('Galaxy A26 5G', 'SM-A266BZKAEUB', storage='256 GB'),
                   family('Galaxy A37 5G', 'SM-A376BZKAEUB', storage='256 GB')]
        self.uk = [family('Galaxy A17', 'SM-A175FZKBEUB', site='uk'),
                   family('Galaxy A17 5G', 'SM-A176BZKAEUB', site='uk')]
        self.de = [family('Galaxy A26 5G', 'SM-A266BLGBEUB', site='de', storage='128 GB')]

    def transport(self, request):
        self.requests.append(str(request.url))
        self.assertEqual(request.url.host, 'searchapi.samsung.com')
        if '/finder/' in request.url.path:
            return response({'productList': {'fr': self.fr, 'uk': self.uk, 'de': self.de}[request.url.params['siteCode']]})
        code = request.url.params['modelList']
        if request.url.params['siteCode'] == 'de':
            return response(german_specs(code))
        five_g = code.startswith(('SM-A176', 'SM-A266', 'SM-A376'))
        return response(specs(code, storage='256 Go' if code.startswith(('SM-A266', 'SM-A376')) else '128 Go',
                              network='4G LTE, 5G FDD Sub6' if five_g else '2G GSM, 3G WCDMA, 4G LTE FDD'))

    async def test_a17_lte_and_5g_are_separate_and_a18_is_not_invented_5g(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            lte = await products.lookup_product_phone(client, 'Samsung A17 4G 128 Go')
            five_g = await products.lookup_product_phone(client, 'Galaxy A17 5G 128 Go')
            latest = await products.lookup_product_phone(client, 'Galaxy A18 128 Go')
            missing = await products.lookup_product_phone(client, 'Galaxy A18 5G 128 Go')
        self.assertEqual(lte['model'], 'Galaxy A17')
        self.assertEqual(lte['specs']['reseau'], '4G LTE')
        self.assertEqual(five_g['model'], 'Galaxy A17 5G')
        self.assertEqual(five_g['specs']['reseau'], '5G')
        self.assertNotIn('A17', five_g['aliases'])
        self.assertNotIn('Galaxy A17', five_g['aliases'])
        self.assertEqual(latest['capacities'], ['128 Go'])
        self.assertIsNone(missing)

    async def test_a26_a37_match_their_exact_generation_without_number_substitution(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            for query, name in [('Samsung A26 256 Go', 'Galaxy A26 5G'), ('Samsung A37 5G 256 Go', 'Galaxy A37 5G')]:
                item = await products.lookup_product_phone(client, query)
                self.assertEqual(item['model'], name)
                self.assertEqual(item['capacities'], ['256 Go'])
                self.assertEqual(item['specs']['reseau'], '5G')

    async def test_new_generation_is_discovered_from_live_finder_without_code_changes(self):
        self.fr.append(family('Galaxy A19', 'SM-A195FZKDEUB', storage='256 GB', english='Galaxy A19 4G'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            families = await products.list_product_families(client)
        self.assertIn('Galaxy A19', [row['name'] for row in families])

    async def test_lookup_caches_index_and_specs_and_rejects_other_brands_without_network(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            self.assertIsNone(await products.lookup_product_phone(client, 'iPhone 17 256 Go'))
            self.assertEqual(self.requests, [])
            await products.lookup_product_phone(client, 'Samsung A18 128 Go')
            await products.lookup_product_phone(client, 'Samsung A18 256 Go')
        self.assertEqual(len(self.requests), 4)

    def test_specs_use_rom_not_ram_free_space_or_sd_and_exact_sku(self):
        model = {'sku': 'SM-A185FZKDEUB', 'capacity': '128 Go'}
        item = products.parse_product_specs(specs(model['sku']), model)
        self.assertEqual(item['capacity'], '128 Go')
        self.assertEqual(item['specs']['photo'], '50 + 5 + 2 Mpx (arrière)')
        self.assertEqual(item['specs']['ecran'], '6,7″ Super AMOLED')
        self.assertEqual(item['specs']['batterie'], '5 000 mAh')
        self.assertNotIn('ram', item['specs'])
        self.assertIsNone(products.parse_product_specs(specs('SM-A176BZKAEUB'), model))
        self.assertIsNone(products.parse_product_specs(specs(model['sku'], storage='256 Go'), model))

    async def test_full_sku_preserves_its_exact_capacity(self):
        self.fr[0]['modelList'].append({**self.fr[0]['modelList'][0], 'modelCode': 'SM-A185FZKFEUB',
                                      'pdpUrl': '/fr/smartphones/galaxy-a/galaxy-a18-black-256gb-sm-a185fzkfeub/',
                                      'fmyChipList': [{'fmyChipType': 'MOBILE MEMORY', 'fmyChipCode': '256 GB'}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            item = await products.lookup_product_phone(client, 'SM-A185FZKDEUB')
            family_item = await products.lookup_product_phone(client, 'Samsung A18 128 Go')
        self.assertEqual(item['capacities'], ['128 Go'])
        self.assertIn('SM-A185FZKDEUB', item['manufacturer_references'])
        self.assertEqual(item['sku_capacities']['SM-A185FZKFEUB'], '256 Go')
        self.assertEqual(len(item['sources']), 1)
        self.assertEqual(len(family_item['sources']), 2)
        self.assertTrue(any('256gb-sm-a185fzkfeub' in source['url'] for source in family_item['sources']))

    async def test_failed_uk_index_cannot_turn_a17_into_an_unqualified_5g_alias(self):
        def transport(request):
            if '/finder/' in request.url.path and request.url.params['siteCode'] == 'uk':
                return httpx.Response(503)
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            ambiguous = await products.lookup_product_phone(client, 'Samsung A17 128 Go')
            explicit = await products.lookup_product_phone(client, 'Samsung A17 5G 128 Go')
            wrong_network = await products.lookup_product_phone(client, 'Samsung A17 4G 128 Go')
        self.assertIsNone(ambiguous)
        self.assertEqual(explicit['model'], 'Galaxy A17 5G')
        self.assertNotIn('A17', explicit['aliases'])
        self.assertNotIn('Galaxy A17', explicit['aliases'])
        self.assertIsNone(wrong_network)

    async def test_failed_index_is_cached_for_sixty_seconds(self):
        count = 0
        def failing(request):
            nonlocal count
            count += 1
            return httpx.Response(503)
        async with httpx.AsyncClient(transport=httpx.MockTransport(failing)) as client:
            self.assertIsNone(await products.lookup_product_phone(client, 'Galaxy A18 128 Go'))
            self.assertIsNone(await products.lookup_product_phone(client, 'Galaxy A18 128 Go'))
        self.assertEqual(count, 3)
        self.assertTrue(all(entry[1] == 60 for entry in products._INDEX_CACHE.values()))

    async def test_external_redirect_never_reaches_another_host(self):
        def redirect(request):
            self.assertEqual(request.url.host, 'searchapi.samsung.com')
            return httpx.Response(302, headers={'location': 'https://example.com/data'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
            self.assertIsNone(await products.lookup_product_phone(client, 'Galaxy A18 128 Go'))

    async def test_weekly_collection_discovers_missing_models_with_max_four_in_flight(self):
        active = maximum = 0
        async def transport(request):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.001)
            active -= 1
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await products.collect_product_phones(client, [{'model': 'Galaxy A17'}])
        self.assertTrue(result['discovery_ok'])
        self.assertEqual(result['discovered'], 4)
        self.assertEqual(len(result['phones']), 5)
        self.assertLessEqual(maximum, 4)
        self.assertEqual(result['errors'], [])

    async def test_a26_retired_from_fr_and_uk_is_discovered_in_german_storefront(self):
        self.fr = [row for row in self.fr if row['fmyMarketingName'] != 'Galaxy A26 5G']
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            item = await products.lookup_product_phone(client, 'Samsung A26 5G 128 Go')
        self.assertEqual(item['model'], 'Galaxy A26 5G')
        self.assertEqual(item['source_region'], 'de')
        self.assertEqual(item['capacities'], ['128 Go'])
        self.assertEqual(item['specs']['photo'], '50 + 8 + 2 Mpx (arrière)')
        self.assertEqual(item['specs']['ecran'], '6,7″ Super AMOLED 120 Hz')
        self.assertEqual(item['specs']['batterie'], '5 000 mAh')
        self.assertEqual(item['specs']['reseau'], '5G')
        self.assertTrue(item['sources'][0]['url'].startswith('https://www.samsung.com/de/'))

    async def test_german_family_named_5g_requires_5g_in_infrastructure_specs(self):
        self.fr = [row for row in self.fr if row['fmyMarketingName'] != 'Galaxy A26 5G']
        def transport(request):
            if '/spec/' in request.url.path and request.url.params['siteCode'] == 'de':
                return response(german_specs(request.url.params['modelList'], network='GSM (2G), FDD-LTE (4G)'))
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            self.assertIsNone(await products.lookup_product_phone(client, 'Samsung A26 5G 128 Go'))

    async def test_weekly_collection_counts_exact_models_after_regional_dedup_and_falls_back(self):
        def transport(request):
            if '/spec/' in request.url.path and request.url.params.get('modelList', '').startswith('SM-A266') and request.url.params['siteCode'] == 'fr':
                return httpx.Response(503)
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            result = await products.collect_product_phones(client, [])
        matches = [item for item in result['phones'] if item['model'] == 'Galaxy A26 5G']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['source_region'], 'de')
        self.assertEqual(result['checked'], 5)

    async def test_unseen_model_refreshes_positive_cache_before_six_hour_expiry(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.transport)) as client:
            await products.list_product_families(client)
            for site, entry in list(products._INDEX_CACHE.items()):
                products._INDEX_CACHE[site] = (time.monotonic() - 120, entry[1], entry[2])
            self.fr.append(family('Galaxy A19', 'SM-A195FZKDEUB', english='Galaxy A19 4G'))
            item = await products.lookup_product_phone(client, 'Galaxy A19 128 Go')
            self.assertEqual(item['model'], 'Galaxy A19')
            count = sum('/finder/' in url for url in self.requests)
            self.assertEqual(count, 6)
            self.assertIsNone(await products.lookup_product_phone(client, 'Galaxy A99 128 Go'))
            self.assertEqual(sum('/finder/' in url for url in self.requests), count)

    async def test_new_storage_option_refreshes_old_index_then_validates_exact_sku(self):
        new_sku = 'SM-A185FZKFEUB'
        def transport(request):
            if '/spec/' in request.url.path and request.url.params['modelList'] == new_sku:
                return response(specs(new_sku, storage='256 Go'))
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await products.lookup_product_phone(client, 'Galaxy A18 128 Go')
            for site, entry in list(products._INDEX_CACHE.items()):
                products._INDEX_CACHE[site] = (time.monotonic() - 120, entry[1], entry[2])
            self.fr[0]['modelList'].append({**self.fr[0]['modelList'][0], 'modelCode': new_sku,
                                          'pdpUrl': '/fr/smartphones/galaxy-a/galaxy-a18-black-256gb-' + new_sku.lower() + '/',
                                          'fmyChipList': [{'fmyChipType': 'MOBILE MEMORY', 'fmyChipCode': '256 GB'}]})
            item = await products.lookup_product_phone(client, 'Galaxy A18 256 Go')
        self.assertEqual(item['capacities'], ['128 Go', '256 Go'])
        self.assertEqual(item['specs']['reseau'], '4G LTE')
        self.assertTrue(item['sources'][0]['url'].endswith(new_sku.lower() + '/'))
        self.assertEqual(sum('/finder/' in url for url in self.requests), 6)

    async def test_concurrent_unknown_model_queries_share_each_index_refresh(self):
        async def transport(request):
            await asyncio.sleep(0.001)
            return self.transport(request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            await products.list_product_families(client)
            for site, entry in list(products._INDEX_CACHE.items()):
                products._INDEX_CACHE[site] = (time.monotonic() - 120, entry[1], entry[2])
            self.fr.append(family('Galaxy A19', 'SM-A195FZKDEUB', english='Galaxy A19 4G'))
            results = await asyncio.gather(*(products.lookup_product_phone(client, 'Galaxy A19 128 Go') for _ in range(2)))
        self.assertTrue(all(item['model'] == 'Galaxy A19' for item in results))
        self.assertEqual(sum('/finder/' in url for url in self.requests), 6)


if __name__ == '__main__':
    unittest.main()

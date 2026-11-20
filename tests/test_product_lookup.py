from copy import deepcopy
import json
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import server
from openai_phones import OpenAIServiceError, parse_openai_response, search_openai_evidence
from product_lookup import FIELDS
from test_product_catalog import TABLET


TV_SPECS = {'Technologie écran': 'OLED', 'Résolution': '4K UHD',
            'Fréquence native': '120 Hz', 'Smart TV': 'webOS'}
CONSOLE_SPECS = {'Stockage': 'SSD 1 To', 'Édition': 'Sans lecteur',
                 'Performances': '4K à 120 i/s', 'Inclus': 'Manette sans fil'}
SOURCE = {'title': 'Fiche constructeur', 'url': 'https://www.samsung.com/fr/tablets/galaxy-tab-s11/'}


def catalogue_row(category, brand, model, specs, capacities=(), references=()):
    return {
        'category': category, 'brand': brand, 'model': model,
        'model_key': f'{category}|{brand}|{model}', 'specs': specs,
        'available_capacities': list(capacities), 'manufacturer_references': list(references),
        'verification_status': 'vérifié', 'sources': [SOURCE],
    }


def web_result(category, brand, model, specs, *, capacities=(), inches=None, references=()):
    url = SOURCE['url']
    return ({
        'verified': True, 'brand': brand, 'model': model, 'specs': specs,
        'available_capacities': list(capacities), 'inches': inches,
        'manufacturer_references': list(references),
        'citations': {'identity': url, 'capacities': url, 'inches': url, 'references': url,
                      'specs': {field: url for field in FIELDS[category]}},
    }, [SOURCE])


class ProductLookupTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rows = []
        for patcher in (
            patch.dict(os.environ, {'APP_ACCESS_TOKEN': 'local-test'}),
            patch.object(server, 'OPENAI_KEY', 'test-key'),
            patch.object(server, 'OPENAI_MODEL', 'gpt-5.4-mini'),
            patch.object(server.store, 'url', 'https://project.supabase.co'),
            patch.object(server.store, 'key', 'server-key'),
            patch.object(server, 'REQUEST_LOG', {}),
            patch.object(server, 'GLOBAL_REQUEST_LOG', []),
        ):
            self.enterContext(patcher)
        self.db = self.enterContext(patch.object(server.store, 'request', new=AsyncMock(side_effect=self.database)))
        self.reserve = self.enterContext(patch.object(server.store, 'reserve', new=AsyncMock(return_value=True)))
        self.web = self.enterContext(patch('product_lookup.search_openai_evidence', new_callable=AsyncMock))
        self.client = await self.enterAsyncContext(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=server.app), base_url='http://local',
        ))

    async def database(self, method, path, *, params, **kwargs):
        self.assertEqual(path, 'product_catalog')
        if method == 'GET':
            if 'model_key' in params:
                return [row for row in self.rows if 'eq.' + row['model_key'] == params['model_key']]
            rows = [row for row in self.rows if 'eq.' + row['category'] == params['category']]
            offset = int(params['offset'])
            return rows[offset:offset + int(params['limit'])]
        self.assertEqual(method, 'POST')
        self.assertEqual(params, {'on_conflict': 'model_key'})
        self.assertIn('ignore-duplicates', kwargs['prefer'])
        row = deepcopy(kwargs['json'])
        existing = [item for item in self.rows if item['model_key'] == row['model_key']]
        if existing:
            return []
        self.rows.append(row)
        return [row]

    async def lookup(self, category, query, *, authenticated=True):
        return await self.client.post('/api/products/lookup',
                                      json={'category': category, 'query': query},
                                      auth=('encre', 'local-test') if authenticated else None)

    def assert_no_paid_lookup(self):
        self.reserve.assert_not_awaited()
        self.web.assert_not_awaited()

    def assert_no_write(self):
        self.assertTrue(all(call.args[0] == 'GET' for call in self.db.await_args_list))

    async def test_known_variants_from_catalogue(self):
        self.rows = [
            deepcopy(TABLET), catalogue_row('tablet', 'SAMSUNG', 'Galaxy Tab S11',
                {'Écran': '11 pouces AMOLED', 'Puce': 'Dimensity 9400+', 'Photo': '13 Mpx'}, ['256 Go']),
            catalogue_row('tablet', 'SAMSUNG', 'Galaxy Tab S11 Ultra',
                {'Écran': '14 pouces AMOLED', 'Puce': 'Dimensity 9400+', 'Photo': '13 Mpx'}, ['256 Go']),
            catalogue_row('console', 'PLAYSTATION', 'PS5 Pro — 2 To Digital', {
                **CONSOLE_SPECS, 'Stockage': 'SSD 2 To',
            }, ['2 To'], ['CFI-7000']),
            catalogue_row('console', 'PLAYSTATION', 'PS5 Slim Digital — 825 Go', {
                **CONSOLE_SPECS, 'Stockage': 'SSD 825 Go',
            }, ['825 Go']),
            catalogue_row('console', 'PLAYSTATION', 'PS5 Slim Digital — 1 To', CONSOLE_SPECS, ['1 To']),
            catalogue_row('console', 'XBOX', 'Xbox Series X Digital', CONSOLE_SPECS, ['1 To']),
            catalogue_row('console', 'XBOX', 'Xbox Series X avec lecteur', {
                **CONSOLE_SPECS, 'Édition': 'Lecteur de disque',
            }, ['1 To']),
            catalogue_row('tv', 'LG', 'C4', {**TV_SPECS, 'Taille': '55 pouces'}, references=['OLED55C4']),
            catalogue_row('tv', 'LG', 'C4', {**TV_SPECS, 'Taille': '65 pouces'}, references=['OLED65C4']),
        ]
        cases = [
            ('tablet', 'iPad Air M4 11 pouces 256 GB', 'iPad Air 11 pouces (M4)', 'capacity', '256 Go'),
            ('tablet', 'Galaxy Tab S 11 256 Go avec 12 Go de RAM', 'Galaxy Tab S11', 'capacity', '256 Go'),
            ('console', 'Sony PlayStation 5 Pro', 'PS5 Pro — 2 To Digital', 'reference', 'CFI-7000'),
            ('console', 'console PS5 Digital 825 GB', 'PS5 Slim Digital — 825 Go', 'available_capacities', ['825 Go']),
            ('console', 'Xbox Series X sans lecteur de disque', 'Xbox Series X Digital', 'available_capacities', ['1 To']),
            ('console', 'Xbox Series X disque', 'Xbox Series X avec lecteur', 'available_capacities', ['1 To']),
            ('tv', 'TV LG C4 OLED 55 pouces', 'C4', 'inches', 55),
            ('tv', 'LG OLED65C4', 'C4', 'inches', 65),
        ]
        for category, query, model, field, value in cases:
            with self.subTest(query=query):
                result = await self.lookup(category, query)
                self.assertEqual(result.status_code, 200, result.text)
                data = result.json()
                self.assertEqual(data['origin'], 'catalogue')
                self.assertEqual([(p['model'], p[field]) for p in data['products']], [(model, value)])
        self.assert_no_paid_lookup()
        self.assert_no_write()

    async def test_known_ambiguous_variants_and_unconfirmed_facts(self):
        self.rows = [
            catalogue_row('tv', 'LG', 'C4', {**TV_SPECS, 'Taille': '55 pouces'}),
            catalogue_row('tv', 'LG', 'C4', {**TV_SPECS, 'Taille': '65 pouces'}),
            {**TABLET, 'verification_status': 'à vérifier'},
        ]
        response = await self.lookup('tv', 'LG C4')
        self.assertEqual(response.status_code, 200)
        self.assertEqual({p['inches'] for p in response.json()['products']}, {55, 65})
        self.assertEqual((await self.lookup('tablet', 'A5678')).status_code, 404)
        self.assert_no_paid_lookup()
        self.assert_no_write()

    async def test_verified_web_lookup_upsert_and_second_lookup_without_ia(self):
        cases = [
            ('tablet', 'Galaxy Tab S11 256 Go', web_result(
                'tablet', 'Samsung', 'Galaxy Tab S11',
                {'screen': '11 pouces AMOLED', 'processor': 'Dimensity 9400+', 'camera': '13 Mpx'},
                capacities=['128 Go', '256 Go'],
            )),
            ('console', 'Xbox Series X Digital 1 To', web_result(
                'console', 'Xbox', 'Xbox Series X Digital',
                {'storage': 'SSD 1 To', 'edition': 'Sans lecteur', 'performance': '4K à 120 i/s', 'included': 'Manette Xbox'},
                capacities=['1 To'],
            )),
            ('tv', 'LG C4 55 pouces', web_result(
                'tv', 'LG', 'C4', {'technology': 'OLED', 'resolution': '4K UHD', 'refresh_rate': '120 Hz'},
                inches=55, references=['OLED55C4'],
            )),
            ('tv', 'LG C4 65 pouces', web_result(
                'tv', 'LG', 'C4', {'technology': 'OLED', 'resolution': '4K UHD', 'smart_tv': 'webOS'},
                inches=65, references=['OLED65C4'],
            )),
        ]
        for category, query, evidence in cases:
            with self.subTest(query=query):
                self.web.return_value = evidence
                first = await self.lookup(category, query)
                self.assertEqual(first.status_code, 200, first.text)
                self.assertEqual(first.json()['origin'], 'web')
                second = await self.lookup(category, query)
                self.assertEqual(second.status_code, 200, second.text)
                self.assertEqual(second.json()['origin'], 'catalogue')
                self.assertEqual(first.json()['products'], second.json()['products'])
                self.assertTrue(self.web.await_args.kwargs['citations_only'])
                self.assertIn(f'catégorie {category}', self.web.await_args.args[0])
        self.assertEqual(self.reserve.await_count, len(cases))
        self.assertEqual(self.web.await_count, len(cases))
        self.assertEqual(len({row['model_key'] for row in self.rows}), len(cases))
        for row in self.rows:
            self.assertEqual(row['verification_status'], 'vérifié')
            self.assertTrue(row['verified_at'].endswith('+00:00'))
            self.assertEqual(row['sources'], [SOURCE])
            self.assertNotIn('price', row)

    async def test_responses_api_and_catalogue_round_trip(self):
        category, query = 'tv', 'LG OLED55C4'
        facts, _ = web_result(category, 'LG', 'OLED55C4', {
            'technology': 'OLED', 'resolution': '4K UHD', 'smart_tv': 'webOS',
        }, inches=55)
        source = {'url': 'https://www.lg.com/fr/tv/oled55c4/', 'title': 'OLED55C4'}
        facts['citations'] = {
            'identity': source['url'], 'inches': source['url'],
            'specs': {field: source['url'] for field in facts['specs']},
        }
        calls = []

        def upstream(request):
            calls.append(request)
            self.assertEqual(str(request.url), 'https://api.openai.com/v1/responses')
            self.assertEqual(request.headers['authorization'], 'Bearer test-key')
            body = json.loads(request.content)
            self.assertIn(query, body['input'])
            self.assertEqual(body['max_tool_calls'], 1)
            self.assertEqual(body['tools'], [{'type': 'web_search', 'search_context_size': 'low'}])
            self.assertFalse(body['store'])
            return httpx.Response(200, json={'status': 'completed', 'output': [
                {'type': 'web_search_call', 'status': 'completed'},
                {'type': 'message', 'status': 'completed', 'content': [
                    {'type': 'output_text', 'text': json.dumps(facts), 'annotations': [
                        {'type': 'url_citation', **source},
                    ]},
                ]},
            ]})

        client_class = httpx.AsyncClient
        transport = httpx.MockTransport(upstream)
        with patch('product_lookup.search_openai_evidence', new=search_openai_evidence), patch(
            'openai_phones.httpx.AsyncClient', side_effect=lambda **kwargs: client_class(transport=transport, **kwargs)
        ):
            first = await self.lookup(category, query)
            second = await self.lookup(category, query)
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertEqual((first.json()['origin'], second.json()['origin']), ('web', 'catalogue'))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.rows[0]['sources'], [source])
        self.assertNotIn('test-key', first.text)
        self.assertNotIn('server-key', first.text)

    async def test_ambiguous_web_choices_must_be_cited_and_do_not_write(self):
        url = SOURCE['url']
        self.web.return_value = ({
            'verified': False,
            'choices': [
                {'description': 'LG C4 55 pouces', 'url': url},
                {'description': 'LG C4 65 pouces', 'url': url},
                {'description': 'LG C4 77 pouces', 'url': 'https://example.com/uncited'},
            ],
        }, [SOURCE])
        result = await self.lookup('tv', 'LG C4')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()['choices'], ['LG C4 55 pouces', 'LG C4 65 pouces'])
        self.assert_no_write()

    async def test_wrong_variant_and_uncited_facts_rejected(self):
        result = web_result('console', 'PlayStation', 'PS5 Pro Digital',
                            {'storage': 'SSD 2 To', 'edition': 'Sans lecteur',
                             'performance': '4K', 'included': 'DualSense'}, capacities=['2 To'])
        self.web.return_value = result
        self.assertEqual((await self.lookup('console', 'PS5 Digital 825 Go')).status_code, 404)
        self.assertEqual((await self.lookup('console', 'PS5')).status_code, 404)
        self.web.return_value = web_result('tablet', 'Samsung', 'Galaxy Tab S11', {
            'screen': '11 pouces AMOLED', 'processor': 'Dimensity 9400+', 'camera': '13 Mpx',
        }, capacities=['256 Go'])
        self.web.return_value[0]['citations']['specs']['processor'] = 'https://example.com/unverified'
        self.assertEqual((await self.lookup('tablet', 'Galaxy Tab S11')).status_code, 404)
        self.web.return_value[0]['citations']['identity'] = []
        self.assertEqual((await self.lookup('tablet', 'Galaxy Tab S11')).status_code, 404)
        self.web.return_value = None
        self.assertEqual((await self.lookup('tablet', 'Galaxy Tab S11')).status_code, 404)
        self.assert_no_write()

    async def test_quota_network_timeout_and_failed_upsert(self):
        self.reserve.return_value = False
        self.assertEqual((await self.lookup('tv', 'LG C4 55 pouces')).status_code, 429)
        self.web.assert_not_awaited()
        self.reserve.return_value = True
        self.web.side_effect = OpenAIServiceError('Connexion au service OpenAI impossible.')
        self.assertEqual((await self.lookup('tv', 'LG C4 55 pouces')).status_code, 502)
        self.web.side_effect = TimeoutError()
        self.assertEqual((await self.lookup('tv', 'LG C4 55 pouces')).status_code, 504)
        self.assert_no_write()
        self.web.side_effect = None
        self.web.return_value = web_result('tv', 'LG', 'C4', {
            'technology': 'OLED', 'resolution': '4K UHD', 'smart_tv': 'webOS',
        }, inches=55)
        self.db.side_effect = [[], RuntimeError('private database data')]
        response = await self.lookup('tv', 'LG C4 55 pouces')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('private database data', response.text)
        self.db.side_effect = RuntimeError('database inaccessible')
        calls = self.web.await_count
        self.assertEqual((await self.lookup('tv', 'LG C4 55 pouces')).status_code, 502)
        self.assertEqual(self.web.await_count, calls)

    async def test_concurrent_insert_reuses_existing_verified_row(self):
        self.web.return_value = web_result('tv', 'LG', 'C4', {
            'technology': 'OLED', 'resolution': '4K UHD', 'smart_tv': 'webOS',
        }, inches=55)
        existing = catalogue_row('tv', 'LG', 'C4', {**TV_SPECS, 'Taille': '55 pouces'})
        self.db.side_effect = [[], [], [existing]]
        result = await self.lookup('tv', 'LG C4 55 pouces')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(len(result.json()['products'][0]['specs']), 4)
        self.assertEqual(self.db.await_args.kwargs['params']['model_key'], 'eq.TV|LG|C4|55')

    async def test_authentication_validation_and_missing_ia(self):
        self.assertEqual((await self.lookup('tv', 'LG C4', authenticated=False)).status_code, 401)
        self.assertEqual((await self.lookup('phone', 'iPhone 12')).status_code, 422)
        self.assertEqual((await self.lookup('tv', 'Q' * 121)).status_code, 422)
        self.assertEqual((await self.lookup('tv', '55 pouces')).status_code, 400)
        self.assertEqual((await self.lookup('tv', 'LG C4 55 pouces ou 65 pouces')).status_code, 400)
        self.assertEqual((await self.lookup('tv', 'LG C4 55/65 pouces')).status_code, 400)
        self.assertEqual((await self.lookup('tablet', 'iPad Air 128 ou 256 Go')).status_code, 400)
        response = await self.client.post('/api/products/lookup', auth=('encre', 'local-test'),
                                          content=json.dumps({'category': 'tv', 'query': 'LG C4'}))
        self.assertIn(response.status_code, (415, 422))
        self.db.assert_not_awaited()
        for path in ('/', '/affiches', '/assets/catalog-search.js'):
            response = await self.client.get(path, auth=('encre', 'local-test'))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('test-key', response.text)
            self.assertNotIn('server-key', response.text)
        with patch.object(server, 'OPENAI_KEY', ''):
            self.assertEqual((await self.lookup('tv', 'LG C4')).status_code, 503)
        self.assert_no_paid_lookup()

    async def test_browsing_catalogue_pages_without_ia(self):
        self.rows = [
            catalogue_row('tv', 'LG', f'C4X{i}', {**TV_SPECS, 'Taille': '55 pouces'})
            for i in range(201)
        ]
        response = await self.lookup('tv', 'LG C4X200 55 pouces')
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([p['model'] for p in response.json()['products']], ['C4X200'])
        self.assertEqual(self.db.await_count, 2)
        self.assert_no_paid_lookup()


class ProductEvidenceTests(unittest.TestCase):
    def test_citations_require_successful_web_search_and_exclude_consulted_sources(self):
        source = SOURCE['url']
        response = {'status': 'completed', 'output': [
            {'type': 'web_search_call', 'status': 'completed',
             'action': {'sources': [{'url': 'https://example.com/consulted'}]}},
            {'type': 'message', 'status': 'completed', 'content': [
                {'type': 'output_text', 'text': '{"verified":false}', 'annotations': [
                    {'type': 'url_citation', 'url': source},
                ]},
            ]},
        ]}
        self.assertEqual([s['url'] for s in parse_openai_response(response, citations_only=True)[1]], [source])
        response['output'][1]['content'][0]['annotations'] = []
        self.assertIsNone(parse_openai_response(response, citations_only=True))
        response['output'][0]['status'] = 'failed'
        self.assertIsNone(parse_openai_response(response, citations_only=True))

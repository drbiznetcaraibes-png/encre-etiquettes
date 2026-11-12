import unittest
import json
from copy import deepcopy
import httpx
from unittest.mock import AsyncMock, patch
import server
from openai_phones import parse_openai_response, _safe_source, search_openai_web, OpenAIServiceError


def response(model="Pixel 9", brand="GOOGLE", capacity="128 Go"):
    return ({
        "verified": True, "brand": brand, "model": model, "capacity": capacity,
        "specs": {"ecran":"6,3 pouces OLED", "photo":"50 Mpx", "batterie":"4700 mAh"}
    }, [{"title": "Fiche officielle", "url": "https://store.google.com/product/pixel_9_specs"}])


def grounded_payload():
    parsed, sources = response()
    source = sources[0]
    return {"id": "resp_test", "object": "response", "status": "completed", "output": [
        {"type": "reasoning", "id": "rs_test", "summary": []},
        {"type": "web_search_call", "id": "ws_test", "status": "completed",
         "action": {"type": "search", "queries": ["Google Pixel 9 technical specifications"],
                    "sources": [{"type": "url", **source}]}},
        {"type": "message", "id": "msg_test", "role": "assistant", "status": "completed", "content": [
            {"type": "output_text", "text": json.dumps(parsed), "annotations": [
                {"type": "url_citation", **source, "start_index": 0, "end_index": 1}]}]},
    ]}


class OpenAIResponseTests(unittest.TestCase):
    def test_real_web_search_and_sources_required(self):
        p = grounded_payload()
        parsed=parse_openai_response(p)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1][0]["url"],"https://store.google.com/product/pixel_9_specs")
        p["output"].pop(1)
        self.assertIsNone(parse_openai_response(p))

    def test_response_must_be_completed_even_when_json_and_sources_exist(self):
        for status in ('failed', 'incomplete', 'in_progress', 'cancelled', None):
            with self.subTest(status=status):
                payload = grounded_payload()
                if status is None:
                    payload.pop('status')
                else:
                    payload['status'] = status
                self.assertIsNone(parse_openai_response(payload))

    def test_search_must_be_completed_even_with_citations(self):
        for status in ('failed', 'incomplete', 'in_progress', None):
            with self.subTest(status=status):
                payload = grounded_payload()
                if status is None:
                    payload['output'][1].pop('status')
                else:
                    payload['output'][1]['status'] = status
                self.assertIsNone(parse_openai_response(payload))

    def test_partial_message_and_failed_tool_sources_are_ignored(self):
        payload = grounded_payload()
        failed = deepcopy(payload['output'][1])
        failed['status'] = 'failed'
        failed['action']['sources'] = [{'url': 'https://example.com/failed-search'}]
        payload['output'].insert(1, failed)
        result = parse_openai_response(payload)
        self.assertEqual(len(result[1]), 1)
        self.assertNotIn('failed-search', result[1][0]['url'])
        payload['output'][-1]['status'] = 'incomplete'
        self.assertIsNone(parse_openai_response(payload))

    def test_unsafe_sources_rejected(self):
        for url in ("http://example.com", "https://localhost/path", "https://127.0.0.1/",
                    "https://192.168.1.1/", "https://user:secret@example.com/",
                    "https://[::ffff:192.168.1.1]/", "https://[::1]/", "https://[fe80::1]/",
                    "https://foo.localhost/", "https://localhost./", "https://a.internal/",
                    "https://100.64.0.1/", "https://0.0.0.0/", "https://224.0.0.1/",
                    "https://127.000.000.001/", "https://0x7f.0x0.0x0.0x1/",
                    "https://2130706433/", "https://[::1", "https://example.com:bad/",
                    "https://example.com:8080/specs", "https://example.com/\npath"):
            with self.subTest(url=url):
                self.assertIsNone(_safe_source(url))
        self.assertIsNotNone(_safe_source("https://www.apple.com/fr/iphone/"))
        self.assertIsNotNone(_safe_source("https://8.8.8.8/specs"))
        self.assertIsNotNone(_safe_source("https://[2606:4700:4700::1111]/specs"))


class OpenAIFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.config = patch.object(server.store, 'url', 'https://test.supabase.co')
        self.config.start()
        self.key = patch.object(server.store, 'key', 'test')
        self.key.start()
        self.reserve = patch.object(server.store, 'reserve', new=AsyncMock(return_value=True))
        self.reserve.start()
        self.addCleanup(self.config.stop)
        self.addCleanup(self.key.stop)
        self.addCleanup(self.reserve.stop)
        server.CACHE.clear()
        server.OPENAI_CALL_LOG.clear()

    async def test_model_and_capacity_checked(self):
        with patch.object(server,"OPENAI_KEY","test"), patch.object(server,"search_openai_web",new=AsyncMock(return_value=response())):
            result=await server.openai_lookup("Google Pixel 9 128 Go",True)
            self.assertEqual(result["capacity"],"128 Go")
            self.assertEqual(result["search_mode"],"openai_grounded")
            self.assertEqual(len(result["specs"]),3)

    async def test_neighboring_model_rejected(self):
        with patch.object(server,"OPENAI_KEY","test"), patch.object(server,"search_openai_web",new=AsyncMock(return_value=response(model="Pixel 9 Pro"))):
            self.assertIsNone(await server.openai_lookup("Google Pixel 9 128 Go",True))

    async def test_unverified_storage_not_assumed(self):
        with patch.object(server,"OPENAI_KEY","test"), patch.object(server,"search_openai_web",new=AsyncMock(return_value=response(capacity="256 Go"))):
            result=await server.openai_lookup("Google Pixel 9 128 Go",True)
            self.assertEqual(result["capacity"],"Non précisée")
            self.assertFalse(result["capacity_confirmed"])

    async def test_daily_guard(self):
        with patch.object(server,"OPENAI_KEY","test"), patch.object(server,"OPENAI_DAILY_MAX",0), patch.object(server,"search_openai_web",new_callable=AsyncMock) as search:
            self.assertIsNone(await server.openai_lookup("Google Pixel 9",True))
            search.assert_not_awaited()

    async def test_manufacturer_result_avoids_paid_fallback(self):
        parsed,sources=response()
        result={**parsed,'status':'verified_online','sources':sources}
        with patch.object(server,'lookup_official_phone',new=AsyncMock(return_value=result)) as official, patch.object(server,'OPENAI_KEY','test'), patch.object(server,'openai_lookup',new_callable=AsyncMock) as ai:
            found=await server._online_lookup('GOOGLE PIXEL 9',True)
            self.assertEqual(found['model'],'Pixel 9')
            official.assert_awaited_once()
            ai.assert_not_awaited()

    async def test_paid_fallback_only_after_all_manufacturers(self):
        parsed,sources=response()
        result={**parsed,'status':'verified_online','sources':sources,'search_mode':'openai_grounded'}
        providers={name:AsyncMock(return_value=None) for name in (
            'lookup_official_phone','lookup_xiaomi_phone','lookup_samsung_phone','lookup_manufacturer_phone')}
        with patch.multiple(server,**providers), patch.object(server,'OPENAI_KEY','test'), patch.object(server,'openai_lookup',new=AsyncMock(return_value=result)) as ai:
            found=await server._online_lookup('GOOGLE PIXEL 9',True)
            self.assertEqual(found['search_mode'],'openai_grounded')
            for provider in providers.values():provider.assert_awaited_once()
            ai.assert_awaited_once_with('GOOGLE PIXEL 9',is_model_query=True)

    async def test_unknown_redmi_automatically_uses_ai_and_saves_verified_result(self):
        parsed, _ = response(model='REDMI A7 Pro', brand='XIAOMI')
        sources = [{'title': 'Xiaomi — caractéristiques', 'url': 'https://www.mi.com/global/product/redmi-a7-pro/specs/'}]
        providers = {name: AsyncMock(return_value=None) for name in (
            'lookup_official_phone', 'lookup_xiaomi_phone', 'lookup_samsung_phone', 'lookup_manufacturer_phone')}
        with patch.multiple(server, **providers), patch.object(server, 'OPENAI_KEY', 'test-only'), \
                patch.object(server.store, 'get', new=AsyncMock(return_value=None)), \
                patch.object(server.store, 'save', new_callable=AsyncMock) as save, \
                patch.object(server, 'search_openai_web', new=AsyncMock(return_value=(parsed, sources))) as search:
            found = await server.online_lookup('Xiaomi Redmi A7 Pro 128 Go', is_model_query=True)
        self.assertEqual(found['search_mode'], 'openai_grounded')
        self.assertEqual(found['model'], 'REDMI A7 Pro')
        self.assertEqual(found['capacity'], '128 Go')
        search.assert_awaited_once()
        self.assertEqual(save.await_args.args[0]['verification_status'], 'vérifié')
        self.assertEqual(save.await_args.args[0]['sources'], sources)
        for provider in providers.values():
            provider.assert_awaited_once()

    async def test_new_marketing_names_can_reach_ai_without_a_local_grammar_update(self):
        for query, model, brand in [('iPhone Fold', 'iPhone Fold', 'APPLE'),
                                    ('Samsung Galaxy Z TriFold', 'Galaxy Z TriFold', 'SAMSUNG')]:
            with self.subTest(query=query), patch.object(server, 'OPENAI_KEY', 'test-only'), \
                    patch.object(server, 'search_openai_web', new=AsyncMock(return_value=response(model=model, brand=brand))):
                server.OPENAI_CALL_LOG.clear()
                self.assertIsNotNone(server._query_model(query))
                self.assertEqual((await server.openai_lookup(query, True))['model'], model)

    async def test_unapproved_model_is_blocked_before_reservation(self):
        with patch.object(server, 'OPENAI_KEY', 'test-only'), patch.object(server, 'OPENAI_MODEL', 'gpt-6-astra'), \
                patch.object(server.store, 'reserve', new_callable=AsyncMock) as reserve:
            with self.assertRaisesRegex(RuntimeError, 'Modèle IA non autorisé'):
                await server.openai_lookup('Redmi A7 Pro', True)
            reserve.assert_not_awaited()


class OpenAIDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_responses_request_keeps_existing_model_and_cost_limits(self):
        with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=httpx.Response(200, json=grounded_payload()))) as post:
            result = await search_openai_web('Google Pixel 9', 'test-only')
        self.assertIsNotNone(result)
        self.assertEqual(post.call_args.args[0], 'https://api.openai.com/v1/responses')
        body = post.call_args.kwargs['json']
        self.assertEqual(body['model'], 'gpt-5.4-mini')
        self.assertEqual(body['tools'], [{'type': 'web_search', 'search_context_size': 'low'}])
        self.assertEqual(body['include'], ['web_search_call.action.sources'])
        self.assertEqual(body['tool_choice'], 'required')
        self.assertEqual(body['max_tool_calls'], 1)
        self.assertEqual(body['max_output_tokens'], 2200)
        self.assertEqual(body['reasoning'], {'effort': 'low'})
        self.assertIs(body['store'], False)

    async def test_incomplete_response_never_yields_a_partial_phone(self):
        payload = grounded_payload()
        payload['status'] = 'incomplete'
        payload['incomplete_details'] = {'reason': 'max_output_tokens'}
        with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=httpx.Response(200, json=payload))):
            with self.assertRaisesRegex(OpenAIServiceError, 'Réponse OpenAI incomplète'):
                await search_openai_web('Google Pixel 9', 'test-only')

    async def test_insufficient_credits_are_distinct_from_rate_limit(self):
        for code,expected in [('insufficient_quota','Crédits API'),('rate_limit_exceeded','Limite de requêtes')]:
            with self.subTest(code=code), patch.object(httpx.AsyncClient,'post',new=AsyncMock(return_value=httpx.Response(429,json={'error':{'code':code,'message':'never_print_this_key'}}))):
                with self.assertRaises(OpenAIServiceError) as error:
                    await search_openai_web('Google Pixel 9','test-only')
                self.assertIn(expected,str(error.exception))
                self.assertNotIn('never_print',str(error.exception))

    async def test_quota_type_is_recognized_when_provider_code_is_absent(self):
        with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=httpx.Response(
                429, json={'error': {'type': 'insufficient_quota', 'message': 'never_print_this_key'}}))):
            with self.assertRaisesRegex(OpenAIServiceError, 'Crédits API'):
                await search_openai_web('Redmi A7 Pro', 'test-only')

    async def test_only_allowlisted_request_parameter_is_exposed(self):
        for param,visible in [('max_tool_calls',True),('never_print_this_key',False)]:
            with self.subTest(param=param), patch.object(httpx.AsyncClient,'post',new=AsyncMock(return_value=httpx.Response(400,json={'error':{'param':param,'message':'never_print_this_key'}}))):
                with self.assertRaises(OpenAIServiceError) as error:
                    await search_openai_web('Google Pixel 9','test-only')
                message=str(error.exception)
                self.assertEqual('max_tool_calls' in message,visible)
                self.assertNotIn('never_print',message)

    async def test_rejected_key_is_not_echoed(self):
        with patch.object(httpx.AsyncClient,'post',new=AsyncMock(return_value=httpx.Response(401,json={'error':{'message':'never_print_this_key'}}))):
            with self.assertRaises(OpenAIServiceError) as error:
                await search_openai_web('Google Pixel 9','test-only')
            self.assertIn('Clé API OpenAI refusée',str(error.exception))
            self.assertNotIn('never_print',str(error.exception))


if __name__ == "__main__":
    unittest.main()

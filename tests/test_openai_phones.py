import unittest
from unittest.mock import AsyncMock, patch
import server
from openai_phones import parse_openai_response, _safe_source


def response(model="Pixel 9", brand="GOOGLE", capacity="128 Go"):
    return ({
        "verified": True, "brand": brand, "model": model, "capacity": capacity,
        "specs": {"ecran":"6,3 pouces OLED", "photo":"50 Mpx", "batterie":"4700 mAh"}
    }, [{"title": "Fiche officielle", "url": "https://store.google.com/product/pixel_9_specs"}])


class OpenAIResponseTests(unittest.TestCase):
    def test_real_web_search_and_sources_required(self):
        p = {"output":[
            {"type":"web_search_call","action":{"sources":[{"url":"https://example.com/specs","title":"Fiche"}]}},
            {"type":"message","content":[{"type":"output_text","text":'{"verified":true,"brand":"GOOGLE","model":"Pixel 9"}'}]}
        ]}
        parsed=parse_openai_response(p)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[1][0]["url"],"https://example.com/specs")
        p["output"].pop(0)
        self.assertIsNone(parse_openai_response(p))

    def test_unsafe_sources_rejected(self):
        for url in ("http://example.com","https://localhost/path","https://127.0.0.1/","https://192.168.1.1/","https://user:secret@example.com/"):
            self.assertIsNone(_safe_source(url))
        self.assertIsNotNone(_safe_source("https://www.apple.com/fr/iphone/"))


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


if __name__ == "__main__":
    unittest.main()

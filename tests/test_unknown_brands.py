"""New manufacturers must reach grounded lookup rather than the HTTP 400 gate."""
import os
import unittest
from unittest.mock import AsyncMock,patch
import httpx
import server
from database_models import invalidate_database_models_cache


class UnknownBrandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        server.CACHE.clear();server.REQUEST_LOG.clear();server.GLOBAL_REQUEST_LOG.clear()
        invalidate_database_models_cache()

    async def test_unlisted_brand_reaches_ai_and_is_saved_then_reused(self):
        self.assertEqual(server._query_model('Fairdevice Zenith X1'),('Fairdevice Zenith X1','FAIRDEVICE'))
        parsed={'verified':True,'brand':'FAIRDEVICE','model':'Zenith X1','capacity':'Non précisée',
                'specs':{'ecran':'6,5 pouces OLED','photo':'50 Mpx','batterie':'5000 mAh'}}
        saved={}
        async def get(key):return saved.get(key)
        async def save(row):saved[row['model_key']]=row
        with patch.dict(os.environ,{'APP_ACCESS_TOKEN':'test'}),patch.object(server,'OPENAI_KEY','test'), \
             patch.object(server.store,'url','https://test.invalid'),patch.object(server.store,'key','test'), \
             patch.object(server.store,'get',new=get),patch.object(server.store,'save',new=save), \
             patch.object(server.store,'reserve',new=AsyncMock(return_value=True)), \
             patch.object(server,'get_database_models',new=AsyncMock(return_value=[])), \
             patch.object(server,'lookup_official_phone',new=AsyncMock(return_value=None)), \
             patch.object(server,'lookup_xiaomi_phone',new=AsyncMock(return_value=None)), \
             patch.object(server,'lookup_samsung_phone',new=AsyncMock(return_value=None)), \
             patch.object(server,'lookup_google_phone',new=AsyncMock(return_value=None)), \
             patch.object(server,'lookup_manufacturer_phone',new=AsyncMock(return_value=None)), \
             patch.object(server,'search_openai_web',new=AsyncMock(return_value=(parsed,[{'url':'https://example.com/specifications'}]))) as ai:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test',auth=('encre','test')) as c:
                first=await c.get('/api/phones/lookup',params={'reference':'Fairdevice Zenith X1'})
                again=await c.get('/api/phones/lookup',params={'reference':'FAIRDEVICE ZENITH X1'})
        self.assertEqual(first.status_code,200,first.text)
        self.assertEqual(again.status_code,200,again.text)
        self.assertEqual(first.json()['search_mode'],'openai_grounded')
        self.assertIn('FAIRDEVICE|ZENITHX1|Non précisée',saved)
        ai.assert_awaited_once()

    def test_plain_prose_and_bare_brands_are_not_models(self):
        for value in ['nouvelle marque','BINOM','trouve un telephone','bonjour tout le monde']:
            with self.subTest(value=value):self.assertIsNone(server._query_model(value))
        self.assertIsNone(server._query_model('SM-S938B'))

    async def test_binom_aliases_are_available_without_ai(self):
        with patch.dict(os.environ,{'APP_ACCESS_TOKEN':'test'}),patch.object(server.store,'url',''), \
             patch.object(server,'openai_lookup',new_callable=AsyncMock) as ai:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='http://test',auth=('encre','test')) as c:
                for name in ['BINOM SX1','BINOM MOBILE SX1']:
                    result=await c.get('/api/phones/lookup',params={'reference':name+' 32 Go'})
                    self.assertEqual(result.status_code,200,result.text)
                    self.assertEqual(result.json()['brand'],'BINOM')
                    self.assertEqual(result.json()['capacity'],'32 Go')
        ai.assert_not_awaited()

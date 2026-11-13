"""Regression cases for shop shorthand, AI identity resolution and durable reuse."""
import unittest
from unittest.mock import AsyncMock, patch
import server


class ModelResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.catalogue=patch.object(server,'_known_phone_models',return_value=[])
        self.registry=patch.object(server,'_DATABASE_MODEL_NAMES',[])
        self.catalogue.start();self.registry.start()
        self.addCleanup(self.catalogue.stop);self.addCleanup(self.registry.stop)

    async def ai_result(self,query,model,brand='XIAOMI',unambiguous=True):
        parsed={'verified':True,'brand':brand,'model':model,'capacity':'256 Go',
                'network_variant_unambiguous':unambiguous,
                'specs':{'ecran':'6,7 pouces AMOLED','photo':'50 Mpx','batterie':'5000 mAh','reseau':'5G'}}
        with patch.object(server,'OPENAI_KEY','test'),patch.object(server.store,'url','https://test.invalid'), \
             patch.object(server.store,'key','test'),patch.object(server.store,'reserve',new=AsyncMock(return_value=True)), \
             patch.object(server,'search_openai_web',new=AsyncMock(return_value=(parsed,[{'url':'https://www.mi.com/global/product/example/specs/'}]))):
            return await server.openai_lookup(query,True)

    async def test_ai_interprets_subbrands_plus_and_omitted_network(self):
        for query,model,brand,expected in [
            ('Xiaomi POCO X5 256 Go','X5 5G','POCO','POCO X5 5G'),
            ('Xiaomi Redmi Note 15 Pro +','Note 15 Pro+ 5G','REDMI','REDMI Note 15 Pro+ 5G'),
            ('XIAOMI REDMI NOTE 15 PRO PLUS','Redmi Note 15 Pro+ 5G','XIAOMI','Redmi Note 15 Pro+ 5G'),
            ('Samsung Galaxy Z Flip 4','Galaxy Z Flip4 5G','SAMSUNG','Galaxy Z Flip4 5G')]:
            with self.subTest(query=query):
                result=await self.ai_result(query,model,brand)
                self.assertIsNotNone(result)
                self.assertEqual(result['model'],expected)

    async def test_ai_cannot_cross_generation_suffix_or_explicit_network(self):
        for query,model in [('POCO X5','POCO X5 Pro 5G'),('POCO X5','POCO X6 5G'),
                            ('Redmi Note 15 Pro','Redmi Note 15 Pro+ 5G'),
                            ('Redmi Note 15 Pro 4G','Redmi Note 15 Pro 5G')]:
            with self.subTest(query=query):
                self.assertIsNone(await self.ai_result(query,model))

    async def test_unknown_network_needs_explicit_unambiguous_evidence(self):
        self.assertIsNone(await self.ai_result('POCO X5','POCO X5 5G',unambiguous=False))
        self.assertIsNone(await self.ai_result('POCO X5','POCO X5 5G',unambiguous='true'))

    async def test_known_4g_variant_blocks_ai_network_inference(self):
        server._DATABASE_MODEL_NAMES[:]=[{'brand':'XIAOMI','model':'POCO X5 4G','_aliases':[]}]
        self.assertIsNone(await self.ai_result('POCO X5','POCO X5 5G'))

    def test_documented_names_resolve_before_db_and_manufacturer_lookup(self):
        server._DATABASE_MODEL_NAMES[:]=[
            {'brand':'XIAOMI','model':'POCO X5 5G','_aliases':[]},
            {'brand':'XIAOMI','model':'Redmi Note 15 Pro+ 5G','_aliases':[]}]
        self.assertEqual(server.database_key('Xiaomi POCO X5 256 Go'),server.database_key('POCO X5 5G 256 Go'))
        for query in ['Redmi Note15 Pro+','Xiaomi Redmi Note 15 Pro +','Redmi Note 15 Pro Plus']:
            self.assertEqual(server._query_model(query),('Redmi Note 15 Pro+ 5G','XIAOMI'))

    async def test_resolved_ai_fiche_is_reused_for_short_and_canonical_names(self):
        saved={}
        async def save(row):saved[row['model_key']]=row
        async def get(key):return saved.get(key)
        result={'status':'verified_online','brand':'XIAOMI','model':'POCO X5 5G',
                'capacity':'256 Go','capacity_confirmed':True,'search_mode':'openai_grounded',
                'specs':{'ecran':'6,7 pouces AMOLED','photo':'50 Mpx','batterie':'5000 mAh'},
                'sources':[{'url':'https://www.mi.com/global/product/poco-x5-5g/specs/'}]}
        with patch.object(server.store,'url','https://test.invalid'),patch.object(server.store,'key','test'), \
             patch.object(server.store,'get',new=get),patch.object(server.store,'save',new=save), \
             patch.object(server,'_online_lookup',new=AsyncMock(return_value=result)) as search:
            first=await server.online_lookup('Xiaomi POCO X5 256 Go',True)
            short=await server.online_lookup('POCO X5 256 Go',True)
            canonical=await server.online_lookup('POCO X5 5G 256 Go',True)
        self.assertEqual(first,short);self.assertEqual(short,canonical)
        search.assert_awaited_once()

    async def test_short_name_uses_native_canonical_fiche_without_ai(self):
        import xiaomi_phones
        with patch.object(server,'_known_phone_models',wraps=self.catalogue.temp_original), \
             patch.object(server,'OPENAI_KEY','test'),patch.object(server,'openai_lookup',new_callable=AsyncMock) as ai:
            server.CACHE.clear()
            result=await server._online_lookup('Xiaomi POCO X5 128 Go',True)
        self.assertEqual(result['model'],'POCO X5 5G')
        self.assertEqual(result['capacity'],'128 Go')
        ai.assert_not_awaited()

    def test_compact_ram_storage_options_keep_both_capacities(self):
        from xiaomi_phones import parse_xiaomi_sheet
        from test_xiaomi_phones import SHEET
        sheet=SHEET.replace('12 Go + 256 Go / 12 Go + 512 Go','6+128/8+256GB')
        item=parse_xiaomi_sheet(sheet,'XIAOMI17T','https://www.mi.com/global/product/xiaomi-17t/specs/')
        self.assertEqual(item['capacities'],['128 Go','256 Go'])

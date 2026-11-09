import os
import unittest
from unittest.mock import AsyncMock, patch
import httpx
import server
from maintenance import catalogue_rows


class PersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_first(self):
        result={'status':'verified_online','model':'Pixel 9'}
        with patch.object(server.store,'url','https://test.supabase.co'), patch.object(server.store,'key','test'), patch.object(server.store,'get',new=AsyncMock(return_value={'result':result})), patch.object(server,'_online_lookup',new_callable=AsyncMock) as web:
            self.assertEqual(await server.online_lookup('Google Pixel 9'),result)
            web.assert_not_awaited()

    async def test_write_verified_only(self):
        with patch.object(server.store,'url','https://test.supabase.co'), patch.object(server.store,'key','test'), patch.object(server.store,'get',new=AsyncMock(return_value=None)), patch.object(server.store,'save',new_callable=AsyncMock) as save, patch.object(server,'_online_lookup',new=AsyncMock(return_value={'status':'not_found'})):
            await server.online_lookup('Google Pixel 9')
            self.assertEqual(save.await_args.args[0]['verification_status'],'incomplet')
            self.assertEqual(save.await_args.args[0]['result'],{})

    async def test_budget_failure_blocks_provider(self):
        with patch.object(server,'OPENAI_KEY','test'), patch.object(server.store,'url','https://test.supabase.co'), patch.object(server.store,'key','test'), patch.object(server.store,'reserve',new=AsyncMock(return_value=False)), patch.object(server,'search_openai_web',new_callable=AsyncMock) as web:
            with self.assertRaises(RuntimeError):
                await server.openai_lookup('Google Pixel 9',True)
            web.assert_not_awaited()

    async def test_access_required_and_health_public(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app),base_url='https://test') as client:
            with patch.dict(os.environ,{'APP_ACCESS_TOKEN':'secret'}):
                self.assertEqual((await client.get('/')).status_code,401)
                self.assertEqual((await client.get('/health')).status_code,200)
                self.assertEqual((await client.get('/',auth=('encre','secret'))).status_code,200)
            with patch.dict(os.environ,{'APP_ACCESS_TOKEN':''}):
                self.assertEqual((await client.get('/')).status_code,401)

    def test_variant_and_capacity_keys(self):
        self.assertEqual(server.database_key('iPhone 16 128 Go'),server.database_key('APPLE iPhone16 128Go'))
        self.assertNotEqual(server.database_key('Pixel 9 Pro'),server.database_key('Pixel 9'))
        self.assertNotEqual(server.database_key('Pixel 9 128 Go'),server.database_key('Pixel 9 256 Go'))
        rows=catalogue_rows()
        self.assertEqual(len(rows),len({r['model_key'] for r in rows}))
        self.assertTrue(all(r['sources'] for r in rows))

    async def test_verified_record_saved_with_sources(self):
        result={'status':'verified_online','brand':'GOOGLE','model':'Pixel 9',
                'capacity':'128 Go','specs':{'ecran':'OLED','photo':'50 Mpx','reseau':'5G'},
                'sources':[{'url':'https://store.google.com/product/pixel_9_specs'}],
                'search_mode':'openai_grounded'}
        with patch.object(server.store,'url','https://test.supabase.co'), patch.object(server.store,'key','test'), patch.object(server.store,'get',new=AsyncMock(return_value=None)), patch.object(server.store,'save',new_callable=AsyncMock) as save, patch.object(server,'_online_lookup',new=AsyncMock(return_value=result)):
            await server.online_lookup('Google Pixel 9 128 Go',True)
            row=save.await_args.args[0]
            self.assertEqual(row['verification_status'],'vérifié')
            self.assertEqual(row['result'],result)
            self.assertIsNotNone(row['next_check_at'])

    async def test_refresh_failure_preserves_verified_record(self):
        with patch.object(server.store,'url','https://test.supabase.co'), patch.object(server.store,'key','test'), patch.object(server.store,'get',new=AsyncMock(return_value={'result':{'status':'verified_online'}})), patch.object(server.store,'save',new_callable=AsyncMock) as save, patch.object(server,'_online_lookup',new=AsyncMock(return_value={'status':'not_found'})):
            await server.online_lookup('Google Pixel 9',True,refresh=True)
            save.assert_not_awaited()

    async def test_database_network_error_sanitized(self):
        from storage import Store
        db=Store()
        db.url='https://test.supabase.co'
        db.key='secret'
        with patch.object(httpx.AsyncClient,'request',new=AsyncMock(side_effect=httpx.ConnectError('secret upstream data'))):
            with self.assertRaisesRegex(RuntimeError,'Base centralisée indisponible'):
                await db.get('test')

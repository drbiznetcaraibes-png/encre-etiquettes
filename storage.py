"""Server-only Supabase REST access. No secrets or raw upstream errors exposed."""
import os
import httpx


class Store:
    def __init__(self):
        self.url = os.getenv('SUPABASE_URL', '').rstrip('/')
        self.key = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')

    @property
    def configured(self):
        return bool(self.url and self.key)

    async def request(self, method, path, **kwargs):
        if not self.configured:
            raise RuntimeError('Base centralisée non configurée.')
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.request(method, self.url + '/rest/v1/' + path,
                    headers={'apikey': self.key, 'Authorization': 'Bearer ' + self.key,
                             'Prefer': 'return=representation,resolution=merge-duplicates'}, **kwargs)
        except httpx.HTTPError:
            raise RuntimeError('Base centralisée indisponible.') from None
        if response.is_error:
            raise RuntimeError('Base centralisée indisponible.')
        try:
            return response.json() if response.content else None
        except ValueError:
            raise RuntimeError('Réponse de la base invalide.') from None

    async def get(self, key):
        rows = await self.request('GET', 'phone_specs', params={
            'model_key': 'eq.' + key, 'verification_status': 'eq.vérifié', 'limit': '1'})
        return rows[0] if rows else None

    async def save(self, row):
        return await self.request('POST', 'phone_specs', params={'on_conflict': 'model_key,capacity'}, json=row)

    async def reserve(self):
        # SQL lock makes the ceiling durable and atomic across all instances.
        return await self.request('POST', 'rpc/reserve_ai_call', json={})


store = Store()

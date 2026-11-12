import unittest
from unittest.mock import patch
import httpx
import google_phones as google


def table(model='Pixel 11 Pro XL', year=2026):
    return f'''<h2>Pixel phones ({year})</h2><table><caption>{model} tech specs</caption>
    <tr><th>Display</th><td>6.8-inch LTPO OLED Smooth Display (1-120 Hz)</td></tr>
    <tr><th>Memory &amp; storage</th><td>Memory 256 GB: 12 GB RAM 512 GB UFS and 1 TB UFS: 16 GB RAM</td></tr>
    <tr><th>Processor</th><td>Google Tensor G6 Titan M3 security coprocessor</td></tr>
    <tr><th>Front camera</th><td>42 MP selfie</td></tr>
    <tr><th>Rear camera</th><td>50 MP wide 48 MP ultrawide 48 MP telephoto</td></tr>
    <tr><th>Battery &amp; charging</th><td>Typical 5115 mAh (minimum 5000 mAh)</td></tr>
    <tr><th>Network</th><td>5G Sub6</td></tr></table>'''


class GoogleTests(unittest.IsolatedAsyncioTestCase):
    def test_storage_ram_camera_and_typical_battery_stay_scoped_to_the_model(self):
        result = google.parse_google_specs(table(), minimum_year=2025, maximum_year=2026)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['capacities'], ['256 Go', '512 Go', '1 To'])
        self.assertEqual(result[0]['specs']['photo'], '50 Mpx (principal)')
        self.assertEqual(result[0]['specs']['batterie'], '5 115 mAh')
        self.assertEqual(result[0]['release_year'], 2026)

    def test_year_window_and_exact_variant_captions(self):
        document = table('Pixel 9', 2024) + table('Pixel 11 Pro', 2026) + table('Pixel 12', 2027)
        phones = google.parse_google_specs(document, minimum_year=2025, maximum_year=2026)
        self.assertEqual([p['model'] for p in phones], ['Pixel 11 Pro'])
        self.assertEqual(google.parse_google_specs(table('Pixel 11 vs Pixel 11 Pro'), minimum_year=2025), [])

    async def test_snapshot_lookup_checks_variant_and_capacity_without_network(self):
        phones = google.parse_google_specs(table(), minimum_year=2025, maximum_year=2026)
        with patch.object(google, 'CATALOG', {'phones': phones}):
            found = await google.lookup_google_phone(None, 'Google Pixel 11 Pro XL 256 Go')
            wrong = await google.lookup_google_phone(None, 'Pixel 11 Pro XL 128 Go')
            neighbor = await google.lookup_google_phone(None, 'Pixel 11 Pro 256 Go')
        self.assertEqual(found['capacity'], '256 Go')
        self.assertEqual(wrong['status'], 'invalid_capacity')
        self.assertIsNone(neighbor)

    async def test_redirect_is_checked_before_following_and_failure_keeps_old_facts(self):
        calls = []
        def transport(request):
            calls.append(request.url.host)
            return httpx.Response(302, headers={'location': 'https://example.com/specs'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport), follow_redirects=True) as client:
            result = await google.collect(client, [{'model': 'Pixel 11 Pro'}])
        self.assertEqual(calls, ['support.google.com'])
        self.assertFalse(result['discovery_ok'])
        self.assertEqual(result['phones'], [])


if __name__ == '__main__':
    unittest.main()

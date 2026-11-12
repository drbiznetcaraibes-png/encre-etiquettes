import asyncio
import unittest
from unittest.mock import AsyncMock, patch

import database_models
from database_models import get_database_models, invalidate_database_models_cache


class FakeStore:
    def __init__(self, pages=None, configured=True):
        self.configured = configured
        self.request = AsyncMock(side_effect=pages or [[]])


def phone(model='Galaxy A16 5G', capacity='128 Go', brand='SAMSUNG',
          confirmed=True, available_capacities=None):
    return {'brand': brand, 'model': model, 'capacity': capacity,
            'confirmed': confirmed, 'available_capacities': available_capacities}


class DatabaseModelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_store_never_requests_database(self):
        store = FakeStore(configured=False)
        self.assertEqual(await get_database_models(store), [])
        store.request.assert_not_awaited()

    async def test_verified_minimal_query_and_capacity_grouping_preserve_variants(self):
        store = FakeStore([[
            phone(capacity='256GB'), phone(capacity='128 Go'), phone(capacity='Non précisée'),
            phone('Galaxy A16 4G', '64 go'), phone('Galaxy S25', '256 Go'),
            phone('Galaxy S25+', '512 Go'), phone('iPhone 17 Pro', '1024 Go', 'apple'),
            phone('iPhone 17 Pro', '1 TB', 'APPLE'), phone('iPhone 17 Pro Max', '2To', 'APPLE'),
            phone('Upcoming phone', 'Non précisée', 'GOOGLE'),
            phone('Upcoming phone', '8 Go RAM', 'GOOGLE'), phone('Upcoming phone', '999 Go', 'GOOGLE'),
            phone('Bad phone', '<script>', ''), None, {'brand': 'APPLE', 'model': 42},
        ]])
        models = await get_database_models(store)
        by_name = {row['model']: row for row in models}
        self.assertEqual(by_name['Galaxy A16 5G']['available_capacities'], ['128 Go', '256 Go'])
        self.assertEqual(by_name['Galaxy A16 4G']['available_capacities'], ['64 Go'])
        self.assertEqual(by_name['Galaxy S25']['available_capacities'], ['256 Go'])
        self.assertEqual(by_name['Galaxy S25+']['available_capacities'], ['512 Go'])
        self.assertEqual(by_name['iPhone 17 Pro']['available_capacities'], ['1 To'])
        self.assertEqual(by_name['iPhone 17 Pro Max']['available_capacities'], ['2 To'])
        self.assertEqual(by_name['Upcoming phone']['available_capacities'], [])
        self.assertTrue(all(row['_aliases'] == [] for row in models))
        self.assertEqual(len(models), 7)
        store.request.assert_awaited_once_with('GET', 'phone_specs', params={
            'select': ('brand,model,capacity,confirmed:result->capacity_confirmed,'
                       'available_capacities:result->available_capacities'),
            'verification_status': 'eq.vérifié',
            'order': 'brand.asc,model.asc,id.asc', 'limit': '1000', 'offset': '0',
        })

    async def test_requested_storage_without_explicit_confirmation_is_not_an_option(self):
        store = FakeStore([[
            phone('False confirmation', '256 Go', confirmed=False),
            phone('Null confirmation', '256 Go', confirmed=None),
            phone('Text confirmation', '256 Go', confirmed='true'),
            phone('Number confirmation', '256 Go', confirmed=1),
            {'brand': 'GOOGLE', 'model': 'Missing confirmation', 'capacity': '256 Go'},
        ]])
        models = await get_database_models(store)
        self.assertEqual(len(models), 5)
        self.assertTrue(all(row['available_capacities'] == [] for row in models))

    async def test_documented_options_exclude_requested_but_unconfirmed_capacity(self):
        store = FakeStore([[
            phone('Pixel 9', '256 Go', 'GOOGLE', confirmed=False,
                  available_capacities=['128 Go']),
        ]])
        self.assertEqual(await get_database_models(store), [{
            'brand': 'GOOGLE', 'model': 'Pixel 9',
            'available_capacities': ['128 Go'], '_aliases': [],
        }])

    async def test_documented_options_are_normalized_validated_and_merged_with_confirmed(self):
        store = FakeStore([[
            phone('Pixel 9', '512 Go', 'GOOGLE', confirmed=True,
                  available_capacities=['128GB', '1 TB', '1024 Go', 'Non précisée',
                                        '8 Go RAM', '999 Go', None, 256]),
            phone('Pixel 9', '256 Go', 'GOOGLE', confirmed=None,
                  available_capacities='256 Go'),
        ]])
        self.assertEqual((await get_database_models(store))[0]['available_capacities'],
                         ['128 Go', '512 Go', '1 To'])

    async def test_success_cache_is_five_minutes_and_caller_cannot_mutate_it(self):
        store = FakeStore([[phone()], [phone('Galaxy A17 5G')]])
        with patch.object(database_models, 'monotonic', return_value=100) as clock:
            first = await get_database_models(store)
            first[0]['available_capacities'].append('512 Go')
            first[0]['model'] = 'wrong'
            clock.return_value = 399
            cached = await get_database_models(store)
            self.assertEqual(cached[0]['model'], 'Galaxy A16 5G')
            self.assertEqual(cached[0]['available_capacities'], ['128 Go'])
            self.assertEqual(store.request.await_count, 1)
            clock.return_value = 400
            self.assertEqual((await get_database_models(store))[0]['model'], 'Galaxy A17 5G')
            self.assertEqual(store.request.await_count, 2)

    async def test_pages_stop_at_short_page_and_have_stable_offsets(self):
        first = [phone(f'Phone {i:04}') for i in range(1000)]
        second = [phone('New weekly phone', '256 Go')]
        store = FakeStore([first, second])
        self.assertEqual(len(await get_database_models(store)), 1001)
        self.assertEqual([call.kwargs['params']['offset'] for call in store.request.await_args_list], ['0', '1000'])

    async def test_pages_bounded_to_three_thousand_rows(self):
        pages = [[phone(f'Phone {page * 1000 + i:04}') for i in range(1000)] for page in range(4)]
        store = FakeStore(pages)
        self.assertEqual(len(await get_database_models(store)), 3000)
        self.assertEqual(store.request.await_count, 3)
        self.assertEqual(store.request.await_args.kwargs['params']['offset'], '2000')

    async def test_concurrent_callers_share_read_and_cancellation_is_isolated(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def request(*args, **kwargs):
            started.set()
            await release.wait()
            return [phone()]

        store = FakeStore()
        store.request.side_effect = request
        cancelled = asyncio.create_task(get_database_models(store))
        await started.wait()
        callers = [asyncio.create_task(get_database_models(store)) for _ in range(8)]
        await asyncio.sleep(0)
        cancelled.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await cancelled
        release.set()
        results = await asyncio.gather(*callers)
        self.assertTrue(all(result[0]['model'] == 'Galaxy A16 5G' for result in results))
        self.assertEqual(store.request.await_count, 1)

    async def test_failure_keeps_stale_cache_and_retries_after_one_minute(self):
        store = FakeStore([[phone()], RuntimeError('private upstream detail'), [phone('New weekly phone')]])
        with patch.object(database_models, 'monotonic', return_value=100) as clock:
            initial = await get_database_models(store)
            clock.return_value = 400
            self.assertEqual(await get_database_models(store), initial)
            clock.return_value = 459
            self.assertEqual(await get_database_models(store), initial)
            self.assertEqual(store.request.await_count, 2)
            clock.return_value = 460
            self.assertEqual((await get_database_models(store))[0]['model'], 'New weekly phone')

    async def test_initial_failure_is_cached_empty_and_invalid_payload_is_failure(self):
        store = FakeStore([{'error': 'not a row list'}, [phone()]])
        with patch.object(database_models, 'monotonic', return_value=100) as clock:
            self.assertEqual(await get_database_models(store), [])
            clock.return_value = 159
            self.assertEqual(await get_database_models(store), [])
            self.assertEqual(store.request.await_count, 1)
            clock.return_value = 160
            self.assertEqual(len(await get_database_models(store)), 1)

    async def test_later_page_failure_does_not_replace_stale_with_partial_snapshot(self):
        store = FakeStore([[phone('Original phone')], [phone(f'New {i}') for i in range(1000)], RuntimeError('unavailable')])
        original = await get_database_models(store)
        invalidate_database_models_cache(store)
        self.assertEqual(await get_database_models(store), original)
        self.assertEqual(store.request.await_count, 3)

    async def test_timeout_is_total_across_pages(self):
        async def request(*args, **kwargs):
            await asyncio.sleep(0.04)
            return [phone(f'Phone {i}') for i in range(1000)]

        store = FakeStore()
        store.request.side_effect = request
        with patch.object(database_models, 'FETCH_TIMEOUT_SECONDS', 0.06):
            started = asyncio.get_running_loop().time()
            self.assertEqual(await get_database_models(store), [])
            self.assertLess(asyncio.get_running_loop().time() - started, 0.3)
        self.assertEqual(store.request.await_count, 2)
        self.assertEqual(await get_database_models(store), [])
        self.assertEqual(store.request.await_count, 2)

    async def test_invalidation_expires_only_target_store_and_keeps_other_cache(self):
        first = FakeStore([[phone()], [phone('New weekly phone')]])
        second = FakeStore([[phone('Other database')]])
        await get_database_models(first)
        await get_database_models(second)
        invalidate_database_models_cache(first)
        self.assertEqual((await get_database_models(first))[0]['model'], 'New weekly phone')
        self.assertEqual((await get_database_models(second))[0]['model'], 'Other database')
        self.assertEqual(second.request.await_count, 1)


if __name__ == '__main__':
    unittest.main()

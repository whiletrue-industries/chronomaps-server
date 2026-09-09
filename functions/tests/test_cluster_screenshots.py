"""
Tests for the clustering pipeline's small-workspace fallback.

Workspaces with too few records for t-SNE are drawn as one block in the
middle of the map, under a single 'future screenshots' cluster, instead of
being skipped.
"""

from unittest.mock import patch

import numpy as np
import pytest

from cluster_screenshots import calc_tsne
from cluster_screenshots.calc_tsne import (
    FALLBACK_CLUSTER_TITLE, cluster_screenshots_inner, fallback_grid, single_cluster,
)
from cluster_screenshots.tsne_params import TSNEParams


def _cells(grid, out_dim):
    """Grid fractions -> integer (x, y) cells, the way create_tsne_image rounds them."""
    return [
        (round(pos[1] * (out_dim[0] - 1)), round(pos[0] * (out_dim[1] - 1)))
        for pos in grid
    ]


class TestFallbackGrid:
    def test_shape_matches_calc_tsne_grid(self):
        grid = fallback_grid(5, (23, 20))
        assert grid.shape == (5, 2)
        assert grid.min() >= 0 and grid.max() <= 1

    def test_cells_are_distinct_and_contiguous(self):
        out_dim = (23, 20)
        cells = _cells(fallback_grid(7, out_dim), out_dim)
        assert len(set(cells)) == 7
        xs = sorted({x for x, _ in cells})
        ys = sorted({y for _, y in cells})
        assert xs == list(range(xs[0], xs[-1] + 1))
        assert ys == list(range(ys[0], ys[-1] + 1))

    def test_block_sits_in_the_middle(self):
        out_dim = (23, 20)
        cells = _cells(fallback_grid(9, out_dim), out_dim)
        xs = [x for x, _ in cells]
        ys = [y for _, y in cells]
        assert (min(xs), max(xs)) == (10, 12)
        assert (min(ys), max(ys)) == (8, 10)

    def test_single_record_lands_centre(self):
        out_dim = (23, 20)
        assert _cells(fallback_grid(1, out_dim), out_dim) == [(11, 9)]

    def test_zero_records(self):
        assert fallback_grid(0, (23, 20)).shape == (0, 2)


class TestSingleCluster:
    def test_spans_every_grid_entry(self):
        info = dict(grid=[
            dict(pos=[10, 8], id='a', metadata=dict(rotate=10)),
            dict(pos=[11.5, 8], id='b', metadata=dict(rotate=-4)),
            dict(pos=[10, 9], id='c', metadata={}),
        ])
        clusters = single_cluster(info, FALLBACK_CLUSTER_TITLE)
        assert len(clusters) == 1
        cluster = clusters[0]
        assert cluster['title']['english'] == 'future screenshots'
        assert cluster['bounds'] == [[10, 8], [12.5, 10]]
        assert cluster['average_rotation'] == pytest.approx(2)

    def test_empty_grid_gives_no_cluster(self):
        assert single_cluster(dict(grid=[]), FALLBACK_CLUSTER_TITLE) == []


def _fake_records(count):
    # No screenshot_url, so get_image draws the bundled placeholder instead of
    # fetching anything.
    return [
        dict(_id=f'rec{i}', created_at=f'2025-01-{i + 1:02d}', embedding=[float(i)] * 4,
             future_scenario_description=f'scenario {i}')
        for i in range(count)
    ]


def _small_params():
    # A small grid keeps the rendered image tiny; SIDE sets the pixel scale.
    return TSNEParams(OUT_DIM_X=6, SIDE=120, MIN_TSNE_RECORDS=10)


def _run(records, params):
    def load(config, out, params):
        out.extend(records)
        yield dict(msg='loaded')

    actions = {}
    with patch.object(calc_tsne, 'load_records', load):
        for msg in cluster_screenshots_inner([], params):
            if 'action' in msg:
                actions[msg['action']] = msg
    return actions


class TestClusterScreenshotsInnerFallback:
    def test_too_few_records_still_produce_a_map(self):
        params = _small_params()
        with patch.object(calc_tsne, 'generate_tsne', side_effect=AssertionError('t-SNE must not run')), \
             patch.object(calc_tsne, 'calc_tsne_grid', side_effect=AssertionError('lapjv must not run')):
            actions = _run(_fake_records(5), params)

        assert 'tiles' in actions
        info = actions['clusters']['info']
        assert {g['id'] for g in info['grid']} == {f'rec{i}' for i in range(5)}
        assert info['clusters'] == single_cluster(info, FALLBACK_CLUSTER_TITLE)
        assert info['clusters'][0]['title'] == FALLBACK_CLUSTER_TITLE

    def test_fallback_block_is_centred_on_the_map(self):
        params = _small_params()
        actions = _run(_fake_records(4), params)
        info = actions['clusters']['info']
        xs = [int(g['pos'][0]) for g in info['grid']]
        ys = [int(g['pos'][1]) for g in info['grid']]
        # 6 x 5 grid: a 2x2 block sits at columns 2-3, rows 1-2.
        assert params.OUT_DIM == (6, 5)
        assert (min(xs), max(xs)) == (2, 3)
        assert (min(ys), max(ys)) == (1, 2)

    def test_no_records_writes_nothing(self):
        actions = _run([], _small_params())
        assert actions == {}

    def test_unchanged_records_are_skipped_before_the_fallback(self):
        records = _fake_records(3)
        params = _small_params()
        first = _run(records, params)
        state_hash = first['clusters']['info']['state_hash']

        def load(config, out, params):
            out.extend(records)
            yield dict(msg='loaded')

        with patch.object(calc_tsne, 'load_records', load):
            actions = [m for m in cluster_screenshots_inner([], params, last_state_hash=state_hash) if 'action' in m]
        assert actions == []

    def test_enough_records_take_the_tsne_path(self):
        params = _small_params()
        records = _fake_records(10)
        fake_2d = np.zeros((10, 2))

        with patch.object(calc_tsne, 'generate_tsne', return_value=fake_2d) as tsne, \
             patch.object(calc_tsne, 'calc_tsne_grid', return_value=fallback_grid(10, params.OUT_DIM)) as lap:
            actions = _run(records, params)

        tsne.assert_called_once()
        assert tsne.call_args.kwargs['perplexity'] == params.perplexity_for(10)
        lap.assert_called_once()
        # Clustering and titling are left to find_clusters downstream.
        assert 'clusters' not in actions['clusters']['info']


class TestPerplexity:
    """Perplexity scales with the set: a fixed 50 capped at n-1 flattens small maps."""

    @pytest.mark.parametrize('count, expected', [
        (10, 5),     # floor: n/3 would be 3, which is too few neighbours to be meaningful
        (11, 5),     # the case that prompted this: was 10 of 11, i.e. everyone is a neighbour
        (15, 5),
        (30, 10),
        (90, 30),
        (150, 50),   # ceiling
        (500, 50),
    ])
    def test_scales_with_record_count(self, count, expected):
        assert TSNEParams().perplexity_for(count) == expected

    @pytest.mark.parametrize('count', [2, 3, 5, 6])
    def test_always_below_the_record_count(self, count):
        # t-SNE refuses perplexity >= n; the floor must give way to that.
        assert 1 <= TSNEParams().perplexity_for(count) < count

    def test_ceiling_is_configurable(self):
        assert TSNEParams(PERPLEXITY=20).perplexity_for(500) == 20


def _response(payload, status=200):
    resp = type('Resp', (), {})()
    resp.status_code = status
    resp.json = lambda: payload
    def raise_for_status():
        if status >= 400:
            raise calc_tsne.requests.HTTPError(f'{status} error')
    resp.raise_for_status = raise_for_status
    return resp


class TestItemsAreFetchedInPages:
    """A large workspace's items are fetched page by page: one request for all
    of them exceeds Cloud Run's 32 MB response limit, the body comes back
    truncated, and the workspace fails to cluster on every run."""

    def _serve(self, total, params, status=200):
        # The API pages an ordered list by offset; the ids let us see what was kept.
        all_items = [dict(_id=f'r{i}') for i in range(total)]
        calls = []

        def fake_get(url, req_params=None, headers=None):
            if not url.endswith('/items'):
                return _response(dict(title='WS'))
            calls.append(dict(req_params))
            start = req_params['page'] * req_params['page_size']
            return _response(all_items[start:start + req_params['page_size']], status)

        records = []
        with patch.object(calc_tsne.requests, 'get', fake_get), \
             patch.object(calc_tsne, 'ensure_analysis') as ensure:
            ensure.return_value = iter(())
            list(calc_tsne.load_records([('ws', 'key')], records, params))
        fetched = ensure.call_args.args[0] if ensure.called else None
        return calls, fetched

    def test_pages_until_a_short_page(self):
        params = TSNEParams(FETCH_PAGE_SIZE=200)   # TO_PLOT*2 = 690 wanted
        calls, fetched = self._serve(450, params)
        assert [c['page'] for c in calls] == [0, 1, 2]
        assert all(c['page_size'] == 200 for c in calls)
        assert [c['include_embedding'] for c in calls] == ['true'] * 3
        assert [i['_id'] for i in fetched] == [f'r{i}' for i in range(450)]

    def test_stops_once_enough_are_in_hand(self):
        params = TSNEParams(FETCH_PAGE_SIZE=200)
        calls, fetched = self._serve(5000, params)
        assert [c['page'] for c in calls] == [0, 1, 2, 3]
        assert len(fetched) == params.TO_PLOT * 2

    def test_a_page_of_exactly_the_page_size_is_followed_by_an_empty_one(self):
        params = TSNEParams(FETCH_PAGE_SIZE=200)
        calls, fetched = self._serve(200, params)
        assert [c['page'] for c in calls] == [0, 1]
        assert len(fetched) == 200

    def test_an_item_repeated_across_pages_is_kept_once(self):
        params = TSNEParams(FETCH_PAGE_SIZE=2)
        pages = [[dict(_id='a'), dict(_id='b')], [dict(_id='b'), dict(_id='c')], [dict(_id='d')]]

        def fake_get(url, req_params=None, headers=None):
            if not url.endswith('/items'):
                return _response(dict(title='WS'))
            return _response(pages[req_params['page']])

        records = []
        with patch.object(calc_tsne.requests, 'get', fake_get), \
             patch.object(calc_tsne, 'ensure_analysis') as ensure:
            ensure.return_value = iter(())
            list(calc_tsne.load_records([('ws', 'key')], records, params))
        assert [i['_id'] for i in ensure.call_args.args[0]] == ['a', 'b', 'c', 'd']

    def test_an_http_error_is_raised_not_parsed(self):
        # The batch loop catches it per workspace and reports the status,
        # instead of a JSONDecodeError on a truncated body.
        with pytest.raises(calc_tsne.requests.HTTPError):
            self._serve(10, TSNEParams(FETCH_PAGE_SIZE=200), status=503)

    def test_an_error_body_skips_the_workspace(self):
        def fake_get(url, req_params=None, headers=None):
            return _response(dict(error='nope') if url.endswith('/items') else dict(title='WS'))

        records = []
        with patch.object(calc_tsne.requests, 'get', fake_get), \
             patch.object(calc_tsne, 'ensure_analysis') as ensure:
            list(calc_tsne.load_records([('ws', 'key')], records, TSNEParams()))
        ensure.assert_not_called()
        assert records == []


class TestAnalysisRunsRegardlessOfCount:
    """Embeddings and AI favorability/plausibility are backfilled for every
    fetched item, before the count decides whether t-SNE or the fallback runs."""

    def _load(self, items, params):
        def fake_get(url, req_params=None, headers=None):
            return _response(items if url.endswith('/items') else dict(title='WS'))

        records = []
        with patch.object(calc_tsne.requests, 'get', fake_get), \
             patch.object(calc_tsne, 'ensure_analysis') as ensure:
            ensure.return_value = iter(())
            list(calc_tsne.load_records([('ws', 'key')], records, params))
        return ensure, records

    def test_few_items_are_still_analysed(self):
        items = [dict(_id=f'r{i}', created_at=f'2025-01-0{i + 1}',
                      future_scenario_description=f'd{i}') for i in range(3)]
        ensure, _ = self._load(items, _small_params())
        ensure.assert_called_once()
        assert ensure.call_args.args[0] == items
        assert len(ensure.call_args.args[0]) == 3

    def test_items_the_map_rejects_are_still_analysed(self):
        # No favorability and no created_at: use_item drops them from the map,
        # but they are exactly the items the backfill exists for.
        items = [dict(_id='r0', future_scenario_description='d0')]
        ensure, records = self._load(items, _small_params())
        ensure.assert_called_once()
        assert ensure.call_args.args[0] == items
        assert records == []

"""
Tests for the dropbox_ingest module.

Covers the pure pieces (credentials parsing, state merging, batching, cropping)
and the folder flow end to end against a fake Dropbox client.
"""

import datetime
import json
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest
from PIL import Image

from dropbox_ingest import (
    ConfigurationError, FolderConfig, Settings, assign_batches, batches_from,
    derive_handler_url, empty_state, entry_key, find_config_entry, folder_created_at,
    is_known, merge_batches, merge_states, parse_credentials, process_folder, read_state,
    run_ingest, scan_time, upload_scan, write_state, MAX_FILE_ATTEMPTS,
)
from dropbox_ingest.dropbox_api import (
    DropboxClient, DropboxConflict, DropboxError, team_namespace_id,
)
from dropbox_ingest.images import ImageRejected, crop_to_ratio, prepare_image

NOW = datetime.datetime(2026, 8, 25, 12, 0, 0, tzinfo=datetime.timezone.utc)
CUTOFF = datetime.datetime(2026, 8, 20, 0, 0, 0, tzinfo=datetime.timezone.utc)


# -- helpers ----------------------------------------------------------------

def make_settings(**overrides):
    defaults = dict(
        app_key='key', app_secret='secret', refresh_token='refresh',
        root_path='/archive', folder_cutoff=CUTOFF, settle_seconds=180,
        chronomaps_api_url='https://api.example.com',
        screenshot_handler_url='https://handler.example.com',
    )
    defaults.update(overrides)
    return Settings(**defaults)


def batch(author_id, first, last=None):
    return {'author_id': author_id, 'first_scanned_at': first, 'last_scanned_at': last or first}


def file_entry(path, *, client_modified='2026-08-25T10:00:00Z', server_modified=None,
               content_hash=None, size=1000):
    name = path.rsplit('/', 1)[-1]
    return {
        '.tag': 'file', 'name': name, 'path_display': path, 'path_lower': path.lower(),
        'client_modified': client_modified, 'server_modified': server_modified or client_modified,
        'content_hash': content_hash or f'hash-{name}', 'size': size, 'rev': f'rev-{name}',
    }


def folder_entry(path):
    name = path.rsplit('/', 1)[-1]
    return {'.tag': 'folder', 'name': name, 'path_display': path, 'path_lower': path.lower()}


def image_bytes(width, height, fmt='JPEG'):
    buffer = BytesIO()
    Image.new('RGB', (width, height), (200, 180, 160)).save(buffer, format=fmt)
    return buffer.getvalue()


class FakeDropbox:
    """In-memory stand-in for DropboxClient."""

    def __init__(self, entries_by_folder, files=None):
        self.entries_by_folder = entries_by_folder
        self.files = files or {}
        self.uploads = []
        self.rev_counter = 0

    def list_folder(self, path, recursive=False):
        return iter(self.entries_by_folder.get(path, []))

    def get_metadata(self, path):
        if path in self.files:
            return {'.tag': 'file', 'path_display': path, 'rev': self.files[path][1]}
        return None

    def download(self, path):
        if path not in self.files:
            raise AssertionError(f'unexpected download of {path}')
        return self.files[path][0]

    def upload(self, path, data, rev=None):
        existing = self.files.get(path)
        if existing and (rev is None or existing[1] != rev):
            # Mirrors Dropbox: `add` fails if the file exists, `update` fails on a stale rev.
            raise DropboxConflict('rev mismatch', 409, {})
        self.rev_counter += 1
        new_rev = f'rev{self.rev_counter}'
        self.files[path] = (data, new_rev)
        self.uploads.append((path, data, rev))
        return {'rev': new_rev}


# -- credentials ------------------------------------------------------------

class TestParseCredentials:
    def test_key_value_lines(self):
        config = parse_credentials('workspace: ws-1\napi_key: key-1\n')
        assert config.workspace == 'ws-1'
        assert config.api_key == 'key-1'
        assert config.enabled is True
        assert config.ignore_cutoff is False

    def test_json(self):
        config = parse_credentials(json.dumps({'workspace': 'ws-2', 'api_key': 'key-2'}))
        assert (config.workspace, config.api_key) == ('ws-2', 'key-2')

    def test_comments_equals_and_quotes(self):
        config = parse_credentials('# a comment\nworkspace = "ws-3"\nAPI_KEY = key-3\n\n')
        assert (config.workspace, config.api_key) == ('ws-3', 'key-3')

    def test_optional_overrides(self):
        config = parse_credentials(
            'workspace: ws\napi_key: key\nenabled: false\nignore_cutoff: yes\n'
            'batch_gap_seconds: 300\nratio: 0.6\nratio_tolerance: 0.2\n'
            'max_uploads_per_run: 5\ntime_source: server\nrotate_landscape: cw\n')
        assert config.enabled is False
        assert config.ignore_cutoff is True
        assert config.batch_gap_seconds == 300
        assert config.ratio == 0.6
        assert config.ratio_tolerance == 0.2
        assert config.max_uploads_per_run == 5
        assert config.time_source == 'server'
        assert config.rotate_landscape == 'cw'

    def test_missing_fields_rejected(self):
        with pytest.raises(ConfigurationError):
            parse_credentials('workspace: ws-only\n')

    def test_unparsable_json_rejected(self):
        with pytest.raises(ConfigurationError):
            parse_credentials('{not json')

    def test_non_numeric_override_is_a_folder_problem_not_a_crash(self):
        """A decimal comma must fail this folder, not the whole scheduled run."""
        with pytest.raises(ConfigurationError):
            parse_credentials('workspace: ws\napi_key: k\nratio: 0,53\n')


class TestDeriveHandlerUrl:
    def test_cloud_run_form(self):
        assert derive_handler_url('https://chronomaps-api-qjzuw7ypfq-ez.a.run.app/') == \
            'https://screenshot-handler-qjzuw7ypfq-ez.a.run.app'

    def test_cloud_functions_form(self):
        assert derive_handler_url('https://europe-west4-chronomaps3.cloudfunctions.net/chronomaps_api') == \
            'https://europe-west4-chronomaps3.cloudfunctions.net/screenshot_handler'

    def test_unrecognised_form_is_an_error_not_a_silent_no_op(self):
        with pytest.raises(ConfigurationError):
            derive_handler_url('https://api.internal.example.com/v2')


class TestDropboxClientTransport:
    """Network trouble must arrive as DropboxError, not as a raw requests error."""

    def _client(self, session):
        client = DropboxClient('key', 'secret', 'refresh', session=session)
        client._token, client._token_expires_at = 'token', 2 ** 40
        return client

    def test_connection_errors_are_retried_then_wrapped(self, monkeypatch):
        import requests as requests_module
        monkeypatch.setattr('dropbox_ingest.dropbox_api.time.sleep', lambda _s: None)
        session = Mock()
        session.post.side_effect = requests_module.ConnectionError('reset by peer')

        with pytest.raises(DropboxError) as excinfo:
            self._client(session).get_metadata('/archive/ws/a.jpg')

        assert 'ConnectionError' in str(excinfo.value)
        assert session.post.call_count > 1, 'transient errors are retried'

    def test_recovers_when_a_retry_succeeds(self, monkeypatch):
        import requests as requests_module
        monkeypatch.setattr('dropbox_ingest.dropbox_api.time.sleep', lambda _s: None)
        ok = Mock(status_code=200)
        ok.json.return_value = {'rev': 'r1'}
        session = Mock()
        session.post.side_effect = [requests_module.ReadTimeout('slow'), ok]

        assert self._client(session).get_metadata('/archive/ws/a.jpg') == {'rev': 'r1'}

    def test_token_is_refreshed_once_for_concurrent_workers(self):
        import threading
        token_response = Mock(status_code=200)
        token_response.json.return_value = {'access_token': 'fresh', 'expires_in': 14400}
        session = Mock()
        session.post.return_value = token_response

        client = DropboxClient('key', 'secret', 'refresh', session=session)
        threads = [threading.Thread(target=client.access_token) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert session.post.call_count == 1, 'a locked refresh, not one per worker'

    def test_team_namespace_detected_only_when_home_differs_from_root(self):
        personal = {'root_info': {'root_namespace_id': '12345', 'home_namespace_id': '12345'}}
        team = {'root_info': {'root_namespace_id': '99999', 'home_namespace_id': '12345'}}
        assert team_namespace_id(personal) is None
        assert team_namespace_id(team) == '99999'
        assert team_namespace_id({}) is None

    def test_threads_get_their_own_session(self):
        import threading
        client = DropboxClient('key', 'secret', 'refresh')
        sessions = []
        threads = [threading.Thread(target=lambda: sessions.append(client.session)) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sessions[0] is not sessions[1]


# -- listing / eligibility --------------------------------------------------

class TestFolderEligibility:
    def test_config_file_must_be_at_folder_root(self):
        entries = [file_entry('/archive/ws/sub/chronomaps.config'), file_entry('/archive/ws/a.jpg')]
        assert find_config_entry(entries, '/archive/ws') is None

    def test_config_file_found_by_any_accepted_name(self):
        for name in ('chronomaps.config', '.chronomaps.config', 'chronomaps.txt'):
            entries = [file_entry(f'/archive/ws/{name}')]
            assert find_config_entry(entries, '/archive/ws')['name'] == name

    def test_folder_created_at_is_oldest_server_modified(self):
        entries = [
            folder_entry('/archive/ws/sub'),
            file_entry('/archive/ws/b.jpg', server_modified='2026-08-22T09:00:00Z'),
            file_entry('/archive/ws/a.jpg', server_modified='2026-08-21T09:00:00Z'),
        ]
        assert folder_created_at(entries) == datetime.datetime(2026, 8, 21, 9, 0, tzinfo=datetime.timezone.utc)

    def test_folder_created_at_none_when_empty(self):
        assert folder_created_at([folder_entry('/archive/ws/sub')]) is None


class TestIsKnown:
    def test_uploaded_and_skipped_are_known(self):
        state = empty_state('ws')
        state['files']['h1'] = {'item_id': 'i1'}
        state['skipped']['h2'] = {'reason': 'ratio'}
        assert is_known(state, 'h1') and is_known(state, 'h2')

    def test_failures_retry_until_quarantined(self):
        state = empty_state('ws')
        state['failed']['h3'] = {'attempts': MAX_FILE_ATTEMPTS - 1}
        assert not is_known(state, 'h3')
        state['failed']['h3'] = {'attempts': MAX_FILE_ATTEMPTS}
        assert is_known(state, 'h3')


# -- batching ---------------------------------------------------------------

class TestAssignBatches:
    def test_close_scans_share_an_author(self):
        entries = [
            file_entry('/ws/1.jpg', client_modified='2026-08-25T10:00:00Z'),
            file_entry('/ws/2.jpg', client_modified='2026-08-25T10:00:30Z'),
            file_entry('/ws/3.jpg', client_modified='2026-08-25T10:01:00Z'),
        ]
        assignments, _ = assign_batches(entries, None, 120)
        authors = {a[2] for a in assignments}
        assert len(authors) == 1

    def test_gap_starts_a_new_batch(self):
        entries = [
            file_entry('/ws/1.jpg', client_modified='2026-08-25T10:00:00Z'),
            file_entry('/ws/2.jpg', client_modified='2026-08-25T10:05:00Z'),
        ]
        assignments, batches = assign_batches(entries, None, 120)
        assert assignments[0][2] != assignments[1][2]
        assert [b['author_id'] for b in batches] == [assignments[0][2], assignments[1][2]]
        assert batches[-1]['last_scanned_at'] == '2026-08-25T10:05:00Z'

    def test_boundary_is_inclusive(self):
        entries = [
            file_entry('/ws/1.jpg', client_modified='2026-08-25T10:00:00Z'),
            file_entry('/ws/2.jpg', client_modified='2026-08-25T10:02:00Z'),
        ]
        assignments, _ = assign_batches(entries, None, 120)
        assert assignments[0][2] == assignments[1][2]

    def test_late_arrival_joins_previous_run_batch(self):
        """A page that syncs late still lands in the batch it was scanned with."""
        history = [batch('author-from-earlier-run', '2026-08-25T09:59:00Z', '2026-08-25T10:00:00Z')]
        entries = [file_entry('/ws/late.jpg', client_modified='2026-08-25T10:01:00Z',
                              server_modified='2026-08-25T11:30:00Z')]
        assignments, _ = assign_batches(entries, history, 120)
        assert assignments[0][2] == 'author-from-earlier-run'

    def test_old_scan_arriving_late_starts_its_own_batch(self):
        history = [batch('current-batch', '2026-08-25T09:58:00Z', '2026-08-25T10:00:00Z')]
        entries = [file_entry('/ws/old.jpg', client_modified='2026-08-22T09:00:00Z',
                              server_modified='2026-08-25T11:00:00Z')]
        assignments, _ = assign_batches(entries, history, 120)
        assert assignments[0][2] != 'current-batch'

    def test_late_page_rejoins_its_own_batch_not_the_newest_one(self):
        """With two batches already known, a straggler must match the right one."""
        history = [batch('author-a', '2026-08-25T10:00:00Z', '2026-08-25T10:02:00Z'),
                   batch('author-b', '2026-08-25T10:30:00Z', '2026-08-25T10:32:00Z')]
        entries = [file_entry('/ws/straggler.jpg', client_modified='2026-08-25T10:03:00Z',
                              server_modified='2026-08-25T11:40:00Z')]
        assignments, _ = assign_batches(entries, history, 120)
        assert assignments[0][2] == 'author-a'

    def test_batch_history_is_capped(self):
        entries = [file_entry(f'/ws/{i}.jpg', client_modified=f'2026-08-25T{10 + i:02d}:00:00Z')
                   for i in range(13)]
        _assignments, batches = assign_batches(entries, None, 120)
        assert len(batches) == 10, 'only the most recent batches stay joinable'

    def test_batches_from_summarises_ranges(self):
        entries = [file_entry('/ws/1.jpg', client_modified='2026-08-25T10:00:00Z'),
                   file_entry('/ws/2.jpg', client_modified='2026-08-25T10:01:00Z')]
        assignments, _ = assign_batches(entries, None, 120)
        summary = batches_from(assignments)
        assert summary == [{'author_id': assignments[0][2],
                            'first_scanned_at': '2026-08-25T10:00:00Z',
                            'last_scanned_at': '2026-08-25T10:01:00Z'}]

    def test_sorted_by_scan_time_not_sync_time(self):
        entries = [
            file_entry('/ws/second.jpg', client_modified='2026-08-25T10:01:00Z',
                       server_modified='2026-08-25T10:01:10Z'),
            file_entry('/ws/first.jpg', client_modified='2026-08-25T10:00:00Z',
                       server_modified='2026-08-25T11:00:00Z'),
        ]
        assignments, _ = assign_batches(entries, None, 120)
        assert [a[0]['name'] for a in assignments] == ['first.jpg', 'second.jpg']

    def test_time_source_override(self):
        entry = file_entry('/ws/a.jpg', client_modified='2026-08-25T10:00:00Z',
                           server_modified='2026-08-25T11:00:00Z')
        assert scan_time(entry).hour == 10
        assert scan_time(entry, 'server').hour == 11


# -- state ------------------------------------------------------------------

class TestState:
    def test_read_state_missing_file(self):
        client = FakeDropbox({}, {})
        state, rev = read_state(client, '/archive/ws', 'ws-1')
        assert rev is None and state['files'] == {} and state['workspace'] == 'ws-1'

    def test_read_state_rejects_corrupt_file(self):
        client = FakeDropbox({}, {'/archive/ws/chronomaps.state.json': (b'not json', 'rev1')})
        with pytest.raises(ConfigurationError):
            read_state(client, '/archive/ws', 'ws-1')

    def test_merge_keeps_both_sides_records_and_batches(self):
        remote = {'files': {'a': {'item_id': 'remote'}}, 'skipped': {}, 'failed': {},
                  'workspace': 'ws', 'recent_batches': [batch('r', '2026-08-25T09:00:00Z')]}
        local = {'files': {'b': {'item_id': 'local'}}, 'skipped': {}, 'failed': {},
                 'workspace': 'ws', 'recent_batches': [batch('l', '2026-08-25T10:00:00Z')]}
        merged = merge_states(remote, local)
        assert set(merged['files']) == {'a', 'b'}
        assert [b['author_id'] for b in merged['recent_batches']] == ['r', 'l']

    def test_merge_batches_widens_a_known_batch(self):
        merged = merge_batches([batch('a', '2026-08-25T10:00:00Z', '2026-08-25T10:01:00Z')],
                               [batch('a', '2026-08-25T09:59:00Z', '2026-08-25T10:05:00Z')])
        assert merged == [batch('a', '2026-08-25T09:59:00Z', '2026-08-25T10:05:00Z')]

    def test_legacy_last_batch_state_is_migrated(self):
        legacy = json.dumps({'version': 1, 'workspace': 'ws', 'files': {}, 'skipped': {}, 'failed': {},
                             'last_batch': {'author_id': 'old', 'last_scanned_at': '2026-08-25T10:00:00Z'}})
        client = FakeDropbox({}, {'/archive/ws/chronomaps.state.json': (legacy.encode(), 'rev1')})
        state, _rev = read_state(client, '/archive/ws', 'ws')
        assert state['recent_batches'] == [batch('old', '2026-08-25T10:00:00Z')]
        assert 'last_batch' not in state

    def test_first_write_merges_with_a_concurrent_creation(self):
        """Two runs creating the state file at once must not lose each other's records."""
        concurrent = json.dumps({'version': 1, 'workspace': 'ws', 'files': {'theirs': {'item_id': 'x'}},
                                 'skipped': {}, 'failed': {}, 'last_batch': None}).encode()
        client = FakeDropbox({}, {'/archive/ws/chronomaps.state.json': (concurrent, 'their-rev')})
        state = empty_state('ws')
        state['files']['ours'] = {'item_id': 'y'}

        written, _ = write_state(client, '/archive/ws', state, None)

        assert set(written['files']) == {'theirs', 'ours'}

    def test_write_state_merges_on_conflict(self):
        """A concurrent writer's records survive our write."""
        concurrent = json.dumps({'version': 1, 'workspace': 'ws', 'files': {'theirs': {'item_id': 'x'}},
                                 'skipped': {}, 'failed': {}, 'last_batch': None}).encode()
        client = FakeDropbox({}, {'/archive/ws/chronomaps.state.json': (concurrent, 'newer-rev')})
        state = empty_state('ws')
        state['files']['ours'] = {'item_id': 'y'}

        written, rev = write_state(client, '/archive/ws', state, 'stale-rev')

        assert set(written['files']) == {'theirs', 'ours'}
        assert rev is not None


# -- image preparation ------------------------------------------------------

class TestPrepareImage:
    def test_exact_ratio_passes_through(self):
        data, info = prepare_image(image_bytes(530, 1000))
        assert Image.open(BytesIO(data)).size == (530, 1000)
        assert info['rotated'] is False

    def test_within_tolerance_is_cropped_to_target(self):
        data, _ = prepare_image(image_bytes(560, 1000))  # 0.56 -> 1.06x target
        width, height = Image.open(BytesIO(data)).size
        assert width / height == pytest.approx(0.53, abs=0.005)

    def test_outside_tolerance_is_rejected(self):
        with pytest.raises(ImageRejected) as excinfo:
            prepare_image(image_bytes(750, 1000))  # 0.75 -> 1.42x target
        assert 'aspect ratio' in excinfo.value.reason

    def test_tolerance_boundaries(self):
        prepare_image(image_bytes(583, 1000))  # 1.10x — allowed
        with pytest.raises(ImageRejected):
            prepare_image(image_bytes(590, 1000))  # 1.11x — rejected

    def test_tiny_image_rejected(self):
        with pytest.raises(ImageRejected) as excinfo:
            prepare_image(image_bytes(106, 200))
        assert 'too small' in excinfo.value.reason

    def test_unreadable_rejected(self):
        with pytest.raises(ImageRejected):
            prepare_image(b'this is not an image')

    def test_oversized_file_rejected(self):
        with pytest.raises(ImageRejected):
            prepare_image(image_bytes(530, 1000), max_bytes=10)

    def test_landscape_rejected_by_default_rotated_when_configured(self):
        landscape = image_bytes(1000, 530)
        with pytest.raises(ImageRejected):
            prepare_image(landscape)
        data, info = prepare_image(landscape, rotate_landscape='cw')
        width, height = Image.open(BytesIO(data)).size
        assert info['rotated'] is True and width < height

    def test_large_scan_is_downscaled(self):
        data, _ = prepare_image(image_bytes(2650, 5000))
        width, height = Image.open(BytesIO(data)).size
        assert (width, height) <= (2120, 4000)
        assert width / height == pytest.approx(0.53, abs=0.005)

    def test_huge_image_rejected_before_decoding(self):
        """Four workers decoding at once must not be able to exhaust the container."""
        with pytest.raises(ImageRejected) as excinfo:
            prepare_image(image_bytes(4000, 7547), max_pixels=1_000_000)
        assert 'too many pixels' in excinfo.value.reason

    def test_crop_to_ratio_centres(self):
        cropped = crop_to_ratio(Image.new('RGB', (1000, 1000)))
        assert cropped.size == (530, 1000)


# -- upload -----------------------------------------------------------------

class TestUploadScan:
    def _session(self, post_payload=None, post_status=200, put_status=200):
        session = Mock()
        post_response = Mock(status_code=post_status, text='')
        post_response.json.return_value = post_payload or {'item_id': 'item-1', 'item_key': 'key-1'}
        session.post.return_value = post_response
        session.put.return_value = Mock(status_code=put_status, text='')
        return session

    def test_posts_in_automatic_mode_and_puts_author_id(self):
        session = self._session()
        config = FolderConfig(workspace='ws-1', api_key='api-key')
        item_id, item_key, metadata_error = upload_scan(
            make_settings(), config, b'jpeg', 'page.jpg', 'author-9',
            {'source': 'dropbox'}, session=session)

        assert (item_id, item_key, metadata_error) == ('item-1', 'key-1', None)
        post_params = session.post.call_args.kwargs['params']
        assert post_params == {'workspace': 'ws-1', 'api_key': 'api-key', 'automatic': 'true'}
        assert 'image' in session.post.call_args.kwargs['files']

        put_args = session.put.call_args
        assert put_args.args[0] == 'https://api.example.com/ws-1/item-1'
        assert put_args.kwargs['json']['author_id'] == 'author-9'
        assert put_args.kwargs['json']['source'] == 'dropbox'
        assert put_args.kwargs['headers'] == {'Authorization': 'api-key'}
        assert put_args.kwargs['params'] == {'item-key': 'key-1'}

    def test_metadata_is_not_sent_to_the_vision_prompt(self):
        """Bookkeeping fields must not ride along in the handler's `metadata` form field."""
        session = self._session()
        upload_scan(make_settings(), FolderConfig(workspace='ws', api_key='k'), b'jpeg', 'p.jpg',
                    'author', {'source': 'dropbox'}, session=session)
        assert 'data' not in session.post.call_args.kwargs
        assert set(session.post.call_args.kwargs['files']) == {'image'}

    def test_handler_failure_raises(self):
        session = self._session(post_status=403)
        with pytest.raises(RuntimeError):
            upload_scan(make_settings(), FolderConfig(workspace='ws', api_key='k'), b'x', 'p.jpg',
                        'a', {}, session=session)

    def test_metadata_failure_is_reported_not_raised(self):
        """The item already exists — raising would make the next run duplicate it."""
        session = self._session(put_status=500)
        item_id, _key, metadata_error = upload_scan(
            make_settings(), FolderConfig(workspace='ws', api_key='k'), b'x', 'p.jpg',
            'a', {}, session=session)
        assert item_id == 'item-1'
        assert 'metadata update returned 500' in metadata_error


# -- folder flow ------------------------------------------------------------

class FolderFixture:
    """A Dropbox folder with a credentials file and a few scans."""

    def __init__(self, files=(), config_text='workspace: ws-1\napi_key: key-1\n',
                 created='2026-08-24T09:00:00Z', state=None):
        self.folder = folder_entry('/archive/ws')
        entries = [file_entry('/archive/ws/chronomaps.config', server_modified=created)]
        stored = {'/archive/ws/chronomaps.config': (config_text.encode(), 'rev-config')}
        for entry_spec in files:
            name, data, client_modified, server_modified = entry_spec[:4]
            content_hash = entry_spec[4] if len(entry_spec) > 4 else f'hash-{name}'
            path = f'/archive/ws/{name}'
            entries.append(file_entry(path, client_modified=client_modified,
                                      server_modified=server_modified, content_hash=content_hash,
                                      size=len(data)))
            stored[path] = (data, f'rev-{name}')
        if state is not None:
            stored['/archive/ws/chronomaps.state.json'] = (json.dumps(state).encode(), 'rev-state')
            entries.append(file_entry('/archive/ws/chronomaps.state.json', server_modified=created))
        self.client = FakeDropbox({'/archive/ws': entries, '/archive': [self.folder]}, stored)

    def run(self, settings=None, **kwargs):
        return list(process_folder(self.client, self.folder, settings or make_settings(),
                                   now=NOW, **kwargs))

    def state(self):
        return json.loads(self.client.files['/archive/ws/chronomaps.state.json'][0])


def upload_session():
    """A requests-like session that names each item after the file it received.

    Uploads run concurrently, so the response must depend on the request rather
    than on call order.
    """
    session = Mock()

    def post(*args, **kwargs):
        filename = kwargs['files']['image'][0]
        response = Mock(status_code=200, text='')
        response.json.return_value = {'item_id': f'item-{Path(filename).stem}', 'item_key': 'k'}
        return response

    session.post.side_effect = post
    session.put.return_value = Mock(status_code=200, text='')
    return session


class TestProcessFolder:
    def test_folder_without_credentials_is_skipped_untouched(self):
        client = FakeDropbox({'/archive/ws': [file_entry('/archive/ws/a.jpg')]}, {})
        results = list(process_folder(client, folder_entry('/archive/ws'), make_settings(), now=NOW))
        assert results == [{'folder': 'ws', 'action': 'skip-folder', 'reason': 'no credentials file'}]
        assert client.uploads == []

    def test_folder_created_before_cutoff_is_skipped(self):
        fixture = FolderFixture(files=[('a.jpg', image_bytes(530, 1000),
                                        '2026-08-19T10:00:00Z', '2026-08-19T10:00:00Z')],
                                created='2026-08-19T09:00:00Z')
        results = fixture.run()
        assert results[0]['action'] == 'skip-folder'
        assert 'before cutoff' in results[0]['reason']

    def test_ignore_cutoff_override_backfills_old_folder(self):
        fixture = FolderFixture(
            files=[('a.jpg', image_bytes(530, 1000), '2026-08-19T10:00:00Z', '2026-08-19T10:00:00Z')],
            config_text='workspace: ws-1\napi_key: key-1\nignore_cutoff: true\n',
            created='2026-08-19T09:00:00Z')
        results = fixture.run(session=upload_session())
        assert [r['action'] for r in results if r['action'] == 'uploaded']

    def test_disabled_folder_is_skipped(self):
        fixture = FolderFixture(config_text='workspace: ws-1\napi_key: key-1\nenabled: false\n')
        assert fixture.run()[0]['reason'] == 'disabled in credentials file'

    def test_uploads_new_scans_and_records_state(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
            ('b.jpg', image_bytes(530, 1000), '2026-08-25T10:00:40Z', '2026-08-25T10:01:00Z'),
        ])
        results = fixture.run(session=upload_session())

        uploaded = [r for r in results if r['action'] == 'uploaded']
        assert len(uploaded) == 2
        assert len({r['author_id'] for r in uploaded}) == 1, 'same batch shares an author'

        state = fixture.state()
        assert set(state['files']) == {'hash-a.jpg', 'hash-b.jpg'}
        assert state['files']['hash-a.jpg']['item_id'] == 'item-a'  # named after a.jpg
        assert [b['author_id'] for b in state['recent_batches']] == [uploaded[0]['author_id']]

    def test_already_uploaded_scans_are_not_uploaded_again(self):
        state = dict(empty_state('ws-1'), files={'hash-a.jpg': {'item_id': 'item-a'}})
        fixture = FolderFixture(
            files=[('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z')],
            state=state)
        session = upload_session()
        results = fixture.run(session=session)
        assert results[0]['new'] == 0
        assert session.post.call_count == 0

    def test_scans_still_syncing_are_deferred(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T11:59:50Z', '2026-08-25T11:59:55Z'),
        ])
        results = fixture.run(session=upload_session())
        assert results[0]['ready'] == 0 and results[0]['syncing'] == 1

    def test_wrong_ratio_is_skipped_and_remembered(self):
        fixture = FolderFixture(files=[
            ('wide.jpg', image_bytes(1000, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
        ])
        results = fixture.run(session=upload_session())
        skipped = [r for r in results if r['action'] == 'skip-image']
        assert len(skipped) == 1 and 'aspect ratio' in skipped[0]['reason']
        assert 'hash-wide.jpg' in fixture.state()['skipped']

    def test_upload_failure_is_recorded_for_retry(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
        ])
        session = Mock()
        session.post.return_value = Mock(status_code=500, text='boom')
        results = fixture.run(session=session)

        assert [r for r in results if r['action'] == 'error']
        failed = fixture.state()['failed']['hash-a.jpg']
        assert failed['attempts'] == 1
        assert not is_known(fixture.state(), 'hash-a.jpg'), 'retried on the next run'

    def test_separate_batches_get_separate_authors(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
            ('b.jpg', image_bytes(530, 1000), '2026-08-25T10:30:00Z', '2026-08-25T10:30:20Z'),
        ])
        results = fixture.run(session=upload_session())
        authors = {r['author_id'] for r in results if r['action'] == 'uploaded'}
        assert len(authors) == 2

    def test_max_uploads_per_run_caps_and_defers(self):
        fixture = FolderFixture(
            files=[(f'{i}.jpg', image_bytes(530, 1000), f'2026-08-25T10:0{i}:00Z',
                    f'2026-08-25T10:0{i}:20Z') for i in range(3)],
            config_text='workspace: ws-1\napi_key: key-1\nmax_uploads_per_run: 2\n')
        results = fixture.run(session=upload_session())
        assert [r for r in results if r['action'] == 'capped'][0]['deferred'] == 1
        assert len([r for r in results if r['action'] == 'uploaded']) == 2
        assert len(fixture.state()['files']) == 2

    def test_dry_run_writes_nothing(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
        ])
        results = fixture.run(dry_run=True)
        assert [r['action'] for r in results if r['action'] == 'would-upload']
        assert fixture.client.uploads == []


class TestPartialFailures:
    """Cases where an image could be uploaded twice, or lost entirely."""

    def test_failed_metadata_put_does_not_re_upload_next_run(self):
        session = Mock()
        post_response = Mock(status_code=200, text='')
        post_response.json.return_value = {'item_id': 'item-a', 'item_key': 'k'}
        session.post.return_value = post_response
        session.put.return_value = Mock(status_code=500, text='nope')

        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
        ])
        results = fixture.run(session=session)

        uploaded = [r for r in results if r['action'] == 'uploaded']
        assert len(uploaded) == 1 and 'metadata update returned 500' in uploaded[0]['metadata_error']
        record = fixture.state()['files']['hash-a.jpg']
        assert record['item_id'] == 'item-a'
        assert 'metadata_error' in record, 'visible to a human, but never retried'

        # Second run over the same folder: the item exists, so nothing is re-posted.
        second = FolderFixture(
            files=[('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z')],
            state=fixture.state())
        session.post.reset_mock()
        second.run(session=session)
        assert session.post.call_count == 0

    def test_duplicate_content_in_one_listing_uploads_once(self):
        """Conflicted copies share a content hash — and the state file has one record per hash."""
        data = image_bytes(530, 1000)
        fixture = FolderFixture(files=[
            ('a.jpg', data, '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z', 'same-hash'),
            ('a (conflicted copy).jpg', data, '2026-08-25T10:00:05Z', '2026-08-25T10:00:25Z', 'same-hash'),
        ])
        session = upload_session()
        results = fixture.run(session=session)

        assert results[0]['duplicates'] == 1
        assert len([r for r in results if r['action'] == 'uploaded']) == 1
        assert session.post.call_count == 1
        assert set(fixture.state()['files']) == {'same-hash'}

    def test_short_download_is_retryable_not_a_permanent_skip(self):
        fixture = FolderFixture(files=[
            ('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z'),
        ])
        fixture.client.files['/archive/ws/a.jpg'] = (b'truncated', 'rev-a.jpg')
        results = fixture.run(session=upload_session())

        assert [r for r in results if r['action'] == 'error']
        state = fixture.state()
        assert 'hash-a.jpg' in state['failed'] and 'hash-a.jpg' not in state['skipped']

    def test_quarantined_files_are_reported_every_run(self):
        state = dict(empty_state('ws-1'),
                     failed={'hash-a.jpg': {'path': '/archive/ws/a.jpg', 'error': 'boom',
                                            'attempts': MAX_FILE_ATTEMPTS}})
        fixture = FolderFixture(
            files=[('a.jpg', image_bytes(530, 1000), '2026-08-25T10:00:00Z', '2026-08-25T10:00:20Z')],
            state=state)
        results = fixture.run(session=upload_session())

        quarantined = [r for r in results if r['action'] == 'quarantined']
        assert len(quarantined) == 1 and quarantined[0]['attempts'] == MAX_FILE_ATTEMPTS
        assert results[0]['quarantined'] == 1

    def test_batch_history_is_persisted_per_chunk(self):
        """After the first flush, history must describe what was processed, not the whole run."""
        files = [(f'a{i}.jpg', image_bytes(530, 1000), f'2026-08-25T10:0{i}:00Z',
                  f'2026-08-25T10:0{i}:20Z') for i in range(4)]
        files += [('b0.jpg', image_bytes(530, 1000), '2026-08-25T11:00:00Z', '2026-08-25T11:00:20Z')]
        fixture = FolderFixture(files=files)

        calls = {'count': 0}
        real_upload = fixture.client.upload

        def upload(path, data, rev=None):
            # Fail the second state flush, i.e. after chunk one has been uploaded.
            if path.endswith('chronomaps.state.json'):
                calls['count'] += 1
                if calls['count'] > 1:
                    raise RuntimeError('interrupted')
            return real_upload(path, data, rev)

        fixture.client.upload = upload
        with pytest.raises(RuntimeError):
            fixture.run(session=upload_session())

        batches = fixture.state()['recent_batches']
        assert len(batches) == 1, 'only the batch that was actually processed'
        assert batches[0]['last_scanned_at'] == '2026-08-25T10:03:00Z'


class TestRunIngest:
    def test_bad_credentials_in_one_folder_do_not_stop_the_others(self):
        broken, healthy = folder_entry('/archive/broken'), folder_entry('/archive/ok')
        client = FakeDropbox({
            '/archive': [broken, healthy],
            '/archive/broken': [file_entry('/archive/broken/chronomaps.config')],
            '/archive/ok': [file_entry('/archive/ok/chronomaps.config')],
        }, {
            '/archive/broken/chronomaps.config': (b'workspace: ws\napi_key: k\nratio: 0,53\n', 'r'),
            '/archive/ok/chronomaps.config': (b'workspace: ws-ok\napi_key: k\n', 'r'),
        })
        results = list(run_ingest(settings=make_settings(), client=client, dry_run=True, now=NOW))
        assert [r for r in results if r['action'] == 'skip-folder' and 'bad credentials' in r['reason']]
        assert [r for r in results if r.get('workspace') == 'ws-ok']
        assert results[-1]['action'] == 'done'

    def test_run_stops_at_the_deadline(self):
        folders = [folder_entry(f'/archive/ws{i}') for i in range(3)]
        client = FakeDropbox({'/archive': folders}, {})
        settings = make_settings(run_deadline_seconds=-1)
        results = list(run_ingest(settings=settings, client=client, dry_run=True, now=NOW))
        deadline = [r for r in results if r['action'] == 'deadline']
        assert deadline and deadline[0]['deferred'] == 3

    def test_only_folder_filters_by_name(self):
        wanted, other = folder_entry('/archive/ws-a'), folder_entry('/archive/ws-b')
        client = FakeDropbox({'/archive': [wanted, other], '/archive/ws-a': []}, {})
        results = list(run_ingest(settings=make_settings(), client=client, dry_run=True,
                                  only_folder='ws-a', now=NOW))
        folders = {r.get('folder') for r in results if r.get('folder')}
        assert folders == {'ws-a'}

    def test_folder_error_does_not_stop_the_run(self):
        broken, healthy = folder_entry('/archive/broken'), folder_entry('/archive/ok')
        client = FakeDropbox({
            '/archive': [broken, healthy],
            '/archive/broken': [file_entry('/archive/broken/chronomaps.config')],
            '/archive/ok': [],
        }, {'/archive/broken/chronomaps.config': (b'workspace: ws\napi_key: k\n', 'r')})
        client.files['/archive/broken/chronomaps.state.json'] = (b'{corrupt', 'r')
        results = list(run_ingest(settings=make_settings(), client=client, now=NOW))
        assert [r for r in results if r['action'] == 'error']
        assert results[-1]['action'] == 'done'

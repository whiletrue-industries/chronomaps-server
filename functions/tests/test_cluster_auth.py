"""
Tests for who may trigger a clustering run over HTTP (cluster_screenshots/auth.py).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from cluster_screenshots.auth import ClusterRequestDenied, resolve_cluster_request

KEYS = {'ws-a': 'key-a', 'ws-b': 'key-b'}


def _db():
    def collection(name):
        doc = MagicMock()
        doc.exists = name in KEYS
        doc.to_dict.return_value = {'keys': {'admin': KEYS.get(name)}}
        ref = MagicMock()
        ref.document.return_value.get.return_value = doc
        return ref
    return SimpleNamespace(collection=collection)


def _req(authorization=None, **args):
    headers = {'Authorization': authorization} if authorization else {}
    return SimpleNamespace(headers=headers, args=args)


def _resolve(req, admin=False):
    return resolve_cluster_request(req, _db(), verify_admin=lambda r: {'email': 'a@b'} if admin else None)


def _denied(req, status, admin=False):
    with pytest.raises(ClusterRequestDenied) as e:
        _resolve(req, admin=admin)
    assert e.value.status == status


class TestWorkspaceMode:
    def test_admin_key_builds_the_scheduled_runs_config(self):
        assert _resolve(_req('key-a', workspace='ws-a')) == ('ws-a:key-a:3', 'ws-a')

    def test_firebase_admin_needs_no_key(self):
        assert _resolve(_req('Bearer token', workspace='ws-a'), admin=True) == ('ws-a:key-a:3', 'ws-a')

    def test_no_credentials(self):
        _denied(_req(workspace='ws-a'), 403)

    def test_wrong_key(self):
        _denied(_req('key-b', workspace='ws-a'), 403)

    def test_unknown_workspace_is_not_revealed_to_strangers(self):
        _denied(_req('key-a', workspace='nope'), 403)

    def test_unknown_workspace_for_admin(self):
        _denied(_req('Bearer token', workspace='nope'), 404, admin=True)

    def test_path_like_workspace_id(self):
        _denied(_req('key-a', workspace='ws-a/x'), 403)


class TestConfigMode:
    def test_every_entry_carries_its_admin_key(self):
        config = 'ws-a:key-a:5;ws-b:key-b:5'
        assert _resolve(_req(config=config, tag='combined')) == (config, 'combined')

    def test_one_bad_key_denies_the_lot(self):
        _denied(_req(config='ws-a:key-a:5;ws-b:wrong:5', tag='combined'), 403)

    def test_entry_without_a_key(self):
        _denied(_req(config='ws-a', tag='combined'), 403)

    def test_tag_may_not_be_someone_elses_workspace(self):
        _denied(_req(config='ws-a:key-a:5', tag='ws-b'), 403)

    def test_tag_may_be_one_of_the_configs_workspaces(self):
        assert _resolve(_req(config='ws-a:key-a:5', tag='ws-a')) == ('ws-a:key-a:5', 'ws-a')

    def test_firebase_admin_may_cluster_anything(self):
        assert _resolve(_req('Bearer token', config='ws-a:whatever:5', tag='ws-b'), admin=True) == ('ws-a:whatever:5', 'ws-b')

    def test_missing_everything(self):
        _denied(_req(), 400)

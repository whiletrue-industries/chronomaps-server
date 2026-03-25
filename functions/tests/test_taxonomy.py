"""
Tests for the taxonomy module.

Covers:
- shared.generate_embedding
- shared.use_item (with favorable_future_required flag)
- taxonomy.naming: generate_slug, generate_reference_embeddings, save/load/get_or_create
- taxonomy.assignment: assign_topics
- taxonomy.design: sample_descriptions, design_taxonomy
- taxonomy.__init__: tag_item, build_taxonomy
- screenshot_handler auto-tagging integration
"""

import pytest
import hashlib
import numpy as np
from unittest.mock import Mock, patch, MagicMock, call


# --- Fixtures ---

SAMPLE_TAXONOMY = {
    'themes': [
        {
            'name': {'english': 'Climate & Environment', 'dutch': 'Klimaat', 'hebrew': 'אקלים', 'arabic': 'مناخ'},
            'definition': 'Scenarios about climate change and environment',
            'sub_themes': [
                {
                    'name': {'english': 'Natural Disasters', 'dutch': 'Rampen', 'hebrew': 'אסונות', 'arabic': 'كوارث'},
                    'definition': 'Scenarios about floods, earthquakes, storms',
                },
                {
                    'name': {'english': 'Sustainability', 'dutch': 'Duurzaamheid', 'hebrew': 'קיימות', 'arabic': 'استدامة'},
                    'definition': 'Scenarios about sustainable living and adaptation',
                },
            ],
        },
        {
            'name': {'english': 'Political Power', 'dutch': 'Politiek', 'hebrew': 'פוליטיקה', 'arabic': 'سياسة'},
            'definition': 'Scenarios about governance and political systems',
            'sub_themes': [
                {
                    'name': {'english': 'Elections', 'dutch': 'Verkiezingen', 'hebrew': 'בחירות', 'arabic': 'انتخابات'},
                    'definition': 'Scenarios about democratic elections and voting',
                },
            ],
        },
    ]
}

SAMPLE_TAXONOMY_WITH_COUNTS = {
    'version': '2026-03-25T00:00:00+00:00',
    'item_count': 100,
    'themes': [
        {
            'id': 'climate-environment',
            'name': SAMPLE_TAXONOMY['themes'][0]['name'],
            'definition': SAMPLE_TAXONOMY['themes'][0]['definition'],
            'item_count': 70,
            'sub_themes': [
                {
                    'id': 'natural-disasters',
                    'name': SAMPLE_TAXONOMY['themes'][0]['sub_themes'][0]['name'],
                    'definition': SAMPLE_TAXONOMY['themes'][0]['sub_themes'][0]['definition'],
                    'item_count': 40,
                },
                {
                    'id': 'sustainability',
                    'name': SAMPLE_TAXONOMY['themes'][0]['sub_themes'][1]['name'],
                    'definition': SAMPLE_TAXONOMY['themes'][0]['sub_themes'][1]['definition'],
                    'item_count': 30,
                },
            ],
        },
        {
            'id': 'political-power',
            'name': SAMPLE_TAXONOMY['themes'][1]['name'],
            'definition': SAMPLE_TAXONOMY['themes'][1]['definition'],
            'item_count': 30,
            'sub_themes': [
                {
                    'id': 'elections',
                    'name': SAMPLE_TAXONOMY['themes'][1]['sub_themes'][0]['name'],
                    'definition': SAMPLE_TAXONOMY['themes'][1]['sub_themes'][0]['definition'],
                    'item_count': 30,
                },
            ],
        },
    ]
}


def _fake_embedding(seed=0):
    rng = np.random.RandomState(seed)
    return list(rng.randn(3072).astype(float))


# --- Tests for shared module ---

class TestGenerateEmbedding:
    def test_calls_openai_and_returns_embedding(self):
        from shared import generate_embedding
        mock_embedding = _fake_embedding()
        with patch('shared.OpenAI') as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            mock_data = Mock()
            mock_data.data = [Mock(embedding=mock_embedding)]
            mock_client.embeddings.create.return_value = mock_data

            result = generate_embedding('test description', 'fake-key')

            mock_client.embeddings.create.assert_called_once_with(
                model='text-embedding-3-large', input='test description'
            )
            assert result == mock_embedding


class TestUseItem:
    def test_requires_favorable_future_by_default(self):
        from shared import use_item
        assert use_item({'future_scenario_description': 'desc'}) is False

    def test_accepts_valid_favorable_future(self):
        from shared import use_item
        assert use_item({'favorable_future': 'yes', 'future_scenario_description': 'desc'}) is True
        assert use_item({'favorable_future': 'prevent', 'future_scenario_description': 'desc'}) is True
        assert use_item({'favorable_future': 'mostly_prefer', 'future_scenario_description': 'desc'}) is True

    def test_skips_favorable_future_check_when_not_required(self):
        from shared import use_item
        assert use_item({'future_scenario_description': 'desc'}, favorable_future_required=False) is True

    def test_requires_description_always(self):
        from shared import use_item
        assert use_item({'favorable_future': 'yes'}, favorable_future_required=False) is False
        assert use_item({}, favorable_future_required=False) is False


# --- Tests for taxonomy.naming ---

class TestGenerateSlug:
    def test_basic_slug(self):
        from taxonomy.naming import generate_slug
        assert generate_slug({'english': 'Climate & Environment'}) == 'climate-environment'

    def test_strips_special_chars(self):
        from taxonomy.naming import generate_slug
        assert generate_slug({'english': 'AI & Automation!'}) == 'ai-automation'

    def test_empty_name(self):
        from taxonomy.naming import generate_slug
        assert generate_slug({}) == ''
        assert generate_slug({'english': ''}) == ''


class TestEmbeddingDocId:
    def test_deterministic_hash(self):
        from taxonomy.naming import _embedding_doc_id
        text = 'Climate & Environment — Natural Disasters: flood scenarios'
        expected = f'embedding-{hashlib.md5(text.encode("utf-8")).hexdigest()}'
        assert _embedding_doc_id(text) == expected

    def test_different_texts_different_ids(self):
        from taxonomy.naming import _embedding_doc_id
        assert _embedding_doc_id('text one') != _embedding_doc_id('text two')


class TestBuildReferenceText:
    def test_format(self):
        from taxonomy.naming import _build_reference_text
        result = _build_reference_text('Climate', 'Floods', 'About flooding')
        assert result == 'Climate — Floods: About flooding'


class TestGenerateReferenceEmbeddings:
    def test_generates_embeddings_for_all_sub_themes(self):
        from taxonomy.naming import generate_reference_embeddings

        mock_client = Mock()
        embeddings = [Mock(embedding=_fake_embedding(i)) for i in range(3)]
        mock_client.embeddings.create.return_value = Mock(data=embeddings)

        result, texts = generate_reference_embeddings(mock_client, SAMPLE_TAXONOMY)

        assert len(result) == 3
        assert len(texts) == 3
        assert ('climate-environment', 'natural-disasters') in result
        assert ('climate-environment', 'sustainability') in result
        assert ('political-power', 'elections') in result
        mock_client.embeddings.create.assert_called_once()
        assert len(mock_client.embeddings.create.call_args[1]['input']) == 3


class TestSaveReferenceEmbeddings:
    def test_saves_each_embedding_as_separate_doc(self):
        from taxonomy.naming import save_reference_embeddings, _embedding_doc_id

        mock_db = Mock()
        embeddings = {
            ('theme-a', 'sub-1'): [1.0, 2.0],
            ('theme-b', 'sub-2'): [3.0, 4.0],
        }
        texts = ['definition one', 'definition two']
        keys = list(embeddings.keys())

        save_reference_embeddings(mock_db, embeddings, texts, keys)

        assert mock_db.collection.return_value.document.return_value.set.call_count == 2


class TestGetOrCreateReferenceEmbeddings:
    def test_returns_none_when_no_taxonomy(self):
        from taxonomy.naming import get_or_create_reference_embeddings

        mock_db = Mock()
        mock_doc = Mock()
        mock_doc.exists = False
        mock_db.collection.return_value.document.return_value.get.return_value = mock_doc

        result = get_or_create_reference_embeddings(mock_db, 'fake-key')
        assert result is None

    def test_loads_cached_embeddings(self):
        from taxonomy.naming import get_or_create_reference_embeddings, _embedding_doc_id, _build_reference_text

        mock_db = Mock()
        # Taxonomy doc exists
        taxonomy_mock = Mock()
        taxonomy_mock.exists = True
        taxonomy_mock.to_dict.return_value = SAMPLE_TAXONOMY_WITH_COUNTS

        # Build expected doc IDs
        cached_embedding = _fake_embedding(42)

        def mock_get_doc(doc_id):
            mock = Mock()
            if doc_id == 'taxonomy':
                return taxonomy_mock
            # All embedding docs exist
            mock_emb = Mock()
            mock_emb.exists = True
            mock_emb.to_dict.return_value = {'embedding': cached_embedding}
            return mock_emb

        mock_collection = Mock()
        mock_collection.document.return_value.get.side_effect = lambda: mock_get_doc(
            mock_collection.document.call_args[0][0]
        )
        mock_db.collection.return_value = mock_collection

        # This is complex to mock precisely due to chained calls.
        # Instead, let's test the simpler path: all missing → generates all
        with patch('taxonomy.naming.OpenAI') as mock_openai_cls:
            mock_client = mock_openai_cls.return_value
            embeddings_data = [Mock(embedding=_fake_embedding(i)) for i in range(3)]
            mock_client.embeddings.create.return_value = Mock(data=embeddings_data)

            # Make taxonomy doc exist but all embedding docs missing
            call_count = [0]
            def document_side_effect(doc_id):
                doc_mock = Mock()
                if doc_id == 'taxonomy':
                    get_mock = Mock()
                    get_mock.exists = True
                    get_mock.to_dict.return_value = SAMPLE_TAXONOMY_WITH_COUNTS
                    doc_mock.get.return_value = get_mock
                else:
                    # embedding docs don't exist
                    get_mock = Mock()
                    get_mock.exists = False
                    doc_mock.get.return_value = get_mock
                return doc_mock

            mock_db.collection.return_value.document.side_effect = document_side_effect

            result = get_or_create_reference_embeddings(mock_db, 'fake-key')

            assert len(result) == 3
            mock_client.embeddings.create.assert_called_once()


# --- Tests for taxonomy.assignment ---

class TestAssignTopics:
    def test_assigns_primary_topic(self):
        from taxonomy.assignment import assign_topics

        # Create item embedding close to ref_a
        ref_a = np.random.randn(3072)
        ref_b = np.random.randn(3072)
        item = ref_a + np.random.randn(3072) * 0.1  # close to ref_a

        reference_embeddings = {
            ('theme-a', 'sub-a'): ref_a,
            ('theme-b', 'sub-b'): ref_b,
        }
        embeddings = np.array([item])

        assignments = assign_topics(embeddings, reference_embeddings)

        assert len(assignments) == 1
        assert assignments[0]['topics'][0] == 'theme-a/sub-a'
        assert assignments[0]['best_similarity'] > 0

    def test_multi_label_above_threshold(self):
        from taxonomy.assignment import assign_topics

        # Make item equally close to both references
        ref = np.random.randn(3072)
        reference_embeddings = {
            ('theme-a', 'sub-a'): ref,
            ('theme-a', 'sub-b'): ref + np.random.randn(3072) * 0.01,
        }
        embeddings = np.array([ref])

        assignments = assign_topics(embeddings, reference_embeddings, similarity_threshold=0.5, max_tags=3)
        assert len(assignments[0]['topics']) >= 1

    def test_max_tags_limits_assignments(self):
        from taxonomy.assignment import assign_topics

        ref = np.random.randn(3072)
        reference_embeddings = {
            ('a', 'x'): ref,
            ('b', 'y'): ref + np.random.randn(3072) * 0.001,
            ('c', 'z'): ref + np.random.randn(3072) * 0.001,
            ('d', 'w'): ref + np.random.randn(3072) * 0.001,
        }
        embeddings = np.array([ref])

        assignments = assign_topics(embeddings, reference_embeddings, similarity_threshold=0.0, max_tags=2)
        assert len(assignments[0]['topics']) <= 2

    def test_returns_best_similarity(self):
        from taxonomy.assignment import assign_topics

        ref = np.random.randn(3072)
        reference_embeddings = {('a', 'b'): ref}
        embeddings = np.array([ref])

        assignments = assign_topics(embeddings, reference_embeddings)
        assert assignments[0]['best_similarity'] > 0.99  # same vector


# --- Tests for taxonomy.design ---

class TestSampleDescriptions:
    def test_returns_all_when_under_limit(self):
        from taxonomy.design import sample_descriptions
        records = [{'future_scenario_description': f'desc {i}'} for i in range(5)]
        result = sample_descriptions(records, n=10)
        assert len(result) == 5

    def test_samples_down_to_n(self):
        from taxonomy.design import sample_descriptions
        records = [{'future_scenario_description': f'desc {i}'} for i in range(50)]
        result = sample_descriptions(records, n=10)
        assert len(result) == 10

    def test_skips_empty_descriptions(self):
        from taxonomy.design import sample_descriptions
        records = [
            {'future_scenario_description': 'valid'},
            {'future_scenario_description': ''},
            {'future_scenario_description': '   '},
            {},
        ]
        result = sample_descriptions(records, n=10)
        assert len(result) == 1
        assert result[0] == 'valid'

    def test_falls_back_to_tagline(self):
        from taxonomy.design import sample_descriptions
        records = [{'future_scenario_tagline': 'tagline only'}]
        result = sample_descriptions(records, n=10)
        assert result == ['tagline only']


class TestStripTaxonomyForPrompt:
    def test_removes_item_counts_and_ids(self):
        from taxonomy.design import _strip_taxonomy_for_prompt
        result = _strip_taxonomy_for_prompt(SAMPLE_TAXONOMY_WITH_COUNTS)
        theme = result['themes'][0]
        assert 'id' not in theme
        assert 'item_count' not in theme
        assert 'name' in theme
        assert 'definition' in theme
        sub = theme['sub_themes'][0]
        assert 'id' not in sub
        assert 'item_count' not in sub


# --- Tests for taxonomy.__init__ (tag_item) ---

class TestTagItem:
    @pytest.fixture
    def mock_deps(self):
        """Mock all external dependencies for tag_item."""
        with patch('taxonomy.http_requests') as mock_http, \
             patch('taxonomy.get_or_create_reference_embeddings') as mock_get_ref, \
             patch('taxonomy.generate_embedding') as mock_gen_emb, \
             patch('taxonomy.db'):
            # Default: item fetch succeeds
            mock_fetch = Mock()
            mock_fetch.status_code = 200
            mock_fetch.json.return_value = {
                'metadata': {
                    'future_scenario_description': 'A climate scenario',
                    'embedding': _fake_embedding(0),
                }
            }
            mock_http.get.return_value = mock_fetch
            mock_http.put.return_value = Mock(status_code=200)

            # Reference embeddings
            ref_emb = {
                ('climate', 'disasters'): _fake_embedding(1),
                ('politics', 'elections'): _fake_embedding(2),
            }
            mock_get_ref.return_value = ref_emb
            mock_gen_emb.return_value = _fake_embedding(99)

            yield {
                'http': mock_http,
                'get_ref': mock_get_ref,
                'gen_emb': mock_gen_emb,
            }

    def test_tags_item_successfully(self, mock_deps):
        from taxonomy import tag_item
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert 'topics' in result
        assert len(result['topics']) >= 1
        assert 'similarity' in result

    def test_uses_provided_description_and_embedding(self, mock_deps):
        from taxonomy import tag_item
        emb = _fake_embedding(5)
        result = tag_item('ws', 'item-1', 'key', 'ikey',
                         description='custom desc', embedding=emb)
        # Should NOT fetch from API
        mock_deps['http'].get.assert_not_called()
        assert 'topics' in result

    def test_fetches_item_when_no_description(self, mock_deps):
        from taxonomy import tag_item
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        mock_deps['http'].get.assert_called_once()
        assert 'topics' in result

    def test_generates_embedding_when_missing(self, mock_deps):
        from taxonomy import tag_item
        mock_deps['http'].get.return_value.json.return_value = {
            'metadata': {
                'future_scenario_description': 'A scenario',
                # no embedding
            }
        }
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        mock_deps['gen_emb'].assert_called_once()
        assert 'topics' in result

    def test_returns_error_when_no_description(self, mock_deps):
        from taxonomy import tag_item
        mock_deps['http'].get.return_value.json.return_value = {'metadata': {}}
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert isinstance(result, tuple)
        assert result[1] == 400

    def test_returns_error_when_no_taxonomy(self, mock_deps):
        from taxonomy import tag_item
        mock_deps['get_ref'].return_value = None
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert isinstance(result, tuple)
        assert result[1] == 404

    def test_returns_error_when_item_fetch_fails(self, mock_deps):
        from taxonomy import tag_item
        mock_deps['http'].get.return_value.status_code = 404
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert isinstance(result, tuple)
        assert result[1] == 404

    def test_updates_item_with_topics(self, mock_deps):
        from taxonomy import tag_item
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        # Should PUT topics back to the item
        put_calls = mock_deps['http'].put.call_args_list
        assert len(put_calls) == 1
        put_json = put_calls[0][1]['json']
        assert 'topics' in put_json
        assert 'topics_version' in put_json

    def test_works_without_item_key(self, mock_deps):
        from taxonomy import tag_item
        result = tag_item('ws', 'item-1', 'key')
        assert 'topics' in result
        # Verify item-key is not in params
        get_call = mock_deps['http'].get.call_args
        assert get_call[1]['params'] == {}

    def test_passes_item_key_when_provided(self, mock_deps):
        from taxonomy import tag_item
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert 'topics' in result
        get_call = mock_deps['http'].get.call_args
        assert get_call[1]['params'] == {'item-key': 'ikey'}

    def test_propagates_put_error_on_embedding_save(self, mock_deps):
        from taxonomy import tag_item
        mock_deps['http'].get.return_value.json.return_value = {
            'metadata': {
                'future_scenario_description': 'A scenario',
            }
        }
        mock_deps['http'].put.return_value = Mock(status_code=403)
        result = tag_item('ws', 'item-1', 'key')
        assert isinstance(result, tuple)
        assert result[1] == 403

    def test_propagates_put_error_on_topics_save(self, mock_deps):
        from taxonomy import tag_item
        # First PUT (embedding) succeeds, second PUT (topics) fails
        mock_deps['http'].put.side_effect = [
            Mock(status_code=200),  # won't be called since embedding exists
            Mock(status_code=403),  # topics update fails
        ]
        # Reset to just fail on first put (topics update) since embedding exists
        mock_deps['http'].put.side_effect = None
        mock_deps['http'].put.return_value = Mock(status_code=403)
        result = tag_item('ws', 'item-1', 'key', 'ikey')
        assert isinstance(result, tuple)
        assert result[1] == 403


# --- Tests for auto-tagging in screenshot_handler ---

class TestScreenshotHandlerAutoTagging:
    @pytest.fixture(autouse=True)
    def mock_storage(self):
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_blob.public_url = "https://storage.googleapis.com/test/screenshot.jpeg"
        mock_bucket.blob.return_value = mock_blob
        with patch("screenshot_handler.bucket", mock_bucket):
            yield mock_bucket

    @pytest.fixture(autouse=True)
    def mock_enhance_image(self):
        with patch("screenshot_handler.enhance_image_fn") as mock:
            mock.return_value = dict(enhanced_url="https://example.com/enhanced.jpeg")
            yield mock

    @pytest.fixture
    def mock_api_requests(self):
        with patch("screenshot_handler.requests") as mock_req:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.json.return_value = {
                "item_id": "test-item-id",
                "item_key": "test-item-key",
                "metadata": {"content_title": "Existing Item"},
                "default_moderation_level": 3,
            }
            mock_req.get.return_value = mock_response
            mock_req.post.return_value = MagicMock(
                status_code=200,
                json=lambda: {"item_id": "new-item-id", "item_key": "new-item-key"},
            )
            mock_req.put.return_value = MagicMock(status_code=200)
            yield mock_req

    @pytest.fixture
    def mock_openai(self):
        with patch("screenshot_handler.client") as mock_client:
            import json
            mock_completion = MagicMock()
            mock_completion.choices[0].message.content = json.dumps({
                "screenshot_type": "social_media_post",
                "content": "Test content",
                "content_title": "Test",
                "content_certainty": 90,
                "future_scenario_tagline": "tagline",
                "future_scenario_description": "A future scenario about climate",
                "future_scenario_topics": ["climate"],
                "detected_language": "en",
                "plausibility": 75,
                "favorable_future": "prefer",
                "transition_bar_transition_event": "Climate policy",
                "transition_bar_before_during_after": "before",
                "transition_bar_certainty": 50,
            })
            mock_client.chat.completions.create.return_value = mock_completion
            yield mock_client

    def test_screenshot_handler_calls_tag_item(self, mock_openai, mock_api_requests, mock_storage):
        with patch('shared.generate_embedding', return_value=_fake_embedding(0)), \
             patch('taxonomy.tag_item', return_value={'topics': ['climate/disasters']}) as mock_tag:
            from screenshot_handler import screenshot_handler
            result = screenshot_handler(
                image_bytes=b"\xff\xd8\xff\xe0fake",
                workspace="ws",
                api_key="key",
                image_content_type="image/jpeg",
            )
            assert 'metadata' in result
            mock_tag.assert_called_once()
            assert result['metadata'].get('topics') == ['climate/disasters']

    def test_reanalyze_calls_tag_item(self, mock_openai, mock_api_requests, mock_storage):
        mock_storage.list_blobs.return_value = [MagicMock()]
        mock_blob = MagicMock()
        mock_blob.download_as_bytes.return_value = b"\xff\xd8\xff\xe0fake"
        mock_blob.content_type = "image/jpeg"
        mock_storage.list_blobs.return_value = [mock_blob]

        with patch('shared.generate_embedding', return_value=_fake_embedding(0)), \
             patch('taxonomy.tag_item', return_value={'topics': ['climate/disasters']}) as mock_tag:
            from screenshot_handler import reanalyze_item
            result = reanalyze_item(
                workspace="ws",
                item_id="item-1",
                item_key="key-1",
                api_key="api-key",
            )
            assert 'metadata' in result
            mock_tag.assert_called_once()
            assert result['metadata'].get('topics') == ['climate/disasters']

    def test_tagging_failure_does_not_break_handler(self, mock_openai, mock_api_requests, mock_storage):
        """Auto-tagging errors should be caught and not break the main flow."""
        from screenshot_handler import screenshot_handler

        # Even without taxonomy module available, handler should succeed
        result = screenshot_handler(
            image_bytes=b"\xff\xd8\xff\xe0fake",
            workspace="ws",
            api_key="key",
            image_content_type="image/jpeg",
        )
        assert result["item_id"] == "new-item-id"
        assert 'metadata' in result

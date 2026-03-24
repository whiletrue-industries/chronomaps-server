import datetime
import numpy as np
from openai import OpenAI

from firebase_admin import firestore

from config import OPENAI_KEY
from shared import load_all_workspaces_items
from taxonomy.design import sample_descriptions, design_taxonomy
from taxonomy.naming import generate_slug, generate_reference_embeddings
from taxonomy.assignment import assign_topics

db = firestore.client()

TAXONOMY_COLLECTION = 'chronomaps_global'
TAXONOMY_DOCUMENT = 'taxonomy'
BATCH_SIZE = 500


def build_taxonomy(similarity_threshold=0.35, max_tags=3, redesign=False):
    yield dict(msg='Starting cross-workspace taxonomy build...')

    # Step 1: Load all items with embeddings
    records = []
    for msg in load_all_workspaces_items(db, OPENAI_KEY, favorable_future_required=False):
        if 'records' in msg:
            records = msg.pop('records')
        yield msg

    if len(records) < 10:
        yield dict(msg=f'Not enough records ({len(records)}) for taxonomy. Need at least 10.')
        return

    client = OpenAI(api_key=OPENAI_KEY)

    # Step 2: Get or design taxonomy
    existing_taxonomy = _load_existing_taxonomy()
    if existing_taxonomy and not redesign:
        yield dict(msg='Using existing taxonomy (pass redesign=true to regenerate)')
        taxonomy = existing_taxonomy
    else:
        yield dict(msg=f'Loaded {len(records)} items. Sampling descriptions...')
        descriptions = sample_descriptions(records)
        yield dict(msg=f'Sampled {len(descriptions)} descriptions. Designing taxonomy...')

        previous = existing_taxonomy if redesign and existing_taxonomy else None
        if previous:
            yield dict(msg='Redesigning taxonomy based on previous version...')
        taxonomy = design_taxonomy(client, descriptions, previous_taxonomy=previous)

        themes = taxonomy.get('themes', [])
        n_sub = sum(len(t.get('sub_themes', [])) for t in themes)
        yield dict(msg=f'Taxonomy designed: {len(themes)} themes, {n_sub} sub-themes')

        for theme in themes:
            sub_names = [s['name']['english'] for s in theme.get('sub_themes', [])]
            yield dict(msg=f'  {theme["name"]["english"]}: {", ".join(sub_names)}')

    # Step 3: Generate reference embeddings (always, they're cheap)
    yield dict(msg='Generating reference embeddings...')
    reference_embeddings = generate_reference_embeddings(client, taxonomy)
    yield dict(msg=f'Generated {len(reference_embeddings)} reference embeddings')

    # Step 4: Assign topics to each item
    embeddings = np.array([record['embedding'] for record in records])
    yield dict(msg='Assigning topics to items...')
    assignments = assign_topics(
        embeddings, reference_embeddings,
        similarity_threshold=similarity_threshold, max_tags=max_tags
    )

    # Report assignment quality
    topic_lists = [a['topics'] for a in assignments]
    sims = [a['best_similarity'] for a in assignments]
    p10 = float(np.percentile(sims, 10))
    yield dict(msg=f'Assignment stats: n={len(sims)}, min={min(sims):.4f}, max={max(sims):.4f}, mean={np.mean(sims):.4f}, p10={p10:.4f}')
    indexed = sorted(enumerate(sims), key=lambda x: x[1])
    weak_items = [(records[idx], assignments[idx]) for idx, _ in indexed[:10]]
    yield dict(msg='10 weakest matches:')
    for record, assignment in weak_items:
        desc = (record.get('future_scenario_description') or '')[:80]
        yield dict(msg=f'  ({assignment["best_similarity"]:.3f}) [{assignment["topics"][0]}] {desc}...')

    # Step 5: Save topics to Firestore
    yield dict(msg='Saving topics to Firestore...')
    version = datetime.datetime.now(datetime.timezone.utc).isoformat()
    yield from _save_topics(records, topic_lists, version)

    # Step 6: Save taxonomy reference
    yield dict(msg='Saving taxonomy reference...')
    _save_taxonomy_reference(taxonomy, topic_lists, records, version)

    yield dict(msg='Taxonomy build complete.')


def _load_existing_taxonomy():
    doc = db.collection(TAXONOMY_COLLECTION).document(TAXONOMY_DOCUMENT).get()
    if doc.exists:
        return doc.to_dict()
    return None


def _save_topics(records, assignments, version):
    batch = db.batch()
    batch_count = 0
    total_saved = 0

    for record, topics in zip(records, assignments):
        workspace = record['_workspace']
        item_id = record['_id']
        ref = db.collection(workspace).document(item_id)
        batch.update(ref, {
            'metadata.topics': topics,
            'metadata.topics_version': version
        })
        batch_count += 1

        if batch_count >= BATCH_SIZE:
            batch.commit()
            total_saved += batch_count
            yield dict(msg=f'Saved {total_saved} items...')
            batch = db.batch()
            batch_count = 0

    if batch_count > 0:
        batch.commit()
        total_saved += batch_count
    yield dict(msg=f'Saved all {total_saved} items.')


def _save_taxonomy_reference(taxonomy, assignments, records, version):
    theme_item_counts = {}
    sub_theme_item_counts = {}
    for topics in assignments:
        for topic in topics:
            parts = topic.split('/', 1)
            if len(parts) == 2:
                theme_slug, sub_slug = parts
                theme_item_counts[theme_slug] = theme_item_counts.get(theme_slug, 0) + 1
                sub_theme_item_counts[topic] = sub_theme_item_counts.get(topic, 0) + 1

    themes = []
    for theme in taxonomy.get('themes', []):
        theme_slug = generate_slug(theme['name'])
        sub_themes = []
        for sub_theme in theme.get('sub_themes', []):
            sub_slug = generate_slug(sub_theme['name'])
            full_slug = f'{theme_slug}/{sub_slug}'
            sub_themes.append(dict(
                id=sub_slug,
                name=sub_theme['name'],
                definition=sub_theme.get('definition', ''),
                item_count=sub_theme_item_counts.get(full_slug, 0)
            ))
        themes.append(dict(
            id=theme_slug,
            name=theme['name'],
            definition=theme.get('definition', ''),
            item_count=theme_item_counts.get(theme_slug, 0),
            sub_themes=sub_themes
        ))

    taxonomy_doc = dict(
        version=version,
        item_count=len(records),
        themes=themes
    )

    db.collection(TAXONOMY_COLLECTION).document(TAXONOMY_DOCUMENT).set(taxonomy_doc)

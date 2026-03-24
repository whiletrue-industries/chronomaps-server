import datetime
import numpy as np
from openai import OpenAI

from firebase_admin import firestore

from config import OPENAI_KEY
from shared import load_all_workspaces_items
from taxonomy.clustering import build_hierarchy
from taxonomy.naming import name_theme, name_sub_theme, generate_slug
from taxonomy.assignment import assign_topics

db = firestore.client()

BATCH_SIZE = 500


def build_taxonomy(similarity_threshold=0.35, max_tags=3):
    yield dict(msg='Starting cross-workspace taxonomy build...')

    # Step 1: Load all items with embeddings
    records = []
    for msg in load_all_workspaces_items(db, OPENAI_KEY):
        if 'records' in msg:
            records = msg['records']
        yield msg

    if len(records) < 10:
        yield dict(msg=f'Not enough records ({len(records)}) for taxonomy. Need at least 10.')
        return

    embeddings = np.array([record['embedding'] for record in records])
    yield dict(msg=f'Clustering {len(records)} items into hierarchical taxonomy...')

    # Step 2: Build two-level hierarchy
    hierarchy = build_hierarchy(embeddings)
    theme_labels = hierarchy['theme_labels']
    sub_theme_centroids = hierarchy['sub_theme_centroids']
    theme_centroids = hierarchy['theme_centroids']

    n_themes = len(theme_centroids)
    n_sub_themes = len(sub_theme_centroids)
    yield dict(msg=f'Found {n_themes} themes, {n_sub_themes} sub-themes')

    # Step 3: Name clusters via LLM
    yield dict(msg='Naming taxonomy nodes...')
    client = OpenAI(api_key=OPENAI_KEY)

    theme_names = {}
    existing_theme_names = []
    for theme_id in sorted(theme_centroids.keys()):
        mask = theme_labels == theme_id
        theme_descriptions = [
            records[i].get('future_scenario_description', records[i].get('future_scenario_tagline', ''))
            for i in range(len(records)) if mask[i]
        ]
        theme_name = name_theme(client, theme_descriptions, existing_theme_names)
        theme_names[theme_id] = theme_name
        existing_theme_names.append(theme_name['english'])
        yield dict(msg=f'Theme {theme_id}: {theme_name["english"]} ({sum(mask)} items)')

    sub_theme_names = {}
    for (theme_id, sub_theme_id) in sorted(sub_theme_centroids.keys()):
        mask_theme = theme_labels == theme_id
        mask_sub = np.array([
            hierarchy['sub_theme_labels'][i] == sub_theme_id
            for i in range(len(records))
        ])
        mask = mask_theme & mask_sub
        sub_descriptions = [
            records[i].get('future_scenario_description', records[i].get('future_scenario_tagline', ''))
            for i in range(len(records)) if mask[i]
        ]
        existing_sub_names = [
            sub_theme_names[k]['english']
            for k in sub_theme_names if k[0] == theme_id
        ]
        sub_name = name_sub_theme(client, sub_descriptions, theme_names[theme_id], existing_sub_names)
        sub_theme_names[(theme_id, sub_theme_id)] = sub_name
        yield dict(msg=f'  Sub-theme {theme_id}/{sub_theme_id}: {sub_name["english"]} ({sum(mask)} items)')

    # Step 4: Assign topics to each item
    yield dict(msg='Assigning topics to items...')
    assignments = assign_topics(
        embeddings, sub_theme_centroids, theme_names, sub_theme_names,
        similarity_threshold=similarity_threshold, max_tags=max_tags
    )

    # Step 5: Save topics to Firestore
    yield dict(msg='Saving topics to Firestore...')
    version = datetime.datetime.now(datetime.timezone.utc).isoformat()
    yield from _save_topics(records, assignments, version)

    # Step 6: Save taxonomy reference
    yield dict(msg='Saving taxonomy reference...')
    _save_taxonomy_reference(theme_names, sub_theme_names, hierarchy, assignments, records, version)

    yield dict(msg='Taxonomy build complete.')


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


def _save_taxonomy_reference(theme_names, sub_theme_names, hierarchy, assignments, records, version):
    # Count items per theme/sub-theme
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
    for theme_id in sorted(hierarchy['theme_centroids'].keys()):
        theme_name = theme_names[theme_id]
        theme_slug = generate_slug(theme_name)
        sub_themes = []
        for (tid, sid) in sorted(sub_theme_names.keys()):
            if tid != theme_id:
                continue
            sub_name = sub_theme_names[(tid, sid)]
            sub_slug = generate_slug(sub_name)
            full_slug = f'{theme_slug}/{sub_slug}'
            sub_themes.append(dict(
                id=sub_slug,
                name=sub_name,
                item_count=sub_theme_item_counts.get(full_slug, 0)
            ))
        themes.append(dict(
            id=theme_slug,
            name=theme_name,
            item_count=theme_item_counts.get(theme_slug, 0),
            sub_themes=sub_themes
        ))

    taxonomy_doc = dict(
        version=version,
        item_count=len(records),
        themes=themes
    )

    db.collection('__global__').document('.taxonomy').set(taxonomy_doc)

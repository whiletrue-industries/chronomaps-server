import hashlib
import json
import re

from openai import OpenAI

from shared import EMBEDDING_MODEL

TAXONOMY_COLLECTION = 'chronomaps_global'
TAXONOMY_DOCUMENT = 'taxonomy'


def generate_slug(name):
    english = name.get('english', '')
    slug = english.lower().strip()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug


def _call_llm(client, prompt):
    completion = client.chat.completions.create(
        model="gpt-4.1",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.5,
        response_format={"type": "json_object"}
    )
    return json.loads(completion.choices[0].message.content)


def _embedding_doc_id(definition_text):
    h = hashlib.md5(definition_text.encode('utf-8')).hexdigest()
    return f'embedding-{h}'


def _build_reference_text(theme_name, sub_name, definition):
    return f"{theme_name} — {sub_name}: {definition}"


def generate_reference_embeddings(client, taxonomy):
    texts = []
    keys = []
    for theme in taxonomy['themes']:
        theme_name = theme['name']['english']
        theme_slug = generate_slug(theme['name'])
        for sub_theme in theme.get('sub_themes', []):
            sub_name = sub_theme['name']['english']
            sub_slug = generate_slug(sub_theme['name'])
            reference_text = _build_reference_text(theme_name, sub_name, sub_theme['definition'])
            texts.append(reference_text)
            keys.append((theme_slug, sub_slug))

    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=texts
    )
    embeddings = {
        key: data.embedding
        for key, data in zip(keys, response.data)
    }
    return embeddings, texts


def save_reference_embeddings(db, reference_embeddings, reference_texts, keys):
    for (theme_slug, sub_slug), embedding, text in zip(keys, reference_embeddings.values(), reference_texts):
        doc_id = _embedding_doc_id(text)
        db.collection(TAXONOMY_COLLECTION).document(doc_id).set({
            'embedding': embedding if isinstance(embedding, list) else list(embedding),
            'theme_slug': theme_slug,
            'sub_theme_slug': sub_slug,
            'definition': text,
        })


def get_or_create_reference_embeddings(db, openai_key):
    taxonomy_doc = db.collection(TAXONOMY_COLLECTION).document(TAXONOMY_DOCUMENT).get()
    if not taxonomy_doc.exists:
        return None
    taxonomy = taxonomy_doc.to_dict()

    # Build expected references from taxonomy
    expected = []
    for theme in taxonomy.get('themes', []):
        theme_name = theme['name']['english']
        theme_slug = generate_slug(theme['name'])
        for sub_theme in theme.get('sub_themes', []):
            sub_name = sub_theme['name']['english']
            sub_slug = generate_slug(sub_theme['name'])
            ref_text = _build_reference_text(theme_name, sub_name, sub_theme.get('definition', ''))
            doc_id = _embedding_doc_id(ref_text)
            expected.append((doc_id, theme_slug, sub_slug, ref_text))

    # Fetch cached embeddings
    embeddings = {}
    missing = []
    for doc_id, theme_slug, sub_slug, ref_text in expected:
        doc = db.collection(TAXONOMY_COLLECTION).document(doc_id).get()
        if doc.exists:
            embeddings[(theme_slug, sub_slug)] = doc.to_dict()['embedding']
        else:
            missing.append((doc_id, theme_slug, sub_slug, ref_text))

    # Generate and save missing embeddings
    if missing:
        client = OpenAI(api_key=openai_key)
        texts = [m[3] for m in missing]
        response = client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
        for (doc_id, theme_slug, sub_slug, ref_text), data in zip(missing, response.data):
            embedding = data.embedding
            embeddings[(theme_slug, sub_slug)] = embedding
            db.collection(TAXONOMY_COLLECTION).document(doc_id).set({
                'embedding': embedding,
                'theme_slug': theme_slug,
                'sub_theme_slug': sub_slug,
                'definition': ref_text,
            })

    return embeddings

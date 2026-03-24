import json
import re

from openai import OpenAI

from shared import EMBEDDING_MODEL


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


def generate_reference_embeddings(client, taxonomy):
    texts = []
    keys = []
    for theme in taxonomy['themes']:
        theme_name = theme['name']['english']
        theme_slug = generate_slug(theme['name'])
        for sub_theme in theme.get('sub_themes', []):
            sub_name = sub_theme['name']['english']
            sub_slug = generate_slug(sub_theme['name'])
            # Combine theme + sub-theme + definition for a rich reference vector
            reference_text = f"{theme_name} — {sub_name}: {sub_theme['definition']}"
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
    return embeddings

import json
import re
from pathlib import Path

from openai import OpenAI

THEME_PROMPT = Path(__file__).with_name('THEME_NAMING_PROMPT.md').read_text().strip()
SUB_THEME_PROMPT = Path(__file__).with_name('SUB_THEME_NAMING_PROMPT.md').read_text().strip()

MAX_TAGLINES_PER_CLUSTER = 40


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


def name_theme(client, descriptions, existing_names=None):
    taglines = descriptions[:MAX_TAGLINES_PER_CLUSTER]
    taglines_text = '\n- '.join(taglines)
    taglines_text = f'- {taglines_text}'

    previous = ''
    if existing_names:
        prev_text = '\n- '.join(existing_names)
        previous = (
            'To avoid repeating themes which were already used, NEVER repeat any item from the following list '
            '(either exactly or similarly).\nBe more exact, specific and creative to make sure your response is '
            'unique and clearly distinguishable from the following:\n- ' + prev_text
        )

    prompt = THEME_PROMPT.replace(':TAGLINES:', taglines_text).replace(':PREVIOUS:', previous)
    return _call_llm(client, prompt)


def name_sub_theme(client, descriptions, parent_theme, existing_names=None):
    taglines = descriptions[:MAX_TAGLINES_PER_CLUSTER]
    taglines_text = '\n- '.join(taglines)
    taglines_text = f'- {taglines_text}'

    previous = ''
    if existing_names:
        prev_text = '\n- '.join(existing_names)
        previous = (
            'NEVER repeat any item from the following list (either exactly or similarly).\n'
            'Be more specific to make sure your response is unique and clearly distinguishable from:\n- ' + prev_text
        )

    parent_text = parent_theme.get('english', '')
    prompt = (SUB_THEME_PROMPT
              .replace(':TAGLINES:', taglines_text)
              .replace(':PREVIOUS:', previous)
              .replace(':PARENT_THEME:', parent_text))
    return _call_llm(client, prompt)

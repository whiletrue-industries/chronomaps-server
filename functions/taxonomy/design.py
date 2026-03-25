import json
import random
from pathlib import Path

from taxonomy.naming import _call_llm

DESIGN_PROMPT = Path(__file__).with_name('DESIGN_TAXONOMY_PROMPT.md').read_text().strip()

MAX_SAMPLE_SIZE = 200

PREVIOUS_TAXONOMY_INSTRUCTIONS = """## Previous Taxonomy

A taxonomy already exists. Your task is to **refine and update** it based on the new sample of descriptions, NOT to start from scratch.

Rules for refinement:
- **Keep existing theme and sub-theme names exactly as-is** unless there is a strong reason to rename (e.g., the name is misleading).
- You may **add** new themes or sub-themes if the sample reveals topics not covered.
- You may **remove** themes or sub-themes that are clearly redundant or have no matching scenarios.
- You may **merge** overlapping themes/sub-themes.
- You may **split** a theme if it has grown too broad.
- Preserve the IDs (slugified names) as much as possible for consistency.

The existing taxonomy is:
```json
:TAXONOMY_JSON:
```
"""


def sample_descriptions(records, n=MAX_SAMPLE_SIZE):
    descriptions = []
    for record in records:
        desc = record.get('future_scenario_description') or record.get('future_scenario_tagline') or ''
        if desc.strip():
            descriptions.append(desc.strip())
    if len(descriptions) <= n:
        return descriptions
    return random.sample(descriptions, n)


def design_taxonomy(client, descriptions, previous_taxonomy=None):
    numbered = [f'{i+1}. {desc}' for i, desc in enumerate(descriptions)]
    descriptions_text = '\n'.join(numbered)

    if previous_taxonomy:
        # Strip embeddings and item counts from previous taxonomy before sending to LLM
        clean_taxonomy = _strip_taxonomy_for_prompt(previous_taxonomy)
        taxonomy_json = json.dumps(clean_taxonomy, indent=2, ensure_ascii=False)
        previous_section = PREVIOUS_TAXONOMY_INSTRUCTIONS.replace(':TAXONOMY_JSON:', taxonomy_json)
    else:
        previous_section = ''

    prompt = (DESIGN_PROMPT
              .replace(':DESCRIPTIONS:', descriptions_text)
              .replace(':PREVIOUS_TAXONOMY:', previous_section))
    return _call_llm(client, prompt)


def _strip_taxonomy_for_prompt(taxonomy):
    themes = []
    for theme in taxonomy.get('themes', []):
        sub_themes = []
        for sub in theme.get('sub_themes', []):
            sub_themes.append({
                'name': sub['name'],
                'definition': sub.get('definition', '')
            })
        themes.append({
            'name': theme['name'],
            'definition': theme.get('definition', ''),
            'sub_themes': sub_themes
        })
    return {'themes': themes}

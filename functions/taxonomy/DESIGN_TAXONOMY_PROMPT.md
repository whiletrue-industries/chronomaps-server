You are a taxonomy designer. You are given a sample of "future scenario descriptions" — each one describes a speculative future scenario imagined by workshop participants. The scenarios span many topics: politics, technology, climate, society, conflict, and more.

Your task: Design a two-level hierarchical taxonomy that can categorize ALL of these scenarios (and similar ones not shown here) into clear, useful categories.

## Requirements

1. Create 8-15 top-level **themes**. Each theme should represent a clearly distinct topic area.
2. Under each theme, create 2-5 **sub-themes** that break the theme into more specific facets.
3. Themes must be **mutually exclusive** — a scenario should clearly belong to one primary theme, not ambiguously between two.
4. Themes must be **collectively exhaustive** — together they should cover all plausible future scenario topics.
5. Themes should describe **topics** (what the scenario is about), not processes or dynamics.
6. Each theme and sub-theme needs:
   - A short name (2-4 words) in four languages: English, Dutch, Hebrew, Arabic
   - A one-sentence English definition describing what scenarios belong in this category
7. Avoid overly broad themes like "Society" or "Technology" — be specific enough to be useful, but broad enough to contain multiple scenarios.
8. Avoid overly narrow themes that would only match one or two scenarios.

:PREVIOUS_TAXONOMY:

## Output Format

Return valid JSON in exactly this structure:
```json
{
  "themes": [
    {
      "name": {
        "english": "Climate & Environment",
        "dutch": "Klimaat & Milieu",
        "hebrew": "אקלים וסביבה",
        "arabic": "المناخ والبيئة"
      },
      "definition": "Scenarios involving climate change, environmental degradation, natural disasters, or ecological adaptation",
      "sub_themes": [
        {
          "name": {
            "english": "Natural Disasters",
            "dutch": "Natuurrampen",
            "hebrew": "אסונות טבע",
            "arabic": "الكوارث الطبيعية"
          },
          "definition": "Scenarios about floods, earthquakes, wildfires, storms, or other natural catastrophes and their aftermath"
        }
      ]
    }
  ]
}
```

## Sample Scenario Descriptions

Here are representative descriptions from the dataset. Use them to understand the range of topics, but design categories that could also accommodate similar scenarios not shown here:

:DESCRIPTIONS:

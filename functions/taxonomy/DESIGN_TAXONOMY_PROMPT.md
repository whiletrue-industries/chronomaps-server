You are a taxonomy designer. You are given a sample of "future scenario descriptions" — each one describes a speculative future scenario imagined by workshop participants. The workshops are situated in the context of the Israeli-Palestinian region: most participants are Israelis, Palestinians, and people closely connected to the region or to Jewish life elsewhere, and their scenarios most often engage with the political, social, religious, and human realities of that context. Other scenarios touch on broader concerns (climate, technology, work, family life) that may not be region-specific.

Your task: Design a two-level hierarchical taxonomy that captures the actual substance of these scenarios — what people are imagining, fearing, hoping for, or grappling with — and that lets a future scenario be placed in a clearly relevant category.

## Editorial direction

The taxonomy must feel topical and grounded in the content, not bland or template-driven. Concretely:

- **Prioritize regional and identity themes.** The dominant subject matter is expected to be the Israeli-Palestinian conflict and its aftermath, Israeli politics and society, Palestinian politics and society, Jewish identity (religious, secular, diasporic), and the relationships between these. Themes and sub-themes in these areas should be the most specific and the most numerous, *to the extent that the sample supports them*. Look at the sample to decide how much weight each deserves — do not pad with regional themes that have no matching scenarios, and do not flatten regional richness into one generic "Politics" bucket.
- **Be specific where the content is specific.** Prefer sub-themes like "Checkpoints and freedom of movement", "Settler violence and rule of law", "Religion and the state", "Military service and conscientious objection", "Diaspora–Israel relations", "Hebrew/Arabic language politics", "Right of return and demography", "Two-state / one-state / confederation futures", "Memory, trauma, and reconciliation" — over vague labels like "Politics", "Religion", or "Society". The exact set should come from what you actually see in the sample.
- **Keep generic themes as a fallback, not the centerpiece.** Climate, technology/AI, work and economy, health, family, etc. belong in the taxonomy when scenarios cover them, but only as many as the content warrants. Do not default to a "balanced" mix that dilutes the regional focus.
- **Categorize by what the scenario is *about*, not by the medium it uses.** A scenario in which someone asks an AI assistant about getting through a checkpoint is about the checkpoint, not about AI. Only treat the medium as the topic when the technology itself is the substantive concern (e.g., surveillance, deepfakes destroying trust, AI replacing rabbinic authority).
- **Avoid euphemism and avoid taking a political side in the labels.** Use names that participants from across the political spectrum would recognize as describing the same phenomenon. Where multiple framings exist, prefer the most neutral descriptive one.

## Structural requirements

1. Create 8-15 top-level **themes**. Each theme should represent a clearly distinct topic area.
2. Under each theme, create 2-6 **sub-themes** that break the theme into more specific facets.
3. Themes must be **mutually exclusive** — a scenario should clearly belong to one primary theme, not ambiguously between two.
4. Themes must be **collectively exhaustive** — together they should cover all plausible scenarios in the sample and similar ones.
5. Themes should describe **topics** (what the scenario is about), not processes or dynamics.
6. Each theme and sub-theme needs:
   - A short name (2-5 words) in four languages: English, Dutch, Hebrew, Arabic
   - A one-sentence English definition describing what scenarios belong in this category. The definition should be concrete enough that an embedding generated from it lands close to scenarios that actually belong there.
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
        "english": "Conflict, Peace & Political Futures",
        "dutch": "Conflict, Vrede & Politieke Toekomsten",
        "hebrew": "סכסוך, שלום ועתידים פוליטיים",
        "arabic": "الصراع والسلام والمستقبل السياسي"
      },
      "definition": "Scenarios imagining how the Israeli-Palestinian conflict evolves — escalation, settlement, partition, confederation, prolonged stalemate, or transformation of the political relationship between the two peoples",
      "sub_themes": [
        {
          "name": {
            "english": "Two-state / One-state / Confederation",
            "dutch": "Twee staten / Eén staat / Confederatie",
            "hebrew": "שתי מדינות / מדינה אחת / קונפדרציה",
            "arabic": "دولتان / دولة واحدة / كونفدرالية"
          },
          "definition": "Scenarios that imagine a specific constitutional arrangement between Israelis and Palestinians, including formal peace agreements, annexation, partition, or shared sovereignty"
        }
      ]
    }
  ]
}
```

The example above shows the *style* of regional specificity expected; do not copy its themes verbatim. Derive the actual themes and sub-themes from the sample below.

## Sample Scenario Descriptions

Here are representative descriptions from the dataset. Use them to understand the range of topics, the relative weight of regional vs. generic concerns, and the specific framings participants are using. Design categories that could also accommodate similar scenarios not shown here:

:DESCRIPTIONS:

# chronomaps-server

Firebase Cloud Functions (Python 3.12) backing Chronomaps: a REST API over Firestore
workspaces, AI analysis of scanned/screenshotted "future scenarios", Dropbox ingest, and
t-SNE maps rendered to map tiles in GCS.

Reference docs live in `docs/` — [API.md](docs/API.md) (full REST reference) and
[DROPBOX_SETUP.md](docs/DROPBOX_SETUP.md) (connecting a Dropbox folder to a workspace).
This file is only for things that are easy to get wrong.

## Read this before deploying anything

**`functions/functions.yaml` is the deployment manifest, and it wins.** firebase-tools reads
it *instead of* discovering the decorators in `main.py`:

- A new `@https_fn` / `@scheduler_fn` function is **not deployed** until it is listed there.
  The deploy succeeds and silently omits it.
- Its values **override the decorator** — memory, region, timeout, and `schedule`.

Changing `schedule=` in a decorator therefore does nothing on its own. This has bitten us:
a change to the Dropbox poll interval shipped, passed review, and had no effect, because the
manifest still carried the old value. If you change a function's shape, change both.

`functions/.env.chronomaps3` is different — non-secret config there **is** applied to every
function independently of the manifest. Credentials go to Secret Manager and are named under
`secretEnvironmentVariables` (and in `params`) in the manifest.

Deploys run from CI on push to `main` (`.github/workflows/deploy.yml`, `firebase deploy
--only functions`). A push deploys *everything*, so an unrelated merge can ship work in
progress.

## Tests

```bash
cd functions && pytest tests/          # 298 tests, ~20s, no cloud access needed
```

Config in `functions/pytest.ini`; CI installs `functions/requirements-test.txt`. Coverage is
measured against `chronomaps_api` only, so most modules read 0% — that is expected, not a
gap in the run.

## Clustering / t-SNE (`functions/cluster_screenshots/`)

Two entry points, easy to confuse — they have different signatures and one wrapped the other
by mistake for months:

- `cluster_screenshots(config, tag=…)` — one tag, `config` a `ws:key:moderation` string.
  Behind the public `cluster_screenshots` HTTP endpoint.
- `cluster_screenshots_all(config_tag_tuples, …)` — every workspace with a `.config` doc.
  Behind the scheduled `cluster_its_time`.

Output lands in `gs://chronomaps3-eu/tiles/`:

```
tiles/<tag>/config.json          {set_id, state_hash, update_time}   ← which set is current
tiles/<tag>/<set_id>/config.json {dim, grid[], clusters[], …}        ← the map itself
tiles/<tag>/<set_id>/<z>/<x>/<y>.png
```

`set_id` cycles 0–15, so each run writes a new set and the old one stays servable. Both
JSONs are uploaded then `make_public()`d — objects are individually public, the bucket is
not, so a **missing** object returns 404 and a **non-public** one returns 403. That
distinction is the fastest way to tell "never generated" from "ACL problem".

An item only reaches a map if `shared.use_item` accepts it, and a workspace with fewer than
10 accepted records writes nothing at all — its map 404s forever. `use_item` needs a
description, a `created_at`, and a favorability; `shared.resolve_favorable_future` takes the
human answer (`favorable_future`) first and falls back to the model's
(`ai_favorable_future`), so check both before concluding an item is unusable.

`if_changed=True` skips a workspace whose record set is unchanged, but the hash is over
record **ids** only — editing an existing item does not trigger a rebuild.

### Known constraints

Worth knowing before diagnosing a stalled map:

- **Memory.** The canvas is sized from the fixed 23×20 grid, not the record count —
  ~1.2 GB per workspace whether it has 34 records or 345, plus a PIL copy. Two workspaces in
  one invocation can exceed the 8 GB limit; the run dies with a 503, not a timeout.
  `origin/adaptive-tsne-grid` sizes the grid to the records and is the real fix.
- **Time.** 1800s request timeout. A run that hits it returns 504 with work half done.
- **Ordering.** `cluster_screenshots_all` walks `db.collections()` in the same lexicographic
  order every run and always restarts from the beginning, so when runs die early the
  workspaces late in the alphabet are never reached. There is no cursor.
- **No lock**, unlike the Dropbox ingest — overlapping runs both walk the same workspaces.

To re-cluster one workspace without waiting for the batch, call the `cluster_screenshots`
endpoint directly (see `run-jma25-tsne.sh`); it gets a whole invocation to itself.

## Dropbox ingest (`functions/dropbox_ingest/`)

A folder opts in with a `chronomaps.config` file (`workspace:` + `api_key:`) — use the
**collaborate** key, never admin: the archive is usually a shared folder and anyone who can
open it can read that file. `parse_credentials` is the parser to match when generating one.

Runs hold a Firestore lease (`dropbox_ingest/lock.py`), so overlapping invocations are safe
— a second one logs `action: skipped` and exits.

## Conventions

- Errors that abort a per-item or per-workspace loop should be contained so one bad record
  cannot stop the batch — that failure mode has cost us weeks of un-regenerated maps.
- Work submitted to a `ThreadPoolExecutor` must have its futures checked. A swallowed
  `make_public()` failure leaves an object that quietly 403s for every reader.
- Prefer narrow `PUT`s to the items API over re-analysing and writing a whole record —
  `analyze_image` regenerates `created_at`, which reorders every map that sorts by it.

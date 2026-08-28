# Setting up the Dropbox auto-ingest

How to connect a Dropbox folder of scanned pages to Chronomaps, from nothing. Follow it once per
Dropbox account; adding further workshop folders afterwards is only step 7.

What the ingest does, in one line: every few minutes it looks at the subfolders of one Dropbox
folder, and for each one that holds a credentials file, uploads new scans to that workspace — cropped
to the page ratio, deduplicated, with one shared `author_id` per scan batch. The reference for its
behaviour is [API.md](API.md#dropbox-auto-ingest); this document is only the setup.

You will need: admin access to the Dropbox account that receives the scans, and permission to write
secrets in the `chronomaps3` Firebase project.

---

## 1. Create the Dropbox app

At https://www.dropbox.com/developers/apps → **Create app**:

| Field | Value | Why |
|---|---|---|
| API | **Scoped access** | The only option that supports refresh tokens |
| Access | **Full Dropbox** | An *App folder* app can only see its own folder, never an archive that already exists |
| Name | e.g. `chronomaps-ingest` | Must be globally unique |

## 2. Grant permissions — before minting any token

*Permissions* tab, tick exactly these, then **Submit**:

```
account_info.read      files.metadata.read      files.content.read      files.content.write
```

`content.write` is needed only to write `chronomaps.state.json` (the record of what has been
uploaded) back into each folder. `account_info.read` lets the setup check detect a team space.

A token minted before this tab is submitted silently lacks the scopes, and the failure surfaces much
later as a 401 in the middle of an ingest. `scripts/dropbox_authorize.py` warns if it happens.

## 3. Settings tab

- **Access token expiration: Short-lived.** This is what makes the OAuth flow return a refresh token.
- **Redirect URIs: leave empty.** The setup script uses the copy-the-code flow; adding a redirect URI
  means both the authorize call and the token exchange have to send it.
- **Allow public clients (Implicit Grant & PKCE): Disallow.** The ingest authenticates with the app
  secret.
- Ignore the *Generated access token* button — that is a manual short-lived token, not what we need.
- **Webhooks: none.** The function polls; a webhook would need a public unauthenticated endpoint that
  answers Dropbox's `?challenge=` handshake.
- The app can stay in **Development** status: it is linked to a single account, well under the 50-user
  limit, so no App Review is needed.

Copy the **App key** and **App secret** into files, e.g. `../dropbox-app-key.txt` and
`../dropbox-app-secret.txt` (outside the repo).

## 4. Mint the refresh token

```bash
# print the URL to approve
python scripts/dropbox_authorize.py \
    --key-file ../dropbox-app-key.txt --secret-file ../dropbox-app-secret.txt --url-only

# exchange the code Dropbox shows you
python scripts/dropbox_authorize.py \
    --key-file ../dropbox-app-key.txt --secret-file ../dropbox-app-secret.txt \
    --code <CODE> --output ../dropbox-refresh-token.txt
```

Approve as the account that can actually see the scan folder. The code is single-use and expires in
minutes; get a fresh one if it goes stale. Prefer the `--*-file` and `--output` options over passing
and printing values: arguments are visible in the process list, and the refresh token is a long-lived
credential that should not sit in terminal scrollback.

## 5. Store the credentials

```bash
firebase functions:secrets:set DROPBOX_APP_KEY       --project chronomaps3 --data-file ../dropbox-app-key.txt
firebase functions:secrets:set DROPBOX_APP_SECRET    --project chronomaps3 --data-file ../dropbox-app-secret.txt
firebase functions:secrets:set DROPBOX_REFRESH_TOKEN --project chronomaps3 --data-file ../dropbox-refresh-token.txt
```

Non-secret configuration lives in `functions/.env.chronomaps3`, which is committed so a deploy from
CI needs no manual setup:

```
DROPBOX_ROOT_PATH=/futuring_workshops_archive
DROPBOX_FOLDER_CUTOFF=2026-08-20T00:00:00Z
# DROPBOX_NAMESPACE_ID=        # only for a team space — step 6 tells you
```

## 6. Verify the connection

```bash
export DROPBOX_APP_KEY="$(cat ../dropbox-app-key.txt)"
export DROPBOX_APP_SECRET="$(cat ../dropbox-app-secret.txt)"
export DROPBOX_REFRESH_TOKEN="$(cat ../dropbox-refresh-token.txt)"
export DROPBOX_ROOT_PATH=/futuring_workshops_archive
python scripts/dropbox_check.py
```

It authenticates, reports whether a **team space** is in play, and lists every subfolder with the
reason it would or would not be ingested. If the root folder cannot be listed but the account has a
team space, uncomment `DROPBOX_NAMESPACE_ID` with the value the script prints: members of a Dropbox
team have a personal home *and* a team space, and paths resolve against home unless a path root is
set — a folder in the team space is otherwise simply invisible.

## 7. Enable a workshop folder

Create a file named `chronomaps.config` in the folder (`.chronomaps.config` and `chronomaps.txt`
also work). Both fields come from the workspace's auto-input link
(`https://mapfutur.es/?workspace=<workspace>&api_key=<key>&automatic=true`):

```
workspace: 00000000-1111-2222-3333-444444444444
api_key: 55555555-6666-7777-8888-999999999999
```

Two things to know about the key:

- The **collaborate** key is enough for the whole ingest path, and is the right choice because the
  archive is usually a *shared* Dropbox folder — anyone with folder access can read this file. An
  admin key there would hand them full control of the workspace, including deleting every item.
- A collaborate key is only accepted **while collaboration is enabled on the workspace**. If it is
  off, every image fails with 403. Enable it in the admin UI, or use the admin key instead.

Optional settings, all with sensible defaults — see [API.md](API.md#credentials-file) for the list.
The one worth knowing is `ignore_cutoff: true`, needed to ingest a folder whose existing scans
predate `DROPBOX_FOLDER_CUTOFF`.

## 8. Preview, then run

```bash
python dropbox_ingest_cli.py --dry-run --folder "<folder name>"   # uploads nothing, writes nothing
python dropbox_ingest_cli.py --folder "<folder name>"             # for real
```

The dry run lists candidates and their batch grouping. It does not download images, so it cannot
show which will be rejected for their aspect ratio — that appears in the real run.

Once the function is deployed, `dropbox_ingest_scheduled` does this every minute on its own.
`POST /dropbox_ingest?dry_run=true&folder=<name>` (with an admin Firebase token) runs it on demand.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Token refresh failed: 400 invalid_client` | App key/secret mismatch, or the secret was regenerated — re-run step 4 |
| Root folder cannot be listed | Path typo, or the folder is in the team space — set `DROPBOX_NAMESPACE_ID` (step 6) |
| `401` mid-ingest | Token minted before permissions were submitted — re-run step 4 |
| Every image errors with 403 | Collaboration disabled on the workspace, or a stale key in `chronomaps.config` |
| Folder is listed as "before the cutoff" | It already contained scans older than `DROPBOX_FOLDER_CUTOFF`; add `ignore_cutoff: true` |
| `action: quarantined` in the output | A file failed 3 times and is no longer retried; fix the cause, then delete its entry from `chronomaps.state.json` |
| An image was uploaded twice | Should not happen — check whether `chronomaps.state.json` was deleted or the folder was copied |
| Nothing at all is ingested | No credentials file in any folder; `scripts/dropbox_check.py` says which folders are skipped and why |

## Rotating or revoking access

Regenerating the app secret, or disconnecting the app under
https://www.dropbox.com/account/connected_apps, invalidates the refresh token immediately — the
ingest then fails on every run until step 4 is repeated. To stop ingest for one folder without
touching credentials, put `enabled: false` in its `chronomaps.config`.

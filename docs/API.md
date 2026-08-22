# Chronomaps Server API Documentation

## Overview

Chronomaps Server is a Firebase Cloud Functions-based backend for managing collaborative workspaces that collect and process future scenario screenshots. The system uses AI-powered analysis to extract structured information from screenshots and provides clustering and visualization capabilities.

### Technology Stack

- **Framework**: Python 3.12 with Flask
- **Cloud Platform**: Firebase Functions (Google Cloud Functions)
- **Database**: Firebase Firestore
- **Storage**: Firebase Storage
- **AI/ML**: OpenAI GPT-5.4, scikit-learn, NumPy
- **Deployment Region**: europe-west4

---

## Multi-Database Support

All endpoints under `chronomaps_api` accept an optional `db` query parameter that selects which Firestore database the request targets:

- `db=<database-id>` — route the request to the named Firestore database.
- Omitted — route to the project's **default** Firestore database (backwards compatible).

The parameter is honored uniformly across every endpoint (workspace and item CRUD, listings, aggregation, taxonomy, `/all-items`, `/config`, etc.) — individual endpoint sections below do not repeat it.

Example:
```bash
curl "https://region-project.cloudfunctions.net/chronomaps_api/?db=staging" \
  -H "Authorization: Bearer $FIREBASE_TOKEN"
```

> Only the Flask-based `chronomaps_api` function honors `db`. Auxiliary Cloud Functions (`screenshot_handler`, `complete_flow`, `cluster_screenshots`, `build_taxonomy`, `tag_item`, etc.) always use the default Firestore database.

---

## Authentication & Authorization

The API uses a multi-tier key-based authorization system.

### Privilege Levels

| Level | Name | Access |
|-------|------|--------|
| 4 | `PRIVILEGE_ADMIN` | Full workspace control |
| 3 | `PRIVILEGE_PRIVATE_KEY` | Item-specific edit access |
| 2 | `PRIVILEGE_COLLABORATE` | Create/edit items when collaboration is enabled |
| 1 | `PRIVILEGE_VIEW` | Read-only access with view key |
| 0 | `PRIVILEGE_PUBLIC` | Public read access when enabled |

### Authorization Methods

**1. API Key Header**
```
Authorization: <api-key>
```

**2. Item Key Query Parameter**
```
?item-key=<item-key>
```

**3. Firebase Auth Token** (for workspace listing/creation)
```
Authorization: Bearer <firebase-token>
```

**4. Database Bearer Key** (override for Firebase-auth endpoints)
```
Authorization: Bearer <db-key>
```
When the target database's `config/config` document defines a `key`, that value can be sent as a bearer token in place of a Firebase ID token on any endpoint that normally requires Firebase auth (`GET /`, `POST /`, `GET /all-items`, `PUT /global/<key>`, `POST /build_taxonomy`). The override is per-database — each db has its own `key`. Configure or rotate it by writing to `config/config` in that db.

### Workspace Keys

Each workspace has three UUID4 keys:
- `admin` - Full control over workspace and all items
- `collaborate` - Add/edit items when collaboration is enabled
- `view` - Read-only access to workspace

---

## Moderation Levels

Items in the system have a `_private_moderation` field that controls their visibility and status. The moderation system uses numeric values to represent different states:

| Value | Label | Description |
|-------|-------|-------------|
| 5 | Highlighted | Featured/exemplary content |
| 4 | Approved | Reviewed and approved for display |
| 3 | Not flagged | Default state, visible to all users |
| 2 | Pending | Awaiting moderation review |
| 1 | Flagged | Marked for review or concern |
| 0 | Rejected | Reviewed and rejected |
| -1 | Deleted | Soft-deleted content |

### Moderation Filtering

- **Admin users** (privilege level 4) can see and manage items at any moderation level
- **Non-admin users** (privilege levels 0-3) can only see items with `_private_moderation >= 3`

### Default Moderation Level

When creating a workspace, you can set a `default_moderation_level` in the workspace metadata. This value determines the initial moderation status for new items created via the screenshot handler:

- If specified, new items will use this value as their `_private_moderation`
- If not specified, defaults to `3` (Not flagged)
- Recommended values: `2` (Pending) for manual moderation workflows, or `3` (Not flagged) for automatic approval

---

## Core API Endpoints

Base URL: `https://<region>-<project>.cloudfunctions.net/chronomaps_api`

### Database Configuration

#### Get Database Metadata

```http
GET /config
```

**Authentication**: None (public endpoint)

**Description**: Returns the `metadata` field of the target database's `config/config` document. Use this to discover human-readable information about a database (title, description, branding, etc.) without authenticating. The response intentionally excludes the `key` and `admins` fields.

**Response**:
```json
{
  "metadata": {
    "title": "Production Database",
    "description": "Live chronomaps data"
  }
}
```

If the `config/config` document does not exist, or has no `metadata` field, the endpoint returns `{"metadata": {}}` with status 200.

---

### Global Key-Value Store

A per-database key-value store. Values are scoped to the target database (selected with `?db=`), not to a workspace. Admins write; anyone can read.

#### Set Global Key

```http
PUT /global/<key>
POST /global/<key>
Authorization: Bearer <firebase-token | db-key>
Content-Type: application/json

<any JSON value>
```

**Authentication**: Firebase Bearer Token (admin users only) or the database bearer key

**Description**: Stores the request body as the value for `<key>` in the target database, overwriting any previous value. The body must be valid JSON and may be any JSON type (object, array, string, number, boolean, `null`). The value is persisted as a JSON-encoded string in the `global_keys/<key>` document, along with `updated_at` and `updated_by`.

Keys are URL path segments: non-empty, no `/`, at most 1500 bytes, and not of the form `__name__`.

**Response** (200):
```json
{
  "key": "featured_workspace",
  "value": {"id": "abc123", "title": "Spring 2026"},
  "updated_at": "2026-08-21T10:00:00+00:00"
}
```

**Errors**: `400` invalid key or non-JSON body, `401` not authenticated / not an admin.

#### Read Global Key

```http
GET /global/<key>
```

**Authentication**: None (public endpoint)

**Description**: Returns the stored JSON value for `<key>` as the response body (`Content-Type: application/json`). The body is the value itself, not wrapped in an envelope.

**Response** (200):
```json
{"id": "abc123", "title": "Spring 2026"}
```

**Errors**: `404` if the key has not been set in this database.

---

### Workspace Management

#### List Workspaces

```http
GET /
```

**Authentication**: Firebase Bearer Token (admin users only)

**Response**:
```json
{
  "workspaces": [
    {
      "id": "workspace_id",
      "metadata": { ... }
    }
  ]
}
```

---

#### Create Workspace

```http
POST /
```

**Authentication**: Firebase Bearer Token (admin users only)

**Request Body**:
```json
{
  "workspace_id": "optional-custom-id",
  "title": "Workspace Title",
  "description": "Workspace Description",
  "event_name": "Event Name",
  "default_moderation_level": 3,
  ...
}
```

`workspace_id` is optional. When omitted, a UUID4 is generated. When supplied, it is used verbatim as the Firestore collection name for the workspace, so the caller is responsible for picking an ID that is unique and a valid Firestore collection identifier. The field is stripped from the body before the rest is persisted as `metadata`.

**Response**:
```json
{
  "workspace_id": "generated-workspace-id",
  "config": {
    "metadata": { ... },
    "keys": {
      "admin": "uuid4",
      "collaborate": "uuid4",
      "view": "uuid4"
    },
    "config": {
      "collaborate": false,
      "public": false
    }
  }
}
```

**Idempotency**: A fresh creation returns HTTP 201. Re-posting with a `workspace_id` that already exists returns HTTP 200 with the *existing* config (keys are not re-generated and metadata is not overwritten).

---

#### Get Workspace

```http
GET /<workspace>
```

**Authentication**: Any valid workspace key (admin/collaborate/view)

**Response**:
```json
{
  "metadata": { ... },
  "config": {
    "collaborate": false,
    "public": false
  }
}
```

Note: Full config with keys only returned with admin privilege.

When temporary collaboration is active, the response includes an additional field:
- `temporary_collaboration_ttl`: Number of seconds remaining until temporary collaboration expires (only present when active)

---

#### Update Workspace

```http
PUT /<workspace>?public=<bool>&collaborate=<bool>
```

**Authentication**: Admin key required

**Query Parameters**:
- `public` (optional): Enable/disable public access
- `collaborate` (optional): Enable/disable collaboration

**Request Body**:
```json
{
  "title": "Updated Title",
  "description": "Updated Description",
  ...
}
```

**Response**:
```json
{
  "message": "Workspace updated",
  "updates": {
    "public": true,
    "collaborate": false,
    ...
  }
}
```

---

#### Set Temporary Collaboration

```http
POST /<workspace>/temporary-collaboration?time=<int>&properties=<str>
```

**Authentication**: Admin key required

**Query Parameters**:
- `time` (required): Time in seconds. When `properties` is provided, this is the duration from now. When `properties` is omitted, this is a delta applied to the existing expiry (e.g. `-60` to subtract a minute).
- `properties` (optional): Comma-separated list of property names that collaborators may edit without an item key.

**Behavior**:
- **With `properties`**: Creates or replaces temporary collaboration. Sets expiry to `now + time` and stores the allowed property list.
- **Without `properties`**: Adjusts the expiry of an existing temporary collaboration by `time` seconds. Returns 400 if no temporary collaboration exists.

**Response**:
```json
{
  "expiry": 1742400000.0,
  "ttl": 299.5,
  "allowed_properties": ["title", "description"]
}
```

**Effect on other endpoints**:
- `GET /<workspace>`: Adds `temporary_collaboration_ttl` to the response while active
- `PUT /<workspace>/<item_id>`: Allows collaborate key holders to update items without an item key, but only for the allowed properties

---

#### Delete Temporary Collaboration

```http
DELETE /<workspace>/temporary-collaboration
```

**Authentication**: Admin key required

**Description**: Removes the temporary collaboration configuration from the workspace. This is idempotent — calling it when no temporary collaboration exists does not produce an error.

**Response**: `204 No Content`

---

#### Delete Workspace

```http
DELETE /<workspace>
```

**Authentication**: Admin key required

**Response**:
```json
{
  "message": "Workspace deleted"
}
```

---

### All Items (Cross-Workspace)

#### Get All Items

```http
GET /all-items?page=<int>&page_size=<int>&order_by=<field>&filters=<str>
```

**Authentication**: Firebase Bearer Token (admin users only)

**Query Parameters**:
- `page` (default: 0): Page number
- `page_size` (default: 10): Items per page
- `order_by` (default: -created_at): Field to sort by (prefix with '-' for descending)
- `filters` (optional): Pipe-separated filters (same format as `/<workspace>/items`)

**Description**: Retrieves items across all workspaces. Each item includes a `_workspace` field identifying its source workspace. Admin-level access: all private fields are included and no moderation filter is applied. Results are collected from all workspaces, re-sorted, then paginated.

**Example**:
```bash
curl "https://region-project.cloudfunctions.net/chronomaps_api/all-items?page=0&page_size=20&order_by=-created_at" \
  -H "Authorization: Bearer $FIREBASE_TOKEN"
```

**Response**:
```json
[
  {
    "_id": "item-id-1",
    "_workspace": "workspace-id-1",
    "title": "Item Title",
    "created_at": "2025-01-15T10:30:00Z",
    ...
  },
  {
    "_id": "item-id-2",
    "_workspace": "workspace-id-2",
    "title": "Another Item",
    "created_at": "2025-01-14T09:00:00Z",
    ...
  }
]
```

---

### Item Management

#### Create Item

```http
POST /<workspace>
```

**Authentication**: Admin key OR Collaborate key (when collaboration enabled)

**Request Body**:
```json
{
  "screenshot_type": "news_article",
  "content": "Article content...",
  "content_title": "Article Title",
  "future_scenario_tagline": "Scenario tagline",
  ...
}
```

**Response**:
```json
{
  "item_id": "generated-item-id",
  "item_key": "uuid4"
}
```

---

#### Get Items (Paginated)

```http
GET /<workspace>/items?page=<int>&page_size=<int>&order_by=<field>&filters=<str>
```

**Authentication**: Any valid workspace key

**Query Parameters**:
- `page` (default: 0): Page number
- `page_size` (default: 10): Items per page
- `order_by` (default: -created_at): Field to sort by (prefix with '-' for descending)
- `filters` (optional): Pipe-separated filters (format: `"field op value|field op value"`)

**Filter Operators**:
- `==`: Equals
- `!=`: Not equals
- `>`: Greater than
- `<`: Less than
- `>=`: Greater than or equal
- `<=`: Less than or equal

**Moderation Filtering**:
Non-admin users automatically have a filter applied to only show items with `_private_moderation >= 3` (Not flagged or higher). This means:
- **Admin users** see all items regardless of moderation status
- **Non-admin users** (collaborate, view, public) only see items that are "Not flagged", "Approved", or "Highlighted"

See [Moderation Levels](#moderation-levels) for the complete list of moderation statuses.

**Example**:
```
GET /my-workspace/items?page=0&page_size=20&order_by=-created_at&filters=plausibility>50|automatic==true
```

**Response**:
```json
[
  {
    "id": "item_id",
    "metadata": {
      "screenshot_type": "...",
      "content": "...",
      "created_at": "2025-01-15T10:30:00Z",
      ...
    }
  }
]
```

---

#### Aggregate Items

```http
GET /<workspace>/items/aggregate?field=<field_name>&filters=<filter_expr>
```

**Authentication**: Any valid workspace key

**Query Parameters**:
- `field` (required): The field name in metadata to aggregate by (supports nested fields with dot notation)
- `filters` (optional): Pipe-separated filters to apply before aggregation (same format as `/items` endpoint)

**Description**: Counts items grouped by unique values of the specified field. Useful for analytics and understanding data distribution. Returns a sorted list of value-count pairs.

**Features**:
- Supports nested field paths (e.g., `user.role`, `metadata.tags`)
- Returns `null` for items without the specified field
- Works with string, numeric, boolean, array, and object values
- Results sorted by count (most common first)
- Supports filtering before aggregation
- Uses in-memory fallback if Firestore indexes are missing

**Example**:
```bash
# Count items by status
GET /my-workspace/items/aggregate?field=status
```

**Response**:
```json
[
  {"value": "active", "count": 15},
  {"value": "inactive", "count": 8},
  {"value": null, "count": 2}
]
```

**Example with nested field**:
```bash
# Count items by user role
GET /my-workspace/items/aggregate?field=user.role
```

**Response**:
```json
[
  {"value": "viewer", "count": 25},
  {"value": "editor", "count": 12},
  {"value": "admin", "count": 3},
  {"value": null, "count": 5}
]
```

**Example with filters**:
```bash
# Count plausibility scores only for preferred futures
GET /my-workspace/items/aggregate?field=plausibility&filters=favorable_future==prefer
```

**Response**:
```json
[
  {"value": 80, "count": 15},
  {"value": 75, "count": 10},
  {"value": 85, "count": 8},
  {"value": 90, "count": 3}
]
```

---

#### Get Item

```http
GET /<workspace>/<item_id>?item-key=<item_key>
```

**Authentication**: Any valid workspace key, OR item-key for private access

**Response**:
```json
{
  "metadata": {
    "screenshot_type": "news_article",
    "screenshot_url": "https://...",
    "content": "...",
    "content_title": "...",
    "plausibility": 75,
    "created_at": "2025-01-15T10:30:00Z",
    ...
  }
}
```

---

#### Update Item

```http
PUT /<workspace>/<item_id>?item-key=<item_key>
```

**Authentication**: Admin key OR Collaborate key + item-key

When [temporary collaboration](#set-temporary-collaboration) is active, a collaborate key may also update items **without** an item key — but only the properties listed in the temporary collaboration configuration. Properties not in the allowed list are silently filtered out. Returns 400 if no allowed properties remain after filtering. Admin and item-key access are unaffected (no property filtering).

**Request Body**:
```json
{
  "content_title": "Updated Title",
  "plausibility": 80,
  ...
}
```

**Response**: Updated item metadata

---

#### Delete Item

```http
DELETE /<workspace>/<item_id>?item-key=<item_key>
```

**Authentication**: Admin key OR Collaborate key + item-key

**Response**:
```json
{
  "message": "Item deleted"
}
```

---

#### Delete All Items

```http
DELETE /<workspace>/items
```

**Authentication**: Admin key required

**Response**:
```json
{
  "message": "Items deleted"
}
```

---

## AI-Powered Endpoints

### Screenshot Analysis

```http
POST /screenshot_handler?workspace=<id>&api_key=<key>&automatic=<bool>
```

**Authentication**: Admin key, OR Collaborate key (when collaboration is enabled)

**Content-Type**: multipart/form-data

**Query Parameters**:
- `workspace` (required): Workspace ID
- `api_key` (required): Workspace admin or collaborate key
- `automatic` (optional): Use automatic mode (true/false)

**Request Body**: Image file

**Description**: Analyzes screenshot using GPT-5.4 Vision model to extract structured information including screenshot type, content, future scenario details, and more. Always creates a new item. After creation, automatically generates the item's semantic embedding and assigns taxonomy topics (if a taxonomy exists). To update an existing item's image or re-analyze it, use the `replace_image` or `reanalyze_item` endpoints instead.

**Response**:
```json
{
  "item_id": "generated-item-id",
  "item_key": "uuid4",
  "metadata": {
    "screenshot_type": "news_article | fake_media | future_history_page | ...",
    "content": "Extracted content text",
    "content_title": "Extracted title",
    "content_certainty": 85,
    "transition_bar_event": "Event name if detected",
    "transition_bar_position": "before | during | after",
    "transition_bar_certainty": 70,
    "future_scenario_tagline": "Brief scenario tagline",
    "future_scenario_description": "Detailed scenario description",
    "future_scenario_topics": ["topic1", "topic2"],
    "detected_language": "en",
    "plausibility": 75,
    "favorable_future": "prefer | prevent | mostly_prefer | mostly_prevent",
    "screenshot_url": "https://storage.googleapis.com/...",
    "created_at": "2025-01-15T10:30:00Z",
    "automatic": true
  }
}
```

---

### Replace Item Image

```http
POST /replace_image?workspace=<id>&api_key=<key>&item_id=<id>&item_key=<key>
```

**Authentication**: Admin key, OR Collaborate key + item-key

**Content-Type**: multipart/form-data

**Query Parameters**:
- `workspace` (required): Workspace ID
- `api_key` (required): Workspace admin or collaborate key
- `item_id` (required): Item to replace the image for
- `item_key` (required): Item key for authorization

**Request Body**: Image file (field name: `image`)

**Description**: Replaces an existing item's screenshot image without triggering re-analysis. Useful for correcting or updating an image while preserving all existing metadata and analysis results.

**Response**:
```json
{
  "item_id": "item-id",
  "screenshot_url": "https://storage.googleapis.com/..."
}
```

---

### Re-analyze Item

```http
POST /reanalyze_item?workspace=<id>&api_key=<key>&item_id=<id>&item_key=<key>&automatic=<bool>
```

**Authentication**: Admin key, OR Collaborate key + item-key

**Query Parameters**:
- `workspace` (required): Workspace ID
- `api_key` (required): Workspace admin or collaborate key
- `item_id` (required): Item to re-analyze
- `item_key` (required): Item key for authorization
- `automatic` (optional, default: false): Use automatic analysis mode. When true, existing metadata is passed to the AI as context.

**Description**: Re-runs GPT-5.4 Vision analysis on an existing item using its stored image from Firebase Storage. Does not upload a new image. The item's metadata is replaced with new analysis results while preserving the existing `screenshot_url`. After re-analysis, automatically regenerates the item's semantic embedding and re-assigns taxonomy topics. Useful for re-processing items after prompt improvements or when analysis quality needs improvement.

**Response**:
```json
{
  "item_id": "item-id",
  "automatic": false,
  "metadata": {
    "screenshot_type": "news_article",
    "content": "Re-analyzed content...",
    "content_title": "Updated Title",
    "screenshot_url": "https://storage.googleapis.com/...",
    "created_at": "2025-01-15T10:30:00Z",
    ...
  }
}
```

---

### Item Ingress Agent (Chat Interface)

```http
POST /item_ingress_agent?workspace=<id>&api_key=<key>&item_id=<id>&item_key=<key>&message=<msg>&stream=<bool>
```

**Authentication**: OPENAI_API_KEY and CHRONOMAPS_API_URL secrets required

**Query Parameters**:
- `workspace`: Workspace ID
- `api_key`: Workspace key
- `item_id`: Item ID
- `item_key`: Item key
- `message`: User message to the agent
- `stream` (optional): Enable streaming (true/false)

**Description**: AI chat agent for interactive item property updates using OpenAI Assistants API. Maintains conversation thread per item and can update item properties via function calls.

**Streaming Response** (Server-Sent Events):
```
data: {"kind": "status", "message": "Processing..."}

data: {"kind": "text", "value": "I can help you with that..."}

data: {"kind": "tool", "name": "update_properties", "arguments": {"payload": "{...}"}}

data: {"kind": "event", "event": "thread.run.completed"}
```

**Stream Event Types**:
- `status`: Status messages
- `text`: Agent response text chunks
- `tool`: Tool calls (e.g., property updates)
- `event`: OpenAI API events

---

### Complete Flow

```http
POST /complete_flow?workspace=<id>&api_key=<key>&item_id=<id>&item_key=<key>&locale=<lang>&workshop=<bool>
```

**Authentication**: CHRONOMAPS_API_URL secret required

**Query Parameters**:
- `workspace`: Workspace ID
- `api_key`: Workspace key
- `item_id`: Item ID
- `item_key`: Item key
- `locale` (optional): Language code (e.g., "en", "fr")
- `workshop` (optional): Workshop mode flag

**Request Body**:
```json
{
  "property1": "value1",
  "_private_email": "user@example.com",
  ...
}
```

**Description**: Completes the item workflow by updating properties and optionally sending an email notification.

**Response**:
```json
{
  "success": true
}
```

---

### Cluster Screenshots

```http
POST /cluster_screenshots?config=<config>&tag=<tag>&no_title=<bool>
```

**Authentication**: OPENAI_API_KEY and CHRONOMAPS_API_URL secrets required

**Memory**: 8GB Cloud Function

**Response Type**: Server-Sent Events (stream)

**Query Parameters**:
- `config`: Configuration string (format: `"workspace:admin_key:moderation_level;workspace2:key:level"`)
- `tag`: Tag for the cluster set
- `no_title` (optional): Skip title generation

**Description**: ML-powered screenshot clustering and visualization using t-SNE dimensionality reduction and agglomerative clustering. Generates map tiles (256x256px) for zoom levels and extracts cluster themes using GPT-5.4.

**Stream Events**:
```
data: [timestamp, {"msg": "Starting clustering..."}]

data: [timestamp, {"msg": "Processing 150 items..."}]

data: [timestamp, {"msg": "Generating tiles..."}]

data: [timestamp, {"msg": "Complete!"}]
```

**Generated Output**:
- Map tiles at `/tiles/<tag>/<set_id>/<zoom>/<x>/<y>.png`
- Cluster configuration at `/tiles/<tag>/<set_id>/config.json`
- Current set pointer at `/tiles/<tag>/config.json`

---

### Build Taxonomy

```http
POST /build_taxonomy?similarity_threshold=<float>&max_tags=<int>&redesign=<bool>
```

**Authentication**: Firebase Bearer Token (admin users only)

**Memory**: 8GB Cloud Function

**Response Type**: Server-Sent Events (stream)

**Query Parameters**:
- `similarity_threshold` (default: 0.35): Minimum cosine similarity for assigning additional (non-primary) tags to an item
- `max_tags` (default: 3): Maximum number of topic tags per item
- `redesign` (default: false): When false, reuses the existing taxonomy and only re-assigns items. When true, samples descriptions and asks GPT-5.4 to refine the taxonomy (keeping existing theme names stable where possible).

**Description**: Builds a cross-workspace hierarchical taxonomy. GPT-5.4 designs the taxonomy top-down from a representative sample of item descriptions, producing mutually exclusive themes and sub-themes with names in four languages (English, Dutch, Hebrew, Arabic). Reference embeddings are generated for each sub-theme definition and cached in Firestore for use by the `tag_item` endpoint.

Each item receives multi-label topic tags stored in `metadata.topics`. The full taxonomy reference is saved to the `chronomaps_global/taxonomy` Firestore document and can be retrieved via `GET /taxonomy`.

By default (without `redesign=true`), the endpoint reuses the existing taxonomy and only re-assigns items — completing in ~40 seconds instead of ~90.

**Stream Events**:
```
data: [timestamp, {"msg": "Loading items from workspace-1..."}]

data: [timestamp, {"msg": "Sampled 200 descriptions. Designing taxonomy..."}]

data: [timestamp, {"msg": "Taxonomy designed: 11 themes, 42 sub-themes"}]

data: [timestamp, {"msg": "Assigning topics to items..."}]

data: [timestamp, {"msg": "Taxonomy build complete."}]
```

**Side Effects**:
- Updates `metadata.topics` on every item with valid embeddings (see [Item Structure](#item-structure))
- Creates/overwrites the `chronomaps_global/taxonomy` document (see [Taxonomy Document](#taxonomy-document))
- Caches reference embeddings as `embedding-<hash>` documents in `chronomaps_global`

---

### Get Taxonomy

```http
GET /taxonomy
```

**Authentication**: None (public endpoint)

**Description**: Returns the current taxonomy reference document. This is generated by the `build_taxonomy` endpoint and contains the full hierarchy of themes and sub-themes with item counts.

**Response**:
```json
{
  "version": "2026-03-24T12:00:00+00:00",
  "item_count": 1500,
  "themes": [
    {
      "id": "climate-disaster",
      "name": {
        "english": "Climate & Disaster",
        "dutch": "Klimaat & Ramp",
        "hebrew": "אקלים ואסון",
        "arabic": "المناخ والكوارث"
      },
      "item_count": 230,
      "sub_themes": [
        {
          "id": "rising-sea-levels",
          "name": {
            "english": "Rising Sea Levels",
            "dutch": "Stijgende Zeespiegel",
            "hebrew": "עליית מפלס הים",
            "arabic": "ارتفاع مستوى سطح البحر"
          },
          "item_count": 85
        }
      ]
    }
  ]
}
```

**Error Response** (taxonomy not yet generated):
```json
{
  "error": "Taxonomy not yet generated"
}
```
Status: 404

---

### Tag Item

```http
POST /tag_item?workspace=<id>&item_id=<id>&item_key=<key>
Authorization: <api-key>
```

**Authentication**: Workspace API key via `Authorization` header (or `api_key` query parameter). Admin key or collaborate key + item-key.

**Query Parameters**:
- `workspace` (required): Workspace ID
- `item_id` (required): Item to tag
- `item_key` (optional): Item key for authorization. Not needed if the API key has admin privileges.

**Description**: Tags a single item using the existing taxonomy. Loads cached reference embeddings from Firestore (generating any missing ones on demand), computes the item's embedding if needed, and assigns topic tags via cosine similarity. Updates the item's `metadata.topics` and `metadata.topics_version` fields.

This endpoint is also called automatically by `screenshot_handler` and `reanalyze_item` after creating or re-analyzing an item, so topics are included in their response metadata.

**Response**:
```json
{
  "topics": ["climate-environment-resources/climate-change-disasters", "urban-life-infrastructure/urban-mobility-transport"],
  "similarity": 0.5423
}
```

**Error Responses**:
- 400: Item has no `future_scenario_description`
- 404: No taxonomy exists yet (run `build_taxonomy` first)

---

## Dropbox Auto-Ingest

Scanned pages that land in a Dropbox folder are ingested automatically: cropped to the expected page
proportions, uploaded through `screenshot_handler` in automatic mode, and tagged so all pages from a
single scan batch share one `author_id`.

### How a folder is picked up

The ingest lists the immediate subfolders of `DROPBOX_ROOT_PATH`. A subfolder is ingested only if:

1. It contains a **credentials file** at its root — `chronomaps.config`, `.chronomaps.config` or
   `chronomaps.txt` (first match wins), and
2. It was **created after `DROPBOX_FOLDER_CUTOFF`** (default `2026-08-20T00:00:00Z`). Dropbox exposes
   no folder creation time, so the oldest `server_modified` in the folder is used as the proxy. Set
   `ignore_cutoff: true` in the credentials file to backfill an older folder deliberately.

Images are collected recursively below the folder (`.jpg`, `.jpeg`, `.png`).

### Credentials file

JSON, or simple `key: value` lines (`#` comments allowed):

```
workspace: 0a698fad-7e49-428f-bceb-c9d51b3512e1
api_key: <collaborate or admin key>

# optional
enabled: true               # false disables the folder without deleting the file
ignore_cutoff: false        # true ingests a folder created before the cutoff
batch_gap_seconds: 120      # a longer gap between scans starts a new author batch
ratio: 0.53                 # target width/height
ratio_tolerance: 0.10       # images further off than this are skipped
max_uploads_per_run: 50     # remaining images are picked up on the next run
time_source: auto           # auto | client | server — which Dropbox timestamp is "scan time"
rotate_landscape: off       # off | cw | ccw — rescue landscape scans by rotating them
```

### State file

`chronomaps.state.json` is written next to the credentials file and is what prevents re-uploads:

```json
{
  "version": 1,
  "workspace": "<workspace-id>",
  "files":   {"<dropbox content_hash>": {"path": "...", "item_id": "...", "author_id": "...",
                                          "scanned_at": "...", "uploaded_at": "..."}},
  "skipped": {"<dropbox content_hash>": {"path": "...", "reason": "aspect ratio ...", "at": "..."}},
  "failed":  {"<dropbox content_hash>": {"path": "...", "error": "...", "attempts": 1, "at": "..."}},
  "recent_batches": [{"author_id": "<uuid4>", "first_scanned_at": "...", "last_scanned_at": "..."}]
}
```

- Dedup is keyed on Dropbox's own `content_hash`, so a re-uploaded or moved copy of the same bytes is
  recognised without downloading it. Two copies of the same bytes in one folder (a Dropbox
  "conflicted copy") upload once.
- A `files` entry with a `metadata_error` means the item was created but its `author_id` could not be
  attached. It is deliberately not retried — retrying would create a second item — so fix it by
  PUTting the metadata onto the recorded `item_id`.
- `failed` entries are retried on later runs and quarantined after 3 attempts; a quarantined file is
  reported (`action: quarantined`) on every subsequent run. Delete the entry to force a retry.
- Deleting the whole file causes the folder to be ingested again from scratch.

### Batching and `author_id`

Scans are ordered by **scan time** (`client_modified`, falling back to `server_modified`). A gap
longer than `batch_gap_seconds` starts a new batch, and each batch gets one `uuid4` `author_id` that
is written to every item in it. The last 10 batches are kept in `recent_batches` with their scan
windows, so a page that syncs an hour late rejoins *its own* batch rather than whichever one was
processed most recently. Files that reached Dropbox less than `DROPBOX_SETTLE_SECONDS` ago (default
180) are left for the next run so a batch mid-sync is not split.

### Image preprocessing

Images are centre-cropped to `ratio` (0.53 width:height, matching the scanner UI) and rejected —
recorded under `skipped` — when the ratio is further than `ratio_tolerance` from the target, when the
shortest side is under 300px, or when the file is unreadable or over 30MB. Accepted images are fit
into 2120x4000 and re-encoded as JPEG q85, the same preprocessing the batch uploader uses. Contrast
and white balance are left to the existing `enhance_image` step.

### Upload path

Per image, identical to the app's automatic mode:

```http
POST {SCREENSHOT_HANDLER_URL}?workspace=<id>&api_key=<key>&automatic=true
Content-Type: multipart/form-data     (field: image)

PUT {CHRONOMAPS_API_URL}/<workspace>/<item_id>?item-key=<item_key>
Authorization: <api_key>

{"author_id": "<batch uuid4>", "source": "dropbox", "dropbox_path": "...",
 "dropbox_content_hash": "...", "scanned_at": "..."}
```

Bookkeeping metadata goes in the `PUT`, never in the handler's `metadata` form field — that field is
fed to the vision model as user-provided truth.

### Triggers

```http
POST /dropbox_ingest?dry_run=<bool>&folder=<folder-name>
Authorization: Bearer <firebase-token>
```

**Authentication**: Firebase admin token (`shared.verify_firebase_admin`)

**Description**: Runs the ingest immediately. `dry_run=true` reports what would be uploaded without
uploading anything or writing state files; `folder` limits the run to one workspace folder. Returns
the array of per-step status objects.

`dropbox_ingest_scheduled` runs the same flow every 5 minutes. Both take a Firestore lock
(`chronomaps_global/dropbox_ingest_lock`) so two runs never ingest concurrently, and both stop
starting new work after `DROPBOX_RUN_DEADLINE_SECONDS` (default 1500) so an interrupted chunk cannot
be re-uploaded by the next run.

Locally, the same code path runs via `python dropbox_ingest_cli.py --dry-run [--folder NAME]`.

---

## Data Models

### Workspace Configuration

Stored in Firestore at `<workspace>/.config`

```json
{
  "metadata": {
    "title": "Workspace Title",
    "description": "Workspace Description",
    "event_name": "Event Name",
    "email-template": "Email template text...",
    "default_moderation_level": 3,
    "final-ingress-message": "Final message...",
    ...
  },
  "keys": {
    "admin": "uuid4-admin-key",
    "collaborate": "uuid4-collaborate-key",
    "view": "uuid4-view-key"
  },
  "config": {
    "collaborate": false,
    "public": false
  },
  "temporary_collaboration": {
    "expiry": 1742400000.0,
    "allowed_properties": ["title", "description"]
  }
}
```

Note: `temporary_collaboration` is only present when set by an admin via the [Set Temporary Collaboration](#set-temporary-collaboration) endpoint. It can be removed via the [Delete Temporary Collaboration](#delete-temporary-collaboration) endpoint.

**Metadata Fields**:
- `title`: Workspace display name
- `description`: Workspace description
- `event_name`: Associated event name
- `email-template`: Email template for notifications
- `default_moderation_level`: Initial moderation level for new items (default: 3). See [Moderation Levels](#moderation-levels) for available values.
- `final-ingress-message`: Final message shown to users

---

### Item Structure

Stored in Firestore at `<workspace>/<item_id>`

```json
{
  "key": "uuid4-item-key",
  "metadata": {
    // Screenshot Analysis Results
    "screenshot_type": "fake_media | future_history_page | news_article | social_media | ...",
    "screenshot_url": "https://storage.googleapis.com/...",
    "content": "Extracted content text",
    "content_title": "Extracted title",
    "content_certainty": 85,

    // Transition Bar (if detected)
    "transition_bar_event": "Event name",
    "transition_bar_position": "before | during | after",
    "transition_bar_certainty": 70,

    // Future Scenario Information
    "future_scenario_tagline": "Brief tagline",
    "future_scenario_description": "Detailed description",
    "future_scenario_topics": ["topic1", "topic2"],

    // Analysis Metadata
    "detected_language": "en",
    "plausibility": 75,
    "favorable_future": "prefer | prevent | mostly_prefer | mostly_prevent",
    "created_at": "2025-01-15T10:30:00Z",
    "automatic": true,

    // Taxonomy Tags (set by build_taxonomy or tag_item endpoints, auto-assigned on create/reanalyze)
    "topics": ["climate-disaster/rising-sea-levels", "political-power/surveillance"],
    "topics_version": "2026-03-24T12:00:00+00:00",

    // Private Fields (require PRIVILEGE_PRIVATE_KEY or higher)
    "_private_email": "user@example.com",
    "_private_moderation": 3,  // Moderation status (see Moderation Levels section)
    "_private_ingress-thread-id": "thread_...",
    "_key": "uuid4-item-key"
  }
}
```

### Screenshot Types

- `fake_media`: Fake or manipulated media content
- `future_history_page`: Historical documentation from the future
- `news_article`: News article about future events
- `social_media`: Social media post
- `data_visualization`: Charts, graphs, or data displays
- `other`: Other types of screenshots

### Favorable Future Values

- `prefer`: Desirable future scenario
- `prevent`: Undesirable future scenario
- `mostly_prefer`: Somewhat desirable
- `mostly_prevent`: Somewhat undesirable

### Item Topics

The `topics` field on each item is a flat list of strings in the format `"theme-id/sub-theme-id"`, where IDs are kebab-case slugs derived from the English theme names. This format is queryable using Firestore's `array-contains` operator.

- Topics are assigned by the `build_taxonomy` endpoint and may include 1 to `max_tags` entries per item
- The first entry is always the item's primary (strongest) topic match
- Additional entries are included when cosine similarity to the sub-theme centroid exceeds the `similarity_threshold`
- `topics_version` is an ISO timestamp indicating when topics were last computed

**Example**:
```json
{
  "topics": [
    "climate-disaster/rising-sea-levels",
    "environment-agriculture/food-scarcity"
  ],
  "topics_version": "2026-03-24T12:00:00+00:00"
}
```

---

### Taxonomy Document

Stored in Firestore at `chronomaps_global/taxonomy`. Generated by the `build_taxonomy` endpoint and served by `GET /taxonomy`.

```json
{
  "version": "2026-03-24T12:00:00+00:00",
  "item_count": 1500,
  "themes": [
    {
      "id": "climate-disaster",
      "name": {
        "english": "Climate & Disaster",
        "dutch": "Klimaat & Ramp",
        "hebrew": "אקלים ואסון",
        "arabic": "المناخ والكوارث"
      },
      "item_count": 230,
      "sub_themes": [
        {
          "id": "rising-sea-levels",
          "name": {
            "english": "Rising Sea Levels",
            "dutch": "Stijgende Zeespiegel",
            "hebrew": "עליית מפלס הים",
            "arabic": "ارتفاع مستوى سطح البحر"
          },
          "item_count": 85
        }
      ]
    }
  ]
}
```

**Fields**:
- `version`: ISO timestamp of when the taxonomy was built
- `item_count`: Total number of items processed
- `themes[].id`: Kebab-case slug (matches the theme portion of item topic strings)
- `themes[].name`: Theme name in four languages
- `themes[].item_count`: Number of items assigned to this theme (across all sub-themes)
- `themes[].sub_themes[].id`: Kebab-case slug (matches the sub-theme portion of item topic strings)
- `themes[].sub_themes[].name`: Sub-theme name in four languages
- `themes[].sub_themes[].item_count`: Number of items assigned to this specific sub-theme

---

## Storage Structure

Firebase Storage bucket: `chronomaps3-eu`

```
/<workspace>/<item_id>/screenshot.<ext>     # Original screenshots
/tiles/<tag>/<set_id>/config.json           # Cluster configuration
/tiles/<tag>/<set_id>/<zoom>/<x>/<y>.png    # Map tiles (256x256px)
/tiles/<tag>/config.json                    # Current set pointer
```

---

## Firestore Collections

1. **`<workspace>`** - Each workspace is a collection
   - `.config` document - Workspace configuration and keys
   - `<item_id>` documents - Items in the workspace

2. **`emails`** - Email queue for sending emails via Firestore triggers

3. **`config/config`** - Per-database configuration document. Fields:
   - `admins` (list of email strings): users allowed to use Firebase auth on this db
   - `key` (string, optional): bearer-token override for Firebase-auth endpoints (see [Authorization Methods](#authorization-methods))
   - `metadata` (object, optional): public, human-readable metadata returned by [`GET /config`](#get-database-metadata)

4. **`global_keys`** - Per-database public key-value store (see [Global Key-Value Store](#global-key-value-store)). One document per key:
   - `value` (string): the JSON-encoded value
   - `updated_at` (string): ISO timestamp of the last write
   - `updated_by` (string): email of the admin who wrote it (or `db-key`)

5. **`chronomaps_global`** - Cross-workspace data
   - `taxonomy` document - Hierarchical taxonomy reference (see [Taxonomy Document](#taxonomy-document))
   - `embedding-<hash>` documents - Cached reference embeddings for taxonomy sub-themes

---

## Error Responses

All endpoints return appropriate HTTP status codes with error messages:

**400 Bad Request**
```json
{
  "error": "Error message describing the problem"
}
```

**401 Unauthorized**
```json
{
  "error": "Unauthorized"
}
```

**403 Forbidden**
```json
{
  "error": "Insufficient privileges"
}
```

**404 Not Found**
```json
{
  "error": "Workspace not found"
}
```

**500 Internal Server Error**
```json
{
  "error": "Internal server error",
  "details": "Detailed error message"
}
```

---

## Environment Variables

Required secrets for Firebase Cloud Functions:

- `OPENAI_API_KEY` - OpenAI API key for GPT-5.4 operations
- `CHRONOMAPS_API_URL` - Base URL for the Chronomaps API
- `SERVICE_ACCOUNT_JSON` - Firebase service account credentials
- `CONFIG__ITS_TIME` - Configuration for scheduled clustering job

Required for the Dropbox auto-ingest — **secrets** (Secret Manager, set with
`firebase functions:secrets:set`):

- `DROPBOX_APP_KEY` / `DROPBOX_APP_SECRET` - Dropbox scoped app credentials
- `DROPBOX_REFRESH_TOKEN` - Long-lived token (mint it with `scripts/dropbox_authorize.py`)

And **non-secret configuration**, kept in `functions/.env.chronomaps3` so deploys from CI need no
manual setup:

- `DROPBOX_ROOT_PATH` - Folder holding the per-workspace subfolders
- `DROPBOX_FOLDER_CUTOFF` - ISO timestamp; folders older than this are ignored
- `DROPBOX_NAMESPACE_ID` - Only for a Dropbox Business team space (`scripts/dropbox_check.py`
  prints the value when it is needed)
- `DROPBOX_SETTLE_SECONDS` - Optional; how long a file must be settled before ingest (default 180)
- `DROPBOX_RUN_DEADLINE_SECONDS` - Optional; stop starting work after this long (default 1500)
- `SCREENSHOT_HANDLER_URL` - Optional; derived from `CHRONOMAPS_API_URL` when both follow the
  standard naming, required otherwise

### First-time setup

1. Create a **scoped** app at https://www.dropbox.com/developers/apps with **Full Dropbox** access
   (an *App folder* app can never see a folder that already exists elsewhere).
2. On its *Permissions* tab enable `account_info.read`, `files.metadata.read`, `files.content.read`
   and `files.content.write`, then Submit — before minting any token, or the token will not carry
   the scopes.
3. On its *Settings* tab set **Access token expiration: Short-lived**, leave **Redirect URIs empty**
   (the setup script uses the copy-the-code flow) and leave *Allow public clients* disallowed — the
   ingest authenticates with the app secret. Webhooks are not used; the function polls. The app can
   stay in *Development* status: it is linked to a single account.
4. `python scripts/dropbox_authorize.py --app-key KEY --app-secret SECRET` and follow the prompts.
   Authorize with the account that can see the scan folder — for a team space, that is the member
   whose token the namespace id in step 6 belongs to. Regenerating the app secret later invalidates
   the refresh token.
5. Store the three credentials as secrets, put the root path in `functions/.env.chronomaps3`.
6. `python scripts/dropbox_check.py` — verifies auth, detects a team namespace, and lists which
   folders would be ingested and why the others would not.
7. `python dropbox_ingest_cli.py --dry-run` for a per-image preview before anything is uploaded.

---

## Rate Limits & Quotas

- Cloud Functions timeout: 60 seconds (standard), 540 seconds (clustering)
- Memory allocation: 256MB (standard), 8GB (clustering)
- Max request size: 10MB for image uploads
- Firestore read/write limits apply per Firebase pricing tier

---

## Examples

### Creating a Workspace and Adding an Item

```bash
# 1. Create workspace (requires Firebase auth token)
curl -X POST https://region-project.cloudfunctions.net/chronomaps_api \
  -H "Authorization: Bearer $FIREBASE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Future Scenarios 2050",
    "description": "Workshop scenarios",
    "event_name": "Workshop Jan 2025"
  }'

# Response includes admin key
# {"workspace_id": "abc123", "config": {"keys": {"admin": "KEY123..."}}}

# 2. Upload screenshot for analysis
curl -X POST "https://region-project.cloudfunctions.net/screenshot_handler?workspace=abc123&api_key=KEY123" \
  -F "image=@screenshot.png"

# 3. Get all items
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items?page=0&page_size=20" \
  -H "Authorization: KEY123"
```

### Replacing an Item's Image

```bash
# Replace screenshot without re-analysis
curl -X POST "https://region-project.cloudfunctions.net/replace_image?workspace=abc123&api_key=KEY123&item_id=ITEM_ID&item_key=ITEM_KEY" \
  -F "image=@new-screenshot.png"
```

### Re-analyzing an Item

```bash
# Re-run analysis on existing item using its stored image
curl -X POST "https://region-project.cloudfunctions.net/reanalyze_item?workspace=abc123&api_key=KEY123&item_id=ITEM_ID&item_key=ITEM_KEY"

# Re-analyze in automatic mode (preserves existing metadata as context)
curl -X POST "https://region-project.cloudfunctions.net/reanalyze_item?workspace=abc123&api_key=KEY123&item_id=ITEM_ID&item_key=ITEM_KEY&automatic=true"
```

### Filtering Items

```bash
# Get items with plausibility > 70, ordered by creation date
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items?filters=plausibility>70&order_by=-created_at" \
  -H "Authorization: KEY123"

# Get automatic items that are preferred futures
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items?filters=automatic==true|favorable_future==prefer" \
  -H "Authorization: KEY123"
```

### Targeting a Non-Default Database

```bash
# Read the public metadata for the staging database
curl "https://region-project.cloudfunctions.net/chronomaps_api/config?db=staging"

# List workspaces in the staging database using a Firebase ID token
curl "https://region-project.cloudfunctions.net/chronomaps_api/?db=staging" \
  -H "Authorization: Bearer $FIREBASE_TOKEN"

# List workspaces in the staging database using the per-db bearer key override
curl "https://region-project.cloudfunctions.net/chronomaps_api/?db=staging" \
  -H "Authorization: Bearer $STAGING_DB_KEY"
```

### Aggregating Items

```bash
# Count items by screenshot type
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items/aggregate?field=screenshot_type" \
  -H "Authorization: KEY123"

# Response:
# [
#   {"value": "news_article", "count": 25},
#   {"value": "social_media", "count": 18},
#   {"value": "fake_media", "count": 12},
#   {"value": "future_history_page", "count": 8},
#   {"value": null, "count": 3}
# ]

# Count items by favorable_future preference (sorted by most common)
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items/aggregate?field=favorable_future" \
  -H "Authorization: KEY123"

# Response:
# [
#   {"value": "prefer", "count": 30},
#   {"value": "prevent", "count": 15},
#   {"value": "mostly_prefer", "count": 10},
#   {"value": "mostly_prevent", "count": 5},
#   {"value": null, "count": 2}
# ]

# Count high plausibility items by favorable_future (with filters)
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items/aggregate?field=favorable_future&filters=plausibility>70" \
  -H "Authorization: KEY123"

# Response:
# [
#   {"value": "prefer", "count": 20},
#   {"value": "prevent", "count": 10},
#   {"value": "mostly_prefer", "count": 5}
# ]
```

---

## Support

For issues, questions, or feature requests, please contact the development team or refer to the project repository.

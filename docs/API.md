# Chronomaps Server API Documentation

## Overview

Chronomaps Server is a Firebase Cloud Functions-based backend for managing collaborative workspaces that collect and process future scenario screenshots. The system uses AI-powered analysis to extract structured information from screenshots and provides clustering and visualization capabilities.

### Technology Stack

- **Framework**: Python 3.12 with Flask
- **Cloud Platform**: Firebase Functions (Google Cloud Functions)
- **Database**: Firebase Firestore
- **Storage**: Firebase Storage
- **AI/ML**: OpenAI GPT-4.1, scikit-learn, NumPy
- **Deployment Region**: europe-west4

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

### Workspace Keys

Each workspace has three UUID4 keys:
- `admin` - Full control over workspace and all items
- `collaborate` - Add/edit items when collaboration is enabled
- `view` - Read-only access to workspace

---

## Core API Endpoints

Base URL: `https://<region>-<project>.cloudfunctions.net/chronomaps_api`

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
  "title": "Workspace Title",
  "description": "Workspace Description",
  "event_name": "Event Name",
  "default_moderation_level": 3,
  ...
}
```

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
POST /screenshot_handler?workspace=<id>&api_key=<key>&automatic=<bool>&item_id=<id>&item_key=<key>
```

**Authentication**: OPENAI_API_KEY and CHRONOMAPS_API_URL secrets required

**Content-Type**: multipart/form-data

**Query Parameters**:
- `workspace`: Workspace ID
- `api_key`: Workspace admin or collaborate key
- `automatic` (optional): Use automatic mode (true/false)
- `item_id` (optional): Update existing item
- `item_key` (optional): Item key for updates

**Request Body**: Image file

**Description**: Analyzes screenshot using GPT-4.1 Vision model to extract structured information including screenshot type, content, future scenario details, and more.

**Response**:
```json
{
  "item_id": "generated-or-provided-item-id",
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

**Description**: ML-powered screenshot clustering and visualization using t-SNE dimensionality reduction and agglomerative clustering. Generates map tiles (256x256px) for zoom levels and extracts cluster themes using GPT-4.1.

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
  }
}
```

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

    // Private Fields (require PRIVILEGE_PRIVATE_KEY or higher)
    "_private_email": "user@example.com",
    "_private_moderation": 3,
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

3. **`config/config`** - Global configuration with admin user list

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

- `OPENAI_API_KEY` - OpenAI API key for GPT-4 operations
- `CHRONOMAPS_API_URL` - Base URL for the Chronomaps API
- `SERVICE_ACCOUNT_JSON` - Firebase service account credentials
- `CONFIG__ITS_TIME` - Configuration for scheduled clustering job

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

### Filtering Items

```bash
# Get items with plausibility > 70, ordered by creation date
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items?filters=plausibility>70&order_by=-created_at" \
  -H "Authorization: KEY123"

# Get automatic items that are preferred futures
curl "https://region-project.cloudfunctions.net/chronomaps_api/abc123/items?filters=automatic==true|favorable_future==prefer" \
  -H "Authorization: KEY123"
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

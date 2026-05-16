import datetime
import json
import time
from firebase_admin import firestore, credentials
import flask
import uuid
import hashlib
import threading
import requests
import os
import re

from config import PRIVATE_KEY
from .resolve_firebase_user import require_firebase_auth

app = flask.Flask(__name__)


@app.before_request
def _resolve_db():
    db_id = flask.request.args.get('db')
    flask.g.db = firestore.client(database_id=db_id) if db_id else firestore.client()

# In-memory cache for /all-items
_all_items_cache = None  # dict with 'params' and 'items', or None

PRIVILEGE_ADMIN = 4
PRIVILEGE_PRIVATE_KEY = 3
PRIVILEGE_COLLABORATE = 2
PRIVILEGE_VIEW = 1
PRIVILEGE_PUBLIC = 0

# Helper functions for authentication and utility
def generate_keys():
    return {
        "admin": str(uuid.uuid4()),
        "collaborate": str(uuid.uuid4()),
        "view": str(uuid.uuid4())
    }

def authenticate(workspace, key, required_roles):
    config_ref = flask.g.db.collection(workspace).document(".config")
    config = config_ref.get().to_dict()
    if not config:
        flask.abort(404, "Workspace not found")
    if "admin" in required_roles and key == config["keys"]["admin"]:
        return PRIVILEGE_ADMIN
    if "collaborate" in required_roles and key == config["keys"]["collaborate"] and config["config"]["collaborate"]:
        return PRIVILEGE_COLLABORATE
    if "view" in required_roles:
        if key == config["keys"]["view"]:
            return PRIVILEGE_VIEW
        if config["config"]["public"]:
            return PRIVILEGE_PUBLIC
    flask.abort(403, "Unauthorized")

SALT = PRIVATE_KEY[:16].encode()
def calculate_author_id(email):
    return hashlib.sha256(email.encode() + SALT).hexdigest()

def get_active_temporary_collaboration(workspace):
    config_ref = flask.g.db.collection(workspace).document(".config")
    config = config_ref.get().to_dict()
    tc = config.get("temporary_collaboration") if config else None
    if tc and tc["expiry"] > time.time():
        return tc
    return None

def sanitize_metadata(metadata, exclude_private=True, exclude_embedding=False):
    ret = metadata.copy()
    if exclude_private:
        ret = {k: v for k, v in metadata.items() if not (k.startswith(PRIVATE_KEY) or k == 'embedding')}
    if exclude_embedding:
        ret = {k: v for k, v in ret.items() if k != 'embedding'}
    if 'author_id' not in metadata and '_private_email' in metadata and metadata['_private_email']:
        ret['author_id'] = calculate_author_id(metadata['_private_email'])
    return ret

def parse_filter_expression(filter_expr):
    """
    Parse a filter expression into (field, operator, value).
    Supports both formats:
    - "field op value" (with spaces)
    - "field==value" or "field>=value" (without spaces)
    """
    # Try regex pattern first to handle both with and without spaces
    # Pattern: field_name + operator + value
    # Operators: ==, !=, >=, <=, >, <
    pattern = r'^(.+?)\s*(==|!=|>=|<=|>|<)\s*(.+)$'
    match = re.match(pattern, filter_expr.strip())

    if match:
        field, op, value = match.groups()
        return field.strip(), op, value.strip()

    # Fallback to original whitespace-based split for backward compatibility
    parts = filter_expr.split(None, 2)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]

    raise ValueError(f"Invalid filter expression: {filter_expr}")

def apply_filters_in_memory(items, filters_str):
    """Apply filters to items in memory."""
    if not filters_str:
        return items

    filters = filters_str.split("|")
    for filter_expr in filters:
        filter_key, op, value = parse_filter_expression(filter_expr)
        try:
            value = json.loads(value)
        except:
            pass

        # Navigate nested keys (e.g., "metadata.field")
        def get_nested_value(item, key_path):
            keys = key_path.split('.')
            val = item
            for k in keys:
                if isinstance(val, dict):
                    val = val.get(k)
                else:
                    return None
            return val

        # Apply filter based on operator
        filtered_items = []
        for item in items:
            item_value = get_nested_value(item, filter_key)

            if op == "==":
                if item_value == value:
                    filtered_items.append(item)
            elif op == "!=":
                if item_value != value:
                    filtered_items.append(item)
            elif op == "<":
                if item_value is not None and item_value < value:
                    filtered_items.append(item)
            elif op == "<=":
                if item_value is not None and item_value <= value:
                    filtered_items.append(item)
            elif op == ">":
                if item_value is not None and item_value > value:
                    filtered_items.append(item)
            elif op == ">=":
                if item_value is not None and item_value >= value:
                    filtered_items.append(item)
            elif op == "in":
                if item_value is not None and item_value in value:
                    filtered_items.append(item)
            elif op == "array-contains":
                if isinstance(item_value, list) and value in item_value:
                    filtered_items.append(item)

        items = filtered_items

    return items

def sort_items_in_memory(items, order_by_str):
    """Sort items in memory."""
    if not order_by_str:
        order_by_str = "-created_at"

    reverse = False
    if order_by_str.startswith("-"):
        order_by_str = order_by_str[1:]
        reverse = True

    order_key = 'metadata.' + order_by_str

    def get_nested_value(item, key_path):
        keys = key_path.split('.')
        val = item
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k)
            else:
                return None
        return val

    # Sort with None values at the end, converting to string to handle mixed types
    def sort_key(x):
        val = get_nested_value(x, order_key)
        return (val is None, str(val) if val is not None else '')

    return sorted(items, key=sort_key, reverse=reverse)

def fetch_and_filter_items(workspace, filters=None, order_by=None, db_id=None):
    """
    Fetch items from workspace and apply filters/sorting.

    Returns tuple of (items, use_fallback, index_url).
    Tries Firestore queries first, falls back to in-memory if indexes are missing.
    """
    use_fallback = False
    index_url = None

    try:
        # Try using Firestore indexes first
        direction = firestore.Query.ASCENDING
        items_query = flask.g.db.collection(workspace)
        order_by_field = order_by
        if order_by_field is None:
            order_by_field = "-created_at"
        if order_by_field:
            if order_by_field.startswith("-"):
                order_by_field = order_by_field[1:]
                direction = firestore.Query.DESCENDING
            order_by_field = 'metadata.' + order_by_field
            items_query = items_query.order_by(order_by_field, direction=direction)
        if filters:
            filters_list = filters.split("|")
            for filter_expr in filters_list:
                filter_key, op, value = parse_filter_expression(filter_expr)
                try:
                    value = json.loads(value)
                except:
                    pass
                items_query = items_query.where(filter_key, op, value)
        items = items_query.stream()
        items = [dict(**doc.to_dict(), id=doc.id) for doc in items]
        items = [item for item in items if item['id'][0] != "."]

    except Exception as e:
        msg = str(e)
        if 'The query requires an index' in msg:
            # Fallback to in-memory filtering and sorting
            print(f"Index missing, falling back to in-memory processing: {msg}")
            use_fallback = True

            # Extract index URL for later creation
            if 'https://' in msg:
                index_url = 'https://' + msg.split('https://')[1].split(' ')[0]

            # Fetch all items without filtering/ordering
            items = flask.g.db.collection(workspace).stream()
            items = [dict(**doc.to_dict(), id=doc.id) for doc in items]
            items = [item for item in items if item['id'][0] != "."]

            # Apply filters in memory
            items = apply_filters_in_memory(items, filters)

            # Sort in memory
            items = sort_items_in_memory(items, order_by)

            # Trigger index creation asynchronously
            threading.Thread(
                target=create_firestore_index,
                args=(workspace, order_by, filters, db_id),
                daemon=True
            ).start()
        else:
            raise

    return items, use_fallback, index_url

def create_firestore_index(workspace, order_by_field, filters_str, database_id=None):
    """
    Create a Firestore index using the Admin API.
    This should be called asynchronously to avoid blocking the request.
    """
    try:
        # Get credentials from firebase_admin using the public API
        import firebase_admin
        try:
            app_instance = firebase_admin.get_app()
            cred = app_instance.credential
            access_token = cred.get_access_token().access_token
        except ValueError:
            print("Firebase app not initialized, cannot create index")
            return

        # Get project ID from Firebase app options or credential
        project_id = None
        if hasattr(app_instance.options, 'get'):
            project_id = app_instance.options.get('projectId')

        # Fallback to environment variables (automatically set in GCP Cloud Functions)
        if not project_id:
            project_id = os.environ.get('GOOGLE_CLOUD_PROJECT') or os.environ.get('GCP_PROJECT')

        # Try to get from credential
        if not project_id and hasattr(cred, 'project_id'):
            google_cred = cred.get_credential()
            if hasattr(google_cred, 'project_id'):
                project_id = google_cred.project_id

        if not project_id:
            print("Project ID not found. Please set GOOGLE_CLOUD_PROJECT environment variable.")
            return

        # Build the index definition
        fields = []

        # Add filter fields
        if filters_str:
            filters_list = filters_str.split("|")
            for filter_expr in filters_list:
                filter_key, op, _ = filter_expr.split(None, 2)
                # Firestore requires ASCENDING order for equality filters
                mode = "ASCENDING" if op == "==" else "ASCENDING"
                fields.append({
                    "fieldPath": filter_key,
                    "order": mode
                })

        # Add order_by field if it's different from filter fields
        if order_by_field:
            field_name = order_by_field
            direction = "ASCENDING"
            if field_name.startswith("-"):
                field_name = field_name[1:]
                direction = "DESCENDING"
            field_name = 'metadata.' + field_name

            # Check if this field is already in the filters
            if not any(f['fieldPath'] == field_name for f in fields):
                fields.append({
                    "fieldPath": field_name,
                    "order": direction
                })

        # Create the index
        index_definition = {
            "fields": fields,
            "queryScope": "COLLECTION"
        }

        db_segment = database_id or '(default)'
        url = f"https://firestore.googleapis.com/v1/projects/{project_id}/databases/{db_segment}/collectionGroups/{workspace}/indexes"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        }

        response = requests.post(url, json=index_definition, headers=headers)

        if response.status_code in [200, 201]:
            print(f"Index creation initiated for workspace {workspace}: {response.json()}")
        else:
            print(f"Failed to create index: {response.status_code} - {response.text}")

    except Exception as e:
        print(f"Error creating index: {e}")

# Endpoints
@app.get("/taxonomy")
def get_taxonomy():
    taxonomy_ref = flask.g.db.collection('chronomaps_global').document('taxonomy')
    taxonomy_doc = taxonomy_ref.get()
    if not taxonomy_doc.exists:
        return flask.jsonify({"error": "Taxonomy not yet generated"}), 404
    return flask.jsonify(taxonomy_doc.to_dict()), 200

@app.get("/config")
def get_db_config():
    doc = flask.g.db.collection('config').document('config').get()
    if not doc.exists:
        return flask.jsonify({"metadata": {}}), 200
    return flask.jsonify({"metadata": (doc.to_dict() or {}).get('metadata', {})}), 200

@app.get("/all-items")
@require_firebase_auth
def get_all_items():
    global _all_items_cache
    page = flask.request.args.get("page", 0, type=int)
    page_size = flask.request.args.get("page_size", 10, type=int)
    order_by = flask.request.args.get("order_by")
    filters = flask.request.args.get("filters", type=str)
    db_id = flask.request.args.get("db")

    cache_params = (db_id, order_by, filters)
    if _all_items_cache is not None and _all_items_cache['params'] == cache_params:
        all_items = _all_items_cache['items']
    else:
        all_items = []
        for collection in flask.g.db.collections():
            config_ref = collection.document('.config')
            config_doc = config_ref.get()
            if not config_doc.exists:
                continue

            workspace = collection.id
            items, _, _ = fetch_and_filter_items(workspace, filters, order_by, db_id=db_id)

            items_metadata = [
                sanitize_metadata(
                    dict(**item.get("metadata", {}), _id=item['id'], _workspace=workspace, _key=item.get('key', '')),
                    exclude_private=False, exclude_embedding=True
                )
                for item in items
            ]
            for item in items_metadata:
                item.pop('embedding', None)
            all_items.extend(items_metadata)

        # Re-sort combined results
        all_items = sort_items_in_memory(
            [{"metadata": item} for item in all_items],
            order_by or "-created_at"
        )
        all_items = [item["metadata"] for item in all_items]
        _all_items_cache = {'params': cache_params, 'items': all_items}

    # Paginate
    paginated = all_items[page * page_size:(page + 1) * page_size]
    return flask.jsonify(paginated), 200

@app.get("/")
@require_firebase_auth
def list_workspaces():
    print("Listing workspaces for user:", flask.g.firebase_user.get("email"))
    config_refs = [c.document('.config') for c in flask.g.db.collections()]
    configs = [
        dict(id=snap.reference.parent.id, **snap.to_dict())
        for snap in flask.g.db.get_all(config_refs)
        if snap.exists
    ]
    return {"workspaces": configs}, 200

@app.post("/")
@require_firebase_auth
def create_workspace():
    """Create a workspace. Pass `workspace_id` in the body to set it explicitly; otherwise a UUID4 is generated. Idempotent: re-creation with an existing ID returns the existing config."""
    print("Creating workspace for user:", flask.g.firebase_user.get("email"))
    body = flask.request.json or {}
    workspace_id = body.pop("workspace_id", None) or str(uuid.uuid4())
    metadata = body
    # If workspace already exists, return existing config (idempotent)
    existing = flask.g.db.collection(workspace_id).document(".config").get()
    if existing.exists:
        config = existing.to_dict()
        return {"workspace_id": workspace_id, "config": config}, 200
    keys = generate_keys()
    config = {
        "metadata": metadata,
        "keys": keys,
        "config": {"collaborate": False, "public": False}
    }
    flask.g.db.collection(workspace_id).document(".config").set(config)
    return {"workspace_id": workspace_id, "config": config}, 201

@app.post("/<workspace>")
def create_item(workspace):
    global _all_items_cache
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin", "collaborate"])
    metadata = flask.request.json
    item_id = str(uuid.uuid4())
    item_key = str(uuid.uuid4())
    item = {"key": item_key, "metadata": metadata}
    flask.g.db.collection(workspace).document(item_id).set(item)
    _all_items_cache = None
    return {"item_id": item_id, "item_key": item_key}, 201

@app.get("/<workspace>")
def get_workspace(workspace):
    key = flask.request.headers.get("Authorization")
    admin = authenticate(workspace, key, ["admin", "collaborate", "view"]) >= PRIVILEGE_ADMIN
    config_ref = flask.g.db.collection(workspace).document(".config")
    config = config_ref.get().to_dict()
    ret = config["metadata"]
    if admin:
        ret.update(config["config"])
    tc = config.get("temporary_collaboration")
    if tc:
        ttl = tc["expiry"] - time.time()
        if ttl > 0:
            ret["temporary_collaboration_ttl"] = ttl
    return ret, 200

@app.get("/<workspace>/items")
def get_items(workspace):
    auth_key = flask.request.headers.get("Authorization")
    privilege = authenticate(workspace, auth_key, ["admin", "collaborate", "view"])
    page = flask.request.args.get("page", 0, type=int)
    page_size = flask.request.args.get("page_size", 10, type=int)
    order_by = flask.request.args.get("order_by")
    filters = flask.request.args.get("filters", type=str)
    include_embedding = flask.request.args.get("include_embedding", "false").lower() == "true"

    # Add moderation filter for non-admin users
    if privilege < PRIVILEGE_ADMIN:
        moderation_filter = "metadata._private_moderation>=3"
        if filters:
            filters = f"{filters}|{moderation_filter}"
        else:
            filters = moderation_filter

    # Fetch and filter items
    items, use_fallback, index_url = fetch_and_filter_items(
        workspace, filters, order_by, db_id=flask.request.args.get("db")
    )

    # Sanitize metadata
    items_metadata = [
        sanitize_metadata(
            dict(**item.get("metadata", {}), _id=item['id'], **({"_key": item.get("key")} if privilege > PRIVILEGE_PRIVATE_KEY else {})),
            exclude_private=privilege < PRIVILEGE_PRIVATE_KEY, exclude_embedding=not include_embedding
        )
        for item in items
    ]

    # Paginate
    paginated_items = items_metadata[page * page_size:(page + 1) * page_size]

    # Add header to indicate fallback was used
    response = flask.jsonify(paginated_items)
    if use_fallback:
        response.headers['X-Fallback-Mode'] = 'true'
        if index_url:
            response.headers['X-Index-URL'] = index_url
    return response, 200

@app.get("/<workspace>/items/aggregate")
def aggregate_items(workspace):
    """
    Aggregate items by a field value.

    Query parameters:
    - field: The field name in metadata to aggregate by (required)
    - filters: Optional pipe-separated filters to apply before aggregation

    Returns:
    - A list of objects with 'value' and 'count' fields, sorted by count (descending)
    - Includes null for items without the field
    """
    auth_key = flask.request.headers.get("Authorization")
    authenticate(workspace, auth_key, ["admin", "collaborate", "view"])
    field = flask.request.args.get("field")
    filters = flask.request.args.get("filters", type=str)

    if not field:
        flask.abort(400, "Missing required parameter: field")

    # Fetch and filter items (no ordering needed for aggregation)
    items, use_fallback, index_url = fetch_and_filter_items(
        workspace, filters, order_by=None, db_id=flask.request.args.get("db")
    )

    # Count occurrences of each field value
    counts = {}
    for item in items:
        metadata = item.get("metadata", {})

        # Navigate nested keys (e.g., "nested.field")
        value = metadata
        for key in field.split('.'):
            if isinstance(value, dict):
                value = value.get(key)
            else:
                value = None
                break

        # Keep the original value for the response
        if value is None:
            value_key = None
        elif isinstance(value, (list, dict)):
            # For complex types, use JSON string as internal key for counting
            value_key = json.dumps(value, sort_keys=True)
        else:
            value_key = value

        counts[value_key] = counts.get(value_key, 0) + 1

    # Convert to list of objects and sort by count descending
    result = [
        {"value": value, "count": count}
        for value, count in counts.items()
    ]
    result.sort(key=lambda x: x["count"], reverse=True)

    # Add headers to indicate fallback was used
    response = flask.jsonify(result)
    if use_fallback:
        response.headers['X-Fallback-Mode'] = 'true'
        if index_url:
            response.headers['X-Index-URL'] = index_url
    return response, 200

@app.get("/<workspace>/<item_id>")
def get_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    privilege = authenticate(workspace, key, ["admin", "collaborate", "view"])
    item_ref = flask.g.db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if not item:
        flask.abort(404, "Item not found")
    if item_key:
        if not item or item["key"] != item_key:
            flask.abort(403, "Unauthorized")
        privilege = PRIVILEGE_PRIVATE_KEY
    return sanitize_metadata(item["metadata"], privilege < PRIVILEGE_PRIVATE_KEY), 200

@app.put("/<workspace>/<item_id>")
def update_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    temp_collab = None
    if not item_key:
        privilege = authenticate(workspace, key, ["admin", "collaborate"])
        if privilege < PRIVILEGE_ADMIN:
            tc = get_active_temporary_collaboration(workspace)
            if not tc:
                flask.abort(403, "Unauthorized")
            temp_collab = tc
    else:
        privilege = authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = flask.g.db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if not item:
        flask.abort(404, "Item not found")
    if item_key:
        if item["key"] != item_key:
            flask.abort(403, "Unauthorized")
        privilege = PRIVILEGE_PRIVATE_KEY
    metadata = flask.request.json
    if temp_collab:
        metadata = {k: v for k, v in metadata.items() if k in temp_collab["allowed_properties"]}
        if not metadata:
            flask.abort(400, "No allowed properties in request")
    metadata = sanitize_metadata(metadata, privilege < PRIVILEGE_PRIVATE_KEY)
    item["metadata"].update(metadata)
    item["metadata"]["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    item_ref.update({"metadata": item["metadata"]})
    global _all_items_cache
    _all_items_cache = None
    return item["metadata"], 200

@app.delete("/<workspace>/<item_id>")
def delete_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    if not item_key:
        authenticate(workspace, key, ["admin"])
    else:
        authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = flask.g.db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if not item:
        flask.abort(404, "Item not found")
    if item_key:
        if item["key"] != item_key:
            flask.abort(403, "Unauthorized")
    item_ref.delete()
    global _all_items_cache
    _all_items_cache = None
    return {"message": "Item deleted"}, 200

@app.put("/<workspace>")
def update_workspace(workspace):
    def is_it_true(value):
        return value.lower() == 'true'
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    metadata = flask.request.get_json(silent=True)
    public = flask.request.args.get("public", type=is_it_true)
    collaborate = flask.request.args.get("collaborate", type=is_it_true)
    updates = {}
    if metadata:
        updates["metadata"] = metadata
    if public is not None:
        updates["config.public"] = public
    if collaborate is not None:
        updates["config.collaborate"] = collaborate
    if not updates:
        return {"message": "No updates provided"}, 400
    flask.g.db.collection(workspace).document(".config").update(updates)
    return {"message": "Workspace updated", "updates": updates}, 200

@app.post("/<workspace>/temporary-collaboration")
def set_temporary_collaboration(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])

    time_param = flask.request.args.get("time", type=int)
    properties = flask.request.args.get("properties")

    if time_param is None:
        flask.abort(400, "Missing required parameter: time")

    config_ref = flask.g.db.collection(workspace).document(".config")

    if properties:
        expiry = time.time() + time_param
        allowed_properties = list(dict.fromkeys(p.strip() for p in properties.split(",") if p.strip()))
        if not allowed_properties:
            flask.abort(400, "No valid properties provided")
        config_ref.update({
            "temporary_collaboration": {
                "expiry": expiry,
                "allowed_properties": allowed_properties
            }
        })
    else:
        config = config_ref.get().to_dict()
        existing = config.get("temporary_collaboration") if config else None
        if not existing:
            flask.abort(400, "No existing temporary collaboration to adjust")
        expiry = existing["expiry"] + time_param
        allowed_properties = existing["allowed_properties"]
        config_ref.update({
            "temporary_collaboration.expiry": expiry
        })

    ttl = expiry - time.time()
    return {
        "expiry": expiry,
        "ttl": ttl,
        "allowed_properties": allowed_properties
    }, 200

@app.delete("/<workspace>/temporary-collaboration")
def delete_temporary_collaboration(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    config_ref = flask.g.db.collection(workspace).document(".config")
    config_ref.update({"temporary_collaboration": firestore.DELETE_FIELD})
    return "", 204

@app.delete("/<workspace>")
def delete_workspace(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    workspace_ref = flask.g.db.collection(workspace)
    docs = workspace_ref.stream()
    for doc in docs:
        doc.reference.delete()
    return {"message": "Workspace deleted"}, 200

@app.delete("/<workspace>/items")
def delete_items(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    items_ref = flask.g.db.collection(workspace)
    docs = items_ref.stream()
    for doc in docs:
        if doc.id[0] != ".":
            doc.reference.delete()
    return {"message": "Items deleted"}, 200

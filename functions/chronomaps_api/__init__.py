import json
from firebase_admin import firestore
import flask
import uuid
from itertools import islice
from config import PRIVATE_KEY
from .resolve_firebase_user import FireBaseUser

db = firestore.client()
app = flask.Flask(__name__)

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
    config_ref = db.collection(workspace).document(".config")
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

def sanitize_metadata(metadata, exclude_private=True):
    if exclude_private:
        return {k: v for k, v in metadata.items() if not k.startswith(PRIVATE_KEY)}
    return metadata

# Endpoints
@app.post("/")
def create_workspace(user: FireBaseUser):
    metadata = flask.request.json
    workspace_id = str(uuid.uuid4())
    keys = generate_keys()
    config = {
        "metadata": metadata,
        "keys": keys,
        "config": {"collaborate": False, "public": False}
    }
    db.collection(workspace_id).document(".config").set(config)
    return {"workspace_id": workspace_id, "config": config}, 201

@app.get("/")
def list_workspaces(user: FireBaseUser):
    configs = []
    for collection in db.collections():
        ref = collection.document('.config')
        if ref.get().exists:
            config = ref.get().to_dict()
            configs.append(dict(
                id=collection.id,
                **config
            ))
    return {"workspaces": configs}, 200

@app.post("/<workspace>")
def create_item(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin", "collaborate"])
    metadata = flask.request.json
    item_id = str(uuid.uuid4())
    item_key = str(uuid.uuid4())
    item = {"key": item_key, "metadata": metadata}
    db.collection(workspace).document(item_id).set(item)
    return {"item_id": item_id, "item_key": item_key}, 201

@app.get("/<workspace>")
def get_workspace(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin", "collaborate", "view"])
    config_ref = db.collection(workspace).document(".config")
    config = config_ref.get().to_dict()
    return config["metadata"], 200

@app.get("/<workspace>/items")
def get_items(workspace):
    key = flask.request.headers.get("Authorization")
    privilege = authenticate(workspace, key, ["admin", "collaborate", "view"])
    page = flask.request.args.get("page", 0, type=int)
    page_size = flask.request.args.get("page_size", 10, type=int)
    order_by = flask.request.args.get("order_by")
    filters = flask.request.args.get("filters", type=str)
    direction = firestore.Query.ASCENDING
    items = db.collection(workspace)
    if order_by is None:
        order_by = "-created_at"
    if order_by:
        if order_by.startswith("-"):
            order_by = order_by[1:]
            direction = firestore.Query.DESCENDING
        order_by = 'metadata.' + order_by
        items = items.order_by(order_by, direction=direction)
    if filters:
        filters = filters.split("|")
        for filter in filters:
            key, op, value = filter.split(None, 2)
            try:
                value = json.loads(value)
            except:
                pass
            items = items.where(key, op, value)
    items = items.stream()
    items = (dict(**doc.to_dict(), id=doc.id) for doc in items)
    try:
        items_metadata = (
            sanitize_metadata(
                dict(**item.get("metadata", {}), _id=item['id'], **({"_key": item.get("key")} if privilege > PRIVILEGE_PRIVATE_KEY else {})),
                exclude_private=privilege < PRIVILEGE_PRIVATE_KEY
            )
            for item in items
            if item['id'][0] != "."
        )
        paginated_items = list(islice(items_metadata, page * page_size, (page + 1) * page_size))
    except Exception as e:
        msg = str(e)
        if 'The query requires an index' in msg:
            msg = 'https://' + msg.split('https://')[1].split(' ')[0]
            return {'index-required': msg}, 412
    return paginated_items, 200

@app.get("/<workspace>/<item_id>")
def get_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    privilege = authenticate(workspace, key, ["admin", "collaborate", "view"])
    item_ref = db.collection(workspace).document(item_id)
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
    if not item_key:
        privilege = authenticate(workspace, key, ["admin"])
    else:
        privilege = authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if item_key:
        if not item or item["key"] != item_key:
            flask.abort(403, "Unauthorized")
        privilege = PRIVILEGE_PRIVATE_KEY
    metadata = flask.request.json
    metadata = sanitize_metadata(metadata, privilege < PRIVILEGE_PRIVATE_KEY)
    item["metadata"].update(metadata)
    item_ref.update({"metadata": item["metadata"]})
    return item["metadata"], 200

@app.delete("/<workspace>/<item_id>")
def delete_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    if not item_key:
        authenticate(workspace, key, ["admin"])
    else:
        authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if item_key:
        if not item or item["key"] != item_key:
            flask.abort(403, "Unauthorized")
    item_ref.delete()
    return {"message": "Item deleted"}, 200

@app.put("/<workspace>")
def update_workspace(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    metadata = flask.request.json
    public = flask.request.args.get("public", type=bool)
    collaborate = flask.request.args.get("collaborate", type=bool)
    updates = {"metadata": metadata}
    if public is not None:
        updates["config.public"] = public
    if collaborate is not None:
        updates["config.collaborate"] = collaborate
    db.collection(workspace).document(".config").update(updates)
    return {"message": "Workspace updated"}, 200

@app.delete("/<workspace>")
def delete_workspace(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    workspace_ref = db.collection(workspace)
    docs = workspace_ref.stream()
    for doc in docs:
        doc.reference.delete()
    return {"message": "Workspace deleted"}, 200

@app.delete("/<workspace>/items")
def delete_items(workspace):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin"])
    items_ref = db.collection(workspace)
    docs = items_ref.stream()
    for doc in docs:
        if doc.id[0] != ".":
            doc.reference.delete()
    return {"message": "Items deleted"}, 200

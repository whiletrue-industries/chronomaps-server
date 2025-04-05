from firebase_admin import firestore
import flask
import uuid
from itertools import islice

db = firestore.client()
app = flask.Flask(__name__)

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
        return True
    if "collaborate" in required_roles and key == config["keys"]["collaborate"] and config["config"]["collaborate"]:
        return True
    if "view" in required_roles and (key == config["keys"]["view"] or config["config"]["public"]):
        return True
    flask.abort(403, "Unauthorized")

# Endpoints
@app.post("/")
def create_workspace():
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
    authenticate(workspace, key, ["admin", "collaborate", "view"])
    page = flask.request.args.get("page", 0, type=int)
    items = db.collection(workspace).stream()
    items_metadata = (
        item.to_dict()["metadata"] for item in items if item.id != ".config"
    )
    paginated_items = list(islice(items_metadata, page * 10, (page + 1) * 10))
    return paginated_items, 200

@app.get("/<workspace>/<item_id>")
def get_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    authenticate(workspace, key, ["admin", "collaborate", "view"])
    item_ref = db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if not item:
        flask.abort(404, "Item not found")
    return item["metadata"], 200

@app.put("/<workspace>/<item_id>")
def update_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
    if not item or item["key"] != item_key:
        flask.abort(403, "Unauthorized")
    metadata = flask.request.json
    item["metadata"].update(metadata)
    item_ref.update({"metadata": item["metadata"]})
    return {"message": "Item updated"}, 200

@app.delete("/<workspace>/<item_id>")
def delete_item(workspace, item_id):
    key = flask.request.headers.get("Authorization")
    item_key = flask.request.args.get("item-key")
    authenticate(workspace, key, ["admin", "collaborate"])
    item_ref = db.collection(workspace).document(item_id)
    item = item_ref.get().to_dict()
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

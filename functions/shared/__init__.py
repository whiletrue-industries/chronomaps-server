from firebase_admin import firestore
from openai import OpenAI

EMBEDDING_DIMENSION = 3072
EMBEDDING_MODEL = 'text-embedding-3-large'


# The vocabulary the analysis prompt emits ('preferred'/'prevent'/'uncertain')
# plus the yes/no answers the app collects from people.
FAVORABLE_FUTURE_MARKERS = ('yes', 'no', 'prevent', 'prefer', 'uncertain')

DEFAULT_PLAUSIBILITY = 100


def resolve_favorable_future(item):
    """The item's favorable_future, falling back to the AI-inferred value.

    A person's answer wins over the analysis: `favorable_future` is what the
    app collected, `ai_favorable_future` is what the model inferred. Items
    analysed before the model was asked for it have only the latter (or
    neither). Returns '' rather than None so callers can do substring tests
    without guarding.
    """
    for key in ('favorable_future', 'ai_favorable_future'):
        value = item.get(key)
        if isinstance(value, str) and any(m in value for m in FAVORABLE_FUTURE_MARKERS):
            return value
    return ''


def resolve_plausibility(item, default=DEFAULT_PLAUSIBILITY):
    """The item's plausibility as an int, falling back to the AI-inferred value.

    Older items store the string 'None' here, so anything unparseable falls
    through to the next source and finally to `default`.
    """
    for key in ('plausibility', 'ai_plausibility'):
        value = item.get(key)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                continue
    return default


def use_item(item, favorable_future_required=True):
    if favorable_future_required and not resolve_favorable_future(item):
        return False
    if 'future_scenario_description' not in item or not item['future_scenario_description']:
        return False
    if item.get('created_at') is None:
        return False
    return True


def generate_embedding(description, openai_key):
    client = OpenAI(api_key=openai_key)
    result = client.embeddings.create(model=EMBEDDING_MODEL, input=description)
    return result.data[0].embedding


def ensure_embeddings(records, db, openai_key, workspace=None):
    for i, record in enumerate(records):
        if i % 100 == 0:
            yield dict(msg=f'Ensuring embedding {i}/{len(records)}...')
        if 'embedding' in record and record['embedding'] and len(record['embedding']) == EMBEDDING_DIMENSION:
            continue
        if 'future_scenario_description' not in record or not record['future_scenario_description']:
            continue
        embedding = generate_embedding(record['future_scenario_description'], openai_key)
        record['embedding'] = embedding
        _workspace = workspace or record.get('_workspace')
        item_id = record['_id']
        if _workspace and item_id:
            db.collection(_workspace).document(item_id).update({'metadata.embedding': embedding})


def load_all_workspaces_items(db, openai_key, favorable_future_required=True):
    records = []
    for collection in db.collections():
        config_ref = collection.document('.config')
        config_doc = config_ref.get()
        if not config_doc.exists:
            continue
        workspace = collection.id
        yield dict(msg=f'Loading items from {workspace}...')
        items_query = db.collection(workspace).order_by('metadata.created_at', direction=firestore.Query.DESCENDING)
        docs = items_query.stream()
        items = []
        for doc in docs:
            if doc.id.startswith('.'):
                continue
            data = doc.to_dict()
            metadata = data.get('metadata', {})
            item = dict(
                **metadata,
                _id=doc.id,
                _workspace=workspace,
            )
            items.append(item)
        yield dict(msg=f'Got {len(items)} items from {workspace}')
        yield from ensure_embeddings(items, db, openai_key, workspace=workspace)
        valid_items = [item for item in items if use_item(item, favorable_future_required=favorable_future_required) and
                       'embedding' in item and item['embedding'] and
                       len(item['embedding']) == EMBEDDING_DIMENSION]
        yield dict(msg=f'{len(valid_items)} valid items from {workspace}')
        records.extend(valid_items)
    yield dict(msg=f'Total: {len(records)} items across all workspaces', records=records)


def verify_firebase_admin(req):
    from firebase_admin.auth import verify_id_token
    authorization = req.headers.get('Authorization', '')
    if not authorization.startswith('Bearer '):
        return None
    token = authorization[7:]
    try:
        user = verify_id_token(token)
        if user is None:
            return None
        admins = db_get_admins()
        if user.get('email') not in admins:
            return None
        return user
    except Exception:
        return None


def db_get_admins():
    db = firestore.client()
    return db.collection('config').document('config').get().to_dict().get('admins', [])

from firebase_admin import initialize_app, firestore, credentials
from firebase_admin import initialize_app
from firebase_admin.credentials import Certificate
from firebase_functions.params import SecretParam
from firebase_functions import https_fn, options, scheduler_fn
from config import CONFIG__ITS_TIME, SERVICE_ACCOUNT_JSON
from io import BytesIO
import json
import time

# serviceAccount = SecretParam('SERVICE_ACCOUNT_KEY').value
# cred = credentials.Certificate(json.loads(serviceAccount)) if serviceAccount else None
if SERVICE_ACCOUNT_JSON:
    cert = Certificate(json.loads(SERVICE_ACCOUNT_JSON))
    initialize_app(credential=cert)
else:
    print('No service account provided, using default credentials')
    initialize_app()

from chronomaps_api import app as chronomaps_api_app
from screenshot_handler import screenshot_handler as screenshot_handler_fn
from screenshot_handler import replace_item_image as replace_item_image_fn
from screenshot_handler import reanalyze_item as reanalyze_item_fn
from item_ingress_agent import item_ingress_agent as item_ingress_agent_fn
from cluster_screenshots import cluster_screenshots_all as cluster_screenshots_fn
from enhance_image import enhance_image as enhance_image_fn
from enhance_image import enhance_all_images as enhance_all_images_fn
from complete_flow import complete_flow as complete_flow_fn
from taxonomy import build_taxonomy as build_taxonomy_fn
from shared import verify_firebase_admin

CORS = options.CorsOptions(cors_origins="*", cors_methods=["get", "post"])

# Expose Flask app as a single Cloud Function
@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post", "get", "put", "delete"]), secrets=['SERVICE_ACCOUNT_JSON'], memory=options.MemoryOption.GB_1)
def chronomaps_api(req: https_fn.Request) -> https_fn.Response:
    # Pre-read body and cache it on the original request, then provide a fresh
    # stream to the nested Flask context. This prevents the functions-framework's
    # read_request after_request hook from crashing with ClientDisconnected when
    # it tries to read an already-consumed wsgi.input stream.
    body = req.get_data()
    environ = req.environ.copy()
    environ['wsgi.input'] = BytesIO(body)
    with chronomaps_api_app.request_context(environ):
        return chronomaps_api_app.full_dispatch_request()
    
@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['OPENAI_API_KEY', 'CHRONOMAPS_API_URL'], memory=options.MemoryOption.MB_512)
def screenshot_handler(req: https_fn.Request) -> https_fn.Response:
    # Get the request data
    # Workspace and api_key from query parameters:
    workspace = req.args.get('workspace')
    api_key = req.args.get('api_key')
    if not workspace or not api_key:
        return https_fn.Response("Missing workspace or api_key", status=400)
    # Image bytes from attachment:
    image_file = req.files.get('image')
    if not image_file:
        return https_fn.Response("Missing image file", status=400)
    image_bytes = image_file.read()
    content_type = image_file.mimetype
    print(f"Content type: {content_type}")
    automatic = req.args.get('automatic', 'false').lower() == 'true'

    return screenshot_handler_fn(image_bytes=image_bytes, workspace=workspace, api_key=api_key, automatic=automatic, image_content_type=content_type)

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL'], memory=options.MemoryOption.MB_512)
def replace_image(req: https_fn.Request) -> https_fn.Response:
    workspace = req.args.get('workspace')
    api_key = req.args.get('api_key')
    item_id = req.args.get('item_id')
    item_key = req.args.get('item_key')
    if not workspace or not api_key or not item_id or not item_key:
        return https_fn.Response("Missing workspace, api_key, item_id or item_key", status=400)
    image_file = req.files.get('image')
    if not image_file:
        return https_fn.Response("Missing image file", status=400)
    image_bytes = image_file.read()
    content_type = image_file.mimetype
    return replace_item_image_fn(image_bytes=image_bytes, image_content_type=content_type, workspace=workspace, item_id=item_id, item_key=item_key, api_key=api_key)

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['OPENAI_API_KEY', 'CHRONOMAPS_API_URL'], memory=options.MemoryOption.MB_512)
def reanalyze_item(req: https_fn.Request) -> https_fn.Response:
    workspace = req.args.get('workspace')
    api_key = req.args.get('api_key')
    item_id = req.args.get('item_id')
    item_key = req.args.get('item_key')
    if not workspace or not api_key or not item_id or not item_key:
        return https_fn.Response("Missing workspace, api_key, item_id or item_key", status=400)
    automatic = req.args.get('automatic', 'false').lower() == 'true'
    return reanalyze_item_fn(workspace=workspace, item_id=item_id, item_key=item_key, api_key=api_key, automatic=automatic)

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL'], memory=options.MemoryOption.MB_512)
def complete_flow(req: https_fn.Request) -> https_fn.Response:
    # Get the request data
    # Workspace and api_key from query parameters:
    workspace = req.args.get('workspace')
    api_key = req.args.get('api_key')
    if not workspace or not api_key:
        return https_fn.Response("Missing workspace or api_key", status=400)

    item_id = req.args.get('item_id')
    item_key = req.args.get('item_key')

    locale = req.args.get('locale')
    workshop_flow = req.args.get('workshop') == 'true'

    properties = req.get_json(silent=True)
    return complete_flow_fn(workspace_id=workspace, item_id=item_id, item_key=item_key, api_key=api_key, properties=properties, locale=locale, workshop_flow=workshop_flow)

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL', 'OPENAI_API_KEY'], memory=options.MemoryOption.MB_512)
def item_ingress_agent(req: https_fn.Request) -> https_fn.Response:
    # Get the request data
    # Workspace and api_key from query parameters:
    workspace = req.args.get('workspace')
    api_key = req.args.get('api_key')
    item_id = req.args.get('item_id')
    item_key = req.args.get('item_key')
    if not workspace or not api_key or not item_id or not item_key:
        return https_fn.Response("Missing workspace, api_key, item_id or item_key", status=400)
    # Message from request body:
    message = req.args.get('message')
    stream = req.args.get('stream') != 'false'
    # Call the item ingress agent function
    if stream:
        def generate():
            for bit in item_ingress_agent_fn(workspace=workspace, item_id=item_id, api_key=api_key, item_key=item_key, message=message):
                if bit.get('event') != 'thread.message.delta' and bit.get('kind') != 'text':
                    print(json.dumps(bit, ensure_ascii=False))
                yield f"data: {json.dumps(bit, ensure_ascii=False)}\n\n"
        return https_fn.Response(generate(), status=200, mimetype='text/event-stream')
    else:
        # Call the function and get the result
        for bit in item_ingress_agent_fn(workspace=workspace, item_id=item_id, api_key=api_key, item_key=item_key, message=message):
            print(json.dumps(bit, ensure_ascii=False))
        # Return the result as a JSON response
        return dict(status='ok')

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['SERVICE_ACCOUNT_JSON'], memory=options.MemoryOption.MB_512)
def enhance_image(req: https_fn.Request) -> https_fn.Response:
    screenshot_path = req.args.get('path')
    screenshot_url = req.args.get('url')
    side = req.args.get('side', 1000, type=int)
    thumbnail_size = req.args.get('thumbnail_size', 200, type=int)
    force = req.args.get('force', 'false').lower() == 'true'
    if not screenshot_path and not screenshot_url:
        return https_fn.Response("Missing path or url parameter", status=400)
    result = enhance_image_fn(screenshot_path=screenshot_path, screenshot_url=screenshot_url, side=side, thumbnail_size=thumbnail_size, force=force)
    if isinstance(result, tuple):
        return json.dumps(result[0]), result[1]
    return json.dumps(result)

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL', 'OPENAI_API_KEY'], memory=options.MemoryOption.GB_8)
def cluster_screenshots(req: https_fn.Request) -> https_fn.Response:
    # Get the request data
    # Workspace and api_key from query parameters:
    config = req.args.get('config')
    tag = req.args.get('tag')
    no_title = req.args.get('no_title', 'false').lower() == 'true'
    add_title = not no_title
    start = time.time()
    
    def generate():
        for bit in cluster_screenshots_fn(config, tag=tag, add_title=add_title):
            delta = int(time.time() - start)
            bit = [delta, bit]
            yield f"data: {json.dumps(bit, ensure_ascii=False)}\n\n"
    return https_fn.Response(generate(), status=200, mimetype='text/event-stream')


@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['OPENAI_API_KEY', 'SERVICE_ACCOUNT_JSON'], memory=options.MemoryOption.GB_8, timeout_sec=1800)
def build_taxonomy(req: https_fn.Request) -> https_fn.Response:
    user = verify_firebase_admin(req)
    if not user:
        return https_fn.Response("Unauthorized", status=401)
    similarity_threshold = req.args.get('similarity_threshold', 0.35, type=float)
    max_tags = req.args.get('max_tags', 3, type=int)
    redesign = req.args.get('redesign', 'false').lower() == 'true'
    start = time.time()

    def generate():
        for bit in build_taxonomy_fn(similarity_threshold=similarity_threshold, max_tags=max_tags, redesign=redesign):
            delta = int(time.time() - start)
            yield f"data: {json.dumps([delta, bit], ensure_ascii=False)}\n\n"
    return https_fn.Response(generate(), status=200, mimetype='text/event-stream')

@scheduler_fn.on_schedule(region='europe-west1', schedule="every day 03:00", secrets=['SERVICE_ACCOUNT_JSON'], memory=options.MemoryOption.GB_1, timeout_sec=540)
def enhance_all_daily(event: scheduler_fn.ScheduledEvent) -> None:
    print("STARTING daily enhancement backfill")
    for bit in enhance_all_images_fn():
        print(json.dumps(bit, ensure_ascii=False))

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['SERVICE_ACCOUNT_JSON'], memory=options.MemoryOption.GB_1, timeout_sec=540)
def enhance_all(req: https_fn.Request) -> https_fn.Response:
    side = req.args.get('side', 1000, type=int)
    thumbnail_size = req.args.get('thumbnail_size', 200, type=int)
    start = time.time()
    def generate():
        for bit in enhance_all_images_fn(side=side, thumbnail_size=thumbnail_size):
            delta = int(time.time() - start)
            yield f"data: {json.dumps([delta, bit], ensure_ascii=False)}\n\n"
    return https_fn.Response(generate(), status=200, mimetype='text/event-stream')

@scheduler_fn.on_schedule(region='europe-west1', schedule="every 15 minutes", secrets=['CHRONOMAPS_API_URL', 'OPENAI_API_KEY', 'CONFIG__ITS_TIME'], memory=options.MemoryOption.GB_8)
def cluster_its_time(event: scheduler_fn.ScheduledEvent) -> None:
    config_tags_tuples = []
    print("STARTING clustering all workspaces")
    config = CONFIG__ITS_TIME
    if config:
        config_tags_tuples.append((config, 'jma25'))

    def generate():
        print(f"Clustering screenshots with config: {config_tags_tuples}")
        for bit in cluster_screenshots_fn(config_tags_tuples, add_title=False, if_changed=True):
            print(json.dumps(bit, ensure_ascii=False) + '\n')
            yield f"{json.dumps(bit, ensure_ascii=False)}\n\n"
    response = '\n'.join(generate())
    return https_fn.Response(response, status=200, mimetype='plain/text')


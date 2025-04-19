from firebase_admin import initialize_app, firestore, credentials
from firebase_functions.params import SecretParam
from firebase_functions import https_fn, options
import json
import time 

# serviceAccount = SecretParam('SERVICE_ACCOUNT_KEY').value
# cred = credentials.Certificate(json.loads(serviceAccount)) if serviceAccount else None
initialize_app()

from chronomaps_api import app as chronomaps_api_app
from screenshot_handler import screenshot_handler as screenshot_handler_fn
from item_ingress_agent import item_ingress_agent as item_ingress_agent_fn
from cluster_screenshots import cluster_screenshots as cluster_screenshots_fn

CORS = options.CorsOptions(cors_origins="*", cors_methods=["get", "post"])

# Expose Flask app as a single Cloud Function
@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post", "get", "put", "delete"]), memory=options.MemoryOption.MB_512)
def chronomaps_api(req: https_fn.Request) -> https_fn.Response:
    with chronomaps_api_app.request_context(req.environ):
        return chronomaps_api_app.full_dispatch_request()
    
@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['OPENAI_API_KEY', 'CHRONOMAPS_API_URL'])
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

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL', 'OPENAI_API_KEY'])
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
    # Call the item ingress agent function
    def generate():
        for bit in item_ingress_agent_fn(workspace=workspace, item_id=item_id, api_key=api_key, item_key=item_key, message=message):
            yield f"data: {json.dumps(bit, ensure_ascii=False)}\n\n"
    return https_fn.Response(generate(), status=200, mimetype='text/event-stream')

@https_fn.on_request(region='europe-west4', cors=options.CorsOptions(cors_origins="*", cors_methods=["post"]), secrets=['CHRONOMAPS_API_URL'], memory=options.MemoryOption.GB_32)
def cluster_screenshots(req: https_fn.Request) -> https_fn.Response:
    # Get the request data
    # Workspace and api_key from query parameters:
    config = req.args.get('config')
    tag = req.args.get('tag')
    start = time.time()
    def generate():
        for bit in cluster_screenshots_fn(config, tag=tag):
            delta = int(time.time() - start)
            bit = [delta, bit]
            yield f"data: {json.dumps(bit, ensure_ascii=False)}\n\n"
    return https_fn.Response(generate(), status=200, mimetype='text/event-stream')

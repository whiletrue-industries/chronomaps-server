from firebase_admin import initialize_app, firestore, credentials
from firebase_functions.params import SecretParam
from firebase_functions import https_fn
from chronomaps_api import app as chronomaps_api_app
from screenshot_handler import screenshot_handler as screenshot_handler_fn
import json

serviceAccount = SecretParam('SERVICE_ACCOUNT_KEY').value
cred = credentials.Certificate(json.loads(serviceAccount)) if serviceAccount else None
initialize_app(cred)

# Expose Flask app as a single Cloud Function
@https_fn.on_request(region='europe-west4')
def chronomaps_api(req: https_fn.Request) -> https_fn.Response:
    with chronomaps_api_app.request_context(req.environ):
        return chronomaps_api_app.full_dispatch_request()
    
@https_fn.on_request(region='europe-west4', secrets=['OPENAI_API_KEY', 'CHRONOMAPS_API_URL'])
def screenshot_handler(req: https_fn.Request) -> https_fn.Response:
    with screenshot_handler_fn.request_context(req.environ):
        # Get the request data
        # Workspace and api_key from query parameters:
        workspace = req.args.get('workspace')
        api_key = req.args.get('api_key')
        # Image bytes from attachment:
        image_bytes = req.files.get('image').read()
        return screenshot_handler_fn(image_bytes=image_bytes, workspace=workspace, api_key=api_key)
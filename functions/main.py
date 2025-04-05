from firebase_functions import https_fn
from .chronomaps_api import app as chronomaps_api_app

# Expose Flask app as a single Cloud Function
@https_fn.on_request(region='europe-west4', secrets=['SERVICE_ACCOUNT_KEY'])
def chronomaps_api(req: https_fn.Request) -> https_fn.Response:
    with chronomaps_api_app.request_context(req.environ):
        return chronomaps_api_app.full_dispatch_request()
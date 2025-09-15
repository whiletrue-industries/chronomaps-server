from firebase_functions.params import SecretParam

OPENAI_KEY = SecretParam('OPENAI_API_KEY').value.strip()
CHRONOMAPS_API_URL = SecretParam('CHRONOMAPS_API_URL').value.strip()
CONFIG__ITS_TIME = SecretParam('CONFIG__ITS_TIME').value.strip()
SERVICE_ACCOUNT_JSON = SecretParam('SERVICE_ACCOUNT_JSON').value.strip()
BUCKET_NAME = 'chronomaps3-eu'
PRIVATE_KEY = '_private_'

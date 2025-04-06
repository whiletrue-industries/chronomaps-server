from firebase_functions.params import SecretParam

API_KEY = SecretParam('OPENAI_API_KEY').value.strip()
CHRONOMAPS_API_URL = SecretParam('CHRONOMAPS_API_URL').value.strip()

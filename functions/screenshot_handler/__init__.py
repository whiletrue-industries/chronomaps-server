from firebase_admin import firestore
from openai import OpenAI
from firebase_admin import storage
from firebase_functions.params import SecretParam
import base64
import json
import requests

# Use key, instructions, and filename to generate a structured response from openai api
API_KEY = SecretParam('OPENAI_API_KEY').value.strip()
INSTRUCTIONS = open('SCREENSHOT_DESCRIBER_PROMPT.md').read().strip()
CHRONOMAPS_API_URL = SecretParam('CHRONOMAPS_API_URL').value.strip()
client = OpenAI(api_key=API_KEY)

bucket = storage.bucket()

def encode_image(image_bytes):
    return base64.b64encode(image_bytes).decode("utf-8")

def screenshot_handler(image_bytes, workspace, api_key):
    base64_image = encode_image(image_bytes)

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    { "type": "text", "text": INSTRUCTIONS },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                        },
                    },
                ],
            }
        ],
    )

    content = completion.choices[0].message.content
    content = content.split('{', 1)[1]
    content = content.rsplit('}', 1)[0]
    content = '{' + content + '}'
    content = json.loads(content)

    record = dict(
        screenshot_type=content['screenshot_type'],
        transition_bar_event=content['transition_bar_transition_event'],
        transition_bar_position=content['transition_bar_before_during_after'],
        transition_bar_certainty=content['transition_bar_certainty'],
        content=content['content'],
        content_certainty=content['certainty'],
        future_scenario_tagline=content['future_scenario_tagline'],
        future_scenario_description=content['future_scenario_description'],
        future_scenario_topics=content['future_scenario_topics'],
    )

    # Create new item in Chronomaps API
    response = requests.post(f'{CHRONOMAPS_API_URL}/{workspace}', json=record, headers={'Authorization': api_key})
    response.raise_for_status()
    item_data = response.json()
    item_id = item_data['item_id']

    # Save the image to the firebase storage 
    blob = bucket.blob(f'{workspace}/{item_id}/screenshot.jpg')
    blob.upload_from_string(image_bytes, content_type='image/jpeg')
    blob.make_public()
    record['screenshot_url'] = blob.public_url
    record['item_id'] = item_id
    record['item_key'] = item_data['item_key']
    
    return record

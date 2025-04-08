import datetime
from firebase_admin import firestore
from openai import OpenAI
from firebase_admin import storage
from firebase_functions.params import SecretParam
from pathlib import Path
from config import API_KEY, CHRONOMAPS_API_URL
import os
import base64
import json
import requests

# Use key, instructions, and filename to generate a structured response from openai api
INSTRUCTIONS = Path(__file__).with_name('SCREENSHOT_DESCRIBER_PROMPT.md').read_text().strip()
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
        response_format={
            "type": 'json_object'
        }
    )

    content = completion.choices[0].message.content
    if not content:
        print('COMPLETION:', completion.choices[0].message.to_dict())
        return dict(error="No content returned from OpenAI"), 500
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
        content_certainty=content['content_certainty'],
        future_scenario_tagline=content['future_scenario_tagline'],
        future_scenario_description=content['future_scenario_description'],
        future_scenario_topics=content['future_scenario_topics'],
        detected_language=content['detected_language'],
        created_at=datetime.datetime.now(datetime.timezone.utc).isoformat()
    )

    # Create new item in Chronomaps API
    url = os.path.join(CHRONOMAPS_API_URL, workspace)
    response = requests.post(url, json=record, headers={'Authorization': api_key})
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized for new items with this key"), 403
    if response.status_code == 404:
        return dict(error=f"Workspace {workspace} not found"), 404
    response.raise_for_status()
    item_data = response.json()
    item_id = item_data['item_id']

    # Save the image to the firebase storage 
    blob = bucket.blob(f'{workspace}/{item_id}/screenshot.jpg')
    blob.upload_from_string(image_bytes, content_type='image/jpeg')
    blob.make_public()

    url = os.path.join(url, item_id)
    record = {'screenshot_url': blob.public_url}
    params = dict(item_key=item_data['item_key'])
    response = requests.post(url, json=record, headers={'Authorization': api_key}, params=params)

    record['item_id'] = item_id
    record['item_key'] = item_data['item_key']
    
    return record

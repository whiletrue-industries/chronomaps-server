import datetime
from firebase_admin import firestore
from openai import OpenAI
from firebase_admin import storage
from firebase_functions.params import SecretParam
from pathlib import Path
from config import OPENAI_KEY, CHRONOMAPS_API_URL, BUCKET_NAME, PRIVATE_KEY
import os
import base64
import json
import requests

# Use key, instructions, and filename to generate a structured response from openai api
INSTRUCTIONS = Path(__file__).with_name('SCREENSHOT_DESCRIBER_PROMPT.md').read_text().strip()
AUTOMATIC_INSTRUCTIONS = Path(__file__).with_name('AUTOMATIC_SCREENSHOT_DESCRIBER_PROMPT.md').read_text().strip()
client = OpenAI(api_key=OPENAI_KEY)

bucket = storage.bucket(name=BUCKET_NAME)

def encode_image(image_bytes):
    return base64.b64encode(image_bytes).decode("utf-8")

def screenshot_handler(image_bytes, workspace, api_key, automatic=False, image_content_type='image/jpeg', item_id=None, item_key=None):
    base64_image = encode_image(image_bytes)
    prompt = INSTRUCTIONS if not automatic else AUTOMATIC_INSTRUCTIONS

    url = os.path.join(CHRONOMAPS_API_URL, workspace)
    item_metadata = {}
    if item_id and item_key:
        params = {'item-key': item_key}
        item_url = os.path.join(url, item_id)
        response = requests.get(item_url, headers={'Authorization': api_key}, params=params)
        if response.status_code == 403:
            return dict(error=f"Workspace {workspace} and {item_id} not authorized for update"), 403
        if response.status_code == 404:
            return dict(error=f"Workspace {workspace} and {item_id} not found"), 404
        response.raise_for_status()
        item_metadata = response.json()
        if automatic:
            prompt = AUTOMATIC_INSTRUCTIONS + "\n\nProvided item metadata - consider it absolute truth:\n" + json.dumps(item_metadata.get('metadata', {}), indent=2)

    completion = client.chat.completions.create(
        model="gpt-4.1",
        messages=[
            {
                "role": "user",
                "content": [
                    { "type": "text", "text": prompt },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_content_type};base64,{base64_image}",
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
        record = dict(
            content="Couldn't understand anything from the screenshot",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            automatic=automatic,
        )
    else:
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
            content_title=content['content_title'],
            content_certainty=content['content_certainty'],
            future_scenario_tagline=content['future_scenario_tagline'],
            future_scenario_description=content['future_scenario_description'],
            future_scenario_topics=content['future_scenario_topics'],
            detected_language=content['detected_language'],
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            automatic=automatic,
            plausibility=content.get('plausibility'),
            favorable_future=content.get('favorable_future'),
        )

    # Read workspace config
    response = requests.get(url, headers={'Authorization': api_key})
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized for access with this key"), 403
    if response.status_code == 404:
        return dict(error=f"Workspace {workspace} not found"), 404
    response.raise_for_status()
    moderation = response.json().get('default_moderation_level')
    record[f'{PRIVATE_KEY}moderation'] = moderation or 3   # Can show, not moderated

    if item_id and item_key:
        params = {'item-key': item_key}
        item_url = os.path.join(url, item_id)
        response = requests.put(item_url, json=record, headers={'Authorization': api_key}, params=params)
        if response.status_code == 403:
            return dict(error=f"Workspace {workspace} and {item_id} not authorized for update"), 403
        if response.status_code == 404:
            return dict(error=f"Workspace {workspace} and {item_id} not found"), 404
        response.raise_for_status()
    else:
        # Create new item in Chronomaps API
        response = requests.post(url, json=record, headers={'Authorization': api_key})
        if response.status_code == 403:
            return dict(error=f"Workspace {workspace} not authorized for new items with this key"), 403
        if response.status_code == 404:
            return dict(error=f"Workspace {workspace} not found"), 404
        response.raise_for_status()
        item_data = response.json()
        item_id = item_data['item_id']
        item_key = item_data['item_key']
        item_url = os.path.join(url, item_id)

    # Save the image to the firebase storage 
    suffix = image_content_type.split('/')[1]
    blob = bucket.blob(f'{workspace}/{item_id}/screenshot.{suffix}')
    blob.upload_from_string(image_bytes, content_type=image_content_type)
    blob.make_public()

    record_ = {'screenshot_url': blob.public_url}
    params = {'item-key': item_key}
    response = requests.put(url, json=record_, headers={'Authorization': api_key}, params=params)
    print('RESPONSE:', url, record_, params, response.status_code, response.text)
    record['screenshot_url'] = blob.public_url

    record_['item_id'] = item_id
    record_['item_key'] = item_key
    record_['automatic'] = automatic
    record_['metadata'] = record

    return record_

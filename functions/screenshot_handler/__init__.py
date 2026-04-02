import datetime
from openai import OpenAI
from firebase_admin import storage
from pathlib import Path
from config import OPENAI_KEY, CHRONOMAPS_API_URL, BUCKET_NAME, PRIVATE_KEY
from enhance_image import enhance_image as enhance_image_fn
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


def _api_url(workspace, item_id=None):
    """Build the chronomaps API URL for a workspace/item."""
    url = os.path.join(CHRONOMAPS_API_URL, workspace)
    if item_id:
        url = os.path.join(url, item_id)
    return url


def _check_response(response, workspace, item_id=None, action="access"):
    """Check API response for common errors. Returns (error_dict, status_code) or None if ok."""
    target = f"Workspace {workspace}" + (f" and {item_id}" if item_id else "")
    if response.status_code == 403:
        return dict(error=f"{target} not authorized for {action}"), 403
    if response.status_code == 404:
        return dict(error=f"{target} not found"), 404
    response.raise_for_status()
    return None


def fetch_item(workspace, item_id, item_key, api_key):
    """Fetch existing item metadata from the chronomaps API.

    Returns (item_data, None) on success or (error_dict, status_code) on failure.
    """
    url = _api_url(workspace, item_id)
    response = requests.get(url, headers={'Authorization': api_key}, params={'item-key': item_key})
    err = _check_response(response, workspace, item_id, "access")
    if err:
        return err
    return response.json(), None


def get_workspace_moderation(workspace, api_key):
    """Fetch workspace config and return the default moderation level.

    Returns (moderation_level, None) on success or (error_dict, status_code) on failure.
    """
    url = _api_url(workspace)
    response = requests.get(url, headers={'Authorization': api_key})
    err = _check_response(response, workspace, action="access with this key")
    if err:
        return err
    moderation = response.json().get('default_moderation_level')
    return moderation or 3, None


def analyze_image(image_bytes, image_content_type, automatic=False, existing_metadata=None):
    """Run GPT-4 Vision analysis on image bytes.

    Args:
        image_bytes: Raw image bytes.
        image_content_type: MIME type of the image.
        automatic: Use automatic analysis prompt.
        existing_metadata: If provided, appended to automatic prompt as context.

    Returns an analysis record dict.
    """
    base64_image = encode_image(image_bytes)
    prompt = AUTOMATIC_INSTRUCTIONS if automatic else INSTRUCTIONS

    if automatic and existing_metadata:
        prompt = AUTOMATIC_INSTRUCTIONS + "\n\nProvided item metadata - consider it absolute truth:\n" + json.dumps(existing_metadata, indent=2)

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
        return dict(
            content="Couldn't understand anything from the screenshot",
            created_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            automatic=automatic,
        )

    content = content.split('{', 1)[1]
    content = content.rsplit('}', 1)[0]
    content = '{' + content + '}'
    content = json.loads(content)

    return dict(
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
        updated_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        automatic=automatic,
        plausibility=content.get('plausibility'),
        favorable_future=content.get('favorable_future'),
    )


def upload_image(image_bytes, image_content_type, workspace, item_id):
    """Upload image to Firebase Storage and make it public.

    Returns the public URL of the uploaded image.
    """
    suffix = image_content_type.split('/')[1]
    blob = bucket.blob(f'{workspace}/{item_id}/screenshot.{suffix}')
    blob.upload_from_string(image_bytes, content_type=image_content_type)
    blob.make_public()
    return blob.public_url


def update_item(workspace, item_id, item_key, api_key, record):
    """Update an existing item's metadata via the chronomaps API.

    Returns None on success or (error_dict, status_code) on failure.
    """
    url = _api_url(workspace, item_id)
    response = requests.put(url, json=record, headers={'Authorization': api_key}, params={'item-key': item_key})
    return _check_response(response, workspace, item_id, "update")


def create_item(workspace, api_key, record):
    """Create a new item via the chronomaps API.

    Returns (item_data, None) on success or (error_dict, status_code) on failure.
    item_data contains 'item_id' and 'item_key'.
    """
    url = _api_url(workspace)
    response = requests.post(url, json=record, headers={'Authorization': api_key})
    err = _check_response(response, workspace, action="new items with this key")
    if err:
        return err
    return response.json(), None


def download_item_image(workspace, item_id):
    """Download an item's image from Firebase Storage.

    Returns (image_bytes, content_type) or (None, None) if not found.
    """
    prefix = f'{workspace}/{item_id}/screenshot.'
    blobs = list(bucket.list_blobs(prefix=prefix))
    if not blobs:
        return None, None
    blob = blobs[0]
    image_bytes = blob.download_as_bytes()
    content_type = blob.content_type or 'image/jpeg'
    return image_bytes, content_type


def screenshot_handler(image_bytes, workspace, api_key, automatic=False, image_content_type='image/jpeg'):
    """Full flow: analyze image, create new item, upload image."""
    record = analyze_image(image_bytes, image_content_type, automatic=automatic)

    moderation_result = get_workspace_moderation(workspace, api_key)
    if isinstance(moderation_result[0], dict) and 'error' in moderation_result[0]:
        return moderation_result
    record[f'{PRIVATE_KEY}moderation'] = moderation_result[0]

    result = create_item(workspace, api_key, record)
    if result[1] is not None:
        return result
    item_data = result[0]
    item_id = item_data['item_id']
    item_key = item_data['item_key']

    screenshot_url = upload_image(image_bytes, image_content_type, workspace, item_id)

    # Pre-generate enhanced version for faster TSNE processing later
    enhanced_result = enhance_image_fn(screenshot_url=screenshot_url)
    if isinstance(enhanced_result, tuple):
        print('WARNING: failed to enhance image:', enhanced_result[0])

    record_ = {'screenshot_url': screenshot_url}
    err = update_item(workspace, item_id, item_key, api_key, record_)
    if err:
        print('WARNING: failed to store screenshot_url:', err)
    record['screenshot_url'] = screenshot_url

    record_['item_id'] = item_id
    record_['item_key'] = item_key
    record_['automatic'] = automatic
    record_['metadata'] = record

    # Generate embedding and auto-tag with taxonomy
    try:
        from shared import generate_embedding
        from taxonomy import tag_item as tag_item_fn
        from config import OPENAI_KEY as _openai_key
        description = record.get('future_scenario_description', '')
        if description:
            embedding = generate_embedding(description, _openai_key)
            record['embedding'] = embedding
            update_item(workspace, item_id, item_key, api_key, {'embedding': embedding})
            tag_result = tag_item_fn(workspace=workspace, item_id=item_id, api_key=api_key,
                                     item_key=item_key, description=description, embedding=embedding)
            if isinstance(tag_result, dict) and 'topics' in tag_result:
                record['topics'] = tag_result['topics']
    except Exception as e:
        print(f'WARNING: failed to generate embedding/tag item: {e}')

    return record_


def replace_item_image(image_bytes, image_content_type, workspace, item_id, item_key, api_key):
    """Replace an existing item's image without re-analysis."""
    # Verify the item exists and is accessible
    result = fetch_item(workspace, item_id, item_key, api_key)
    if result[1] is not None:
        return result

    screenshot_url = upload_image(image_bytes, image_content_type, workspace, item_id)

    # Pre-generate enhanced version for faster TSNE processing later
    enhanced_result = enhance_image_fn(screenshot_url=screenshot_url, force=True)
    if isinstance(enhanced_result, tuple):
        print('WARNING: failed to enhance image:', enhanced_result[0])

    err = update_item(workspace, item_id, item_key, api_key, {'screenshot_url': screenshot_url})
    if err:
        return err

    return dict(
        item_id=item_id,
        screenshot_url=screenshot_url,
    )


def reanalyze_item(workspace, item_id, item_key, api_key, automatic=False):
    """Re-analyze an existing item using its stored image and metadata."""
    result = fetch_item(workspace, item_id, item_key, api_key)
    if result[1] is not None:
        return result
    item_data = result[0]

    image_bytes, image_content_type = download_item_image(workspace, item_id)
    if image_bytes is None:
        return dict(error=f"No image found for item {item_id} in workspace {workspace}"), 404

    existing_metadata = item_data.get('metadata', {}) if automatic else None
    record = analyze_image(image_bytes, image_content_type, automatic=automatic, existing_metadata=existing_metadata)

    err = update_item(workspace, item_id, item_key, api_key, record)
    if err:
        return err

    # Generate embedding and auto-tag with taxonomy
    try:
        from shared import generate_embedding
        from taxonomy import tag_item as tag_item_fn
        from config import OPENAI_KEY as _openai_key
        description = record.get('future_scenario_description', '')
        if description:
            embedding = generate_embedding(description, _openai_key)
            record['embedding'] = embedding
            update_item(workspace, item_id, item_key, api_key, {'embedding': embedding})
            tag_result = tag_item_fn(workspace=workspace, item_id=item_id, api_key=api_key,
                                     item_key=item_key, description=description, embedding=embedding)
            if isinstance(tag_result, dict) and 'topics' in tag_result:
                record['topics'] = tag_result['topics']
    except Exception as e:
        print(f'WARNING: failed to generate embedding/tag item: {e}')

    return dict(
        item_id=item_id,
        automatic=automatic,
        metadata=record,
    )

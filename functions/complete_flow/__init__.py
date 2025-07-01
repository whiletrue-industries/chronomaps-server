from firebase_admin import firestore
import requests
from config import PRIVATE_KEY, CHRONOMAPS_API_URL
import datetime

db = firestore.client()
EMAIL_PROP = f'{PRIVATE_KEY}email'

POTENTIALS = {
    100: ('projected', 'will happen for sure'),
    75: ('probable', 'almost certain'),
    50: ('plausible', 'not obvious, not surprising'),
    25: ('possible', 'highly unlikely, not impossible'),
    0: ('preposterous', 'can never happen'),
}

def get_formatted_date():
    # Get current date
    today = datetime.datetime.now()
    # Suffixes for the day
    suffix = lambda d: 'th' if 11 <= d <= 13 else {1:'st', 2:'nd', 3:'rd'}.get(d % 10, 'th')
    # Format
    return today.strftime(f"%B {today.day}{suffix(today.day)}, %Y")

def send_email(workspace_id, workspace_metadata, item, item_id, item_key, api_key, locale=None, workshop_flow=False):
    email_address = item.get(EMAIL_PROP)
    email_template = workspace_metadata.get('email-template') or 'after-evaluate'
    if not email_template:
        return {'error': 'No email template found in workspace metadata'}, 400
    locale_path = ''
    locale_email_suffix = ''
    if locale and not locale.startswith('en'):
        locale_email_suffix = f'-{locale}'
        locale_path = f'/{locale}'
    email_template = email_template.replace(':LOCALE:', locale_email_suffix)
    potential, potential_desc  = POTENTIALS.get(item.get('plausibility', 50)) or ('unknown', 'unknown potential')
    preference = 'prefer' if 'prefer' in (item.get('favorable_future') or  '').lower() else 'prevent'
    relative_time = (item.get('transition_bar_position') or '').lower().replace('unclear', '') or 'some time around'

    workshop_param = '&ws=true' if workshop_flow else ''

    data = dict(
        afterword=False,
        date=get_formatted_date(),
        description=workspace_metadata.get('content_title', 'your imagined future'),
        event=workspace_metadata.get('event_name', 'the workshop'),
        img_url=item.get('screenshot_url'),
        ish='-ish' if 'mostly' in (item.get('favorable_future') or '').lower() else '',
        publish_link=f'https://mapfutur.es{locale_path}/props?workspace={workspace_id}&api_key={api_key}&item-id={item_id}&key={item_key}{workshop_param}#publish',
        edit_link=f'https://mapfutur.es{locale_path}/discuss?workspace={workspace_id}&api_key={api_key}&item-id={item_id}&key={item_key}{workshop_param}',
        potential=potential,
        potential_desc=potential_desc,
        preference=preference,
        relative_time=relative_time,
        tagline=item.get('future_scenario_tagline', 'your imagined future'),
        template_type=item.get('screenshot_type').replace('_', ' ') or 'screenshot',
        transition=item.get('transition_bar_event') or 'an unspecified future transition',
    )

    message = dict(
        to=[email_address],
        template=dict(
            name=email_template,
            data=data,
        )
    )
    db.collection('emails').document().set(message)
    return {'success': True}, 200

def complete_flow(workspace_id, item_id, item_key, api_key, properties, locale=None, workshop_flow=False):
    headers = {
        'Authorization': api_key
    }
    params = {
        'item-key': item_key
    }
    workspace_metadata = requests.get(f'{CHRONOMAPS_API_URL}/{workspace_id}', headers=headers).json()
    if properties:
        # Update item properties
        prev_item = requests.get(f'{CHRONOMAPS_API_URL}/{workspace_id}/{item_id}', headers=headers, params=params).json()
        item = requests.put(f'{CHRONOMAPS_API_URL}/{workspace_id}/{item_id}', headers=headers, params=params, json=properties).json()
        if EMAIL_PROP not in prev_item and EMAIL_PROP in item:
            return send_email(workspace_id, workspace_metadata, item, item_id, item_key, api_key, locale, workshop_flow)
        return {'success': True, 'warning': 'Already sent email or no email address found', 'items': [prev_item, item], 'bools': [EMAIL_PROP not in prev_item, EMAIL_PROP in item], 'p': properties}
    return {'error': 'No properties provided for update'}, 400
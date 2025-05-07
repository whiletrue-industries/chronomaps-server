from firebase_admin import firestore
from openai import OpenAI
from firebase_admin import storage
from pathlib import Path
from config import API_KEY, CHRONOMAPS_API_URL, PRIVATE_KEY
import os
import json
import requests

db = firestore.client()

# Use key, instructions, and filename to generate a structured response from openai api
INSTRUCTIONS = Path(__file__).with_name('ITEM_INGRESS_PROMPT.md').read_text().strip()
AGENT_NAME = 'Item Ingress Agent'
TOOLS = [
    dict(
        type='function',
        function=dict(
            name='update_properties',
            description='Update item properties',
            parameters=dict(
                type='object',
                properties=dict(
                    payload=dict(
                        type='string',
                        description='JSON encoded object with item properties to update'
                    )
                ),
                required=['payload']
            )
        )
    )
]

client = OpenAI(api_key=API_KEY)

_assistant_id = None

def get_assistant_id(workspace, workspace_metadata):
    global _assistant_id
    if _assistant_id is not None:
        return _assistant_id
    assistants = client.beta.assistants.list()
    agent_name = f'{AGENT_NAME}-{workspace}'
    for assistant in assistants:
        if assistant.name == agent_name:
            _assistant_id = assistant.id
            break
    last_message = workspace_metadata.get('final-ingress-message') or "Thanks, we're all set!"
    instructions = INSTRUCTIONS.replace('{{final-ingress-message}}', last_message)
    if _assistant_id is None:
        _assistant_id = client.beta.assistants.create(
            name=AGENT_NAME,
            model="gpt-4o",
            description="Chronomaps Item Ingress Agent",
            instructions=instructions,
            tools=TOOLS,
        ).id
    else:
        client.beta.assistants.update(
            assistant_id=_assistant_id,
            instructions=instructions,
            tools=TOOLS,
        )
    return _assistant_id

def fetch_item(workspace, item_id, api_key, item_key):
    url = os.path.join(CHRONOMAPS_API_URL, workspace, item_id)
    params = {'item-key': item_key} if item_key else {}
    response = requests.get(url, headers={'Authorization': api_key}, params=params, timeout=10)
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized for new items with this key"), 403
    if response.status_code == 404:
        return dict(error=f"Item {item_id} not found"), 404
    response.raise_for_status()
    item_data = response.json()
    return item_data, False


def fetch_workspace(workspace, api_key):
    url = os.path.join(CHRONOMAPS_API_URL, workspace)
    response = requests.get(url, headers={'Authorization': api_key}, timeout=10)
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized"), 403
    if response.status_code == 404:
        return dict(error=f"Item {workspace} not found"), 404
    response.raise_for_status()
    workspace_metadata = response.json()
    return workspace_metadata, False

def update_item_properties(workspace, item_id, api_key, item_key, payload):
    url = os.path.join(CHRONOMAPS_API_URL, workspace, item_id)
    response = requests.put(url, json=payload, headers={'Authorization': api_key}, params={'item-key': item_key})
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized updating items with this key"), 403
    if response.status_code == 404:
        return dict(error=f"Item {item_id} not found"), 404
    response.raise_for_status()
    item_data = response.json()
    return item_data, False

def send_email(workspace_id, workspace_metadata, item, item_id, item_key, api_key):
    email_address = item.get(PRIVATE_KEY + 'email')
    if not email_address:
        print('No email address found in item properties')
        return
    email_template = workspace_metadata.get('email-template')
    if not email_template:
        print('No email template found in workspace metadata')
        return
    secret_link = f'https://mapfutur.es/discuss?workspace={workspace_id}&api_key={api_key}&item-id={item_id}&key={item_key}'
    message = dict(
        to=[email_address],
        template=dict(
            name=email_template,
            data=dict(
                link=secret_link,
            )
        )
    )
    db.collection('emails').document().set(message)    

def item_ingress_agent(workspace, item_id, api_key, item_key, message):
    yield dict(kind='status', message='fetching item')
    item, error_code = fetch_item(workspace, item_id, api_key, item_key)
    if error_code:
        yield dict(kind='error', message='Failed to fetch item', code=error_code)
        return
    workspace_metadata, error_code = fetch_workspace(workspace, api_key)
    if error_code:
        yield dict(kind='error', message='Failed to fetch workspace', code=error_code)
        return
    
    new_thread = False
    thread_id = item.pop(PRIVATE_KEY + 'ingress-thread-id', None)
    if not thread_id:
        new_thread = True
        item_json = json.dumps(item, indent=2, ensure_ascii=False)
        yield dict(kind='status', message='creating thread')
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role='user',
            content=item_json,
        )
    else:
        yield dict(kind='status', message='fetching thread', thread_id=thread_id)
        thread = client.beta.threads.retrieve(thread_id)
        yield dict(kind='status', message='got thread', thread_id=thread_id)
        if message != 'initial':
            yield dict(kind='status', message='creating message', thread_id=thread_id)
            client.beta.threads.messages.create(
                thread_id=thread.id,
                role='user',
                content=message,
            )
        messages = client.beta.threads.messages.list(thread_id=thread.id, order='asc')
        role = None
        idx = 0
        yield dict(kind='status', message=f'got messages', thread_id=thread_id)
        for message in messages:
            for content in message.content:
                role = message.role
                if content.type == 'text':
                    text = content.text.value
                    text = text.replace(r'\n', '\n')
                    if role == 'assistant':
                        lines = [line.strip()[:10] for line in text.split('\n') if line.strip()]
                        if any('DONE' in line for line in lines):
                            yield dict(kind='status', status='done')
                            text = text.split('DONE')[0]
                    if message.role == 'user' and idx == 0:
                        continue
                    yield dict(kind='message', role=message.role, content=text, idx=idx)
                    idx += 1
        yield dict(kind='status', message=f'processed {idx} messages', role=role)
        if role == 'assistant':
            yield dict(kind='status', status='completed')
            return
        
    assistant_id = get_assistant_id(workspace, workspace_metadata)
    stream = client.beta.threads.runs.create(
        thread_id=thread.id,
        assistant_id=assistant_id,
        stream=True,
    )

    while stream:
        current_line = ''
        for event in stream:
            yield dict(kind='event', event=event.event)
            if event.event == 'thread.run.completed':
                yield dict(kind='status', status='completed')
                stream = None
                break
            elif event.event == 'thread.run.failed':
                yield dict(kind='status', status='failed')
                stream = None
                break
            elif event.event == 'thread.run.requires_action':
                run = event.data
                tool_outputs = []
                for tool in run.required_action.submit_tool_outputs.tool_calls:
                    msg = dict(kind='tool')
                    try:
                        msg['arguments'] = tool.function.arguments
                        arguments = json.loads(tool.function.arguments)
                        msg['arguments'] = arguments
                        msg['name'] = tool.function.name

                        # Handle different tool types
                        if tool.function.name == 'update_properties':
                            payload = arguments.get('payload')
                            if payload:
                                print('PAYLOAD:', payload)
                                try:
                                    payload = json.loads(payload)
                                    for k in ['email']:
                                        if k in payload:
                                            payload[PRIVATE_KEY + k] = payload.pop(k)
                                    # Update item properties
                                    item, error = update_item_properties(workspace, item_id, api_key, item_key, payload)
                                    if error:
                                        return error
                                    ret = dict(success=True)
                                except json.decoder.JSONDecodeError as e:
                                    ret = dict(
                                        error=f"Invalid JSON payload as argument: {e}, maybe try updating one property at a time."
                                    )
                            else:
                                ret = dict(
                                    error="Missing payload in update_properties tool call"
                                )
                        else:
                            ret = dict(
                                error=f"Unknown tool call: {tool.function.name}, only 'update_properties' is supported"
                            )
                    except:
                        ret = dict(
                            error=f"Invalid tool call: {tool.function.name}, please check the arguments and try again."
                        )
                    msg['retval'] = ret
                    yield msg
                    tool_outputs.append(dict(
                        tool_call_id=tool.id,
                        output=json.dumps(ret, ensure_ascii=False, indent=2)
                    ))

                stream = client.beta.threads.runs.submit_tool_outputs(
                    thread_id=thread.id,
                    run_id=run.id,
                    tool_outputs=tool_outputs,
                    stream=True
                )
            elif event.event == 'thread.message.delta':
                text = ''
                for block in event.data.delta.content:
                    if block.type == 'text' and block.text.value:
                        text += block.text.value
                current_line += text
                current_line = current_line.split('\n')[-1]
                if 'DONE' in current_line:
                    yield dict(kind='status', status='done')
                    send_email(workspace, workspace_metadata, item, item_id, item_key, api_key)
                    text = text.split('DONE')[0]
                yield dict(kind='text', value=text)

    if new_thread:
        # update thread_id in item properties
        updated = update_item_properties(workspace, item_id, api_key,item_key, {
            PRIVATE_KEY + 'ingress-thread-id': thread.id
        })
        print('Updated item with new thread_id:', updated)

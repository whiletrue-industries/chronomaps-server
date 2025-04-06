from firebase_admin import firestore
from openai import OpenAI
from firebase_admin import storage
from pathlib import Path
from config import API_KEY, CHRONOMAPS_API_URL
import os
import base64
import json
import requests

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

def get_assistant_id():
    global _assistant_id
    if _assistant_id is not None:
        return _assistant_id
    assistants = client.beta.assistants.list()
    for assistant in assistants:
        if assistant.name == AGENT_NAME:
            _assistant_id = assistant.id
            break
    if _assistant_id is None:
        _assistant_id = client.beta.assistants.create(
            name=AGENT_NAME,
            model="gpt-4o",
            description="Chronomaps Item Ingress Agent",
            instructions=INSTRUCTIONS,
            tools=TOOLS,
        ).id
    else:
        client.beta.assistants.update(
            assistant_id=_assistant_id,
            instructions=INSTRUCTIONS,
            tools=TOOLS,
        )
    return _assistant_id

def fetch_item(workspace, item_id, api_key):
    url = os.path.join(CHRONOMAPS_API_URL, workspace, item_id)
    response = requests.get(url, headers={'Authorization': api_key})
    if response.status_code == 403:
        return dict(error=f"Workspace {workspace} not authorized for new items with this key"), 403
    if response.status_code == 404:
        return dict(error=f"Item {item_id} not found"), 404
    response.raise_for_status()
    item_data = response.json()
    return item_data, False

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

def item_ingress_agent(workspace, item_id, api_key, item_key, message):
    item, error = fetch_item(workspace, item_id, api_key)
    if error:
        return item, error
    item_json = json.dumps(item, indent=2, ensure_ascii=False)
    
    new_thread = False
    thread_id = item.get('.internal-ingress-thread-id')
    if not thread_id:
        new_thread = True
        thread = client.beta.threads.create()
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role='user',
            content=item_json,
        )
    else:
        thread = client.beta.threads.retrieve(thread_id)
        client.beta.threads.messages.create(
            thread_id=thread.id,
            role='user',
            content=message,
        )
        
    assistant_id = get_assistant_id()
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id,
        assistant_id=assistant_id,
    )

    reply = ''
    while True:
        if run.status not in ['completed', 'requires_action']:
            return f'Unexpected status: {run.status}', 500

        if run.status == 'completed':
            # Log assistant's response when run is completed
            messages = client.beta.threads.messages.list(thread_id=thread.id, order='desc', limit=1)
            for message in messages:
                if message.role == "assistant":
                    for content in message.content:
                        if content.type == 'text':
                            reply = content.text.value
                            if 'DONE' in reply[:10]:
                                return dict(
                                    complete=True
                                )
                            break
                    if reply:
                        break
            break
        
        elif run.status == 'requires_action':
            tool_outputs = []
            for tool in run.required_action.submit_tool_outputs.tool_calls:
                arguments = json.loads(tool.function.arguments)

                # Handle different tool types
                if tool.function.name == 'update_properties':
                    payload = arguments.get('payload')
                    if payload:
                        print('PAYLOAD:', payload)
                        payload = json.loads(payload)
                        # Update item properties
                        ret, error = update_item_properties(workspace, item_id, api_key, item_key, payload)
                        if error:
                            return ret, error
                    else:
                        ret = dict(
                            error="Missing payload in update_properties tool call"
                        )
                else:
                    ret = dict(
                        error=f"Unknown tool call: {tool.function.name}, only 'update_properties' is supported"
                    )
                tool_outputs.append(dict(
                    tool_call_id=tool.id,
                    output=json.dumps(ret, ensure_ascii=False, indent=2)
                ))

            run = client.beta.threads.runs.submit_tool_outputs_and_poll(
                thread_id=thread.id,
                run_id=run.id,
                tool_outputs=tool_outputs
            )

    if new_thread:
        # update thread_id in item properties
        updated = update_item_properties(workspace, item_id, api_key,item_key, {
            '.internal-ingress-thread-id': thread.id
        })
        print('Updated item with new thread_id:', updated)

    return dict(
        complete=False,
        message=reply
    )

import random
import math
import concurrent.futures
import queue
import json
from pathlib import Path
from io import BytesIO

import requests
import numpy as np
from PIL import Image
from openai import OpenAI

from sklearn.manifold import TSNE
from scipy.spatial.distance import cdist

from cluster_screenshots.tsne_params import TSNEParams
from cluster_screenshots.calc_tsne import cluster_screenshots_inner

try:
    from lapjv import lapjv
except ImportError:
    pass
from sklearn.cluster import AgglomerativeClustering

from firebase_admin import storage

from config import OPENAI_KEY, CHRONOMAPS_API_URL, BUCKET_NAME

bucket = storage.bucket(name=BUCKET_NAME)

EXTRACT_TITLE_INSTRUCTIONS = Path(__file__).with_name('EXTRACT_TITLE_PROMPT.md').read_text().strip()

class ThreadPoolExecutorWithQueueSizeLimit(concurrent.futures.ThreadPoolExecutor):
    def __init__(self, maxsize=32, *args, **kwargs):
        super().__init__(*args, max_workers=32, **kwargs)
        self._work_queue = queue.Queue(maxsize=maxsize)

def upload_image(image, tile_size, w, h, prefix, zoom, x, y, params: TSNEParams):
    target = Image.new('RGB', (tile_size, tile_size), params.BG_COLOR)
    left = min(x * tile_size, w)
    upper = min(y * tile_size, h)
    right = min(left + tile_size, w)
    lower = min(upper + tile_size, h)
    target.paste(image.crop((left, upper, right, lower)), (0, 0))
    buff = BytesIO()
    target.save(buff, format='png', compress_level=0)
    buff.seek(0)
    blob = bucket.blob(f'tiles/{prefix}/{zoom}/{x}/{y}.png')
    blob.cache_control = 'public, max-age=600'
    blob.upload_from_file(buff, content_type='image/png')
    blob.make_public()
    del target
    del buff
    del blob

def create_tiles(prefix: str, image: Image):
    w, h = image.size
    tile_size = 256
    num_tiles = math.ceil(w / tile_size), math.ceil(h / tile_size)
    zoom_level = math.ceil(math.log2(max(num_tiles)))
    max_zoom = 8
    min_zoom = 8 - zoom_level
    yield dict(msg=f"Tiles: {prefix} ({w}x{h}) -> {num_tiles[0]}x{num_tiles[1]} ({tile_size}px) {zoom_level} zoom levels")

    with ThreadPoolExecutorWithQueueSizeLimit() as executor:
        for z in range(zoom_level):
            zoom = max_zoom - z
            skip = 2**z
            _num_tiles = tuple(math.ceil(n / skip) for n in num_tiles)
            yield dict(msg=f"Zoom {zoom}: {_num_tiles[0]}x{_num_tiles[1]} ({tile_size}px)")
            if skip > 1:
                image = image.resize((w // 2, h // 2), Image.Resampling.LANCZOS)
                w, h = image.size
            for x in range(_num_tiles[0]):
                # os.makedirs(f'tiles/{prefix}/{zoom}/{x}', exist_ok=True)
                yield dict(msg=f"Zoom {zoom}: row {x}/{_num_tiles[0]}")
                for y in range(_num_tiles[1]):
                    executor.submit(upload_image, image, tile_size, w, h, prefix, zoom, x, y, params)
                    # target.save(f'tiles/{prefix}/{zoom}/{x}/{y}.png', format='PNG', compress_level=0)

def extract_cluster_title(client, taglines, previous=None):
    taglines = f'- {"\n- ".join(taglines)}'
    if previous:
        previous = f'- {"\n- ".join(previous)}'
        previous = 'Avoid repeating these themes - be more exact and specific if possible to make sure your response is unique and clearly distinguishable from the following:\n' + previous
    else:
        previous = ''
    prompt = EXTRACT_TITLE_INSTRUCTIONS.replace(':TAGLINES:', taglines).replace(':PREVIOUS:', previous)

    completion = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
                "role": "user",
                "content": [
                    { "type": "text", "text": prompt },
                ],
            }
        ],
        temperature=0.0000001,
        response_format={
            "type": 'json_object'
        }
    )
    # print(f'PPPP\n{prompt}\n\n{completion.choices[0].message.content}')
    content = completion.choices[0].message.content
    content = json.loads(content)
    return content

def find_clusters(records, tsne, info):
    client = OpenAI(api_key=OPENAI_KEY)
    clustering = AgglomerativeClustering(n_clusters=10, metric='euclidean', distance_threshold=None, linkage='ward')
    clustering.fit(tsne)
    labels = clustering.labels_
    all_labels = set(labels)
    num_clusters = len(all_labels)
    print(f'num_clusters: {num_clusters}')
    label_counts = []
    record_positions = dict(
        (i['id'], i['pos'])
        for i in info['grid']
    )
    records_rotations = dict(
        (i['id'], i['metadata']['rotate'])
        for i in info['grid']
    )
    for _label in all_labels:
        cluster_indexes = [idx for idx, label in enumerate(labels) if label == _label]        
        cluster_members = list(map(lambda x: records[x], cluster_indexes))
        label_counts.append((_label, len(cluster_members), cluster_indexes, cluster_members))

    label_counts.sort(key=lambda x: x[1], reverse=True)
    total = 0
    titles = []
    previous = []

    for label, count, indexes, members in label_counts:
        if count < 3:
            break
        yield dict(msg=f'Cluster {label} size: {count}, {count / len(records) * 100:.2f}% of total')
        taglines = [member['future_scenario_description'] for member in members]

        title = extract_cluster_title(client, taglines, previous)
        previous.append(title['english'])
        cluster_positions = [
            record_positions[member['_id']]
            for member in members
        ]
        cluster_positions_bounds = [
            [min([pos[0] for pos in cluster_positions]) , min([pos[1] for pos in cluster_positions]) ],
            [max([pos[0] for pos in cluster_positions]) + 1, max([pos[1] for pos in cluster_positions]) + 1]
        ]
        cluster_rotations = [
            records_rotations[member['_id']]
            for member in members
        ]
        cluster_average_rotation = sum(cluster_rotations) / len(cluster_rotations)
        titles.append(
            dict(
                title=title,
                bounds=cluster_positions_bounds,
                average_rotation=cluster_average_rotation,
            )
        )

        yield dict(msg=f'Cluster {label}: #{count}, {title["english"]}')

        total += count
        if total > 0.95 * len(records):
            break
    info['clusters'] = titles

def convert_coords(coords, conversion_ratio):
    x, y = coords
    conv_x, conv_y = conversion_ratio
    x = x * conv_x
    y = -y * conv_y
    return y, x

def convert_bounds(bounds, conversion_ratio):
    y1, x1 = convert_coords(bounds[0], conversion_ratio)
    y2, x2 = convert_coords(bounds[1], conversion_ratio)
    return [[min(y1, y2), min(x1, x2)], [max(y1, y2), max(x1, x2)]]

def convert_all_coords(info):
    conversion_ratio = info['conversion_ratio']
    if 'clusters' in info:
        for cluster in info['clusters']:
            cluster['geo_bounds'] = convert_bounds(cluster['bounds'], conversion_ratio)
    for grid in info['grid']:
        pos = grid['pos'][0] + 0.5, grid['pos'][1] + 0.5
        grid['geo_pos'] = convert_coords(pos, conversion_ratio)
        grid['geo_bounds'] = convert_bounds([[grid['pos'][0], grid['pos'][1]], [grid['pos'][0] + 1, grid['pos'][1] + 1]], conversion_ratio)

def cluster_screenshots(config, tag=None):
    config = config.split(';') if config else []
    config = [c.strip().split(':') for c in config if c.strip()]
    params = TSNEParams(
        OPENAI_KEY=OPENAI_KEY,
        CHRONOMAPS_API_URL=CHRONOMAPS_API_URL,
    )

    if tag is None:
        if len(config) > 0:
            tag = config[0][0]
        else:
            tag = 'empty'

    global_config_blob = bucket.blob(f'tiles/{tag}/config.json')
    set_id = 0
    if global_config_blob.exists():
        content = global_config_blob.download_as_text()
        try:
            tag_info = json.loads(content)
            set_id = tag_info['set_id']
            set_id += 1
            if set_id == 16:
                set_id = 0
        except Exception as e:
            print('Error loading config:', e)
            pass

    prefix = f'{tag}/{set_id}'

    for msg in cluster_screenshots_inner(config, params):
        if 'action' in msg:
            if msg['action'] == 'tiles':
                yield from create_tiles(prefix, msg['image'])
            if msg['action'] == 'clusters':
                info = msg['info']
                records = msg['records']
                grid = msg['grid']
                if len(records) > 0:
                    yield from find_clusters(records, grid, info)

                convert_all_coords(info)

                blob = bucket.blob(f'tiles/{prefix}/config.json')
                blob.cache_control = 'no-cache'
                blob.upload_from_string(json.dumps(info), content_type='application/json')
                blob.make_public()

                global_config_blob.cache_control = 'no-cache'
                global_config_blob.upload_from_string(json.dumps(dict(set_id=set_id)), content_type='application/json')
                global_config_blob.make_public()

                yield dict(msg=f'Config uploaded: {blob.public_url}')
            else:
                yield msg


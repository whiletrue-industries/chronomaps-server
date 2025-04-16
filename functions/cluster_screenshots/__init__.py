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
from lapjv import lapjv
from sklearn.cluster import AgglomerativeClustering

from firebase_admin import storage

from config import API_KEY, CHRONOMAPS_API_URL

bucket = storage.bucket()

EMBEDDING_DIMENSION = 3072
PERPLEXITY = 50
TSNE_ITER = 5000
ORIGINAL_IMAGE_SIZE = (530, 1000)
CELL_RATIOS = (1.86, 1.135)

OUT_DIM_X = 30
OUT_RATIO = 9/16
OUT_DIM_Y = int(math.ceil(OUT_DIM_X * ORIGINAL_IMAGE_SIZE[0] * CELL_RATIOS[0] * OUT_RATIO / (ORIGINAL_IMAGE_SIZE[1] * CELL_RATIOS[1])))
out_dim = (OUT_DIM_X, OUT_DIM_Y)
TO_PLOT = int(out_dim[0] * out_dim[1] * 0.75)
PADDING_RATIO = 0.285

EXTRACT_TITLE_INSTRUCTIONS = Path(__file__).with_name('EXTRACT_TITLE_PROMPT.md').read_text().strip()

class ThreadPoolExecutorWithQueueSizeLimit(concurrent.futures.ThreadPoolExecutor):
    def __init__(self, maxsize=32, *args, **kwargs):
        super().__init__(*args, max_workers=32, **kwargs)
        self._work_queue = queue.Queue(maxsize=maxsize)


def load_records(config, records):
    params = dict(page_size=TO_PLOT, order_by='-created_at')
    for workspace, api_key in config:
        yield dict(msg=f'Fetching from {workspace}...')
        items = requests.get(f'{CHRONOMAPS_API_URL}/{workspace}/items', params, headers={'Authorization': api_key}).json()
        yield dict(msg=f'Got {len(items)} items.')
        yield from ensure_embeddings(items, workspace, api_key)
        records.extend(items)
    records.sort(key=lambda x: x['created_at'], reverse=True)

def ensure_embeddings(records, workspace, api_key):
    openai = OpenAI(api_key=API_KEY)
    for i, record in enumerate(records):
        if i % 100 == 0:
            yield dict(msg=f'Ensuring embedding {i}/{len(records)}...')
        if 'embedding' in record:
            continue
        description = record['future_scenario_description']
        completion = openai.embeddings.create(
            model="text-embedding-3-large",
            input=description
        )
        embedding = completion.data[0].embedding
        record['embedding'] = embedding
        item_id = record['_id']
        requests.put(f'{CHRONOMAPS_API_URL}/{workspace}/{item_id}', json=dict(embedding=embedding), headers={'Authorization': api_key})

def generate_tsne(activations, perplexity=50, tsne_iter=5000):
    tsne = TSNE(perplexity=perplexity, n_components=2, init='random', n_iter=tsne_iter)
    X_2d = tsne.fit_transform(np.array(activations))
    X_2d -= X_2d.min(axis=0)
    X_2d /= X_2d.max(axis=0)
    return X_2d

def calc_tsne_grid(X_2d, out_dim):
    grid = np.dstack(np.meshgrid(np.linspace(0, 1, out_dim[1]), np.linspace(0, 1, out_dim[0]))).reshape(-1, 2)
    cost_matrix = cdist(grid, X_2d, "sqeuclidean").astype(np.float32)
    cost_matrix = cost_matrix * (100000 / cost_matrix.max())
    shp = cost_matrix.shape
    cost_matrix = np.hstack((cost_matrix, np.zeros((shp[0], shp[0] - shp[1]))))
    _, col_asses, _ = lapjv(cost_matrix)
    grid_jv = grid[col_asses]
    return grid_jv

def get_image(record, target_size):
    # Open the image size, resize it to the target size (maintaining aspect ratio) and return a cropped image of the target size out the center
    if record is not None:
        filename = record.get('screenshot_url')
        rotate = record.get('plausibility') or 100
        rotate = (100 - rotate) / 100 * 32
        favorable_future = record.get('favorable_future')
        sign = 0
        if favorable_future == 'yes':
            sign = 1
        elif favorable_future == 'no':
            sign = -1
        rotate = sign * rotate
    else:
        filename = None
        rotate = random.randint(0, 64) - 32
    inner_target_size = int(target_size[0] / CELL_RATIOS[0]), int(target_size[1] / CELL_RATIOS[1])
    if not filename:
        filename = Path(__file__).with_name('empty-space.png')
        img = Image.open(filename)
        _image = Image.new("RGBA", img.size, "WHITE") 
        _image.paste(img, (0, 0), img)         
        img = _image.convert('RGB')
        img = img.resize(inner_target_size, Image.Resampling.LANCZOS)
    else:
        img = Image.open(requests.get(filename, stream=True).raw)
        ratio = max(inner_target_size[0] / img.width, inner_target_size[1] / img.height)
        # resize the image by ratio:
        img = img.resize((int(img.width * ratio), int(img.height * ratio)), Image.Resampling.LANCZOS)
        # crop the image to the target size out the center
        img = img.crop((img.size[0]//2 - inner_target_size[0]//2, img.size[1]//2 - inner_target_size[1]//2,
                        img.size[0]//2 + inner_target_size[0]//2, img.size[1]//2 + inner_target_size[1]//2))
        img = img.resize(inner_target_size, Image.Resampling.LANCZOS)
    img = img.rotate(rotate, expand=True, fillcolor=(255, 255, 255))
    out_img = Image.new('RGB', target_size, (255, 255, 255))
    assert target_size[0] >= img.width, f'{target_size[0]} < {img.width}'
    assert target_size[1] >= img.height, f'{target_size[1]} < {img.height}'
    out_img.paste(img, ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2))
    metadata = dict(rotate=rotate, favorable_future=sign)
    return out_img, metadata

def create_tsne_image(grid_jv, records, out_dim, to_plot, res, offset, padding, tsne_out):
    # print('>>>', filename)

    out_res_x, out_res_y = res
    offset_x, offset_y = offset
    padding_x, padding_y = padding

    info = dict(dim=out_dim, grid=[])
    info['conversion_ratio'] = (out_res_x / 256, out_res_y / 256)

    out = np.ones((out_dim[1]*out_res_y + padding_y, out_dim[0]*out_res_x + padding_x, 3), dtype=np.uint8) * 255
    positions = dict()
    for pos, record in zip(grid_jv, records[0:to_plot]):
        pos_x = round(pos[1] * (out_dim[0] - 1))# + img_ofs
        pos_y = round(pos[0] * (out_dim[1] - 1))# + img_ofs
        pos = (int(pos_y), int(pos_x))
        positions[pos] = record
    for pos_x in range(out_dim[0]):
        yield dict(msg=f'Creating image: {pos_x}/{out_dim[0]}')
        for pos_y in range(out_dim[1]):
            pos = (pos_y, pos_x)
            record = positions.get(pos)
            img, metadata = get_image(record, res)
            if callable(offset_x):
                _offset_x = offset_x(pos_x, pos_y)
            else:
                _offset_x = offset_x
            if callable(offset_y):
                _offset_y = offset_y(pos_x, pos_y)
            else:
                _offset_y = offset_y
            h_range = pos_y * out_res_y + _offset_y
            w_range = pos_x * out_res_x + _offset_x
            out[h_range:h_range + out_res_y, w_range:w_range + out_res_x] = img
            if record is not None:
                info['grid'].append(dict(pos=[pos_x, pos_y], id=record['_id'], metadata=metadata))

    tsne_out['image'] = out
    tsne_out['info'] = info

def upload_image(image, tile_size, w, h, prefix, zoom, x, y):
    target = Image.new('RGB', (tile_size, tile_size), (255, 255, 255))
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
                    executor.submit(upload_image, image, tile_size, w, h, prefix, zoom, x, y)
                    # target.save(f'tiles/{prefix}/{zoom}/{x}/{y}.png', format='PNG', compress_level=0)

def extract_cluster_title(client, taglines):
    prompt = f'''{EXTRACT_TITLE_INSTRUCTIONS}

List of submission taglines:
- {"\n- ".join(taglines)}
'''
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
        temperature=0.0000001
    )
    # print(f'PPPP\n{prompt}\n\n{completion.choices[0].message.content}')
    return completion.choices[0].message.content

def find_clusters(records, tsne, info):
    client = OpenAI(api_key=API_KEY)
    clustering = AgglomerativeClustering(n_clusters=10, metric='cosine', distance_threshold=None, linkage='complete')
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

    for label, count, indexes, members in label_counts:
        yield dict(msg=f'Cluster {label} size: {count}, {count / len(records) * 100:.2f}% of total')
        taglines = [member['future_scenario_description'] for member in members]

        title = extract_cluster_title(client, taglines)
        cluster_positions = [
            record_positions[member['_id']]
            for member in members
        ]
        cluster_positions_bounds = [
            [min([pos[0] for pos in cluster_positions]) - 0.5, min([pos[1] for pos in cluster_positions]) - 0.5],
            [max([pos[0] for pos in cluster_positions]) + 0.5, max([pos[1] for pos in cluster_positions]) + 0.5]
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

        yield dict(msg=f'Cluster {label}: #{count}, {title}')

        total += count
        if total > 0.85 * len(records):
            break
    info['clusters'] = titles

def get_side(ratio, dim):
    i = 0
    while True:
        tiles = 2**i
        side = int(tiles * 256 / dim * ratio)
        if side >= 1000:
            return side
        i += 1

def convert_coords(coords, conversion_ratio):
    x, y = coords
    conv_x, conv_y = conversion_ratio
    x = x * conv_x
    y = -y * conv_y
    return x, y

def convert_all_coords(info):
    conversion_ratio = info['conversion_ratio']
    for cluster in info['clusters']:
        cluster['geo_bounds'] = [
            convert_coords(cluster['bounds'][0], conversion_ratio),
            convert_coords(cluster['bounds'][1], conversion_ratio)
        ]
    for grid in info['grid']:
        pos = grid['pos'][0] + 0.5, grid['pos'][1] + 0.5
        grid['geo_pos'] = convert_coords(pos, conversion_ratio)
        grid['geo_bounds'] = [
            convert_coords([grid['pos'][0], grid['pos'][1]], conversion_ratio),
            convert_coords([grid['pos'][0] + 1, grid['pos'][1] + 1], conversion_ratio)
        ]

def cluster_screenshots(config):
    config = config.split(';')
    config = [c.split(':') for c in config]

    records = []
    yield from load_records(config, records)
    records = records[:TO_PLOT]

    records, activations = records, [rec['embedding'] for rec in records]

    yield dict(msg=f'Generating 2D representation from {len(records)} records.')
    X_2d = generate_tsne(activations, perplexity=PERPLEXITY, tsne_iter=TSNE_ITER)
    yield dict(msg="Generating image grid (%dx%d, %d images" % (out_dim[0], out_dim[1], len(records)))
    grid = calc_tsne_grid(X_2d, out_dim)

    try:
        # w, h = 530, 1000
        w, h = ORIGINAL_IMAGE_SIZE[0] * CELL_RATIOS[0], ORIGINAL_IMAGE_SIZE[1] * CELL_RATIOS[1]
        dim = max(w, h)
        side = 1000 # get_side(w/dim, OUT_DIM_X)
        res = (int(side*w/dim), int(side*h/dim))
        padding_y = int(h * PADDING_RATIO)
        offset = (0, lambda x, _: padding_y * (x%2))
        padding = (0, padding_y)
        yield dict(msg=f'Creating image: {w}x{h} {side} {res} {padding}')
        tsne = {}
        yield from create_tsne_image(grid, records, out_dim, 10000,
                                     res, offset, padding, tsne)
        image, info = tsne['image'], tsne['info']
        yield dict(msg=f'Got TSNE Image: {image.shape} {image.dtype}')
        image = Image.fromarray(image)
        yield dict(msg="Creating tiles.")
        prefix = f'{config[0][0]}/0'
        yield from create_tiles(prefix, image, info)
        yield dict(msg='Processing complete.')

        yield from find_clusters(records, X_2d, info)

        convert_all_coords(info)

        blob = bucket.blob(f'{prefix}/config.json')
        blob.cache_control = 'public, max-age=600'
        blob.upload_from_string(json.dumps(info), content_type='application/json')
        blob.make_public()
    except Exception as e:
        yield dict(msg="Error generating image:", error=str(e))
        raise


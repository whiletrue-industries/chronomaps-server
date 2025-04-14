import json
import concurrent.futures
from pathlib import Path
import requests
from io import BytesIO
import numpy as np
from sklearn.manifold import TSNE
from scipy.spatial.distance import cdist
from lapjv import lapjv
import random
import math
import os
from PIL import Image
from skimage import transform
from scipy.ndimage import gaussian_filter
from openai import OpenAI

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
SIDE = 1000
PADDING = int(0.285 * SIDE)

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

def generate_tsne(activations, to_plot, perplexity=50, tsne_iter=5000):
    tsne = TSNE(perplexity=perplexity, n_components=2, init='random', n_iter=tsne_iter)
    X_2d = tsne.fit_transform(np.array(activations)[0:to_plot,:])
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

def get_image(filename, target_size):
    # Open the image size, resize it to the target size (maintaining aspect ratio) and return a cropped image of the target size out the center
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
    rotate = random.randint(0, 64) - 32
    img = img.rotate(rotate, expand=True, fillcolor=(255, 255, 255))
    out_img = Image.new('RGB', target_size, (255, 255, 255))
    assert target_size[0] >= img.width, f'{target_size[0]} < {img.width}'
    assert target_size[1] >= img.height, f'{target_size[1]} < {img.height}'
    out_img.paste(img, ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2))
    return out_img

def create_tsne_image(grid_jv, records, out_dim, to_plot, res, offset, padding):
    # print('>>>', filename)
    info = dict(dim=out_dim, grid=[])

    out_res_x, out_res_y = res
    offset_x, offset_y = offset
    padding_x, padding_y = padding
    out = np.ones((out_dim[1]*out_res_y + padding_y, out_dim[0]*out_res_x + padding_x, 3), dtype=np.uint8) * 255
    print("Output:", out_dim, res, out.shape, out.dtype)
    positions = dict()
    for pos, record in zip(grid_jv, records[0:to_plot]):
        pos_x = round(pos[1] * (out_dim[0] - 1))# + img_ofs
        pos_y = round(pos[0] * (out_dim[1] - 1))# + img_ofs
        pos = (int(pos_y), int(pos_x))
        positions[pos] = record
    for pos_x in range(out_dim[0]):
        for pos_y in range(out_dim[1]):
            pos = (pos_y, pos_x)
            record = positions.get(pos)
            if record is not None:
                image_url = record.get('screenshot_url')
            else:
                image_url = None
            print(f"Processing {pos}: {image_url}")
            img = get_image(image_url, res)
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
                info['grid'].append(dict(pos=dict(x=pos_x, y=pos_y), item=record['_id']))

    return out, info

def create_tiles(prefix: str, image: Image):
    w, h = image.size
    max_size = max(w, h)
    tile_size = 256
    num_tiles = math.ceil(max_size / tile_size)
    zoom_level = math.ceil(math.log2(num_tiles))
    num_tiles = 2**zoom_level
    max_zoom = 8
    min_zoom = 8 - zoom_level
    yield dict(msg=f"Tiles: {prefix} ({w}x{h}) -> {num_tiles}x{num_tiles} ({tile_size}px) {zoom_level} levels")

    for z in range(zoom_level):
        zoom = max_zoom - z
        skip = 2**z
        _num_tiles = num_tiles // skip
        yield dict(msg=f"Zoom {zoom}: {_num_tiles}x{_num_tiles} ({tile_size}px)")
        if skip > 1:
            image = image.resize((w // 2, h // 2), Image.Resampling.LANCZOS)
            w, h = image.size
        for x in range(_num_tiles):
            # os.makedirs(f'tiles/{prefix}/{zoom}/{x}', exist_ok=True)
            for y in range(_num_tiles):
                target = Image.new('RGB', (tile_size, tile_size), (255, 255, 255))
                left = min(x * tile_size, w)
                upper = min(y * tile_size, h)
                right = min(left + tile_size, w)
                lower = min(upper + tile_size, h)
                target.paste(image.crop((left, upper, right, lower)), (0, 0))
                buff = BytesIO()
                target.save(buff, format='png', compress_level=0)
                buff.seek(0)
                blob = bucket.blob(f'tiles/{prefix}/{zoom}/{x}/{y}.png').upload_from_file(buff, content_type='image/png')
                blob.make_public()
                # target.save(f'tiles/{prefix}/{zoom}/{x}/{y}.png', format='PNG', compress_level=0)

def cluster_screenshots(config):
    config = config.split(';')
    config = [c.split(':') for c in config]

    records = []
    yield from load_records(config, records)
    records = records[:TO_PLOT]

    records, activations = records, [rec['embedding'] for rec in records]

    yield dict(msg=f'Generating 2D representation from {len(records)} records.')
    X_2d = generate_tsne(activations, TO_PLOT, PERPLEXITY, TSNE_ITER)
    yield dict(msg="Generating image grid (%dx%d, %d images" % (out_dim[0], out_dim[1], len(records)))
    grid = calc_tsne_grid(X_2d, out_dim)

    try:
        # w, h = 530, 1000
        w, h = ORIGINAL_IMAGE_SIZE[0] * CELL_RATIOS[0], ORIGINAL_IMAGE_SIZE[1] * CELL_RATIOS[1]
        dim = max(w, h)
        res = (int(SIDE*w/dim), int(SIDE*h/dim))
        offset = (0, lambda x, _: PADDING * (x%2))
        padding = (0, PADDING)
        yield dict(msg=f'Creating image: {w}x{h} {res} {padding}')
        image, info = create_tsne_image(grid, records, out_dim, 10000,
                                        res, offset, padding)
        yield dict(msg=f'Got TSNE Image: {image.shape} {image.dtype}')
        image = Image.fromarray(image)
        yield dict(msg="Creating tiles.")
        yield from create_tiles(f'{config[0]}/0', image)
        yield dict(msg='Processing complete.')

    except Exception as e:
        yield dict(msg="Error generating image:", error=str(e))
        raise

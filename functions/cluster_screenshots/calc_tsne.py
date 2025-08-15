import hashlib
from pathlib import Path

import requests
import numpy as np
from PIL import Image, ImageOps, ImageDraw, ImageFont
from openai import OpenAI

from sklearn.manifold import TSNE
from scipy.spatial.distance import cdist

try:
    from cluster_screenshots.tsne_params import TSNEParams
except ImportError:
    from tsne_params import TSNEParams
try:
    from lapjv import lapjv
except ImportError:
    pass

FONT = Path(__file__).with_name('SourceSans.ttf')


def use_item(item):
    favorable_future = item.get('favorable_future')
    if not favorable_future:
        return False
    if favorable_future in ['yes', 'no']:
        return True
    if 'prevent' in favorable_future or 'prefer' in favorable_future:
        return True
    if 'future_scenario_description' not in item or not item['future_scenario_description']:
        return False
    return False

def load_records(config, records, params: TSNEParams):
    if len(config) > 1:
        max_per_workspace = int(params.TO_PLOT / 2)
    else:
        max_per_workspace = params.TO_PLOT
    for workspace, api_key, *extra in config:
        if extra:
            min_range = int(extra[0])
            moderation_range = ','.join(str(i) for i in range(min_range, 6))
            req_params = dict(page_size=params.TO_PLOT*2, order_by='-created_at', filters=f'metadata._private_moderation in [{moderation_range}]')
            yield dict(msg=f'Fetching from {workspace}... ({moderation_range})')
        else:
            req_params = dict(page_size=params.TO_PLOT*2, order_by='-created_at')
            yield dict(msg=f'Fetching from {workspace}...')
        workspace_metadata = requests.get(f'{params.CHRONOMAPS_API_URL}/{workspace}', req_params, headers={'Authorization': api_key}).json()
        items = requests.get(f'{params.CHRONOMAPS_API_URL}/{workspace}/items', req_params, headers={'Authorization': api_key}).json()
        if isinstance(items, list):
            yield dict(msg=f'Got {len(items)} items.')
            yield from ensure_embeddings(items, workspace, api_key, params)
            items = [item for item in items if use_item(item)]
            items = sorted(items, key=lambda x: x['created_at'], reverse=True)
            items = items[:max_per_workspace]
            for item in items:
                item['workspace_title'] = workspace_metadata.get('title') or workspace_metadata.get('context-label') or workspace_metadata.get('source')
            records.extend(items)
    records.sort(key=lambda x: x['created_at'], reverse=True)

def ensure_embeddings(records, workspace, api_key, params: TSNEParams):
    openai = OpenAI(api_key=params.OPENAI_KEY)
    for i, record in enumerate(records):
        if i % 100 == 0:
            yield dict(msg=f'Ensuring embedding {i}/{len(records)}...')
        if 'embedding' in record and record['embedding'] and len(record['embedding']) == params.EMBEDDING_DIMENSION:
            continue
        description = record['future_scenario_description']
        completion = openai.embeddings.create(
            model="text-embedding-3-large",
            input=description
        )
        embedding = completion.data[0].embedding
        record['embedding'] = embedding
        item_id = record['_id']
        requests.put(f'{params.CHRONOMAPS_API_URL}/{workspace}/{item_id}', json=dict(embedding=embedding), headers={'Authorization': api_key})

def generate_tsne(activations, perplexity=50, tsne_iter=5000):
    tsne = TSNE(perplexity=perplexity, n_components=2, init='random', max_iter=tsne_iter)
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

def white_patch(im: Image.Image,
                white_pct: float = 90.0,   # “bright” threshold (percentile)
                sat_thresh: float = 0.20    # drop coloured pixels
               ) -> Image.Image:
    """
    White-balance a scanned page by forcing the brightest,
    least-saturated pixels to 255 in every channel.
    """
    im = im.convert("RGB")
    arr = np.asarray(im).astype(np.float32)

    # 1. luminance & saturation
    lum  = arr.mean(axis=2)                       # cheap grayscale
    cmax = arr.max(axis=2);  cmin = arr.min(axis=2)
    sat  = np.where(cmax > 0, (cmax - cmin) / cmax, 0)

    # 2. pick candidate “paper” pixels
    bright   = lum >= np.percentile(lum, white_pct)
    neutral  = sat  < sat_thresh
    mask     = bright & neutral

    if not np.any(mask):                          # nothing found → fall back
        mean   = arr.mean(axis=(0, 1))
    else:
        mean   = arr[mask].reshape(-1, 3).mean(axis=0)

    # 3. linear scale so mean → 255
    scale  = 255.0 / mean
    result = np.clip(arr * scale, 0, 255).astype(np.uint8)

    return Image.fromarray(result, "RGB")

def gray_world(im: Image.Image) -> Image.Image:
    """
    Scale each RGB channel so that their means are equal
    (the classic 'gray-world' assumption).
    """
    im = im.convert("RGB")                       # be explicit
    arr = np.asarray(im).astype(np.float32)

    # channel means and scale factors
    mean = arr.mean(axis=(0, 1))
    target = mean.mean()                         # overall average
    scale = target / mean                        # three scale factors

    # apply, clip, back to PIL
    balanced = np.clip(arr * scale, 0, 255).astype(np.uint8)
    return Image.fromarray(balanced, "RGB")

# ---------- 2.  Contrast stretch to common black/white points ----------
def stretch_contrast(im: Image.Image,
                     low_pct: float = 1.0,
                     high_pct: float = 99.0) -> Image.Image:
    """
    Histogram-stretch so the low_pct / high_pct percentiles map to 0/255.
    Keeps extreme outliers from blowing the image out.
    """
    return ImageOps.autocontrast(
        im, cutoff=(low_pct, 100 - high_pct), preserve_tone=True
    )                                            # Pillow ≥8.0 supports tuple cutoff

def get_image(record, target_size, pos_x, pos_y, params: TSNEParams, save=None):
    # Open the image size, resize it to the target size (maintaining aspect ratio) and return a cropped image of the target size out the center
    metadata = dict()
    to_save = None
    if record is not None:
        filename = record.get('screenshot_url')
        filename = filename.replace('https://storage.googleapis.com/chronomaps3.firebasestorage.app', 'https://storage.googleapis.com/chronomaps3-eu')
        rotate = record.get('plausibility') if isinstance(record.get('plausibility'), int) else 100
        rotate = int(rotate)
        rotate = (100 - rotate) / 100 * 32
        favorable_future = record.get('favorable_future')
        sign = 0
        if favorable_future == 'yes' or 'prefer' in favorable_future:
            sign = 1
        elif favorable_future == 'no' or 'prevent' in favorable_future:
            sign = -1
        rotate = sign * rotate
        metadata = dict(
            rotate=rotate,
            sign=sign,
            mostly='mostly' in favorable_future,
            favorable_future=favorable_future,
            timestamp=record['created_at'],
            lang=record.get('detected_language'),
            url=filename
        )
    else:
        filename = None
        rotate = (521 * pos_x + 967 * pos_y) % 64 - 32   # Pseudo-random rotation
    inner_target_size = int(target_size[0] / params.CELL_RATIOS[0]), int(target_size[1] / params.CELL_RATIOS[1])
    if not filename:
        filename = Path(__file__).with_name('empty-space.png')
        img = Image.open(filename)
        _image = Image.new("RGBA", img.size, "WHITE") 
        _image.paste(img, (0, 0), img)         
        img = _image.convert('RGB')
        img = img.resize(inner_target_size, Image.Resampling.LANCZOS)
    else:
        side_ext = '' if params.SIDE == 1000 else f'.{params.SIDE}'
        enhanced_filename = filename.replace('.jpeg', f'.enhanced{side_ext}.jpeg')
        enhanced = True
        try:
            img = Image.open(requests.get(enhanced_filename, stream=True).raw)
            metadata['url'] = enhanced_filename
        except:
            try:
                img = Image.open(requests.get(filename, stream=True).raw)
                enhanced = False
            except Exception as e:
                print('Error opening image:', filename)
                raise

        if not enhanced:
            img = white_patch(img)
            img = stretch_contrast(img, low_pct=1, high_pct=99)
            img = img.resize(inner_target_size, Image.Resampling.LANCZOS)
            object_path = enhanced_filename.replace('https://storage.googleapis.com/chronomaps3-eu/', '')
            to_save = img, object_path

        if record.get('workspace_title') and params.ADD_TITLE:
            img = ImageOps.expand(img, border=(0, 0, 0, 48), fill=params.BG_COLOR)  # Add a border around the image
            draw = ImageDraw.Draw(img)

            # 2 – load a TrueType/OpenType font
            font = ImageFont.truetype(str(FONT), size=32)      # point size in pixels

            # 3 – measure the text & centre it
            bbox = draw.textbbox((0, 0), record.get('workspace_title'), font=font)     # (x0, y0, x1, y1)
            w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]

            xy   = ((inner_target_size[0]  - w)//2, inner_target_size[1] + 8)

            # 4 – draw (with optional outline for legibility)
            draw.text(xy, record.get('workspace_title'),
                    font=font,
                    fill='#000000',                # text colour
            )
                # stroke_width=1,                         # outline thickness
                # stroke_fill="white")                    # outline colour

        if params.LOCAL:
            os.makedirs('tsne-images', exist_ok=True)
            filename = f"{record['workspace_title']} - {record['future_scenario_tagline']}".replace('/', '-')
            img.save(f'tsne-images/{filename}.jpg', format='JPEG', quality=85)


    img = img.rotate(rotate, expand=True, fillcolor=params.BG_COLOR, resample=Image.Resampling.BICUBIC)
    out_img = Image.new('RGB', target_size, params.BG_COLOR)
    # assert target_size[0] >= img.width, f'{target_size[0]} < {img.width}'
    # assert target_size[1] >= img.height, f'{target_size[1]} < {img.height}'
    out_img.paste(img, ((target_size[0] - img.width) // 2, (target_size[1] - img.height) // 2))
    return out_img, metadata, to_save

def create_tsne_image(grid_jv, records, out_dim, res, offset, padding, pos_offset, tsne_out, params: TSNEParams):
    # print('>>>', filename)

    out_res_x, out_res_y = res
    offset_x, offset_y = offset
    padding_x, padding_y = padding

    info = dict(
        dim=out_dim,
        grid=[],
        padding_ratio=params.PADDING_RATIO,
        conversion_ratio=(out_res_x / 256, out_res_y / 256),
        cell_ratios=params.CELL_RATIOS
    )

    out = np.ones((out_dim[1]*out_res_y + padding_y, out_dim[0]*out_res_x + padding_x, 3), dtype=np.uint8) * np.array(params.BG_COLOR, dtype=np.uint8)
    positions = dict()
    for pos, record in zip(grid_jv, records):
        pos_x = round(pos[1] * (out_dim[0] - 1))# + img_ofs
        pos_y = round(pos[0] * (out_dim[1] - 1))# + img_ofs
        pos = (int(pos_y), int(pos_x))
        positions[pos] = record
    for pos_x in range(out_dim[0]):
        yield dict(msg=f'Creating image: {pos_x}/{out_dim[0]}')
        for pos_y in range(out_dim[1]):
            pos = (pos_y, pos_x)
            record = positions.get(pos)
            img, metadata, to_save = get_image(record, res, pos_x, pos_y, params)
            if to_save is not None:
                yield dict(action='save_image', image=to_save[0], path=to_save[1], metadata=metadata)
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
            if callable(pos_offset[0]):
                pos_offset_x = pos_offset[0](pos_x, pos_y)
            else:
                pos_offset_x = pos_offset[0]
            if callable(pos_offset[1]):
                pos_offset_y = pos_offset[1](pos_x, pos_y)
            else:
                pos_offset_y = pos_offset[1]
            if record is not None:
                info['grid'].append(dict(pos=[pos_x + pos_offset_x, pos_y + pos_offset_y], id=record['_id'], metadata=metadata))

    tsne_out['image'] = out
    tsne_out['info'] = info

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

def cluster_screenshots_inner(config, params: TSNEParams, last_state_hash=None):
    records = []
    yield from load_records(config, records, params)
    records = records[:params.TO_PLOT]
    yield dict(msg=f'GOT {len(records)} - top record created at {records[0]["created_at"] if records else "N/A"}')
    new_state_hash = ''.join([rec['_id'] for rec in records])
    new_state_hash = hashlib.md5(new_state_hash.encode('utf-8')).hexdigest()
    if last_state_hash and last_state_hash != new_state_hash:
        yield dict(msg=f'No new records - same hash ({last_state_hash})')
        return

    records, activations = records, [rec['embedding'] for rec in records]

    if len(records) > 0:
        yield dict(msg=f'Generating 2D representation from {len(records)} records.')
        X_2d = generate_tsne(activations, perplexity=params.PERPLEXITY, tsne_iter=params.TSNE_ITER)
        yield dict(msg="Generating image grid (%dx%d, %d images" % (params.OUT_DIM[0], params.OUT_DIM[1], len(records)))
        grid = calc_tsne_grid(X_2d, params.OUT_DIM)
        grid = grid[:len(records)]
        yield dict(msg=f"Got grid, X_2d.shape: {X_2d.shape}, grid shape: {grid.shape}")
    else:
        grid = []

    try:
        # w, h = 530, 1000
        w, h = params.ORIGINAL_IMAGE_SIZE[0] * params.CELL_RATIOS[0], params.ORIGINAL_IMAGE_SIZE[1] * params.CELL_RATIOS[1]
        dim = max(w, h)
        side = params.SIDE # get_side(w/dim, OUT_DIM_X)
        res = (int(side*w/dim), int(side*h/dim))
        if params.V_OFFSET:
            padding_y = int(res[1] * params.PADDING_RATIO)
            offset = (0, lambda x, _: padding_y * (x%2))
            pos_offset = (0, lambda x, _: params.PADDING_RATIO * (x%2))
            padding = (0, padding_y)
        else:
            padding_x = int(res[0] * params.PADDING_RATIO)
            offset = (lambda _, y: padding_x * (y%2), 0)
            pos_offset = (lambda _, y: params.PADDING_RATIO * (y%2), 0)
            padding = (padding_x, 0)

        yield dict(msg=f'Params: {params}')
        yield dict(msg=f'Creating image: {w}x{h} {side} {res} {padding}')
        tsne = {}
        yield from create_tsne_image(grid, records, params.OUT_DIM, res, offset, padding, pos_offset, tsne, params)
        image, info = tsne['image'], tsne['info']
        yield dict(msg=f'Got TSNE Image: {image.shape} {image.dtype}')
        image = Image.fromarray(image)
        if not params.LOCAL:
            yield dict(msg="Creating tiles.")
            yield dict(action='tiles', image=image)
        else:
            yield dict(msg="Creating Image.")        
            image.save('tsne.png', format='PNG', compress_level=9)

        yield dict(msg='Processing complete.')
        info['state_hash'] = new_state_hash
        yield dict(action='clusters', info=info, records=records, grid=grid)

    except Exception as e:
        yield dict(msg="Error generating image:", error=str(e))
        raise


if __name__ == '__main__':
    import sys
    import os
    config = sys.argv[1] if len(sys.argv) > 1 else ''
    configs = [c.strip().split(':') for c in config.split(';') if c.strip()]
    print(f'Configs: {configs}')

    params = TSNEParams(
        OUT_RATIO = 25/43,
        OUT_DIM_X = 32,
        CHRONOMAPS_API_URL='https://chronomaps-api-qjzuw7ypfq-ez.a.run.app',
        OPENAI_KEY=os.environ.get('OPENAI_KEY'),
        LOCAL= True,
        FILL_RATIO = 0.9,
        V_OFFSET = False,
        SIDE = 1600,
    )

    for item in cluster_screenshots_inner(configs, params):
        print(str(item)[:1000])

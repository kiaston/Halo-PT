#!/usr/bin/env python3
"""
add_round.py - convert Zeiss .czi images into zoomable tiles for the PT viewer.

Usage
-----
  # add a round with two images (newest rounds appear first in the viewer)
  python add_round.py --round "2026 Round 2" --czi slideA.czi --label "Specimen A" \
                                            --czi slideB.czi --label "Specimen B"

  # list rounds / remove a round
  python add_round.py --list
  python add_round.py --remove "2026 Round 2"

Output goes into ./docs (the folder GitHub Pages publishes).

Requirements: Python 3.9+, then  pip install pylibCZIrw pillow numpy
"""
import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from PIL import Image

Image.MAX_IMAGE_PIXELS = None

TILE = 512          # tile size in pixels
QUALITY = 85        # JPEG quality for tiles
CHUNK_TILES = 8     # full-res chunk read from the CZI = CHUNK_TILES*TILE square
HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.join(HERE, "docs")
MANIFEST = os.path.join(SITE, "manifest.json")

DEFAULT_COLORS = [(0, 255, 0), (255, 0, 255), (0, 128, 255), (255, 255, 0), (255, 0, 0), (0, 255, 255)]


# --------------------------------------------------------------------------- helpers
def slugify(s):
    s = re.sub(r"[^A-Za-z0-9]+", "-", s.strip()).strip("-").lower()
    return s or "item"


def load_manifest():
    if os.path.exists(MANIFEST):
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    return {"title": "Digital Microscopy Proficiency Testing", "rounds": []}


def save_manifest(m):
    os.makedirs(SITE, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=2)


def channel_colors(reader, n):
    """Try to read channel display colours from the CZI metadata, else defaults."""
    colors = []
    try:
        chans = reader.metadata["ImageDocument"]["Metadata"]["DisplaySetting"]["Channels"]["Channel"]
        if isinstance(chans, dict):
            chans = [chans]
        for ch in chans[:n]:
            c = ch.get("Color", "")
            c = c.lstrip("#")
            if len(c) == 8:  # #AARRGGBB
                c = c[2:]
            colors.append(tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)))
    except Exception:
        colors = []
    while len(colors) < n:
        colors.append(DEFAULT_COLORS[len(colors) % len(DEFAULT_COLORS)])
    return colors


def pixel_size_um(reader):
    """Pixel size in micrometres (X), if recorded."""
    try:
        dist = reader.metadata["ImageDocument"]["Metadata"]["Scaling"]["Items"]["Distance"]
        if isinstance(dist, dict):
            dist = [dist]
        for d in dist:
            if d.get("@Id") == "X" and float(d["Value"]) > 0:
                return float(d["Value"]) * 1e6
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- rendering
def list_scenes(path):
    """Return the scanned areas (scenes) in the file as (x, y, w, h) rectangles."""
    from pylibCZIrw import czi
    with czi.open_czi(path) as r:
        scenes = r.scenes_bounding_rectangle
        if len(scenes) > 1:
            return [tuple(scenes[k]) for k in sorted(scenes)]
        return [tuple(r.total_bounding_rectangle)]


def make_render_plan(path, z, t, roi=None, gamma=1.0):
    """Inspect the file once and decide how to turn raw pixels into 8-bit RGB."""
    from pylibCZIrw import czi
    with czi.open_czi(path) as r:
        box = r.total_bounding_box
        rect = czi.Rectangle(*roi) if roi else r.total_bounding_rectangle
        n_ch = box.get("C", (0, 1))[1] - box.get("C", (0, 1))[0]
        ptypes = [r.get_channel_pixel_type(c) for c in range(n_ch)]
        plan = {"rect": (rect.x, rect.y, rect.w, rect.h), "plane": {"Z": z, "T": t},
                "ptypes": ptypes, "um_per_px": pixel_size_um(r), "gamma": gamma}

        if ptypes[0].startswith("Bgr"):
            plan["mode"] = "rgb"
            plan["bg"] = (1.0, 1.0, 1.0)   # brightfield: white background outside scan area
            if ptypes[0] == "Bgr48":
                plan["rgb_range"] = sample_range(r, rect, {"C": 0, "Z": z, "T": t}, rgb=True)
        else:
            plan["mode"] = "fluor" if n_ch > 1 else "gray"
            plan["bg"] = (0.0, 0.0, 0.0)
            plan["colors"] = channel_colors(r, n_ch) if n_ch > 1 else [(255, 255, 255)]
            plan["ranges"] = [sample_range(r, rect, {"C": c, "Z": z, "T": t}) for c in range(n_ch)]
    return plan


def sample_range(r, rect, plane, rgb=False):
    """Auto-contrast: 0.1 / 99.9 percentiles of a low-res overview."""
    zoom = min(1.0, 2000.0 / max(rect.w, rect.h))
    a = r.read(roi=(rect.x, rect.y, rect.w, rect.h), plane=plane, zoom=zoom).astype(np.float32)
    a = a[a > 0] if (a > 0).any() else a.ravel()
    lo, hi = np.percentile(a, [0.1, 99.9])
    if hi <= lo:
        hi = lo + 1
    return float(lo), float(hi)


def render(r, plan, roi):
    """Read one region from the CZI and return an 8-bit RGB numpy array."""
    plane = dict(plan["plane"])
    if plan["mode"] == "rgb":
        plane["C"] = 0
        a = r.read(roi=roi, plane=plane, background_pixel=plan["bg"])
        a = a[..., ::-1]  # BGR -> RGB
        if a.dtype != np.uint8:
            lo, hi = plan["rgb_range"]
            a = np.clip((a.astype(np.float32) - lo) * (255.0 / (hi - lo)), 0, 255).astype(np.uint8)
        return apply_gamma(np.ascontiguousarray(a), plan)
    out = None
    for c, (color, (lo, hi)) in enumerate(zip(plan["colors"], plan["ranges"])):
        plane["C"] = c
        a = r.read(roi=roi, plane=plane, background_pixel=plan["bg"])[..., 0].astype(np.float32)
        a = np.clip((a - lo) / (hi - lo), 0, 1)
        layer = a[..., None] * np.array(color, np.float32)[None, None, :]
        out = layer if out is None else out + layer
    return apply_gamma(np.clip(out, 0, 255).astype(np.uint8), plan)


def apply_gamma(a, plan):
    g = plan.get("gamma", 1.0)
    if g == 1.0:
        return a
    lut = (255.0 * (np.arange(256) / 255.0) ** g).round().astype(np.uint8)
    return lut[a]


# --------------------------------------------------------------------------- tiling
def level_dims(w, h, max_level, level):
    s = 2 ** (max_level - level)
    return math.ceil(w / s), math.ceil(h / s)


def save_tile(img, path):
    img.save(path, "JPEG", quality=QUALITY, optimize=True)


def work_full_res(args):
    """Worker: read one chunk of the CZI at full resolution and cut it into tiles."""
    path, plan, out_dir, cx, cy = args
    from pylibCZIrw import czi
    X0, Y0, W, H = plan["rect"]
    chunk = TILE * CHUNK_TILES
    x0, y0 = cx * chunk, cy * chunk
    w, h = min(chunk, W - x0), min(chunk, H - y0)
    with czi.open_czi(path) as r:
        a = render(r, plan, (X0 + x0, Y0 + y0, w, h))
    n = 0
    for ty in range(math.ceil(h / TILE)):
        for tx in range(math.ceil(w / TILE)):
            tile = a[ty * TILE:(ty + 1) * TILE, tx * TILE:(tx + 1) * TILE]
            col, row = cx * CHUNK_TILES + tx, cy * CHUNK_TILES + ty
            save_tile(Image.fromarray(tile), os.path.join(out_dir, f"{col}_{row}.jpg"))
            n += 1
    return n


def work_downsample(args):
    """Worker: build one lower-level tile from its (up to) four children."""
    src_dir, dst_dir, col, row, lw, lh = args
    tw, th = min(TILE, lw - col * TILE), min(TILE, lh - row * TILE)
    canvas = Image.new("RGB", (tw * 2, th * 2))
    cw = ch = 0
    for dy in (0, 1):
        for dx in (0, 1):
            p = os.path.join(src_dir, f"{col * 2 + dx}_{row * 2 + dy}.jpg")
            if os.path.exists(p):
                with Image.open(p) as im:
                    canvas.paste(im, (dx * TILE, dy * TILE))
                    cw = max(cw, dx * TILE + im.width)
                    ch = max(ch, dy * TILE + im.height)
    canvas = canvas.crop((0, 0, cw, ch)).resize((tw, th), Image.LANCZOS)
    save_tile(canvas, os.path.join(dst_dir, f"{col}_{row}.jpg"))


def convert(path, out_base, z=0, t=0, workers=None, roi=None, gamma=1.0):
    """Convert one CZI (or one scene of it) into a Deep Zoom pyramid at out_base.dzi + out_base_files/."""
    t0 = time.time()
    plan = make_render_plan(path, z, t, roi, gamma)
    _, _, W, H = plan["rect"]
    max_level = math.ceil(math.log2(max(W, H)))
    files = out_base + "_files"
    if os.path.exists(files):
        shutil.rmtree(files)
    print(f"  {os.path.basename(path)}: {W} x {H} px, {plan['mode']} "
          f"({', '.join(plan['ptypes'])}), {max_level + 1} zoom levels")

    workers = workers or max(1, min(8, (os.cpu_count() or 2)))
    chunk = TILE * CHUNK_TILES
    full_dir = os.path.join(files, str(max_level))
    os.makedirs(full_dir)
    jobs = [(path, plan, full_dir, cx, cy)
            for cy in range(math.ceil(H / chunk)) for cx in range(math.ceil(W / chunk))]
    done = 0
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(work_full_res, j) for j in jobs]):
            f.result()
            done += 1
            print(f"\r    full resolution: {done}/{len(jobs)} chunks", end="", flush=True)
        print()
        for level in range(max_level - 1, -1, -1):
            lw, lh = level_dims(W, H, max_level, level)
            dst = os.path.join(files, str(level))
            os.makedirs(dst)
            src = os.path.join(files, str(level + 1))
            jobs = [(src, dst, c, r, lw, lh)
                    for r in range(math.ceil(lh / TILE)) for c in range(math.ceil(lw / TILE))]
            list(ex.map(work_downsample, jobs, chunksize=16))
            print(f"\r    building zoom levels: {max_level - level}/{max_level}", end="", flush=True)
        print()

    with open(out_base + ".dzi", "w") as f:
        f.write(f'<?xml version="1.0" encoding="UTF-8"?>\n'
                f'<Image xmlns="http://schemas.microsoft.com/deepzoom/2008" Format="jpg" '
                f'Overlap="0" TileSize="{TILE}"><Size Width="{W}" Height="{H}"/></Image>\n')
    n_tiles = sum(len(fs) for _, _, fs in os.walk(files))
    size_mb = sum(os.path.getsize(os.path.join(d, x)) for d, _, fs in os.walk(files) for x in fs) / 1e6
    print(f"    done: {n_tiles} tiles, {size_mb:.0f} MB, {time.time() - t0:.0f}s")
    return {"width": W, "height": H, "tileSize": TILE, "overlap": 0, "format": "jpg",
            "umPerPixel": plan["um_per_px"]}


# --------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="Convert CZI images into a PT round for the viewer.")
    ap.add_argument("--round", help='round name, e.g. "2026 Round 2"')
    ap.add_argument("--czi", action="append", default=[], help="path to a .czi file (repeat)")
    ap.add_argument("--label", action="append", default=[], help="label for the matching --czi (repeat)")
    ap.add_argument("--z", type=int, default=0, help="Z-plane to use for z-stacks (default 0)")
    ap.add_argument("--gamma", type=float, default=1.0,
                    help="brightness curve: below 1 lightens midtones, above 1 darkens (default 1 = unchanged)")
    ap.add_argument("--combine-scenes", action="store_true",
                    help="keep all scanned areas of a file on one canvas instead of one tab per area")
    ap.add_argument("--title", help="set the site title shown in the header")
    ap.add_argument("--workers", type=int, help="parallel processes (default: CPU count, max 8)")
    ap.add_argument("--list", action="store_true", help="list rounds")
    ap.add_argument("--remove", metavar="ROUND", help="remove a round and its tiles")
    a = ap.parse_args()

    m = load_manifest()
    if a.title:
        m["title"] = a.title
        save_manifest(m)
        print(f"Title set to: {a.title}")

    if a.list:
        for rd in m["rounds"]:
            print(f"- {rd['name']}: " + ", ".join(i["label"] for i in rd["images"]))
        return
    if a.remove:
        rid = slugify(a.remove)
        m["rounds"] = [rd for rd in m["rounds"] if rd["id"] != rid]
        shutil.rmtree(os.path.join(SITE, "rounds", rid), ignore_errors=True)
        save_manifest(m)
        print(f"Removed {a.remove}")
        return
    if not a.round or not a.czi:
        if not a.title:
            ap.print_help()
        return

    for p in a.czi:
        if not os.path.isfile(p):
            sys.exit(f"File not found: {p}")
    labels = a.label + [os.path.splitext(os.path.basename(p))[0] for p in a.czi[len(a.label):]]

    rid = slugify(a.round)
    rdir = os.path.join(SITE, "rounds", rid)
    os.makedirs(rdir, exist_ok=True)
    print(f"Round: {a.round}")
    images = []
    for p, label in zip(a.czi, labels):
        scenes = [None] if a.combine_scenes else list_scenes(p)
        if len(scenes) > 1:
            print(f"  {os.path.basename(p)} has {len(scenes)} scanned areas -> one tab each")
        for k, roi in enumerate(scenes, 1):
            name = f"image{len(images) + 1}"
            tab = f"{label} - area {k}" if len(scenes) > 1 else label
            info = convert(p, os.path.join(rdir, name), z=a.z, workers=a.workers, roi=roi, gamma=a.gamma)
            images.append({"label": tab, "dzi": f"rounds/{rid}/{name}.dzi", **info})

    m["rounds"] = [rd for rd in m["rounds"] if rd["id"] != rid]
    m["rounds"].insert(0, {"id": rid, "name": a.round, "images": images})
    save_manifest(m)
    print(f"\nSaved. Commit and push in GitHub Desktop to publish (see README).")


if __name__ == "__main__":
    main()

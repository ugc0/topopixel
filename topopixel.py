from logger import log
import os
import rasterio
os.environ["PROJ_LIB"] = os.path.join(os.path.dirname(rasterio.__file__), "proj_data")
os.environ["PROJ_DATA"] = os.environ["PROJ_LIB"]
import argparse
import math
import time
import requests
import rasterio
import numpy as np
import struct
import osmnx as ox
from shapely.geometry import Polygon, Point, shape, MultiPolygon, LineString as SLine, box
from shapely.ops import unary_union, polygonize, linemerge, snap
from shapely.geometry import Polygon as ShPoly
import geopandas as gpd
import pandas as pd
from rasterio.transform import from_bounds
from rasterio.merge import merge as rasterio_merge
from rasterio.windows import from_bounds as window_from_bounds
import rasterio.features
import trimesh
import triangle as tr
import traceback
import warnings
import requests as _req
import pickle
import re
from pyproj import Transformer
import uuid
import zipfile
import xml.etree.ElementTree as ET
from scipy.ndimage import gaussian_filter, uniform_filter
from scipy.cluster.vq import kmeans2
import io
import json
import boto3
import csv

OVERPASS_ENDPOINTS = {
    "private.coffee": "https://overpass.private.coffee/api",
    "gall": "https://gall.openstreetmap.de/api",
    "lambert": "https://lambert.openstreetmap.de/api"
}

_OSM_CACHE_SUBLABELS = {
    "water": ["water_areas", "waterways", "coastlines"],
    "vegetation": ["forest", "other_veg"],
}

def check_overpass_endpoints():
    status = {}
    for name, url in OVERPASS_ENDPOINTS.items():
        try:
            r = _req.get(f"{url}/status", headers={"User-Agent": "topopixel/1.0"}, timeout=(2, 2))
            status[name] = r.status_code == 200
        except Exception:
            status[name] = False
        log(f"[OVERPASS] {name} : {'OK' if status[name] else 'KO'}")
    return status

def apply_overpass_strategy(status):
    if status.get("private.coffee"):
        ox.settings.overpass_url = OVERPASS_ENDPOINTS["private.coffee"]
        log(f"[OVERPASS] stratégie : private.coffee (parallèle)")
        return "parallel"
    elif status.get("gall") and status.get("lambert"):
        ox.settings.overpass_url = "https://overpass-api.de/api"
        log(f"[OVERPASS] stratégie : gall+lambert (séquentiel)")
        return "sequential"
    elif status.get("gall") or status.get("lambert"):
        name = "gall" if status.get("gall") else "lambert"
        ox.settings.overpass_url = OVERPASS_ENDPOINTS[name]
        log(f"[OVERPASS] stratégie : {name} seul (séquentiel)")
        return "sequential"
    else:
        ox.settings.overpass_url = OVERPASS_ENDPOINTS["private.coffee"]
        log(f"[OVERPASS] aucun endpoint disponible")
        return "unavailable"

_overpass_status = {}
_overpass_strategy = "sequential"

warnings.filterwarnings("ignore", category=RuntimeWarning, module="trimesh")

os.environ["CPL_LOG"] = "NUL"

RADIUS_M = 3_000
GPXZ_API_KEY = os.environ.get("GPXZ_API_KEY", "")
RESOLUTION_M = 5
CACHE_DIR = "cache"

ROAD_LEVELS_DRIVABLE = ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street", "service", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link"]
ROAD_LEVELS_NON_DRIVABLE = ["pedestrian", "track", "footway", "bridleway", "steps", "corridor", "path", "cycleway"]
ROAD_LEVELS_RAIL = ["railway"]
ROAD_LEVELS = ROAD_LEVELS_DRIVABLE + ROAD_LEVELS_NON_DRIVABLE + ROAD_LEVELS_RAIL
MIN_WATERWAY_LENGTH_M = 100
MIN_WATER_AREA_M2 = 1000
BASE_THICKNESS = 20
ROAD_HEIGHT = 6.0
WATER_HEIGHT = 3.0
MIN_BUILDING_AREA_M2 = 500.0
MIN_VEG_AREA_M2 = 500.0
DEFAULT_BUILDING_HEIGHT_M = 6.0
BUILDING_HEIGHT_SCALE = 10.0
METERS_PER_LEVEL = 3.0
GPXZ_URL = "https://api.gpxz.io/v1/elevation/hires-raster"
RIVER_WIDTH_PX = 3
MAX_AREA_KM2 = 9.0
Z_SCALE = 1.0
ROAD_WIDTH_PX = 1.0
SIZE_MM = 120.0
TREE_HEIGHT = 10.0
TREE_RADIUS = 4.0
TREE_DENSITY = 5
TREE_SECTIONS = 7
MAX_TREES = 6000
BUILDING_MIN_HEIGHT = 3.0
BUILDING_MAX_HEIGHT = 20.0
GPX_WIDTH_PX = 2.0
GPX_HEIGHT = 4.0
ROADS_Z_BOT_RATIO_PCT = 33
WATER_Z_BOT_RATIO_PCT = 33
VEG_Z_BOT_RATIO_PCT = 90
BUILDINGS_Z_BOT_RATIO_PCT = 90
GPX_Z_BOT_RATIO_PCT = 95
MAX_TERRAIN_GRID_DIM = 800

LCFM_CATALOGUE_URL = "https://s3.waw3-1.cloudferro.com/swift/v1/CatalogueCSV/landcover_landuse/dynamic_land_cover/lcm_global_10m_yearly_v1/lcm_global_10m_yearly_v1_cog.csv"
LCFM_S3_ENDPOINT = "https://eodata.dataspace.copernicus.eu/"

WORLDCOVER_S2_GRID_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/esa_worldcover_grid_composites.fgb"

LCFM_CLASS_TO_LAYER = {
    10: "vegetation",
    20: "vegetation",
    30: "vegetation",
    40: "vegetation",
    50: "vegetation",
    60: "vegetation",
    70: "vegetation",
    80: "terrain",
    90: "buildings",
    100: "water",
    110: "terrain",
}

LCFM_TREE_CLASSES = {10}

DEFAULT_SATELLITE_CALIBRATION = {
    "water": [
        {"color": [85, 86, 61], "nir": 17, "threshold": 40},
        {"color": [25, 61, 56], "nir": 12, "threshold": 35},
    ],
    "vegetation": [
        {"color": [73, 107, 68], "nir": 188, "threshold": 80},
        {"color": [173, 163, 109], "nir": 166, "threshold": 60},
        {"color": [49, 65, 37], "nir": 195, "threshold": 45},
    ],
    "buildings": [
        {"color": [179, 136, 124], "nir": 77, "threshold": 45},
        {"color": [147, 139, 98], "nir": 135, "threshold": 40},
        {"color": [177, 100, 73], "nir": 149, "threshold": 45},
        {"color": [92, 72, 60], "nir": 66, "threshold": 35},
    ],
    "trees": [
        {"color": [55, 85, 50], "nir": 230, "threshold": 40},
    ],
}

def make_latlon_to_pixel(bbox, cols, rows):
    west, east = bbox["west"], bbox["east"]
    south, north = bbox["south"], bbox["north"]

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - west) / (east - west) * (cols - 1)
        y = (lat - south) / (north - south) * (rows - 1)
        return x, y

    return latlon_to_pixel_xy

def pixel_bbox_polygon(cols, rows):
    return ShPoly([(0, 0), (cols - 1, 0), (cols - 1, rows - 1), (0, rows - 1)])

def make_polygon_projector(bbox, cols, rows, clip_to_bbox=True):
    latlon_to_pixel_xy = make_latlon_to_pixel(bbox, cols, rows)
    bbox_pixel = pixel_bbox_polygon(cols, rows) if clip_to_bbox else None

    def project_polygon(geom):
        if geom.geom_type != "Polygon":
            return None
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords]
                     for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        if bbox_pixel is not None:
            p = p.intersection(bbox_pixel)
            if not p.is_valid:
                p = p.buffer(0)
        return p if not p.is_empty and p.area > 0 else None

    return project_polygon


def meters_to_deg(meters, latitude):
    lat_deg = meters / 111_320
    lon_deg = meters / (111_320 * math.cos(math.radians(latitude)))
    return lat_deg, lon_deg

def compute_bbox(lat, lon, radius_m=RADIUS_M):
    lat_delta, lon_delta = meters_to_deg(radius_m, lat)
    return {
        "south": lat - lat_delta,
        "north": lat + lat_delta,
        "west": lon - lon_delta,
        "east": lon + lon_delta,
    }

def split_bbox(bbox, max_area_km2=MAX_AREA_KM2):
    lat_center = (bbox["north"] + bbox["south"]) / 2
    lat_deg_per_km = 1 / 111.32
    lon_deg_per_km = 1 / (111.32 * math.cos(math.radians(lat_center)))

    lat_span = bbox["north"] - bbox["south"]
    lon_span = bbox["east"] - bbox["west"]
    lat_km = lat_span / lat_deg_per_km
    lon_km = lon_span / lon_deg_per_km
    total_km2 = lat_km * lon_km

    if total_km2 <= max_area_km2:
        return [bbox]

    tile_km = math.sqrt(max_area_km2)
    n_lat = math.ceil(lat_km / tile_km)
    n_lon = math.ceil(lon_km / tile_km)

    tiles = []
    lat_step = lat_span / n_lat
    lon_step = lon_span / n_lon
    for i in range(n_lat):
        for j in range(n_lon):
            tiles.append({
                "south": bbox["south"] + i * lat_step,
                "north": bbox["south"] + (i + 1) * lat_step,
                "west":  bbox["west"]  + j * lon_step,
                "east":  bbox["west"]  + (j + 1) * lon_step,
            })

    log(f"Zone {total_km2:.1f}km² découpée en {len(tiles)} tuiles ({n_lat}×{n_lon})")
    return tiles

def _lcfm_catalogue_path(cache_dir=CACHE_DIR):
    return os.path.join(cache_dir, "landcover", "lcm_global_10m_yearly_v1_cog.csv")

def _fetch_lcfm_catalogue(cache_dir=CACHE_DIR, max_age_days=7):
    path = _lcfm_catalogue_path(cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400
        if age_days < max_age_days:
            return path
    try:
        r = requests.get(LCFM_CATALOGUE_URL, timeout=60)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        log("[LANDCOVER] catalogue LCFM téléchargé")
    except Exception as e:
        if os.path.exists(path):
            log(f"[LANDCOVER] catalogue LCFM non rafraîchi ({e}), utilisation du cache existant")
        else:
            log(f"[LANDCOVER] catalogue LCFM inaccessible : {e}")
            return None
    return path

def _parse_lcfm_bbox(bbox_str):
    coords_str = bbox_str.strip().removeprefix("POLYGON((").removesuffix("))")
    pts = [tuple(map(float, p.split())) for p in coords_str.split(",")]
    lons = [p[0] for p in pts]
    lats = [p[1] for p in pts]
    return min(lons), min(lats), max(lons), max(lats)

def find_lcfm_tiles(bbox, cache_dir=CACHE_DIR):
    catalogue_path = _fetch_lcfm_catalogue(cache_dir)
    if catalogue_path is None:
        return []
    best_by_tile = {}
    with open(catalogue_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            west, south, east, north = _parse_lcfm_bbox(row["bbox"])
            if east < bbox["west"] or west > bbox["east"] or north < bbox["south"] or south > bbox["north"]:
                continue
            tile_key = (west, south)
            nominal_date = row["nominal_date"]
            if tile_key not in best_by_tile or nominal_date > best_by_tile[tile_key]["nominal_date"]:
                best_by_tile[tile_key] = row
    log(f"[LANDCOVER] {len(best_by_tile)} tuile(s) LCFM trouvée(s) pour la bbox")
    return list(best_by_tile.values())

def download_lcfm_tiles(bbox, access_key, secret_key, cache_dir=CACHE_DIR):
    rows = find_lcfm_tiles(bbox, cache_dir)
    if not rows:
        return []
    client = boto3.client(
        "s3",
        endpoint_url=LCFM_S3_ENDPOINT,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name="default",
    )
    landcover_dir = os.path.join(cache_dir, "landcover")
    os.makedirs(landcover_dir, exist_ok=True)
    paths = []
    for row in rows:
        s3_path = row["s3_path"]
        prefix = s3_path[len("s3://eodata/"):]
        tile_id = row["name"].removesuffix("_cog")
        real_key = f"{prefix}/{tile_id}_MAP.tif"
        fname = f"{tile_id}_MAP.tif"
        local_path = os.path.join(landcover_dir, fname)
        if os.path.exists(local_path):
            log(f"[LANDCOVER CACHE] hit : {fname}")
            paths.append(local_path)
            continue
        try:
            client.download_file("eodata", real_key, local_path)
            log(f"[LANDCOVER] téléchargé : {fname}")
            paths.append(local_path)
        except Exception as e:
            log(f"[LANDCOVER] téléchargement échoué pour '{real_key}' : {e}")
    return paths

def reproject_landcover_to_grid(tile_paths, dst_transform, dst_crs, dst_shape, resolution_m):
    if not tile_paths:
        return None

    from rasterio.warp import reproject, Resampling

    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        if len(srcs) > 1:
            mosaic, mosaic_transform = rasterio_merge(srcs, method="first")
            mosaic_data = mosaic[0]
        else:
            mosaic_data = srcs[0].read(1)
            mosaic_transform = srcs[0].transform
        mosaic_crs = srcs[0].crs
    finally:
        for s in srcs:
            s.close()

    target_height, target_width = dst_shape
    landcover = np.zeros((target_height, target_width), dtype=np.uint8)

    resampling_method = Resampling.nearest if resolution_m <= 10 else Resampling.mode

    reproject(
        source=mosaic_data,
        destination=landcover,
        src_transform=mosaic_transform,
        src_crs=mosaic_crs,
        dst_transform=dst_transform,
        dst_crs=dst_crs,
        resampling=resampling_method,
        src_nodata=255,
        dst_nodata=255,
    )

    log(f"[LANDCOVER] recalé sur la grille DEM : shape={landcover.shape} resampling={resampling_method.name}")
    return landcover

def build_landcover_layer_masks(landcover_grid):
    layer_masks = {}
    for code, layer in LCFM_CLASS_TO_LAYER.items():
        if layer == "terrain":
            continue
        mask = landcover_grid == code
        if not mask.any():
            continue
        layer_masks.setdefault(layer, np.zeros_like(mask))
        layer_masks[layer] |= mask

    tree_mask = np.zeros_like(landcover_grid, dtype=bool)
    for code in LCFM_TREE_CLASSES:
        tree_mask |= landcover_grid == code

    log(f"[LANDCOVER] masques par couche : {[(k, int(v.sum())) for k, v in layer_masks.items()]}")
    return layer_masks, tree_mask

def _majority_filter(mask, size=3):
    smoothed = uniform_filter(mask.astype(np.float32), size=size, mode="nearest")
    return smoothed > 0.5

def _mask_to_polygon(mask, min_area_px=2.0):
    mask = np.flipud(mask)
    mask = _majority_filter(mask)
    if not mask.any():
        return None
    identity = rasterio.transform.Affine.identity()
    shapes_gen = rasterio.features.shapes(mask.astype(np.uint8), mask=mask, transform=identity)
    polys = [shape(geom) for geom, value in shapes_gen]
    if not polys:
        return None
    merged = unary_union(polys).buffer(0.5, join_style=1).buffer(-0.3, join_style=1)
    if merged.is_empty:
        return None
    geoms = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    geoms = [g for g in geoms if g.is_valid and g.area >= min_area_px]
    return geoms

def polygons_to_geo_features(polys, bbox, shape, layer, source):
    rows, cols = shape

    def pixel_to_lonlat(x, y):
        lon = bbox["west"] + x / (cols - 1) * (bbox["east"] - bbox["west"])
        lat = bbox["south"] + y / (rows - 1) * (bbox["north"] - bbox["south"])
        return lon, lat

    features = []
    for poly in polys:
        synthetic_id = _pixel_poly_to_synthetic_id(poly, bbox, shape, layer, source)
        exterior = [pixel_to_lonlat(x, y) for x, y in poly.exterior.coords]
        interiors = [[pixel_to_lonlat(x, y) for x, y in ring.coords] for ring in poly.interiors]
        geo_poly = ShPoly(exterior, interiors)
        if not geo_poly.is_valid:
            geo_poly = geo_poly.buffer(0)
        if geo_poly.is_empty:
            continue
        features.append({"id": synthetic_id, "type": layer, "source": source, "geometry": geo_poly})
    return features

def _pixel_poly_to_synthetic_id(poly, bbox, shape, layer, source):
    rows, cols = shape
    rep = poly.convex_hull.centroid
    lon = bbox["west"] + rep.x / (cols - 1) * (bbox["east"] - bbox["west"])
    lat = bbox["south"] + rep.y / (rows - 1) * (bbox["north"] - bbox["south"])
    width_m = (bbox["east"] - bbox["west"]) * 111320 * math.cos(math.radians((bbox["north"] + bbox["south"]) / 2))
    height_m = (bbox["north"] - bbox["south"]) * 111320
    pixel_area_m2 = (width_m / cols) * (height_m / rows)
    area_m2 = poly.area * pixel_area_m2
    area_bucket = 0
    area = area_m2
    while area > 25:
        area /= 2
        area_bucket += 1
    return f"{layer}_{source}_{lat:.4f}_{lon:.4f}_{area_bucket}"
    
def is_fill_polygon_excluded(poly, bbox, shape, layer, source, excluded_fill_features, resolution_m):
    if not excluded_fill_features:
        return False
    rows, cols = shape
    rep = poly.convex_hull.centroid
    lon = bbox["west"] + rep.x / (cols - 1) * (bbox["east"] - bbox["west"])
    lat = bbox["south"] + rep.y / (rows - 1) * (bbox["north"] - bbox["south"])

    threshold_m = resolution_m * 4
    lat_rad = math.radians((bbox["north"] + bbox["south"]) / 2)

    for excl in excluded_fill_features:
        if excl["type"] != layer or excl["source"] != source:
            continue
        dx_m = (lon - excl["lon"]) * 111320 * math.cos(lat_rad)
        dy_m = (lat - excl["lat"]) * 111320
        dist_m = math.hypot(dx_m, dy_m)
        if dist_m <= threshold_m:
            return True
    return False

def landcover_masks_to_polygons(layer_masks, tree_mask, bbox=None, shape=None, excluded_fill_features=None, resolution_m=5):
    excluded_fill_features = excluded_fill_features or []
    layer_polygons = {}
    for layer, mask in layer_masks.items():
        polys = _mask_to_polygon(mask)
        if bbox is not None and shape is not None and polys:
            polys = [
                p for p in polys
                if not is_fill_polygon_excluded(p, bbox, shape, layer, "landcover", excluded_fill_features, resolution_m)
            ]
        poly = unary_union(polys) if polys else None
        if poly is not None:
            layer_polygons[layer] = poly
            log(f"[LANDCOVER] polygone '{layer}' : aire={poly.area:.1f}px²")
        else:
            log(f"[LANDCOVER] polygone '{layer}' : vide après filtrage/lissage")

    tree_polys = _mask_to_polygon(tree_mask)
    if bbox is not None and shape is not None and tree_polys:
        tree_polys = [
            p for p in tree_polys
            if not is_fill_polygon_excluded(p, bbox, shape, "trees", "landcover", excluded_fill_features, resolution_m)
        ]
    tree_polygon = unary_union(tree_polys) if tree_polys else None
    if tree_polygon is not None:
        log(f"[LANDCOVER] polygone 'trees' : aire={tree_polygon.area:.1f}px²")

    return layer_polygons, tree_polygon

def _worldcover_s2_grid_path(cache_dir=CACHE_DIR):
    return os.path.join(cache_dir, "satellite", "esa_worldcover_grid_composites.fgb")

def _fetch_worldcover_s2_grid(cache_dir=CACHE_DIR, max_age_days=30):
    path = _worldcover_s2_grid_path(cache_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if os.path.exists(path):
        age_days = (time.time() - os.path.getmtime(path)) / 86400
        if age_days < max_age_days:
            return path
    try:
        r = requests.get(WORLDCOVER_S2_GRID_URL, timeout=60)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        log("[SATELLITE] grille des tuiles S2 téléchargée")
    except Exception as e:
        if os.path.exists(path):
            log(f"[SATELLITE] grille non rafraîchie ({e}), utilisation du cache existant")
        else:
            log(f"[SATELLITE] grille inaccessible : {e}")
            return None
    return path

def find_worldcover_s2_tiles(bbox, cache_dir=CACHE_DIR):
    grid_path = _fetch_worldcover_s2_grid(cache_dir)
    if grid_path is None:
        return []
    grid = gpd.read_file(grid_path)
    query_box = box(bbox["west"], bbox["south"], bbox["east"], bbox["north"])
    matches = grid[grid.geometry.intersects(query_box)]
    log(f"[SATELLITE] {len(matches)} tuile(s) S2 trouvée(s) pour la bbox")
    return matches

def download_worldcover_s2_tiles(bbox, cache_dir=CACHE_DIR):
    matches = find_worldcover_s2_tiles(bbox, cache_dir)
    if len(matches) == 0:
        return []
    satellite_dir = os.path.join(cache_dir, "satellite")
    os.makedirs(satellite_dir, exist_ok=True)
    paths = []
    for _, row in matches.iterrows():
        s3_uri = row.get("s2_rgbnir_2021") or row.get("s2_rgbnir_2020")
        if not s3_uri:
            log(f"[SATELLITE] ligne de grille sans URL rgbnir exploitable, tuile={row.get('tile')}")
            continue
        key = s3_uri[len("s3://esa-worldcover-s2/"):]
        url = f"https://esa-worldcover-s2.s3.eu-central-1.amazonaws.com/{key}"
        tile_id = os.path.splitext(os.path.basename(key))[0]
        crop_fname = f"{tile_id}_crop_{bbox['west']:.4f}_{bbox['south']:.4f}_{bbox['east']:.4f}_{bbox['north']:.4f}.tif"
        local_path = os.path.join(satellite_dir, crop_fname)
        if os.path.exists(local_path):
            log(f"[SATELLITE CACHE] hit : {crop_fname}")
            paths.append(local_path)
            continue
        try:
            with rasterio.open(f"/vsicurl/{url}") as src:
                window = rasterio.windows.from_bounds(
                    bbox["west"], bbox["south"], bbox["east"], bbox["north"],
                    transform=src.transform
                )
                window = window.round_lengths().round_offsets()
                data = src.read(window=window, boundless=True, fill_value=0)
                window_transform = src.window_transform(window)
                profile = src.profile.copy()
                profile.update({"height": data.shape[1], "width": data.shape[2], "transform": window_transform})
            with rasterio.open(local_path, "w", **profile) as dst:
                dst.write(data)
            log(f"[SATELLITE] fenêtre téléchargée ({data.shape[2]}x{data.shape[1]}px) : {crop_fname}")
            paths.append(local_path)
        except Exception as e:
            log(f"[SATELLITE] téléchargement échoué pour {url} : {e}")
    return paths

def reproject_satellite_to_grid(tile_paths, dst_transform, dst_crs, dst_shape):
    if not tile_paths:
        return None

    from rasterio.warp import reproject, Resampling

    srcs = [rasterio.open(p) for p in tile_paths]
    try:
        if len(srcs) > 1:
            mosaic, mosaic_transform = rasterio_merge(srcs, method="first")
        else:
            mosaic = srcs[0].read()
            mosaic_transform = srcs[0].transform
        mosaic_crs = srcs[0].crs
        band_count = mosaic.shape[0]
    finally:
        for s in srcs:
            s.close()

    target_height, target_width = dst_shape
    rgb = np.zeros((band_count, target_height, target_width), dtype=np.float32)

    for band_idx in range(band_count):
        reproject(
            source=mosaic[band_idx],
            destination=rgb[band_idx],
            src_transform=mosaic_transform,
            src_crs=mosaic_crs,
            dst_transform=dst_transform,
            dst_crs=dst_crs,
            resampling=Resampling.bilinear,
            src_nodata=0,
            dst_nodata=0,
        )

    log(f"[SATELLITE] recalé sur la grille DEM : shape={rgb.shape}")
    return rgb

def download_dem_gpxz_tiled(bbox, api_key, resolution_m=RESOLUTION_M, cache_dir=CACHE_DIR):
    tiles = split_bbox(bbox)
    if len(tiles) == 1:
        return download_dem_gpxz(bbox, api_key, resolution_m, cache_dir)

    from rasterio.warp import reproject, Resampling, calculate_default_transform

    dst_crs = "EPSG:4326"
    target_width = max(1, round((bbox["east"] - bbox["west"]) * 111320 * math.cos(math.radians((bbox["north"]+bbox["south"])/2)) / resolution_m))
    target_height = max(1, round((bbox["north"] - bbox["south"]) * 111320 / resolution_m))
    dst_transform = from_bounds(bbox["west"], bbox["south"], bbox["east"], bbox["north"], target_width, target_height)

    elevation = np.full((target_height, target_width), np.nan, dtype=np.float32)

    for i, tile in enumerate(tiles):
        log(f"Tuile {i+1}/{len(tiles)}...")
        exact_cache = get_cache_path(tile, resolution_m, cache_dir)
        deja_en_cache = os.path.exists(exact_cache)
        tile_path = download_dem_gpxz(tile, api_key, resolution_m, cache_dir)

        with rasterio.open(tile_path) as src:
            src_data = src.read(1)
            log(f"[TUILE {i+1}] bbox=({tile['south']:.4f},{tile['north']:.4f},{tile['west']:.4f},{tile['east']:.4f}) "
                  f"shape={src_data.shape} min={src_data.min():.1f} max={src_data.max():.1f} "
                  f"source={'CACHE (' + tile_path + ')' if deja_en_cache else 'TELECHARGEMENT FRAIS'}")

            reprojected = np.full((target_height, target_width), np.nan, dtype=np.float32)
            reproject(
                source=src_data,
                destination=reprojected,
                src_transform=src.transform,
                src_crs=src.crs,
                dst_transform=dst_transform,
                dst_crs=dst_crs,
                resampling=Resampling.bilinear,
                src_nodata=np.nan,
                dst_nodata=np.nan,
            )

        valid_mask = ~np.isnan(reprojected)
        elevation[valid_mask] = reprojected[valid_mask]

    log(f"[DEBUG tiled] Matrice assemblée : {elevation.shape}")
    log(f"[DEBUG tiled] élévation min={np.nanmin(elevation):.1f} max={np.nanmax(elevation):.1f} — vérifier cohérence géographique")

    assembled_path = get_cache_path(bbox, resolution_m, cache_dir)
    with rasterio.open(assembled_path, "w", driver="GTiff",
                       height=target_height, width=target_width, count=1,
                       dtype="float32", crs=dst_crs, transform=dst_transform) as dst:
        dst.write(elevation.astype(np.float32), 1)
    log(f"[DEBUG tiled] DEM assemblé sauvegardé : {assembled_path}")
    return assembled_path

def get_cache_path(bbox, resolution_m, cache_dir="cache"):
    os.makedirs(cache_dir, exist_ok=True)
    key = f"{bbox['south']:.4f}_{bbox['north']:.4f}_{bbox['west']:.4f}_{bbox['east']:.4f}_{resolution_m}m"
    return os.path.join(cache_dir, f"dem_{key}.tif")
    
def _parse_cache_bbox(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    m = re.match(r"dem_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_(\d+)m", name)
    if not m:
        return None
    south, north, west, east, res = m.groups()
    return {
        "south": float(south), "north": float(north),
        "west": float(west),   "east": float(east),
        "resolution_m": int(res),
    }

def _find_covering_cache(bbox, resolution_m, cache_dir):
    if not os.path.isdir(cache_dir):
        return None
    margin = resolution_m / 111320 * 0.5
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".tif"):
            continue
        fpath = os.path.join(cache_dir, fname)
        parsed = _parse_cache_bbox(fname)
        if parsed is None:
            continue
        if parsed["resolution_m"] != resolution_m:
            continue
        if (parsed["south"] <= bbox["south"] - margin and
            parsed["north"] >= bbox["north"] + margin and
            parsed["west"]  <= bbox["west"]  - margin and
            parsed["east"]  >= bbox["east"]  + margin):
            log(f"[CACHE] bbox demandée couverte par {fname}")
            
            log(f"[CACHE] cache couvrant trouvé : {fname}")
            log(f"[CACHE] cache bbox : south={parsed['south']} north={parsed['north']} west={parsed['west']} east={parsed['east']}")
            log(f"[CACHE] demandé   : south={bbox['south']} north={bbox['north']} west={bbox['west']} east={bbox['east']}")
            
            return fpath
    return None

def _find_covering_cache_mosaic(bbox, resolution_m, cache_dir, output_path):
    if not os.path.isdir(cache_dir):
        return None
    margin = resolution_m / 111320 * 0.5
    candidates = []
    for fname in os.listdir(cache_dir):
        if not fname.endswith(".tif"):
            continue
        parsed = _parse_cache_bbox(fname)
        if parsed is None or parsed["resolution_m"] != resolution_m:
            continue
        if (parsed["south"] < bbox["north"] and parsed["north"] > bbox["south"] and
            parsed["west"]  < bbox["east"]  and parsed["east"]  > bbox["west"]):
            candidates.append((os.path.join(cache_dir, fname), parsed))

    log(f"[CACHE_MOSAIC] {len(candidates)} tuile(s) candidate(s) pour bbox={bbox}")
    if len(candidates) < 2:
        return None

    union_south = min(p["south"] for _, p in candidates)
    union_north = max(p["north"] for _, p in candidates)
    union_west  = min(p["west"]  for _, p in candidates)
    union_east  = max(p["east"]  for _, p in candidates)

    log(f"[CACHE_MOSAIC] union candidates south={union_south} north={union_north} west={union_west} east={union_east}")
    if not (union_south <= bbox["south"] - margin and
            union_north >= bbox["north"] + margin and
            union_west  <= bbox["west"]  - margin and
            union_east  >= bbox["east"]  + margin):
        log("[CACHE_MOSAIC] union insuffisante pour couvrir la bbox demandée")
        return None

    srcs = [rasterio.open(p) for p, _ in candidates]
    try:
        mosaic, out_transform = rasterio_merge(srcs)
    finally:
        for s in srcs:
            s.close()

    rows, cols = mosaic.shape[1], mosaic.shape[2]
    with rasterio.open(output_path, "w", driver="GTiff",
                       height=rows, width=cols, count=1,
                       dtype="float32", transform=out_transform) as dst:
        dst.write(mosaic[0].astype(np.float32), 1)

    log(f"[CACHE_MOSAIC] assemblée depuis {len(candidates)} tuiles → {output_path} shape={mosaic.shape}")
    return output_path

def _extract_bbox_from_cache(source_path, bbox, output_path):
    from pyproj import Transformer
    with rasterio.open(source_path) as src:
        if src.crs and not src.crs.is_geographic:
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            west, south = transformer.transform(bbox["west"], bbox["south"])
            east, north = transformer.transform(bbox["east"], bbox["north"])
        else:
            west, south, east, north = bbox["west"], bbox["south"], bbox["east"], bbox["north"]

        window = window_from_bounds(west, south, east, north, src.transform)
        data = src.read(1, window=window)
        nodata = src.nodata

    if nodata is not None:
        data[data == nodata] = 0

    rows, cols = data.shape
    if rows == 0 or cols == 0:
        raise ValueError(f"Extraction vide : {rows}×{cols} px pour bbox={bbox}")

    out_transform = from_bounds(
        bbox["west"], bbox["south"], bbox["east"], bbox["north"], cols, rows
    )

    with rasterio.open(output_path, "w", driver="GTiff",
                       height=rows, width=cols, count=1,
                       dtype="float32", transform=out_transform) as dst:
        dst.write(data.astype(np.float32), 1)

    log(f"[CACHE] sous-région extraite → {output_path} ({rows}×{cols} px)")
    return output_path

def classify_satellite_pixels(rgb, calibration):
    red, green, blue, nir = rgb[0], rgb[1], rgb[2], rgb[3]
    valid_mask = (red > 0) | (green > 0) | (blue > 0)

    pixels_rgb_valid = np.stack([red[valid_mask], green[valid_mask], blue[valid_mask]], axis=1).astype(np.float64)
    pixels_nir_valid = nir[valid_mask].astype(np.float64)

    if len(pixels_rgb_valid) == 0:
        return {}

    scale_rgb = np.percentile(pixels_rgb_valid, 99)
    if scale_rgb <= 0:
        scale_rgb = 1.0
    scale_nir = np.percentile(pixels_nir_valid, 99)
    if scale_nir <= 0:
        scale_nir = 1.0

    r255 = np.clip(red.astype(np.float64) / scale_rgb, 0, 1) * 255
    g255 = np.clip(green.astype(np.float64) / scale_rgb, 0, 1) * 255
    b255 = np.clip(blue.astype(np.float64) / scale_rgb, 0, 1) * 255
    nir255 = np.clip(nir.astype(np.float64) / scale_nir, 0, 1) * 255

    pixels_scaled = np.stack([r255, g255, b255, nir255], axis=-1)

    layer_names = list(calibration.keys())
    best_layer_idx = np.full(red.shape, -1, dtype=np.int32)
    best_distance = np.full(red.shape, np.inf, dtype=np.float64)

    for layer_idx, layer in enumerate(layer_names):
        for ref in calibration[layer]:
            ref_vec = np.array(list(ref["color"]) + [ref.get("nir", 128)], dtype=np.float64)
            distance = np.linalg.norm(pixels_scaled - ref_vec, axis=-1)
            within_threshold = distance <= ref["threshold"]
            better = within_threshold & (distance < best_distance)
            best_distance = np.where(better, distance, best_distance)
            best_layer_idx = np.where(better, layer_idx, best_layer_idx)

    best_layer_idx = np.where(valid_mask, best_layer_idx, -1)

    layer_masks = {}
    for idx, layer in enumerate(layer_names):
        mask = best_layer_idx == idx
        if mask.any():
            layer_masks[layer] = mask

    counts = {k: int(v.sum()) for k, v in layer_masks.items()}
    log(f"[SATELLITE] classification directe par pixel : {counts}")
    return layer_masks

def satellite_masks_to_polygons(layer_masks, bbox=None, shape=None, excluded_fill_features=None, resolution_m=5):
    excluded_fill_features = excluded_fill_features or []
    layer_polygons = {}
    for layer, mask in layer_masks.items():
        polys = _mask_to_polygon(mask)
        if bbox is not None and shape is not None and polys:
            polys = [
                p for p in polys
                if not is_fill_polygon_excluded(p, bbox, shape, layer, "satellite", excluded_fill_features, resolution_m)
            ]
        poly = unary_union(polys) if polys else None
        if poly is not None:
            layer_polygons[layer] = poly
            log(f"[SATELLITE] polygone '{layer}' : aire={poly.area:.1f}px²")
    return layer_polygons

def compute_osm_coverage(*polygons):
    valid = [p for p in polygons if p is not None and not p.is_empty]
    if not valid:
        return None
    coverage = unary_union(valid)
    log(f"[PRIORITE] zone OSM couverte : aire={coverage.area:.1f}px²")
    return coverage

def _boolean_op(op, meshes, label):
    t0 = time.time()
    log(f"[{label}] boolean {op.__name__} : début")
    result = op(meshes, engine='manifold')
    log(f"[{label}] boolean {op.__name__} : terminé en {time.time() - t0:.1f}s")
    return result

def _extrude_watertight(poly, height, z_translate, label):
    try:
        m = trimesh.creation.extrude_polygon(poly, height=height)
        m.apply_translation([0, 0, z_translate])
    except Exception as e:
        log(f"[{label}] extrusion d'une pièce échouée : {e}")
        return None

    if not m.is_watertight:
        m.merge_vertices()
        trimesh.repair.fix_normals(m)
        trimesh.repair.fill_holes(m)

    if not m.is_watertight:
        return None

    return m

def _extrude_polygons_watertight(polygons, height, z_translate, label, min_area=0.5):
    meshes = []
    skipped = 0
    for poly in polygons:
        if poly is None or not poly.is_valid or poly.area < min_area:
            skipped += 1
            continue
        m = _extrude_watertight(poly, height, z_translate, label)
        if m is None:
            skipped += 1
            continue
        meshes.append(m)

    if skipped:
        log(f"[{label}] {skipped} pièce(s) écartée(s) sur {len(polygons)} (géométrie irréparable)")

    return meshes

def _concatenate_watertight(meshes, label):
    if not meshes:
        return None

    masque = trimesh.util.concatenate(meshes) if len(meshes) > 1 else meshes[0]
    trimesh.repair.fix_normals(masque)

    if not masque.is_watertight:
        masque.merge_vertices()
        trimesh.repair.fix_normals(masque)
        trimesh.repair.fill_holes(masque)

    log(f"[{label}] masque final : faces={len(masque.faces)} pièces={len(meshes)} watertight={masque.is_watertight}")
    return masque

def build_fill_mask(polygon, z_bot_ratio_pct, mesh_terrain):
    if polygon is None or polygon.is_empty:
        return None
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (z_bot_ratio_pct / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0
    geoms = list(polygon.geoms) if polygon.geom_type == "MultiPolygon" else [polygon]
    masque_meshes = []
    for part in geoms:
        if part.is_empty or part.area < 0.5:
            continue
        try:
            m = trimesh.creation.extrude_polygon(part, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            masque_meshes.append(m)
        except Exception as e:
            log(f"[FILL] extrusion échouée : {e}")
            continue
    if not masque_meshes:
        return None
    masque = trimesh.util.concatenate(masque_meshes) if len(masque_meshes) > 1 else masque_meshes[0]
    trimesh.repair.fix_normals(masque)
    return masque

def download_dem_gpxz(bbox, api_key, resolution_m=RESOLUTION_M, cache_dir=CACHE_DIR, max_retries=5):
    output_path = get_cache_path(bbox, resolution_m, cache_dir)

    if os.path.exists(output_path):
        log(f"DEM en cache exact : {output_path}")
        return output_path

    covering = _find_covering_cache(bbox, resolution_m, cache_dir)
    if covering is not None:
        log(f"DEM couvert par cache existant : {covering}")
        return _extract_bbox_from_cache(covering, bbox, output_path)

    mosaic_path = get_cache_path(bbox, resolution_m, cache_dir)
    mosaic = _find_covering_cache_mosaic(bbox, resolution_m, cache_dir, mosaic_path)
    if mosaic is not None:
        return mosaic

    log(f"Téléchargement DEM GPXZ ({resolution_m}m)...")
    params = {
        "res_m":       resolution_m,
        "bbox_left":   bbox["west"],
        "bbox_right":  bbox["east"],
        "bbox_bottom": bbox["south"],
        "bbox_top":    bbox["north"],
    }
    headers = {"x-api-key": api_key}

    for attempt in range(max_retries):
        response = requests.get(GPXZ_URL, params=params, headers=headers, timeout=120)

        if response.status_code == 200:
            break
        elif response.status_code == 429:
            wait = 2 ** attempt
            log(f"Rate limit 429 — attente {wait}s...")
            time.sleep(wait)
        else:
            raise RuntimeError(f"Erreur GPXZ : {response.status_code}\n{response.text}")
    else:
        raise RuntimeError("GPXZ : trop de tentatives échouées (429)")

    with open(output_path, "wb") as f:
        f.write(response.content)

    log(f"DEM sauvegardé : {output_path} ({os.path.getsize(output_path)} octets)")
    time.sleep(1.5)
    return output_path
 
def load_dem(dem_path):
    with rasterio.open(dem_path) as src:
        elevation = src.read(1).astype(np.float32)
        nodata = src.nodata

    if nodata is not None:
        elevation[elevation == nodata] = 0

    log(f"Matrice : {elevation.shape} pixels")
    log(f"Altitude min : {elevation.min():.1f}m  max : {elevation.max():.1f}m")

    return elevation

def build_terrain_mesh(elevation, z_min):
    rows, cols = elevation.shape
    stride = max(1, math.ceil(max(rows, cols) / MAX_TERRAIN_GRID_DIM))

    if stride > 1:
        elevation_smooth = uniform_filter(elevation, size=stride, mode='nearest')
    else:
        elevation_smooth = elevation

    row_idx = np.arange(0, rows, stride)
    col_idx = np.arange(0, cols, stride)
    elevation_ds = elevation_smooth[np.ix_(row_idx, col_idx)]
    rows_ds, cols_ds = elevation_ds.shape

    log(f"[TERRAIN] grille {rows}x{cols} → {rows_ds}x{cols_ds} (stride={stride})")

    z_surface = (elevation_ds - z_min) / RESOLUTION_M * Z_SCALE

    r_idx, c_idx = np.meshgrid(np.arange(rows_ds), np.arange(cols_ds), indexing='ij')

    verts_top = np.stack([
        col_idx[c_idx.ravel()].astype(np.float64),
        (rows - 1 - row_idx[r_idx.ravel()]).astype(np.float64),
        z_surface.ravel().astype(np.float64)
    ], axis=1)

    verts_bot = np.stack([
        col_idx[c_idx.ravel()].astype(np.float64),
        (rows - 1 - row_idx[r_idx.ravel()]).astype(np.float64),
        np.full(rows_ds * cols_ds, -BASE_THICKNESS, dtype=np.float64)
    ], axis=1)

    def vidx(r, c):
        return r * cols_ds + c

    faces_top, faces_bot = [], []
    for r in range(rows_ds - 1):
        for c in range(cols_ds - 1):
            i00, i10 = vidx(r, c),   vidx(r+1, c)
            i01, i11 = vidx(r, c+1), vidx(r+1, c+1)
            faces_top.append([i00, i01, i10])
            faces_top.append([i01, i11, i10])
            faces_bot.append([i00, i10, i01])
            faces_bot.append([i01, i10, i11])

    n = rows_ds * cols_ds
    faces_bot_off = [[f[0]+n, f[1]+n, f[2]+n] for f in faces_bot]

    faces_sides = []
    for c in range(cols_ds - 1):
        t0, t1 = vidx(0, c), vidx(0, c+1)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, b0, t1])
        faces_sides.append([t1, b0, b1])
        t0, t1 = vidx(rows_ds-1, c), vidx(rows_ds-1, c+1)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, t1, b0])
        faces_sides.append([t1, b1, b0])
    for r in range(rows_ds - 1):
        t0, t1 = vidx(r, 0), vidx(r+1, 0)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, t1, b0])
        faces_sides.append([t1, b1, b0])
        t0, t1 = vidx(r, cols_ds-1), vidx(r+1, cols_ds-1)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, b0, t1])
        faces_sides.append([t1, b0, b1])

    all_verts = np.vstack([verts_top, verts_bot])
    all_faces = np.array(faces_top + faces_bot_off + faces_sides, dtype=np.int32)

    mesh = trimesh.Trimesh(vertices=all_verts, faces=all_faces, process=True)
    trimesh.repair.fix_normals(mesh)

    log(f"[TERRAIN] vertices={len(mesh.vertices)} faces={len(mesh.faces)}")
    log(f"[TERRAIN] watertight={mesh.is_watertight}")
    log(f"[TERRAIN] bounds Z=[{mesh.bounds[0][2]:.3f}, {mesh.bounds[1][2]:.3f}]")
    log(f"[TERRAIN] attendu  Z=[{-BASE_THICKNESS}, {z_surface.max():.3f}]")
    if not mesh.is_watertight:
        broken = trimesh.repair.broken_faces(mesh)
        log(f"[TERRAIN] faces brisées : {len(broken)}")

    return mesh

def build_roads_mask(road_edges, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (ROADS_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    if road_edges is None:
        log("[ROADS] aucune edge OSM, mesh vide")
        return None, None

    latlon_to_pixel_xy = make_latlon_to_pixel(bbox, cols, rows)
    bbox_pixel = pixel_bbox_polygon(cols, rows)
    pixel_lines = []
    for geom in road_edges.geometry:
        coords = list(geom.coords)
        pixel_coords = [latlon_to_pixel_xy(lat, lon) for lon, lat in coords]
        if len(pixel_coords) >= 2:
            pixel_lines.append(SLine(pixel_coords))

    log(f"[ROADS] {len(pixel_lines)} lignes converties")

    buffered = [line.buffer(ROAD_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                for line in pixel_lines]
    buffered = [p for p in buffered if not p.is_empty and p.area > 0]

    if not buffered:
        log("[ROADS] aucun polygone valide")
        return None, None

    merged = unary_union(buffered).buffer(0.3, join_style=1).buffer(-0.2, join_style=1)
    log(f"[ROADS] après union+lissage : type={merged.geom_type}")

    n_polys = len(merged.geoms) if merged.geom_type == "MultiPolygon" else 1
    tolerance = 0.05 if n_polys < 500 else (0.15 if n_polys < 3000 else 0.3)
    merged = merged.simplify(tolerance, preserve_topology=True)
    if not merged.is_valid:
        merged = merged.buffer(0)
    log(f"[ROADS] après simplification (tolerance={tolerance}) : type={merged.geom_type} n_polys={n_polys}")

    geom_list = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
    geom_list = [p for p in geom_list if p.is_valid and p.area >= 0.5]

    if not geom_list:
        log("[ROADS] aucun polygone valide après simplification")
        return None, None

    masque_meshes = _extrude_polygons_watertight(geom_list, z_top - z_bot, z_bot, "ROADS")

    if not masque_meshes:
        log("[ROADS] aucun masque généré")
        return None, None

    masque = _concatenate_watertight(masque_meshes, "ROADS")

    roads_polygon = unary_union(geom_list)
    return masque, roads_polygon

def apply_roads_boolean(masque, mesh_terrain, mesh_terrain_pristine):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain

    try:
        mesh_roads = _boolean_op(trimesh.boolean.intersection, [mesh_terrain_pristine, masque], "ROADS")
    except Exception as e:
        log(f"[ROADS] intersection échouée : {e}")
        return trimesh.Trimesh(), mesh_terrain
    mesh_roads.apply_translation([0, 0, ROAD_HEIGHT])
    log(f"[ROADS] intersection+translation : faces={len(mesh_roads.faces)} watertight={mesh_roads.is_watertight}")
    log(f"[ROADS] bounds Z=[{mesh_roads.bounds[0][2]:.3f},{mesh_roads.bounds[1][2]:.3f}]")

    masque_trou = masque.copy()
    masque_trou.apply_translation([0, 0, ROAD_HEIGHT])
    trimesh.repair.fix_normals(masque_trou)

    try:
        mesh_terrain_new = _boolean_op(trimesh.boolean.difference, [mesh_terrain, masque_trou], "ROADS")
    except Exception as e:
        log(f"[ROADS] soustraction terrain échouée : {e}")
        return mesh_roads, mesh_terrain
    log(f"[ROADS] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")

    return mesh_roads, mesh_terrain_new

def build_water_mask(water_areas, waterways, bbox, shape, mesh_terrain, z_min, enable_bathymetry=False, elevation=None, roads_polygon=None, extra_fill_polygon=None):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (WATER_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0
    sea_level_z = -z_min / RESOLUTION_M
    bbox_pixel = pixel_bbox_polygon(cols, rows)
    latlon_to_pixel_xy = make_latlon_to_pixel(bbox, cols, rows)
    convert_polygon = make_polygon_projector(bbox, cols, rows)

    pixel_polys = []
    has_ocean_col = water_areas is not None and "is_ocean" in water_areas.columns
    if water_areas is not None:
        for idx, row in water_areas.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            is_ocean = bool(row["is_ocean"]) if has_ocean_col else False
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                p = convert_polygon(part)
                if p:
                    pixel_polys.append((p, is_ocean))

    if waterways is not None:
        for geom in waterways.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                p = convert_polygon(geom)
                if p:
                    pixel_polys.append((p, False))
            elif geom.geom_type == "MultiPolygon":
                for part in geom.geoms:
                    p = convert_polygon(part)
                    if p:
                        pixel_polys.append((p, False))
            elif geom.geom_type == "LineString":
                coords = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.coords]
                p = SLine(coords).buffer(RIVER_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                if not p.is_empty and p.area > 0:
                    pixel_polys.append((p, False))
            elif geom.geom_type == "MultiLineString":
                for line in geom.geoms:
                    coords = [latlon_to_pixel_xy(lat, lon) for lon, lat in line.coords]
                    p = SLine(coords).buffer(RIVER_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                    if not p.is_empty and p.area > 0:
                        pixel_polys.append((p, False))

    log(f"[WATER] {len(pixel_polys)} polygones convertis")

    has_fill = extra_fill_polygon is not None and not extra_fill_polygon.is_empty

    if not pixel_polys and not has_fill:
        log("[WATER] aucun polygone, mesh vide")
        return None, None, None

    exploded_polys = []
    for poly, is_ocean in pixel_polys:
        if poly.geom_type == "MultiPolygon":
            for sub in poly.geoms:
                exploded_polys.append((sub, is_ocean))
        else:
            exploded_polys.append((poly, is_ocean))

    non_ocean_polys = [poly for poly, is_ocean in exploded_polys if not is_ocean and poly.is_valid and poly.area >= 0.5]
    ocean_polys = [poly for poly, is_ocean in exploded_polys if is_ocean and poly.is_valid and poly.area >= 0.5]

    water_footprint_parts = []

    masque_meshes = []
    lacs_polys_to_extrude = []
    if non_ocean_polys or has_fill:
        merged = unary_union(non_ocean_polys) if non_ocean_polys else None
        if roads_polygon is not None and not roads_polygon.is_empty and merged is not None:
            merged = merged.difference(roads_polygon)
            if not merged.is_valid:
                merged = merged.buffer(0)
        if has_fill:
            merged = unary_union([merged, extra_fill_polygon]) if merged is not None else extra_fill_polygon
            merged = merged.buffer(0.5, join_style=1).buffer(-0.5, join_style=1)
            if not merged.is_valid:
                merged = merged.buffer(0)
        merged_parts = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]
        log(f"[WATER] {len(non_ocean_polys)} polygones OSM + remplissage éventuel fusionnés en {len(merged_parts)} forme(s)")
        for part in merged_parts:
            if part.is_empty or part.area < 0.5:
                continue
            water_footprint_parts.append(part)
            lacs_polys_to_extrude.append(part)
        masque_meshes = _extrude_polygons_watertight(lacs_polys_to_extrude, z_top - z_bot, z_bot, "WATER")

    ocean_meshes = []
    ocean_skipped = 0
    for poly in ocean_polys:
        if roads_polygon is not None and not roads_polygon.is_empty:
            poly = poly.difference(roads_polygon)
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty or poly.area < 0.5:
                continue
        water_footprint_parts.append(poly)
        try:
            if enable_bathymetry:
                m = _build_ocean_bathymetric_mesh(poly, elevation, bbox, shape, z_min, sea_level_z, z_bot, WATER_HEIGHT)
                if m is None:
                    ocean_skipped += 1
                    continue
            else:
                m = trimesh.creation.extrude_polygon(poly, height=sea_level_z - z_bot)
                m.apply_translation([0, 0, z_bot])
            if not m.is_watertight:
                m.merge_vertices()
                trimesh.repair.fix_normals(m)
                trimesh.repair.fill_holes(m)
            if not m.is_watertight:
                ocean_skipped += 1
                continue
            ocean_meshes.append(m)
        except Exception as e:
            log(f"[WATER] extrusion masque océan échouée : {e}")
            ocean_skipped += 1
            continue
    if ocean_skipped:
        log(f"[WATER] {ocean_skipped} pièce(s) océan écartée(s) sur {len(ocean_polys)}")

    masque_lacs = _concatenate_watertight(masque_meshes, "WATER")
    masque_ocean = _concatenate_watertight(ocean_meshes, "WATER-OCEAN")

    water_polygon = unary_union(water_footprint_parts) if water_footprint_parts else None
    return masque_lacs, masque_ocean, water_polygon

def apply_water_boolean(masque_lacs, masque_ocean, mesh_terrain, mesh_terrain_pristine):
    if masque_lacs is None and masque_ocean is None:
        return trimesh.Trimesh(), mesh_terrain

    mesh_water_parts = []
    masque_trou_parts = []

    if masque_lacs is not None:
        try:
            mesh_lacs = _boolean_op(trimesh.boolean.intersection, [mesh_terrain_pristine, masque_lacs], "WATER")
        except Exception as e:
            log(f"[WATER] intersection lacs échouée : {e}")
            mesh_lacs = None
        if mesh_lacs is not None and len(mesh_lacs.faces) > 0:
            mesh_lacs.apply_translation([0, 0, WATER_HEIGHT])
            mesh_water_parts.append(mesh_lacs)
            masque_trou_lacs = masque_lacs.copy()
            masque_trou_lacs.apply_translation([0, 0, WATER_HEIGHT])
            trimesh.repair.fix_normals(masque_trou_lacs)
            masque_trou_parts.append(masque_trou_lacs)

    if masque_ocean is not None:
        mesh_ocean = masque_ocean.copy()
        mesh_water_parts.append(mesh_ocean)
        masque_trou_ocean = masque_ocean.copy()
        trimesh.repair.fix_normals(masque_trou_ocean)
        masque_trou_parts.append(masque_trou_ocean)

    if not mesh_water_parts:
        log("[WATER] intersection vide — abandon")
        return trimesh.Trimesh(), mesh_terrain

    mesh_water = trimesh.util.concatenate(mesh_water_parts) if len(mesh_water_parts) > 1 else mesh_water_parts[0]

    if len(masque_trou_parts) > 1:
        masque_trou_final = _boolean_op(trimesh.boolean.union, masque_trou_parts, "WATER")
    else:
        masque_trou_final = masque_trou_parts[0]

    vol_before = mesh_terrain.volume
    mesh_terrain_new, terrain_ok = _gpx_safe_difference(mesh_terrain, masque_trou_final, "terrain")
    vol_after = mesh_terrain_new.volume
    expected_removed = masque_trou_final.volume
    log(f"[WATER] volume terrain : avant={vol_before:.1f} après={vol_after:.1f} "
        f"retiré={vol_before - vol_after:.1f} (attendu≈{expected_removed:.1f})")

    log(f"[WATER] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    return mesh_water, mesh_terrain_new

def _build_ocean_bathymetric_mesh(poly, elevation, bbox, shape, z_min, sea_level_z, z_bot, water_height=0.0):
    rows, cols = shape

    minx, miny, maxx, maxy = poly.bounds
    x0 = max(0, int(math.floor(minx)))
    x1 = min(cols - 1, int(math.ceil(maxx)))
    y0 = max(0, int(math.floor(miny)))
    y1 = min(rows - 1, int(math.ceil(maxy)))

    y0_raster = rows - 1 - y1
    y1_raster = rows - 1 - y0
    sub_elev = elevation[y0_raster:y1_raster+1, x0:x1+1]
    sub_elev = np.flipud(sub_elev)

    z_bottom_grid_raw = (sub_elev - z_min) / RESOLUTION_M

    z_bottom_grid = np.maximum(z_bottom_grid_raw, z_bot)

    sub_rows, sub_cols = z_bottom_grid.shape
    xs = np.arange(x0, x0 + sub_cols)
    ys = np.arange(y0, y0 + sub_rows)
    xx, yy = np.meshgrid(xs, ys)

    verts_bottom = np.stack([xx.ravel(), yy.ravel(), z_bottom_grid.ravel()], axis=1)
    verts_top = np.stack([xx.ravel(), yy.ravel(), np.full(xx.size, sea_level_z + water_height)], axis=1)

    n = sub_cols
    faces_bottom = []
    faces_top = []
    for r in range(sub_rows - 1):
        for c in range(sub_cols - 1):
            i0 = r * n + c
            i1 = r * n + c + 1
            i2 = (r + 1) * n + c
            i3 = (r + 1) * n + c + 1
            faces_bottom.append([i0, i2, i1])
            faces_bottom.append([i1, i2, i3])
            faces_top.append([i0, i1, i2])
            faces_top.append([i1, i3, i2])

    verts = np.vstack([verts_bottom, verts_top])
    offset = len(verts_bottom)
    faces_top_offset = [[f[0]+offset, f[1]+offset, f[2]+offset] for f in faces_top]

    side_faces = []
    for r in range(sub_rows - 1):
        for c in [0, sub_cols - 1]:
            i0 = r * n + c
            i1 = (r + 1) * n + c
            j0, j1 = i0 + offset, i1 + offset
            if c == 0:
                side_faces.append([i0, i1, j1])
                side_faces.append([i0, j1, j0])
            else:
                side_faces.append([i0, j1, i1])
                side_faces.append([i0, j0, j1])
    for c in range(sub_cols - 1):
        for r in [0, sub_rows - 1]:
            i0 = r * n + c
            i1 = r * n + c + 1
            j0, j1 = i0 + offset, i1 + offset
            if r == 0:
                side_faces.append([i0, j1, i1])
                side_faces.append([i0, j0, j1])
            else:
                side_faces.append([i0, i1, j1])
                side_faces.append([i0, j1, j0])

    all_faces = faces_bottom + faces_top_offset + side_faces
    grid_mesh = trimesh.Trimesh(vertices=verts, faces=all_faces)
    trimesh.repair.fix_normals(grid_mesh)
    if not grid_mesh.is_volume:
        grid_mesh.merge_vertices()
        trimesh.repair.fix_normals(grid_mesh)
        trimesh.repair.fill_holes(grid_mesh)

    z_grid_mesh = grid_mesh.vertices[:, 2]
    top_count = (np.abs(z_grid_mesh - sea_level_z) < 0.01).sum()
    z_grid_mesh_bottom = z_grid_mesh[z_grid_mesh < sea_level_z - 0.1]

    poly_clean = poly.buffer(0)
    if poly_clean.geom_type == "MultiPolygon":
        poly_clean = max(poly_clean.geoms, key=lambda p: p.area)
    poly_clean = poly_clean.simplify(0.05, preserve_topology=True)
    if poly_clean.geom_type == "Polygon":
        poly_clean = ShPoly(list(poly_clean.exterior.coords))

    clip_solid = trimesh.creation.extrude_polygon(poly_clean, height=(sea_level_z + water_height - z_bot) + 0.02)
    clip_solid.apply_translation([0, 0, z_bot - 0.01])

    try:
        result = _boolean_op(trimesh.boolean.intersection, [grid_mesh, clip_solid], "WATER")
    except Exception as e:
        log(f"[WATER] intersection bathymétrie océan échouée : {e}")
        return None

    if result is None or len(result.faces) == 0:
        return None

    z_res = result.vertices[:, 2]
    z_res_bottom = z_res[z_res < sea_level_z - 0.1]
    z_res_above = z_res[z_res > sea_level_z + 0.1]
    thickness = sea_level_z - z_res_bottom

    if not result.is_watertight:
        result.merge_vertices()
        trimesh.repair.fix_normals(result)
        trimesh.repair.fill_holes(result)

    return result

def build_veg_mask(forest, other_veg, bbox, shape, mesh_terrain, exclude_polygon=None, extra_fill_polygon=None):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (VEG_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    convert_polygon = make_polygon_projector(bbox, cols, rows)

    pixel_polys = []
    for dataset in [forest, other_veg]:
        if dataset is None:
            continue
        for geom in dataset.geometry:
            if geom is None or geom.is_empty:
                continue
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                p = convert_polygon(part)
                if p:
                    pixel_polys.append(p)

    log(f"[VEG] {len(pixel_polys)} polygones convertis")

    has_fill = extra_fill_polygon is not None and not extra_fill_polygon.is_empty

    if not pixel_polys and not has_fill:
        log("[VEG] aucun polygone, mesh vide")
        return None, None

    merged = unary_union(pixel_polys).buffer(0.5, join_style=1).buffer(-0.3, join_style=1) if pixel_polys else None
    if merged is not None:
        log(f"[VEG] après union+lissage : type={merged.geom_type}")

    if exclude_polygon is not None and not exclude_polygon.is_empty and merged is not None:
        merged = merged.difference(exclude_polygon)
        if not merged.is_valid:
            merged = merged.buffer(0)
        log(f"[VEG] après exclusion routes/eau/buildings : type={merged.geom_type}")

    if has_fill:
        merged = unary_union([merged, extra_fill_polygon]) if merged is not None else extra_fill_polygon
        merged = merged.buffer(0.5, join_style=1).buffer(-0.5, join_style=1)
        if not merged.is_valid:
            merged = merged.buffer(0)
        log(f"[VEG] après ajout remplissage landcover/satellite : type={merged.geom_type}")

    if merged is None or merged.is_empty:
        log("[VEG] rien à extruder après fusion")
        return None, None

    veg_polygon = merged
    geom_list = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    masque_meshes = _extrude_polygons_watertight(geom_list, z_top - z_bot, z_bot, "VEG", min_area=1.0)

    if not masque_meshes:
        log("[VEG] aucun masque généré")
        return None, None

    masque = _concatenate_watertight(masque_meshes, "VEG")
    log(f"[VEG] masque bounds Z=[{masque.bounds[0][2]:.3f},{masque.bounds[1][2]:.3f}]")

    if not masque.is_watertight:
        log("[VEG] masque non-watertight même après réparation par pièce — abandon")
        return None, None

    return masque, veg_polygon

def apply_veg_boolean(masque, mesh_terrain, mesh_terrain_pristine):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain

    try:
        mesh_veg = _boolean_op(trimesh.boolean.intersection, [mesh_terrain_pristine, masque], "VEG")
    except Exception as e:
        log(f"[VEG] intersection échouée : {e}")
        return trimesh.Trimesh(), mesh_terrain
    log(f"[VEG] intersection terrain∩masque : faces={len(mesh_veg.faces)} watertight={mesh_veg.is_watertight}")
    log(f"[VEG] bounds Z=[{mesh_veg.bounds[0][2]:.3f},{mesh_veg.bounds[1][2]:.3f}]")

    if not mesh_veg.is_watertight:
        log("[VEG] mesh_veg non-watertight après intersection")
        broken = trimesh.repair.broken_faces(mesh_veg)
        log(f"[VEG] faces brisées : {len(broken)}")

    try:
        mesh_terrain_new = _boolean_op(trimesh.boolean.difference, [mesh_terrain, masque], "VEG")
    except Exception as e:
        log(f"[VEG] soustraction terrain échouée : {e}")
        return mesh_veg, mesh_terrain
    log(f"[VEG] terrain après soustraction masque : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    log(f"[VEG] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")

    return mesh_veg, mesh_terrain_new

def build_trees_mesh(forest, bbox, shape, mesh_veg, extra_fill_polygon=None):
    if mesh_veg is None or len(mesh_veg.faces) == 0:
        log("[TREES] pas de végétation disponible, aucun arbre généré")
        return trimesh.Trimesh()
    rows, cols = shape

    convert_polygon = make_polygon_projector(bbox, cols, rows)

    forest_polys = []
    if forest is not None:
        for geom in forest.geometry:
            if geom is None or geom.is_empty:
                continue
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                p = convert_polygon(part)
                if p:
                    forest_polys.append(p)

    log(f"[TREES] {len(forest_polys)} polygones forêt OSM")

    has_fill = extra_fill_polygon is not None and not extra_fill_polygon.is_empty
    if has_fill:
        fill_parts = list(extra_fill_polygon.geoms) if extra_fill_polygon.geom_type == "MultiPolygon" else [extra_fill_polygon]
        forest_polys.extend(fill_parts)
        log(f"[TREES] +{len(fill_parts)} polygone(s) forêt landcover")

    if not forest_polys:
        log("[TREES] aucun polygone valide")
        return trimesh.Trimesh()

    merged = unary_union(forest_polys)
 
def build_monument_mesh(stl_path, rotation_deg, footprint_polygon, z_base, scale_factor=1.0):
    monument = trimesh.load(stl_path)
    if isinstance(monument, trimesh.Scene):
        monument = trimesh.util.concatenate(list(monument.geometry.values()))

    bounds_native = monument.bounds
    stl_area_xy = (bounds_native[1][0] - bounds_native[0][0]) * (bounds_native[1][1] - bounds_native[0][1])
    footprint_area = footprint_polygon.area

    if stl_area_xy <= 0:
        return None

    ratio = math.sqrt(footprint_area / stl_area_xy) * scale_factor
    scale_matrix = np.diag([ratio, ratio, ratio, 1.0])
    monument.apply_transform(scale_matrix)

    min_rect = footprint_polygon.minimum_rotated_rectangle
    rect_coords = list(min_rect.exterior.coords)
    dx_rect = rect_coords[1][0] - rect_coords[0][0]
    dy_rect = rect_coords[1][1] - rect_coords[0][1]
    footprint_orientation_deg = math.degrees(math.atan2(dy_rect, dx_rect))

    total_rotation_deg = footprint_orientation_deg + rotation_deg
    angle_rad = math.radians(total_rotation_deg)
    rotation_matrix = trimesh.transformations.rotation_matrix(angle_rad, [0, 0, 1])
    monument.apply_transform(rotation_matrix)

    stl_centroid_xy = monument.centroid[:2]
    footprint_centroid = footprint_polygon.centroid
    dx = footprint_centroid.x - stl_centroid_xy[0]
    dy = footprint_centroid.y - stl_centroid_xy[1]
    dz = z_base - monument.bounds[0][2]

    monument.apply_translation([dx, dy, dz])
    trimesh.repair.fix_normals(monument)
    return monument

def build_buildings_mask(buildings, bbox, shape, mesh_terrain, exclude_polygon=None):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (BUILDINGS_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    if buildings is None:
        log("[BUILDINGS] aucun bâtiment, mesh vide")
        return None, [], None

    latlon_to_pixel_xy = make_latlon_to_pixel(bbox, cols, rows)
    bbox_pixel = pixel_bbox_polygon(cols, rows)

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return []
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords] for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.intersection(bbox_pixel)
        if not p.is_valid:
            p = p.buffer(0)
        if p.is_empty:
            return []
        if p.geom_type == "Polygon":
            return [p]
        if p.geom_type == "MultiPolygon":
            return list(p.geoms)
        return []

    building_meshes = []
    masque_meshes = []
    footprint_polys = []
    skip = 0
    count = 0

    for idx, row in buildings.iterrows():
        osm_id = str(idx[1] if isinstance(idx, tuple) else idx)
        geom = row.geometry
        if geom is None or geom.is_empty:
            skip += 1
            continue

        height_m = get_building_height(row)
        height_px = np.clip(height_m / RESOLUTION_M * BUILDING_HEIGHT_SCALE,
                            BUILDING_MIN_HEIGHT, BUILDING_MAX_HEIGHT)

        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]

        for part in parts:
            polys = convert_polygon(part)
            for p in polys:
                if exclude_polygon is not None and not exclude_polygon.is_empty:
                    p = p.difference(exclude_polygon)
                    if not p.is_valid:
                        p = p.buffer(0)
                    if p.is_empty:
                        skip += 1
                        continue
                    if p.geom_type == "MultiPolygon":
                        p = max(p.geoms, key=lambda g: g.area)
                if p.area < 0.5:
                    skip += 1
                    continue
                try:
                    masque = trimesh.creation.extrude_polygon(p, height=z_top - z_bot)
                    masque.apply_translation([0, 0, z_bot])
                    if not masque.is_watertight:
                        skip += 1
                        continue
                    masque_meshes.append(masque)
                    building = trimesh.creation.extrude_polygon(p, height=height_px)
                    trimesh.repair.fix_normals(building)
                    building_meshes.append((building, masque, osm_id, p))
                    footprint_polys.append(p)
                    count += 1
                except Exception as e:
                    log(f"[BUILDINGS] extrusion échouée : {e}")
                    skip += 1
                    continue

    log(f"[BUILDINGS] {count} bâtiments traités, {skip} ignorés")

    if not masque_meshes:
        log("[BUILDINGS] aucun masque généré")
        return None, [], None

    masque_union = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque_union)
    log(f"[BUILDINGS] masque union : faces={len(masque_union.faces)} watertight={masque_union.is_watertight}")

    buildings_polygon = unary_union(footprint_polys) if footprint_polys else None
    return masque_union, building_meshes, buildings_polygon

def apply_buildings_boolean(masque_union, building_meshes, mesh_terrain, mesh_terrain_pristine, monument_meshes=None):
    if masque_union is None or not building_meshes:
        return trimesh.Trimesh(), mesh_terrain

    monument_meshes = monument_meshes or {}

    try:
        mesh_terrain_new = _boolean_op(trimesh.boolean.difference, [mesh_terrain, masque_union], "BUILDINGS")
    except Exception as e:
        log(f"[BUILDINGS] soustraction terrain échouée : {e}")
        return trimesh.Trimesh(), mesh_terrain
    log(f"[BUILDINGS] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")

    z_top = mesh_terrain_pristine.bounds[1][2] + 1.0
    final_buildings = []
    for building, masque, osm_id, footprint in building_meshes:
        if osm_id in monument_meshes:
            final_buildings.append(monument_meshes[osm_id])
            continue

        locs = mesh_terrain_pristine.ray.intersects_location(
            np.array([[building.centroid[0], building.centroid[1], z_top]]),
            np.array([[0, 0, -1]])
        )[0]
        z_base = locs[:, 2].max() if len(locs) > 0 else 0.0
        building.apply_translation([0, 0, z_base])
        trimesh.repair.fix_normals(building)
        final_buildings.append(building)

    if not final_buildings:
        log("[BUILDINGS] aucun bâtiment final")
        return trimesh.Trimesh(), mesh_terrain_new

    mesh_buildings = trimesh.util.concatenate(final_buildings)
    trimesh.repair.fix_normals(mesh_buildings)
    log(f"[BUILDINGS] vertices={len(mesh_buildings.vertices)} faces={len(mesh_buildings.faces)}")
    log(f"[BUILDINGS] watertight={mesh_buildings.is_watertight}")
    log(f"[BUILDINGS] bounds Z=[{mesh_buildings.bounds[0][2]:.3f},{mesh_buildings.bounds[1][2]:.3f}]")

    return mesh_buildings, mesh_terrain_new

def apply_buildings_fill_boolean(masque, mesh_terrain, mesh_terrain_pristine):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain
    try:
        mesh_buildings_fill = _boolean_op(trimesh.boolean.intersection, [mesh_terrain_pristine, masque], "BUILDINGS-FILL")
    except Exception as e:
        log(f"[BUILDINGS-FILL] intersection échouée : {e}")
        return trimesh.Trimesh(), mesh_terrain
    log(f"[BUILDINGS-FILL] intersection terrain∩masque : faces={len(mesh_buildings_fill.faces)} watertight={mesh_buildings_fill.is_watertight}")
    log(f"[BUILDINGS-FILL] bounds Z=[{mesh_buildings_fill.bounds[0][2]:.3f},{mesh_buildings_fill.bounds[1][2]:.3f}]")
    try:
        mesh_terrain_new = _boolean_op(trimesh.boolean.difference, [mesh_terrain, masque], "BUILDINGS-FILL")
    except Exception as e:
        log(f"[BUILDINGS-FILL] soustraction terrain échouée : {e}")
        return mesh_buildings_fill, mesh_terrain
    log(f"[BUILDINGS-FILL] terrain après soustraction masque : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    log(f"[BUILDINGS-FILL] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")
    return mesh_buildings_fill, mesh_terrain_new

def build_gpx_mask(gpx_points, bbox, shape, mesh_terrain, clip_poly2d=None):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (GPX_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0
    latlon_to_pixel_xy = make_latlon_to_pixel(bbox, cols, rows)
    bbox_pixel = pixel_bbox_polygon(cols, rows)
    pixel_points = [latlon_to_pixel_xy(lat, lon) for lon, lat in gpx_points]
    pixel_points = [(x, y) for x, y in pixel_points if 0 <= x <= cols-1 and 0 <= y <= rows-1]
    if len(pixel_points) < 2:
        log("[GPX] pas assez de points dans la bbox")
        return None
    line = SLine(pixel_points)
    buffered = line.buffer(max(1.0, GPX_WIDTH_PX), cap_style=2, join_style=2).intersection(bbox_pixel)
    if clip_poly2d is not None and clip_poly2d.is_valid and not clip_poly2d.is_empty:
        buffered = buffered.intersection(clip_poly2d)
        if not buffered.is_valid:
            buffered = buffered.buffer(0)
    if buffered.is_empty or buffered.area < 0.1:
        log("[GPX] masque vide")
        return None
    geom_list = list(buffered.geoms) if buffered.geom_type == "MultiPolygon" else [buffered]
    geom_list = [poly.simplify(0.1, preserve_topology=True) for poly in geom_list if poly.is_valid and poly.area >= 0.1]
    masque_meshes = _extrude_polygons_watertight(geom_list, z_top - z_bot, z_bot, "GPX", min_area=0.1)
    if not masque_meshes:
        return None
    masque = _concatenate_watertight(masque_meshes, "GPX")
    return masque if masque is not None and masque.is_watertight else None

def _gpx_safe_difference(mesh, cutter, label):
    try:
        result = _boolean_op(trimesh.boolean.difference, [mesh, cutter], f"GPX-{label}")
        if len(result.faces) > 0:
            trimesh.repair.fix_normals(result)
            return result, True
        log(f"[GPX] cut {label} : résultat vide, nouvelle tentative après réparation")
    except Exception as e:
        log(f"[GPX] cut {label} échoué ({e}) — nouvelle tentative après réparation")

    try:
        mesh_fixed = mesh.copy()
        mesh_fixed.merge_vertices()
        trimesh.repair.fix_normals(mesh_fixed)
        result = _boolean_op(trimesh.boolean.difference, [mesh_fixed, cutter], f"GPX-{label}")
        if len(result.faces) > 0:
            trimesh.repair.fix_normals(result)
            log(f"[GPX] cut {label} : réussi après réparation")
            return result, True
        log(f"[GPX] cut {label} : résultat toujours vide après réparation — mesh original conservé")
    except Exception as e:
        log(f"[GPX] cut {label} échoué définitivement ({e}) — mesh original conservé, "
              f"le GPX pourra être masqué à cet endroit")
    return mesh, False

def apply_gpx_boolean(masque, mesh_terrain, mesh_terrain_pristine, other_meshes):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain, other_meshes, []

    try:
        mesh_gpx = trimesh.boolean.intersection([mesh_terrain_pristine, masque], engine='manifold')
    except Exception as e:
        log(f"[GPX] intersection échouée : {e}")
        return trimesh.Trimesh(), mesh_terrain, other_meshes, []
        
    mesh_gpx.apply_translation([0, 0, GPX_HEIGHT])
    trimesh.repair.fix_normals(mesh_gpx)

    masque_trou = masque.copy()
    trimesh.repair.fix_normals(masque_trou)

    mesh_terrain_new, terrain_ok = _gpx_safe_difference(mesh_terrain, masque_trou, "terrain")
    log(f"[GPX] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")

    cut = {}
    failed = [] if terrain_ok else ["terrain"]
    for name, mesh in other_meshes.items():
        if len(mesh.faces) == 0:
            cut[name] = mesh
            continue
        cut[name], ok = _gpx_safe_difference(mesh, masque_trou, name)
        if not ok:
            failed.append(name)
    return mesh_gpx, mesh_terrain_new, cut, failed

def parse_gpx_file(path, bbox):
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
    points = []
    for trkpt in root.findall('.//gpx:trkpt', ns):
        lat = float(trkpt.attrib['lat'])
        lon = float(trkpt.attrib['lon'])
        if bbox["west"] <= lon <= bbox["east"] and bbox["south"] <= lat <= bbox["north"]:
            points.append((lon, lat))
    return points

def _puzzle_cell_polygon(cx0, cx1, cy0, cy1, cell_w, cell_h,tab_right, tab_left, tab_top, tab_bot, tab_radius_ratio=0.14):
    r = min(cell_w, cell_h) * tab_radius_ratio
    n = 40
    pts = []
    xc = (cx0 + cx1) / 2
    if tab_bot is None:
        pts += [(cx0, cy0), (cx1, cy0)]
    elif tab_bot:
        pts += [(cx0, cy0)]
        for i in range(8): pts.append((cx0 + i/7*(xc-r-cx0), cy0))
        for i in range(n+1):
            a = math.pi + math.pi*i/n
            pts.append((xc + r*math.cos(a), cy0 + r*math.sin(a)))
        for i in range(8): pts.append((xc+r + i/7*(cx1-(xc+r)), cy0))
        pts += [(cx1, cy0)]
    else:
        pts += [(cx0, cy0)]
        for i in range(8): pts.append((cx0 + i/7*(xc-r-cx0), cy0))
        for i in range(n+1):
            a = -math.pi*(n-i)/n
            pts.append((xc + r*math.cos(a), cy0 - r*math.sin(a)))
        for i in range(8): pts.append((xc+r + i/7*(cx1-(xc+r)), cy0))
        pts += [(cx1, cy0)]
    yc = (cy0 + cy1) / 2
    if tab_right is None:
        pts += [(cx1, cy1)]
    elif tab_right:
        for i in range(8): pts.append((cx1, cy0 + i/7*(yc-r-cy0)))
        for i in range(n+1):
            a = -math.pi/2 + math.pi*i/n
            pts.append((cx1 + r*math.cos(a), yc + r*math.sin(a)))
        for i in range(8): pts.append((cx1, yc+r + i/7*(cy1-(yc+r))))
    else:
        for i in range(8): pts.append((cx1, cy0 + i/7*(yc-r-cy0)))
        for i in range(n+1):
            a = -math.pi/2 - math.pi*i/n
            pts.append((cx1 + r*math.cos(a), yc + r*math.sin(a)))
        for i in range(8): pts.append((cx1, yc+r + i/7*(cy1-(yc+r))))
    pts += [(cx1, cy1)]
    xc = (cx0 + cx1) / 2
    if tab_top is None:
        pts += [(cx0, cy1)]
    elif tab_top:
        for i in range(8): pts.append((cx1 - i/7*(cx1-(xc+r)), cy1))
        for i in range(n+1):
            a = math.pi*(n-i)/n
            pts.append((xc + r*math.cos(math.pi - a), cy1 + r*math.sin(math.pi - a)))
        for i in range(8): pts.append((xc-r - i/7*(xc-r-cx0), cy1))
        pts += [(cx0, cy1)]
    else:
        for i in range(8): pts.append((cx1 - i/7*(cx1-(xc+r)), cy1))
        for i in range(n+1):
            a = math.pi*(n-i)/n
            pts.append((xc + r*math.cos(math.pi - a), cy1 - r*math.sin(math.pi - a)))
        for i in range(8): pts.append((xc-r - i/7*(xc-r-cx0), cy1))
        pts += [(cx0, cy1)]
    yc = (cy0 + cy1) / 2
    if tab_left is None:
        pts += [(cx0, cy0)]
    elif tab_left:
        for i in range(8): pts.append((cx0, cy1 - i/7*(cy1-(yc+r))))
        for i in range(n+1):
            a = math.pi/2 + math.pi*i/n
            pts.append((cx0 + r*math.cos(a), yc + r*math.sin(a)))
        for i in range(8): pts.append((cx0, yc-r - i/7*(yc-r-cy0)))
        pts += [(cx0, cy0)]
    else:
        for i in range(8): pts.append((cx0, cy1 - i/7*(cy1-(yc+r))))
        for i in range(n+1):
            a = math.pi/2 - math.pi*i/n
            pts.append((cx0 + r*math.cos(a), yc + r*math.sin(a)))
        for i in range(8): pts.append((cx0, yc-r - i/7*(yc-r-cy0)))
        pts += [(cx0, cy0)]
    from shapely.geometry import Polygon as ShPoly
    poly = ShPoly(pts)
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly

def build_puzzle_pieces(mesh_terrain, n_pieces, extra_meshes=None, merge_small_pieces=True, tab_radius_ratio=0.14):

    bounds = mesh_terrain.bounds
    x_min, y_min = bounds[0][0], bounds[0][1]
    x_max, y_max = bounds[1][0], bounds[1][1]
    z_min, z_max = bounds[0][2], bounds[1][2]
    n_cols = math.ceil(math.sqrt(n_pieces))
    n_rows = math.ceil(n_pieces / n_cols)
    cell_w = (x_max - x_min) / n_cols
    cell_h = (y_max - y_min) / n_rows
    z_height = z_max - z_min + 4.0
    r = min(cell_w, cell_h) * tab_radius_ratio
    tab_x = {col: (col % 2 == 1) for col in range(1, n_cols)}
    tab_y = {row: (row % 2 == 1) for row in range(1, n_rows)}

    cells = {}
    for row in range(n_rows):
        for col in range(n_cols):
            idx = row * n_cols + col
            if idx >= n_pieces:
                continue
            cx0 = x_min + col * cell_w
            cx1 = cx0 + cell_w
            cy0 = y_min + row * cell_h
            cy1 = cy0 + cell_h
            right = None if (col == n_cols-1 or idx+1 >= n_pieces) else (not tab_x[col+1])
            left  = None if col == 0 else tab_x[col]
            top   = None if (row == n_rows-1 or idx+n_cols >= n_pieces) else (not tab_y[row+1])
            bot   = None if row == 0 else tab_y[row]
            cells[(row, col)] = {
                "cx0": cx0, "cx1": cx1, "cy0": cy0, "cy1": cy1,
                "right": right, "left": left, "top": top, "bot": bot,
            }

    opposite = {"right": "left", "left": "right", "top": "bot", "bot": "top"}

    def neighbor_key(key, direction):
        row, col = key
        if direction == "right":
            return (row, col + 1)
        if direction == "left":
            return (row, col - 1)
        if direction == "top":
            return (row + 1, col)
        if direction == "bot":
            return (row - 1, col)
        return None

    flatten_sides = {}

    def flatten_both_sides(key, direction, reason):
        flatten_sides.setdefault(key, set()).add(direction)
        nk = neighbor_key(key, direction)
        if nk in cells:
            flatten_sides.setdefault(nk, set()).add(opposite[direction])
            log(f"[PUZZLE] aplatissement '{direction}' sur {key} ET '{opposite[direction]}' sur {nk} ({reason})")

    is_small = {}
    if merge_small_pieces:
        min_extent = r * 4
        min_volume_ratio = 0.10
        for key, c in cells.items():
            rect = box(c["cx0"], c["cy0"], c["cx1"], c["cy1"])
            try:
                mask = trimesh.creation.extrude_polygon(rect, height=z_height)
                mask.apply_translation([0, 0, z_min - 2])
                footprint = trimesh.boolean.intersection([mesh_terrain, mask], engine='manifold')
            except Exception:
                footprint = None
            if footprint is None or len(footprint.faces) == 0:
                is_small[key] = True
                continue
            fb = footprint.bounds
            span_x = fb[1][0] - fb[0][0]
            span_y = fb[1][1] - fb[0][1]
            volume_ratio = footprint.volume / mask.volume if mask.volume > 0 else 0.0
            too_small = span_x < min_extent or span_y < min_extent or volume_ratio < min_volume_ratio
            is_small[key] = too_small
            if too_small:
                log(f"[PUZZLE] cellule {key} trop petite (span=({span_x:.1f},{span_y:.1f}), ratio_volume={volume_ratio:.2f}) — fusion prévue")
    else:
        is_small = {key: False for key in cells}

    merge_target = {}
    for key in list(cells.keys()):
        if not is_small.get(key):
            continue
        row, col = key
        candidates = [
            ((row, col + 1), "right", "left"),
            ((row, col - 1), "left", "right"),
            ((row + 1, col), "top", "bot"),
            ((row - 1, col), "bot", "top"),
        ]
        target = None
        for cand_key, self_side, target_side in candidates:
            if cand_key in cells and not is_small.get(cand_key):
                target = (cand_key, self_side, target_side)
                break
        if target is None:
            for cand_key, self_side, target_side in candidates:
                if cand_key in cells and cand_key != key:
                    target = (cand_key, self_side, target_side)
                    break
        if target is not None:
            merge_target[key] = target

    def resolve_root(key):
        seen = set()
        while key in merge_target and key not in seen:
            seen.add(key)
            key = merge_target[key][0]
        return key

    absorbed_by = {}
    for small_key, (target_key, self_side, target_side) in merge_target.items():
        root_key = resolve_root(target_key)
        absorbed_by.setdefault(root_key, []).append((small_key, self_side))
        flatten_sides.setdefault(small_key, set()).add(self_side)
        flatten_sides.setdefault(root_key, set()).add(target_side)

    edge_len = {"right": cell_h, "left": cell_h, "top": cell_w, "bot": cell_w}
    for root_key, members in absorbed_by.items():
        group_keys = [root_key] + [m[0] for m in members]
        by_direction = {"right": [], "left": [], "top": [], "bot": []}
        for gk in group_keys:
            c = cells[gk]
            for direction in ("right", "left", "top", "bot"):
                if direction in flatten_sides.get(gk, ()):
                    continue
                if c[direction] is not None:
                    by_direction[direction].append(gk)
        for direction, keys_with_feature in by_direction.items():
            if len(keys_with_feature) > 1:
                keys_with_feature.sort(key=lambda k: edge_len[direction], reverse=True)
                for extra_key in keys_with_feature[1:]:
                    flatten_both_sides(extra_key, direction, "doublon apres fusion")

    def get_sides(key):
        c = cells[key]
        sides = dict(right=c["right"], left=c["left"], top=c["top"], bot=c["bot"])
        for flat_side in flatten_sides.get(key, ()):
            sides[flat_side] = None
        return sides

    def build_group_polygon(root_key):
        c = cells[root_key]
        sides = get_sides(root_key)
        poly2d = _puzzle_cell_polygon(c["cx0"], c["cx1"], c["cy0"], c["cy1"], cell_w, cell_h,
                                      sides["right"], sides["left"], sides["top"], sides["bot"], tab_radius_ratio)
        for small_key, _ in absorbed_by.get(root_key, []):
            sc = cells[small_key]
            small_sides = get_sides(small_key)
            small_poly = _puzzle_cell_polygon(sc["cx0"], sc["cx1"], sc["cy0"], sc["cy1"], cell_w, cell_h,
                                              small_sides["right"], small_sides["left"], small_sides["top"], small_sides["bot"], tab_radius_ratio)
            poly2d = unary_union([poly2d, small_poly])
        return poly2d, sides

    def build_mask_and_terrain(poly, key):
        try:
            m = trimesh.creation.extrude_polygon(poly, height=z_height)
            m.apply_translation([0, 0, z_min - 2])
        except Exception as e:
            log(f"[PUZZLE] masque {key} échoué: {e}")
            return None, None
        try:
            tp_mesh = trimesh.boolean.intersection([mesh_terrain, m], engine='manifold')
        except Exception as e:
            log(f"[PUZZLE] terrain pièce {key} échoué: {e}")
            return m, None
        return m, tp_mesh

    r_probe = r
    min_probe_volume_ratio = 0.02
    reference_probe_volume = r_probe * r_probe * (z_max - z_min)

    def probe_material(mesh_piece, direction, c):
        cx0, cx1, cy0, cy1 = c["cx0"], c["cx1"], c["cy0"], c["cy1"]
        xc, yc = (cx0 + cx1) / 2, (cy0 + cy1) / 2
        centers = {
            "right": (cx1 + r_probe / 2, yc),
            "left": (cx0 - r_probe / 2, yc),
            "top": (xc, cy1 + r_probe / 2),
            "bot": (xc, cy0 - r_probe / 2),
        }
        px, py = centers[direction]
        probe_box = trimesh.creation.box(extents=[r_probe * 1.5, r_probe * 1.5, z_height])
        probe_box.apply_translation([px, py, z_min - 2 + z_height / 2])
        try:
            hit = trimesh.boolean.intersection([mesh_piece, probe_box], engine='manifold')
            return hit.volume if hit is not None and len(hit.faces) > 0 else 0.0
        except Exception:
            return 0.0

    root_keys = [key for key in cells if key not in merge_target]

    for root_key in root_keys:
        poly2d, sides = build_group_polygon(root_key)
        if poly2d.is_empty or poly2d.area < 1:
            continue
        _, terrain_piece = build_mask_and_terrain(poly2d, root_key)
        if terrain_piece is None or len(terrain_piece.faces) == 0:
            continue
        c = cells[root_key]
        for direction in ("right", "left", "top", "bot"):
            if sides[direction] is not True:
                continue
            vol = probe_material(terrain_piece, direction, c)
            if vol < reference_probe_volume * min_probe_volume_ratio:
                flatten_both_sides(root_key, direction, f"tenon trop petit (volume={vol:.1f})")

    pieces = []
    for root_key in root_keys:
        poly2d, sides = build_group_polygon(root_key)
        if poly2d.is_empty or poly2d.area < 1:
            continue
        mask, terrain_piece = build_mask_and_terrain(poly2d, root_key)
        if terrain_piece is None or len(terrain_piece.faces) == 0 or terrain_piece.volume <= mesh_terrain.volume * 0.0001:
            continue
        piece_layers = {"terrain": terrain_piece}
        if extra_meshes:
            for name, extra in extra_meshes:
                if extra is None or len(extra.faces) == 0:
                    continue
                try:
                    ep = trimesh.boolean.intersection([extra, mask], engine='manifold')
                    if ep is not None and len(ep.faces) > 0:
                        piece_layers[name] = ep
                except Exception:
                    pass
        total_faces = sum(len(m.faces) for m in piece_layers.values())
        log(f"[PUZZLE] pièce {root_key}: faces={total_faces} layers={list(piece_layers.keys())}")
        pieces.append(piece_layers)
    return pieces

def export_puzzle_3mf(pieces, output_path, plate_size=256, puzzle_gap_mm=5.0):
    LAYER_COLORS = {
        "terrain":    "#FFFFFF",
        "roads":      "#000000",
        "water":      "#0094FF",
        "vegetation": "#00D921",
        "trees":      "#006921",
        "buildings":  "#898989",
    }

    piece_centers = []
    for layers in pieces:
        all_verts = np.vstack([mesh.vertices for mesh in layers.values()])
        piece_centers.append(all_verts[:, :2].mean(axis=0))
    global_center = np.mean(piece_centers, axis=0)

    leaves = []
    piece_leaf_ids = []
    for i, layers in enumerate(pieces):
        direction = piece_centers[i] - global_center
        norm = np.linalg.norm(direction)
        offset_xy = direction / norm * puzzle_gap_mm if norm > 1e-6 else np.zeros(2)
        ids_for_piece = []
        for name, mesh in layers.items():
            color = LAYER_COLORS.get(name, "#FF0000" if name.startswith("gpx_") else "#888888")
            exploded = mesh.copy()
            exploded.apply_translation([offset_xy[0], offset_xy[1], 0])
            leaves.append((f"piece{i}_{name}", exploded, color))
            ids_for_piece.append(len(leaves))
        piece_leaf_ids.append(ids_for_piece)

    all_bounds = np.array([m.bounds for _, m, _ in leaves])
    global_min = all_bounds[:, 0, :].min(axis=0)
    global_max = all_bounds[:, 1, :].max(axis=0)
    item_x = plate_size / 2 - (global_min[0] + global_max[0]) / 2
    item_y = plate_size / 2 - (global_min[1] + global_max[1]) / 2
    item_z = -global_min[2]
    item_transform = f"1 0 0 0 1 0 0 0 1 {item_x:.6f} {item_y:.6f} {item_z:.6f}"

    obj_model_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
        '<metadata name="BambuStudio:3mfVersion">1</metadata>',
        '<resources>'
    ]
    for i, (label, mesh, _) in enumerate(leaves):
        obj_model_lines.append(f'<object id="{i+1}" type="model" p:UUID="{uuid.uuid4()}"><mesh><vertices>')
        for v in mesh.vertices:
            obj_model_lines.append(f'<vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>')
        obj_model_lines.append('</vertices><triangles>')
        for f in mesh.faces:
            obj_model_lines.append(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>')
        obj_model_lines.append('</triangles></mesh></object>')
    obj_model_lines.append('</resources></model>')

    n_leaves = len(leaves)
    n_pieces = len(pieces)
    piece_obj_ids = [n_leaves + i + 1 for i in range(n_pieces)]

    main_model_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
        '<metadata name="Application">BambuStudio-02.06.00.51</metadata>',
        '<metadata name="BambuStudio:3mfVersion">1</metadata>',
        '<resources>'
    ]
    for i in range(n_pieces):
        piece_uuid = str(uuid.uuid4())
        main_model_lines.append(f'<object id="{piece_obj_ids[i]}" type="model" p:UUID="{piece_uuid}"><components>')
        for leaf_id in piece_leaf_ids[i]:
            main_model_lines.append(f'<component p:path="/3D/Objects/object_1.model" objectid="{leaf_id}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
        main_model_lines.append('</components></object>')
    main_model_lines.append('</resources>')

    build_uuid = str(uuid.uuid4())
    main_model_lines.append(f'<build p:UUID="{build_uuid}">')
    for oid in piece_obj_ids:
        main_model_lines.append(f'<item objectid="{oid}" p:UUID="{uuid.uuid4()}" transform="{item_transform}" printable="1"/>')
    main_model_lines.append('</build></model>')

    model_settings_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>']
    filament_colors = []
    for i in range(n_pieces):
        model_settings_lines.append(f'<object id="{piece_obj_ids[i]}">')
        model_settings_lines.append(f'<metadata key="name" value="piece_{i}"/>')
        model_settings_lines.append('<metadata key="extruder" value="1"/>')
        for leaf_id in piece_leaf_ids[i]:
            label, _, color = leaves[leaf_id - 1]
            filament_colors.append(color)
            model_settings_lines += [
                f'<part id="{leaf_id}" subtype="normal_part">',
                f'<metadata key="name" value="{label}"/>',
                f'<metadata key="extruder" value="{leaf_id}"/>',
                '</part>'
            ]
        model_settings_lines.append('</object>')
    model_settings_lines.append('<plate>')
    model_settings_lines.append('<metadata key="plater_id" value="1"/>')
    model_settings_lines.append('<metadata key="plater_name" value=""/>')
    model_settings_lines.append('<metadata key="locked" value="false"/>')
    model_settings_lines.append('<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>')
    for i, oid in enumerate(piece_obj_ids):
        model_settings_lines.append('<model_instance>')
        model_settings_lines.append(f'<metadata key="object_id" value="{oid}"/>')
        model_settings_lines.append('<metadata key="instance_id" value="0"/>')
        model_settings_lines.append('</model_instance>')
    model_settings_lines.append('</plate>')
    model_settings_lines.append('<assemble>')
    for i, oid in enumerate(piece_obj_ids):
        model_settings_lines.append(f'<assemble_item object_id="{oid}" instance_id="0" transform="1 0 0 0 1 0 0 0 1 0 0 0" offset="0 0 0"/>')
    model_settings_lines.append('</assemble>')
    model_settings_lines.append('</config>')

    n = len(filament_colors)
    filament_settings = json.dumps({
        "filament_colour": filament_colors,
        "default_filament_colour": [""] * n,
        "filament_colour_type": ["1"] * n,
        "filament_settings_id": ["Bambu PLA Basic @BBL A1M"] * n,
        "filament_type": ["PLA"] * n,
        "filament_vendor": ["Bambu Lab"] * n,
    }, indent=2)
    rels_main = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    rels_obj = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('3D/Objects/object_1.model', '\n'.join(obj_model_lines))
        zf.writestr('3D/3dmodel.model', '\n'.join(main_model_lines))
        zf.writestr('3D/_rels/3dmodel.model.rels', rels_obj)
        zf.writestr('_rels/.rels', rels_main)
        zf.writestr('[Content_Types].xml', types)
        zf.writestr('Metadata/model_settings.config', '\n'.join(model_settings_lines))
        zf.writestr('Metadata/project_settings.config', filament_settings)
        zf.writestr('Metadata/filaments_colors.json', filament_settings)
        zf.writestr('Metadata/filament_sequence.json', '{"plate_1":{"nozzle_sequence":[],"optimal_assignment":[],"sequence":[]}}')

    log(f"[PUZZLE] exporté {n_pieces} pièces ({n_leaves} objets colorés) → {output_path}")

def save_mesh(mesh, output_path, name):
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    broken = trimesh.repair.broken_faces(mesh)
    mesh.export(output_path)
    if len(broken) > 0:
        log(f"[AVERT] [{name}] {len(broken)} faces brisées")
    else:
        log(f"[OK] [{name}] aucune face brisée")

def save_3mf(meshes, stl_paths, output_path, gpx_list=None, plate_size=180):
    LAYER_COLORS = {
        "terrain_base.stl":       "#FFFFFF",
        "terrain_roads.stl":      "#000000",
        "terrain_water.stl":      "#0094FF",
        "terrain_vegetation.stl": "#00D921",
        "terrain_trees.stl":      "#006921",
        "terrain_buildings.stl":  "#898989",
    }

    meshes_data = [(os.path.basename(p), m) for m, p in zip(meshes, stl_paths) if len(m.faces) > 0]

    if gpx_list:
        stl_dir = os.path.dirname(output_path)
        for i, gpx in enumerate(gpx_list):
            fname = f"terrain_gpx_{i}.stl"
            path = os.path.join(stl_dir, fname)
            if not os.path.exists(path):
                continue
            mesh = trimesh.load(path)
            if isinstance(mesh, trimesh.Scene):
                mesh = trimesh.util.concatenate(list(mesh.geometry.values()))
            if len(mesh.faces) == 0:
                continue
            LAYER_COLORS[fname] = gpx.get("color", "#FF0000")
            meshes_data.append((fname, mesh))

    all_bounds = np.array([m.bounds for _, m in meshes_data])
    global_min = all_bounds[:, 0, :].min(axis=0)
    global_max = all_bounds[:, 1, :].max(axis=0)
    item_x = plate_size / 2 - (global_min[0] + global_max[0]) / 2
    item_y = plate_size / 2 - (global_min[1] + global_max[1]) / 2
    item_z = -global_min[2]

    obj_model_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
        '<metadata name="BambuStudio:3mfVersion">1</metadata>',
        '<resources>'
    ]
    for i, (fname, mesh) in enumerate(meshes_data):
        obj_model_lines.append(f'<object id="{i+1}" type="model" p:UUID="{uuid.uuid4()}"><mesh><vertices>')
        for v in mesh.vertices:
            obj_model_lines.append(f'<vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>')
        obj_model_lines.append('</vertices><triangles>')
        for f in mesh.faces:
            obj_model_lines.append(f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>')
        obj_model_lines.append('</triangles></mesh></object>')
    obj_model_lines.append('</resources></model>')

    main_obj_id = len(meshes_data) + 1
    main_uuid = str(uuid.uuid4())
    main_model_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
        '<metadata name="Application">BambuStudio-02.06.00.51</metadata>',
        '<metadata name="BambuStudio:3mfVersion">1</metadata>',
        '<resources>',
        f'<object id="{main_obj_id}" type="model" p:UUID="{main_uuid}">',
        '<components>'
    ]
    for i, (fname, _) in enumerate(meshes_data):
        main_model_lines.append(f'<component p:path="/3D/Objects/object_1.model" objectid="{i+1}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
    build_uuid = str(uuid.uuid4())
    item_uuid = str(uuid.uuid4())
    item_transform = f"1 0 0 0 1 0 0 0 1 {item_x:.6f} {item_y:.6f} {item_z:.6f}"
    main_model_lines += ['</components></object>', '</resources>', f'<build p:UUID="{build_uuid}"><item objectid="{main_obj_id}" p:UUID="{item_uuid}" transform="{item_transform}" printable="1"/></build>', '</model>']

    model_settings_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>', f'<object id="{main_obj_id}">',
                            f'<metadata key="name" value="topopixel"/>', f'<metadata key="extruder" value="1"/>']
    filament_colors = []
    for i, (fname, _) in enumerate(meshes_data):
        color_hex = LAYER_COLORS.get(fname, "#888888").lstrip("#")[:6]
        filament_colors.append(f'#{color_hex.upper()}')
        label = fname.replace("terrain_", "").replace(".stl", "")
        model_settings_lines += [
            f'<part id="{i+1}" subtype="normal_part">',
            f'<metadata key="name" value="{label}"/>',
            f'<metadata key="extruder" value="{i+1}"/>',
            f'<metadata key="source_file" value="{fname}"/>',
            '</part>'
        ]
    model_settings_lines += [
        '</object>',
        '<plate>',
        '<metadata key="plater_id" value="1"/>',
        '<metadata key="plater_name" value=""/>',
        '<metadata key="locked" value="false"/>',
        '<metadata key="thumbnail_file" value="Metadata/plate_1.png"/>',
        '<model_instance>',
        f'<metadata key="object_id" value="{main_obj_id}"/>',
        '<metadata key="instance_id" value="0"/>',
        '</model_instance>',
        '</plate>',
        '<assemble>',
        f'<assemble_item object_id="{main_obj_id}" instance_id="0" transform="1 0 0 0 1 0 0 0 1 0 0 0" offset="0 0 0"/>',
        '</assemble>',
        '</config>'
    ]

    n = len(filament_colors)
    filament_settings = json.dumps({
        "filament_colour": filament_colors,
        "default_filament_colour": [""] * n,
        "filament_colour_type": ["1"] * n,
        "filament_settings_id": ["Bambu PLA Basic @BBL A1M"] * n,
        "filament_type": ["PLA"] * n,
        "filament_vendor": ["Bambu Lab"] * n,
    }, indent=2)
    rels_main = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    rels_obj = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/Objects/object_1.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    types = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('3D/Objects/object_1.model', '\n'.join(obj_model_lines))
        zf.writestr('3D/3dmodel.model', '\n'.join(main_model_lines))
        zf.writestr('3D/_rels/3dmodel.model.rels', rels_obj)
        zf.writestr('_rels/.rels', rels_main)
        zf.writestr('[Content_Types].xml', types)
        zf.writestr('Metadata/model_settings.config', '\n'.join(model_settings_lines))
        zf.writestr('Metadata/project_settings.config', filament_settings)
        zf.writestr('Metadata/filaments_colors.json', filament_settings)
        zf.writestr('Metadata/filament_sequence.json', '{"plate_1":{"nozzle_sequence":[],"optimal_assignment":[],"sequence":[]}}')

    log(f"[3MF] exporté : {output_path}")
    
def add_anchor(mesh, cols, rows):
    anchor = trimesh.creation.box(extents=[0.001, 0.001, 0.001])
    anchor.apply_translation([0, 0, 0])
    anchor2 = trimesh.creation.box(extents=[0.001, 0.001, 0.001])
    anchor2.apply_translation([cols - 1, rows - 1, 0])
    return trimesh.util.concatenate([mesh, anchor, anchor2])

def _osm_cache_path(bbox, data_type, cache_dir=CACHE_DIR):
    os.makedirs(os.path.join(cache_dir, "osm"), exist_ok=True)
    key = f"{data_type}_{bbox['south']:.6f}_{bbox['north']:.6f}_{bbox['west']:.6f}_{bbox['east']:.6f}"
    return os.path.join(cache_dir, "osm", f"{key}.pkl")

def _parse_osm_cache_bbox(fname):
    m = re.match(
        r"(\w+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)\.pkl",
        fname
    )
    if not m:
        return None
    data_type, s, n, w, e = m.groups()
    return {"data_type": data_type, "south": float(s), "north": float(n), "west": float(w), "east": float(e)}

def _parse_satellite_cache_bbox(fname):
    m = re.match(r".*_crop_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)\.tif$", fname)
    if not m:
        return None
    west, south, east, north = (float(g) for g in m.groups())
    return {"west": west, "south": south, "east": east, "north": north}

def _parse_landcover_cache_bbox(fname):
    m = re.match(r".*_([NS])(\d{2})([EW])(\d{3})_MAP\.tif$", fname)
    if not m:
        return None
    ns, lat_str, ew, lon_str = m.groups()
    lat = int(lat_str) * (1 if ns == "N" else -1)
    lon = int(lon_str) * (1 if ew == "E" else -1)
    return {"west": lon, "south": lat, "east": lon + 3, "north": lat + 3}

def _osm_cache_load(bbox, data_type, cache_dir=CACHE_DIR):
    osm_dir = os.path.join(cache_dir, "osm")
    if not os.path.isdir(osm_dir):
        return None
    def trunc(v): return round(v, 6)
    s = trunc(bbox["south"])
    n = trunc(bbox["north"])
    w = trunc(bbox["west"])
    e = trunc(bbox["east"])
    for fname in os.listdir(osm_dir):
        parsed = _parse_osm_cache_bbox(fname)
        if parsed is None or parsed["data_type"] != data_type:
            continue
        cs, cn, cw, ce = parsed["south"], parsed["north"], parsed["west"], parsed["east"]
        if cs <= s and cn >= n and cw <= w and ce >= e:
            log(f"[OSM CACHE] hit : {fname}")
            fpath = os.path.join(osm_dir, fname)
            with open(fpath, "rb") as f:
                data = pickle.load(f)
            return _osm_cache_clip(data, bbox, _OSM_CACHE_SUBLABELS.get(data_type))
    log(f"[OSM CACHE] miss : {data_type}")
    return None

def _osm_cache_clip(data, bbox, labels=None):
    if data is None:
        return None
    if isinstance(data, tuple):
        return tuple(
            _osm_cache_clip(d, bbox, labels[i] if isinstance(labels, list) and i < len(labels) else None)
            for i, d in enumerate(data)
        )
    try:
        if isinstance(data, gpd.GeoDataFrame) and len(data) > 0:
            clipped = data.cx[bbox["west"]:bbox["east"], bbox["south"]:bbox["north"]]
            tag = f" {labels}" if isinstance(labels, str) else ""
            log(f"[OSM CACHE] clip{tag} : {len(data)} → {len(clipped)} features")
            return clipped
    except Exception:
        pass
    return data

def _osm_cache_save(bbox, data_type, data, cache_dir=CACHE_DIR):
    path = _osm_cache_path(bbox, data_type, cache_dir)
    with open(path, "wb") as f:
        pickle.dump(data, f)
    log(f"[OSM CACHE] sauvegardé : {os.path.basename(path)}")

def download_roads(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "roads", cache_dir)
    if cached is not None:
        return cached

    log("Téléchargement des routes OSM...")
    try:
        graph = ox.graph_from_bbox(
            bbox=(bbox["west"], bbox["south"], bbox["east"], bbox["north"]),
            network_type="all",
            retain_all=True,
            truncate_by_edge=True,
        )
        edges = ox.graph_to_gdfs(graph, nodes=False)
        log(f"{len(edges)} segments de route téléchargés")
        _osm_cache_save(bbox, "roads", edges, cache_dir)
        return edges
    except Exception as e:
        log(f"Aucune route : {e}")
        return None

def download_railways(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "railways", cache_dir)
    if cached is not None:
        return cached

    log("Téléchargement des voies ferrées OSM...")
    try:
        rail_polygon = box(bbox["west"], bbox["south"], bbox["east"], bbox["north"])
        rails = ox.features_from_polygon(rail_polygon, tags={"railway": ["rail", "light_rail", "narrow_gauge", "subway", "tram"]})
        rails = rails[rails.geometry.type == "LineString"]
        if len(rails) == 0:
            return None
        rails = rails.copy()
        rails["highway"] = "railway"
        rails = rails[["geometry", "highway"]].reset_index(drop=True)
        log(f"{len(rails)} segments de rail téléchargés")
        _osm_cache_save(bbox, "railways", rails, cache_dir)
        return rails
    except Exception as e:
        log(f"Aucun rail : {e}")
        return None

def download_osm_all(bbox, cache_dir=CACHE_DIR):
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])
    t0 = time.time()

    cached_water = _osm_cache_load(bbox, "water", cache_dir)
    cached_veg = _osm_cache_load(bbox, "vegetation", cache_dir)
    cached_buildings = _osm_cache_load(bbox, "buildings", cache_dir)

    if cached_water is not None and cached_veg is not None and cached_buildings is not None:
        water_areas, waterways, coastlines = cached_water
        forest, other_veg = cached_veg
        return {
            "water": {"water_areas": water_areas, "waterways": waterways, "coastlines": coastlines},
            "vegetation": {"forest": forest, "other_veg": other_veg},
            "buildings": {"buildings": cached_buildings},
        }

    all_features = ox.features.features_from_bbox(bounds, {
        "natural": ["water", "wood", "grassland", "scrub", "coastline"],
        "landuse": ["reservoir", "forest", "meadow", "grass"],
        "waterway": ["river", "canal", "stream"],
        "leisure": "park",
        "building": True,
    })
    log(f"[OSM ALL] {len(all_features)} features en {time.time()-t0:.1f}s")

    nat = all_features["natural"] if "natural" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    luse = all_features["landuse"] if "landuse" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    wway = all_features["waterway"] if "waterway" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    leis = all_features["leisure"] if "leisure" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    bld = all_features["building"] if "building" in all_features.columns else pd.Series(dtype=str, index=all_features.index)

    poly_mask = all_features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    line_mask = all_features.geometry.geom_type.isin(["LineString", "MultiLineString"])

    water_mask = (nat.isin(["water"]) | luse.isin(["reservoir"])) & poly_mask
    waterway_mask = wway.isin(["river", "canal", "stream"]) & (poly_mask | line_mask)
    coastline_mask = nat.isin(["coastline"]) & line_mask
    forest_mask = nat.isin(["wood"]) | luse.isin(["forest"])
    other_veg_mask = nat.isin(["grassland", "scrub"]) | luse.isin(["meadow", "grass"]) | leis.isin(["park"])
    building_mask = bld.notna() & bld.ne("")

    water_areas = all_features[water_mask]
    waterways = all_features[waterway_mask]
    coastlines = all_features[coastline_mask]
    forest = all_features[forest_mask & poly_mask]
    other_veg = all_features[other_veg_mask & poly_mask]
    buildings = all_features[building_mask & poly_mask]

    ocean_polygon = _build_ocean_polygon(coastlines if not coastlines.empty else None, bbox)
    final_water_areas = water_areas if not water_areas.empty else None
    if ocean_polygon is not None:
        ocean_gdf = gpd.GeoDataFrame(geometry=[ocean_polygon], crs="EPSG:4326")
        final_water_areas = pd.concat([final_water_areas, ocean_gdf], ignore_index=True) if final_water_areas is not None else ocean_gdf

    water_result = (
        final_water_areas,
        waterways if not waterways.empty else None,
        coastlines if not coastlines.empty else None,
    )
    veg_result = (forest if not forest.empty else None, other_veg if not other_veg.empty else None)

    _osm_cache_save(bbox, "water", water_result, cache_dir)
    _osm_cache_save(bbox, "vegetation", veg_result, cache_dir)
    _osm_cache_save(bbox, "buildings", buildings if not buildings.empty else None, cache_dir)

    log(f"[OSM ALL] water={len(water_areas)} waterways={len(waterways)} coastlines={len(coastlines)} forest={len(forest)} other_veg={len(other_veg)} buildings={len(buildings)}")

    return {
        "water": {"water_areas": water_result[0], "waterways": water_result[1], "coastlines": water_result[2]},
        "vegetation": {"forest": veg_result[0], "other_veg": veg_result[1]},
        "buildings": {"buildings": buildings if not buildings.empty else None},
    }
    
def download_water(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "water", cache_dir)
    if cached is not None:
        return cached

    log("Téléchargement des données hydrographiques OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])

    try:
        all_features = ox.features.features_from_bbox(bounds, {
            "natural": ["water", "coastline"],
            "landuse": "reservoir",
            "waterway": ["river", "canal", "stream"],
        })
    except Exception as e:
        log(f"Aucune donnée hydrographique : {e}")
        result = (None, None, None)
        _osm_cache_save(bbox, "water", result, cache_dir)
        return result

    nat = all_features["natural"] if "natural" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    luse = all_features["landuse"] if "landuse" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    wway = all_features["waterway"] if "waterway" in all_features.columns else pd.Series(dtype=str, index=all_features.index)

    poly_mask = all_features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    line_mask = all_features.geometry.geom_type.isin(["LineString", "MultiLineString"])

    water_mask = (nat.isin(["water"]) | luse.isin(["reservoir"])) & poly_mask
    waterway_mask = wway.isin(["river", "canal", "stream"]) & (poly_mask | line_mask)
    coastline_mask = nat.isin(["coastline"]) & line_mask

    water_areas = all_features[water_mask]
    waterways = all_features[waterway_mask]
    coastlines = all_features[coastline_mask]

    log(f"{len(water_areas)} surfaces d'eau (>= {MIN_WATER_AREA_M2}m²)")
    log(f"{len(waterways)} cours d'eau (>= {MIN_WATERWAY_LENGTH_M}m)")
    log(f"{len(coastlines)} segments de côte")

    ocean_polygon = _build_ocean_polygon(coastlines if not coastlines.empty else None, bbox)
    final_water_areas = water_areas.copy() if not water_areas.empty else None
    if final_water_areas is not None:
        final_water_areas["is_ocean"] = False
    if ocean_polygon is not None:
        ocean_gdf = gpd.GeoDataFrame({"is_ocean": [True]}, geometry=[ocean_polygon], crs="EPSG:4326")
        final_water_areas = pd.concat([final_water_areas, ocean_gdf], ignore_index=True) if final_water_areas is not None else ocean_gdf

    result = (
        final_water_areas,
        waterways if not waterways.empty else None,
        coastlines if not coastlines.empty else None,
    )
    _osm_cache_save(bbox, "water", result, cache_dir)
    return result

def _build_ocean_polygon(coastlines, bbox, resolution_m=RESOLUTION_M):
    if coastlines is None or len(coastlines) == 0:
        return None

    lines = []
    for geom in coastlines.geometry:
        if geom is None or geom.is_empty:
            continue
        if geom.geom_type == "LineString":
            lines.append(geom)
        elif geom.geom_type == "MultiLineString":
            lines.extend(list(geom.geoms))

    if not lines:
        return None

    tolerance = 0.0002
    snapped_lines = list(lines)
    for i in range(len(snapped_lines)):
        for j in range(len(snapped_lines)):
            if i != j:
                snapped_lines[i] = snap(snapped_lines[i], snapped_lines[j], tolerance)
    merged_lines = linemerge(snapped_lines)

    bbox_edges = [
        SLine([(bbox["west"], bbox["south"]), (bbox["east"], bbox["south"])]),
        SLine([(bbox["east"], bbox["south"]), (bbox["east"], bbox["north"])]),
        SLine([(bbox["east"], bbox["north"]), (bbox["west"], bbox["north"])]),
        SLine([(bbox["west"], bbox["north"]), (bbox["west"], bbox["south"])]),
    ]

    all_lines = [merged_lines] + bbox_edges
    node = unary_union(all_lines)
    polygons = list(polygonize(node))

    if not polygons:
        return None

    ocean_candidates = []
    offset = 0.0001
    for line in lines:
        coords = list(line.coords)
        for i in range(len(coords) - 1):
            x0, y0 = coords[i]
            x1, y1 = coords[i + 1]
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length < 1e-9:
                continue
            dxn, dyn = dx / length, dy / length
            mx, my = (x0 + x1) / 2, (y0 + y1) / 2
            right_point = Point(mx + dyn * offset, my - dxn * offset)
            if not (bbox["west"] <= right_point.x <= bbox["east"] and bbox["south"] <= right_point.y <= bbox["north"]):
                continue
            found = [p for p in polygons if p.contains(right_point)]
            if found:
                ocean_candidates = found
                break
        if ocean_candidates:
            break

    if not ocean_candidates:
        return None

    ocean = unary_union(ocean_candidates)
    ocean = ocean.buffer(0)

    min_width_m = resolution_m * 1.5
    min_width_deg = min_width_m / 111320
    opened = ocean.buffer(-min_width_deg).buffer(min_width_deg)
    if opened.geom_type == "MultiPolygon":
        opened = max(opened.geoms, key=lambda p: p.area)
    if opened.geom_type == "Polygon" and opened.area > ocean.area * 0.5:
        ocean = opened

    if ocean.geom_type == "Polygon":
        ocean = ShPoly(list(ocean.exterior.coords))

    return ocean
  
def download_vegetation(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "vegetation", cache_dir)
    if cached is not None:
        return cached

    log("Téléchargement de la végétation OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])

    try:
        forest = ox.features.features_from_bbox(bounds, {
            "natural": "wood",
            "landuse": "forest"
        })
        forest = forest[forest.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        log(f"{len(forest)} zones de forêt")
    except Exception:
        forest = None
        log("Aucune forêt trouvée")

    try:
        other_veg = ox.features.features_from_bbox(bounds, {
            "natural": ["grassland", "scrub"],
            "landuse": ["meadow", "grass"],
            "leisure": "park"
        })
        other_veg = other_veg[other_veg.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        log(f"{len(other_veg)} autres zones de végétation")
    except Exception:
        other_veg = None
        log("Aucune autre végétation trouvée")

    result = (forest, other_veg)
    _osm_cache_save(bbox, "vegetation", result, cache_dir)
    return result

def download_buildings(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "buildings", cache_dir)
    if cached is not None:
        return cached

    log("Téléchargement des bâtiments OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])
    try:
        buildings = ox.features.features_from_bbox(bounds, {"building": True})
        buildings = buildings[buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        log(f"{len(buildings)} bâtiments (>= {MIN_BUILDING_AREA_M2}m²)")
        _osm_cache_save(bbox, "buildings", buildings, cache_dir)
        return buildings
    except Exception as e:
        log(f"Aucun bâtiment : {e}")
        return None

def get_building_height(row):
    if "height" in row.index and pd.notna(row["height"]):
        try:
            return float(str(row["height"]).replace("m", "").strip())
        except ValueError:
            pass
    if "building:levels" in row.index and pd.notna(row["building:levels"]):
        try:
            return float(row["building:levels"]) * METERS_PER_LEVEL
        except ValueError:
            pass
    return DEFAULT_BUILDING_HEIGHT_M
    
def compute_layer_stats(road_edges, water_areas, waterways, forest, other_veg, buildings, gpx_list, bbox):
    stats = {}

    if road_edges is not None and len(road_edges) > 0:
        edges_m = road_edges.to_crs("EPSG:3857")
        stats["roads"] = {
            "count": len(edges_m),
            "distance_m": float(edges_m.geometry.length.sum()),
        }

    water_stats = {}
    if waterways is not None and len(waterways) > 0:
        ww_m = waterways.to_crs("EPSG:3857")
        water_stats["waterway_count"] = len(ww_m)
        water_stats["waterway_distance_m"] = float(ww_m.geometry.length.sum())
    if water_areas is not None and len(water_areas) > 0:
        wa_m = water_areas.to_crs("EPSG:3857")
        water_stats["area_count"] = len(wa_m)
        water_stats["area_ha"] = float(wa_m.geometry.area.sum() / 10000)
    if water_stats:
        stats["water"] = water_stats

    veg_geoms = []
    if forest is not None and len(forest) > 0:
        veg_geoms.append(forest)
    if other_veg is not None and len(other_veg) > 0:
        veg_geoms.append(other_veg)
    if veg_geoms:
        veg_all = pd.concat(veg_geoms)
        veg_m = veg_all.to_crs("EPSG:3857")
        stats["vegetation"] = {
            "count": len(veg_m),
            "area_ha": float(veg_m.geometry.area.sum() / 10000),
        }

    if buildings is not None and len(buildings) > 0:
        b_m = buildings.to_crs("EPSG:3857")
        stats["buildings"] = {
            "count": len(b_m),
            "area_ha": float(b_m.geometry.area.sum() / 10000),
        }

    if gpx_list:
        gpx_stats = []
        for gpx in gpx_list:
            points = parse_gpx_file(gpx["path"], bbox)
            distance_m = 0.0
            for i in range(len(points) - 1):
                lon1, lat1 = points[i]
                lon2, lat2 = points[i + 1]
                dx = (lon2 - lon1) * 111320 * math.cos(math.radians((lat1 + lat2) / 2))
                dy = (lat2 - lat1) * 111320
                distance_m += math.hypot(dx, dy)
            gpx_stats.append({
                "path": gpx["path"],
                "color": gpx.get("color", "#FF0000"),
                "distance_m": distance_m,
            })
        stats["gpx"] = gpx_stats

    return stats

def simplify_mesh(mesh,factor=0.5):
    if len(mesh.faces) < 20000:
        return mesh
    target = int(len(mesh.faces) * factor)
    if target >= len(mesh.faces):
        return mesh
    simplified = mesh.simplify_quadric_decimation(face_count=target, aggression=3)
    trimesh.repair.fix_normals(simplified)
    return simplified if simplified.is_watertight else mesh

if __name__ == "__main__":
    from generation_worker import GenerationWorker

    parser = argparse.ArgumentParser(description="Génère un terrain 3D en ligne de commande")
    parser.add_argument("lat", type=float, help="Latitude du centre")
    parser.add_argument("lon", type=float, help="Longitude du centre")

    parser.add_argument("--shape", choices=["rect", "circle", "hexagon", "polygon"], default="rect",
                         help="Forme de découpe du modèle")
    parser.add_argument("--radius", type=float, default=RADIUS_M,
                         help="Rayon en mètres (utilisé pour rect/circle/hexagon)")
    parser.add_argument("--polygon", nargs="+", default=None,
                         help="Points du polygone 'lon,lat lon,lat ...' (requis si --shape polygon)")

    parser.add_argument("--resolution", type=float, default=RESOLUTION_M, help="Résolution DEM (m/px)")
    parser.add_argument("--size-mm", type=float, default=SIZE_MM, help="Taille du modèle en mm")
    parser.add_argument("--base-thickness", type=float, default=BASE_THICKNESS, help="Épaisseur du socle")
    parser.add_argument("--z-scale", type=float, default=Z_SCALE, help="Facteur d'exagération verticale")

    parser.add_argument("--layers", nargs="+",
                         default=["terrain", "roads", "water", "vegetation", "trees", "buildings"],
                         choices=["terrain", "roads", "water", "vegetation", "trees", "buildings", "gpx"],
                         help="Couches à générer")
    parser.add_argument("--railways", action="store_true", help="Inclure les voies ferrées")
    parser.add_argument("--bathymetry", action="store_true", help="Activer la bathymétrie")

    parser.add_argument("--road-width", type=float, default=ROAD_WIDTH_PX, help="Largeur des routes (px)")
    parser.add_argument("--road-height", type=float, default=ROAD_HEIGHT, help="Hauteur des routes")
    parser.add_argument("--roads-z-bot-pct", type=float, default=ROADS_Z_BOT_RATIO_PCT, help="Ratio Z bas routes (%%)")

    parser.add_argument("--river-width", type=float, default=RIVER_WIDTH_PX, help="Largeur cours d'eau (px)")
    parser.add_argument("--water-height", type=float, default=WATER_HEIGHT, help="Hauteur de l'eau")
    parser.add_argument("--min-water-area", type=float, default=MIN_WATER_AREA_M2, help="Aire min. plan d'eau (m²)")
    parser.add_argument("--min-waterway-length", type=float, default=MIN_WATERWAY_LENGTH_M, help="Longueur min. cours d'eau (m)")
    parser.add_argument("--water-z-bot-pct", type=float, default=WATER_Z_BOT_RATIO_PCT, help="Ratio Z bas eau (%%)")

    parser.add_argument("--min-veg-area", type=float, default=MIN_VEG_AREA_M2, help="Aire min. végétation (m²)")
    parser.add_argument("--veg-z-bot-pct", type=float, default=VEG_Z_BOT_RATIO_PCT, help="Ratio Z bas végétation (%%)")
    parser.add_argument("--tree-height", type=float, default=TREE_HEIGHT, help="Hauteur des arbres")
    parser.add_argument("--tree-radius", type=float, default=TREE_RADIUS, help="Rayon des arbres")
    parser.add_argument("--tree-density", type=float, default=TREE_DENSITY, help="Densité des arbres")

    parser.add_argument("--min-building-area", type=float, default=MIN_BUILDING_AREA_M2, help="Aire min. bâtiment (m²)")
    parser.add_argument("--default-building-height", type=float, default=DEFAULT_BUILDING_HEIGHT_M, help="Hauteur bâtiment par défaut")
    parser.add_argument("--building-height-scale", type=float, default=BUILDING_HEIGHT_SCALE, help="Échelle hauteur bâtiments")
    parser.add_argument("--building-min-height", type=float, default=BUILDING_MIN_HEIGHT, help="Hauteur min. bâtiment")
    parser.add_argument("--building-max-height", type=float, default=BUILDING_MAX_HEIGHT, help="Hauteur max. bâtiment")
    parser.add_argument("--meters-per-level", type=float, default=METERS_PER_LEVEL, help="Mètres par étage")
    parser.add_argument("--buildings-z-bot-pct", type=float, default=BUILDINGS_Z_BOT_RATIO_PCT, help="Ratio Z bas bâtiments (%%)")

    parser.add_argument("--gpx", nargs="*", default=[], help="Chemins vers des fichiers GPX à tracer")
    parser.add_argument("--gpx-color", type=str, default="#FF0000", help="Couleur des tracés GPX (hex)")
    parser.add_argument("--gpx-width", type=float, default=GPX_WIDTH_PX, help="Largeur du tracé GPX (px)")
    parser.add_argument("--gpx-height", type=float, default=GPX_HEIGHT, help="Hauteur du tracé GPX")
    parser.add_argument("--gpx-z-bot-pct", type=float, default=GPX_Z_BOT_RATIO_PCT, help="Ratio Z bas GPX (%%)")

    parser.add_argument("--gpxz-key", type=str, default=GPXZ_API_KEY, help="Clé API GPXZ")
    parser.add_argument("--cache-dir", type=str, default=CACHE_DIR, help="Dossier de cache")
    parser.add_argument("--stl-dir", type=str, default="STL", help="Dossier de sortie")
    parser.add_argument("--project-name", type=str, default="topopixel", help="Nom du projet (fichier .3mf)")

    args = parser.parse_args()

    bbox = compute_bbox(args.lat, args.lon, radius_m=args.radius)

    if args.shape == "rect":
        shape_params = {"west": bbox["west"], "south": bbox["south"], "east": bbox["east"], "north": bbox["north"]}
    elif args.shape == "circle":
        shape_params = {"center_lat": args.lat, "center_lon": args.lon, "radius_m": args.radius}
    elif args.shape == "hexagon":
        shape_params = {"center_lat": args.lat, "center_lon": args.lon, "radius_m": args.radius}
    elif args.shape == "polygon":
        if not args.polygon:
            parser.error("--polygon requiert une liste de points 'lon,lat lon,lat ...' avec --shape polygon")
        shape_params = {"points": [tuple(map(float, p.split(","))) for p in args.polygon]}

    layers = list(args.layers)
    if args.gpx and "gpx" not in layers:
        layers.append("gpx")

    ui_params = {
        "ENABLED_LAYERS": layers,
        "RESOLUTION_M": args.resolution,
        "SIZE_MM": args.size_mm,
        "BASE_THICKNESS": args.base_thickness,
        "Z_SCALE": args.z_scale,

        "INCLUDE_RAILWAYS": args.railways,
        "ROAD_WIDTH_PX": args.road_width,
        "ROAD_HEIGHT": args.road_height,
        "ROADS_Z_BOT_RATIO_PCT": args.roads_z_bot_pct,

        "ENABLE_BATHYMETRY": args.bathymetry,
        "RIVER_WIDTH_PX": args.river_width,
        "WATER_HEIGHT": args.water_height,
        "MIN_WATER_AREA_M2": args.min_water_area,
        "MIN_WATERWAY_LENGTH_M": args.min_waterway_length,
        "WATER_Z_BOT_RATIO_PCT": args.water_z_bot_pct,

        "MIN_VEG_AREA_M2": args.min_veg_area,
        "VEG_Z_BOT_RATIO_PCT": args.veg_z_bot_pct,
        "TREE_HEIGHT": args.tree_height,
        "TREE_RADIUS": args.tree_radius,
        "TREE_DENSITY": args.tree_density,

        "MIN_BUILDING_AREA_M2": args.min_building_area,
        "DEFAULT_BUILDING_HEIGHT_M": args.default_building_height,
        "BUILDING_HEIGHT_SCALE": args.building_height_scale,
        "BUILDING_MIN_HEIGHT": args.building_min_height,
        "BUILDING_MAX_HEIGHT": args.building_max_height,
        "METERS_PER_LEVEL": args.meters_per_level,
        "BUILDINGS_Z_BOT_RATIO_PCT": args.buildings_z_bot_pct,

        "GPX_LIST": [{"path": p, "color": args.gpx_color, "enabled": True} for p in args.gpx],
        "GPX_WIDTH_PX": args.gpx_width,
        "GPX_HEIGHT": args.gpx_height,
        "GPX_Z_BOT_RATIO_PCT": args.gpx_z_bot_pct,

        "GPXZ_API_KEY": args.gpxz_key,
        "CACHE_DIR": args.cache_dir,
        "STL_DIR": args.stl_dir,
        "PROJECT_NAME": args.project_name,
    }

    worker = GenerationWorker(
        shape_kind=args.shape,
        shape_params=shape_params,
        ui_params=ui_params,
        preview_workers={},
    )

    result = {}
    worker.progress.connect(lambda msg: log(f"[INFO] {msg}"))
    worker.finished_ok.connect(lambda payload: result.update(files=payload.get("files", [])))
    worker.failed.connect(lambda err: result.update(error=err))
    worker.run()

    if "error" in result:
        log(f"\n[ERREUR]\n{result['error']}")
        sys.exit(1)

    log(f"\nTerminé. Fichiers générés dans : {os.path.abspath(args.stl_dir)}")
    for f in result.get("files", []):
        log(f"  - {f}")

    metadata_path = os.path.join(args.stl_dir, "metadata.json")
    if os.path.exists(metadata_path):
        with open(metadata_path) as f:
            meta = json.load(f)
        log(f"\nAltitude : {meta['altitude_min_m']:.1f} m → {meta['altitude_max_m']:.1f} m")
        log(f"Échelle : {meta['scale_mm']:.4f} mm/px | socle : {meta['base_thickness_mm']:.1f} mm")
        log("\nPoids PLA estimé :")
        for name, grams in meta["pla_grams"].items():
            if name == "gpx":
                continue
            if grams is not None:
                log(f"  - {name:<12} {grams:.1f} g")
        for layer, stats in meta["layer_stats"].items():
            log(f"  [{layer}] {stats}")
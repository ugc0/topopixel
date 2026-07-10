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
from scipy.ndimage import gaussian_filter
import io

OVERPASS_ENDPOINTS = {
    "private.coffee": "https://overpass.private.coffee/api",
    "gall": "https://gall.openstreetmap.de/api",
    "lambert": "https://lambert.openstreetmap.de/api"
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
GPXZ_API_KEY = "ak_0KtNnPbu_v9orPuogXRYmTu0p"
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
BUILDING_MIN_HEIGHT = 3.0
BUILDING_MAX_HEIGHT = 20.0
GPX_WIDTH_PX = 2.0
GPX_HEIGHT = 4.0
ROADS_Z_BOT_RATIO_PCT = 33
WATER_Z_BOT_RATIO_PCT = 33
VEG_Z_BOT_RATIO_PCT = 90
BUILDINGS_Z_BOT_RATIO_PCT = 90
GPX_Z_BOT_RATIO_PCT = 95

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
    z_surface = (elevation - z_min) / RESOLUTION_M * Z_SCALE

    r_idx, c_idx = np.meshgrid(np.arange(rows), np.arange(cols), indexing='ij')

    verts_top = np.stack([
        c_idx.ravel().astype(np.float64),
        (rows - 1 - r_idx.ravel()).astype(np.float64),
        z_surface.ravel().astype(np.float64)
    ], axis=1)

    verts_bot = np.stack([
        c_idx.ravel().astype(np.float64),
        (rows - 1 - r_idx.ravel()).astype(np.float64),
        np.full(rows * cols, -BASE_THICKNESS, dtype=np.float64)
    ], axis=1)

    def vidx(r, c):
        return r * cols + c

    faces_top, faces_bot = [], []
    for r in range(rows - 1):
        for c in range(cols - 1):
            i00, i10 = vidx(r, c),   vidx(r+1, c)
            i01, i11 = vidx(r, c+1), vidx(r+1, c+1)
            faces_top.append([i00, i01, i10])
            faces_top.append([i01, i11, i10])
            faces_bot.append([i00, i10, i01])
            faces_bot.append([i01, i10, i11])

    n = rows * cols
    faces_bot_off = [[f[0]+n, f[1]+n, f[2]+n] for f in faces_bot]

    faces_sides = []
    for c in range(cols - 1):
        t0, t1 = vidx(0, c), vidx(0, c+1)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, b0, t1])
        faces_sides.append([t1, b0, b1])
        t0, t1 = vidx(rows-1, c), vidx(rows-1, c+1)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, t1, b0])
        faces_sides.append([t1, b1, b0])
    for r in range(rows - 1):
        t0, t1 = vidx(r, 0), vidx(r+1, 0)
        b0, b1 = t0+n, t1+n
        faces_sides.append([t0, t1, b0])
        faces_sides.append([t1, b1, b0])
        t0, t1 = vidx(r, cols-1), vidx(r+1, cols-1)
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
        return None

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    bbox_pixel = ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)])
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
        return None

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
        return None

    masque_meshes = []
    failed_count = 0
    for poly in geom_list:
        try:
            m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            if not m.is_watertight:
                m.merge_vertices()
                trimesh.repair.fix_normals(m)
                trimesh.repair.fill_holes(m)
            masque_meshes.append(m)
        except Exception as e:
            failed_count += 1
            continue

    if failed_count > 0:
        log(f"[ROADS] {failed_count} polygones ignorés sur {len(geom_list)}")

    if not masque_meshes:
        log("[ROADS] aucun masque généré")
        return None

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    log(f"[ROADS] masque brut : faces={len(masque.faces)} watertight={masque.is_watertight}")

    if not masque.is_watertight:
        masque.merge_vertices()
        trimesh.repair.fix_normals(masque)
        trimesh.repair.fill_holes(masque)

    return masque

def apply_roads_boolean(masque, mesh_terrain, mesh_terrain_pristine):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain

    try:
        mesh_roads = trimesh.boolean.intersection([mesh_terrain_pristine, masque], engine='manifold')
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
        mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque_trou], engine='manifold')
    except Exception as e:
        log(f"[ROADS] soustraction terrain échouée : {e}")
        return mesh_roads, mesh_terrain
    log(f"[ROADS] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    
    return mesh_roads, mesh_terrain_new

def build_water_mask(water_areas, waterways, bbox, shape, mesh_terrain, z_min, enable_bathymetry=False, elevation=None):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (WATER_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0
    sea_level_z = -z_min / RESOLUTION_M
    bbox_pixel = ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)])

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return None
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords]
                     for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.intersection(bbox_pixel)
        if not p.is_valid:
            p = p.buffer(0)
        return p if not p.is_empty and p.area > 0 else None

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

    if not pixel_polys:
        log("[WATER] aucun polygone, mesh vide")
        return None

    exploded_polys = []
    for poly, is_ocean in pixel_polys:
        if poly.geom_type == "MultiPolygon":
            for sub in poly.geoms:
                exploded_polys.append((sub, is_ocean))
        else:
            exploded_polys.append((poly, is_ocean))

    masque_meshes = []
    ocean_meshes = []
    for poly, is_ocean in exploded_polys:
        if not poly.is_valid or poly.area < 0.5:
            continue
        try:
            if is_ocean:
                if enable_bathymetry:
                    m = _build_ocean_bathymetric_mesh(poly, elevation, bbox, shape, z_min, sea_level_z, z_bot, WATER_HEIGHT)
                    if m is None:
                        continue
                else:
                    m = trimesh.creation.extrude_polygon(poly, height=sea_level_z - z_bot)
                    m.apply_translation([0, 0, z_bot])
                ocean_meshes.append(m)
            else:
                m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
                m.apply_translation([0, 0, z_bot])
                masque_meshes.append(m)
        except Exception as e:
            log(f"[WATER] extrusion masque échouée : {e}")
            continue

    masque_meshes_final = []
    if masque_meshes:
        masque = trimesh.util.concatenate(masque_meshes)
        trimesh.repair.fix_normals(masque)
        if not masque.is_watertight:
            masque.merge_vertices()
            trimesh.repair.fix_normals(masque)
            trimesh.repair.fill_holes(masque)
        masque_meshes_final = [masque]
        log(f"[WATER] masque brut : faces={len(masque.faces)} watertight={masque.is_watertight}")

    ocean_meshes_final = []
    if ocean_meshes:
        ocean_mesh = trimesh.util.concatenate(ocean_meshes)
        trimesh.repair.fix_normals(ocean_mesh)
        if not ocean_mesh.is_watertight:
            ocean_mesh.merge_vertices()
            trimesh.repair.fix_normals(ocean_mesh)
            trimesh.repair.fill_holes(ocean_mesh)
        ocean_meshes_final = [ocean_mesh]
        log(f"[WATER] masque océan : faces={len(ocean_mesh.faces)} watertight={ocean_mesh.is_watertight} sea_level_z={sea_level_z:.3f}")

    if not masque_meshes_final and not ocean_meshes_final:
        log("[WATER] aucun masque généré")
        return None, None

    masque_lacs = masque_meshes_final[0] if masque_meshes_final else None
    masque_ocean = ocean_meshes_final[0] if ocean_meshes_final else None
    return masque_lacs, masque_ocean
    
def apply_water_boolean(masque_lacs, masque_ocean, mesh_terrain, mesh_terrain_pristine):
    if masque_lacs is None and masque_ocean is None:
        return trimesh.Trimesh(), mesh_terrain

    mesh_water_parts = []
    masque_trou_parts = []

    if masque_lacs is not None:
        try:
            mesh_lacs = trimesh.boolean.intersection([mesh_terrain_pristine, masque_lacs], engine='manifold')
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

    mesh_terrain_new = mesh_terrain
    for masque_trou in masque_trou_parts:
        mesh_terrain_new, terrain_ok = _gpx_safe_difference(mesh_terrain_new, masque_trou, "terrain")

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
        result = trimesh.boolean.intersection([grid_mesh, clip_solid], engine='manifold')
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

def build_veg_mask(forest, other_veg, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (VEG_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return None
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords] for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.intersection(ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)]))
        if not p.is_valid:
            p = p.buffer(0)
        return p if not p.is_empty and p.area > 0 else None

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

    if not pixel_polys:
        log("[VEG] aucun polygone, mesh vide")
        return None

    merged = unary_union(pixel_polys).buffer(0.5, join_style=1).buffer(-0.3, join_style=1)
    log(f"[VEG] après union+lissage : type={merged.geom_type}")

    geom_list = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    masque_meshes = []
    for poly in geom_list:
        if not poly.is_valid or poly.area < 1.0:
            continue
        try:
            m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            masque_meshes.append(m)
        except Exception as e:
            log(f"[VEG] extrusion masque échouée : {e}")
            continue

    if not masque_meshes:
        log("[VEG] aucun masque généré")
        return None

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    log(f"[VEG] masque : faces={len(masque.faces)} watertight={masque.is_watertight}")
    log(f"[VEG] masque bounds Z=[{masque.bounds[0][2]:.3f},{masque.bounds[1][2]:.3f}]")

    if not masque.is_watertight:
        log("[VEG] masque non-watertight — abandon")
        return None

    return masque

def apply_veg_boolean(masque, mesh_terrain, mesh_terrain_pristine):
    if masque is None:
        return trimesh.Trimesh(), mesh_terrain

    try:
        mesh_veg = trimesh.boolean.intersection([mesh_terrain_pristine, masque], engine='manifold')
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
        mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque], engine='manifold')
    except Exception as e:
        log(f"[VEG] soustraction terrain échouée : {e}")
        return mesh_veg, mesh_terrain
    log(f"[VEG] terrain après soustraction masque : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    log(f"[VEG] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")

    return mesh_veg, mesh_terrain_new

def build_trees_mesh(forest, bbox, shape, mesh_veg):
    rows, cols = shape

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return None
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords]
                     for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.intersection(ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)]))
        if not p.is_valid:
            p = p.buffer(0)
        return p if not p.is_empty and p.area > 0 else None

    if forest is None:
        log("[TREES] aucune forêt, mesh vide")
        return trimesh.Trimesh()

    forest_polys = []
    for geom in forest.geometry:
        if geom is None or geom.is_empty:
            continue
        parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
        for part in parts:
            p = convert_polygon(part)
            if p:
                forest_polys.append(p)

    log(f"[TREES] {len(forest_polys)} polygones forêt")

    if not forest_polys:
        log("[TREES] aucun polygone valide")
        return trimesh.Trimesh()

    merged = unary_union(forest_polys)

    tree_meshes = []
    minx, miny, maxx, maxy = merged.bounds
    area = merged.area
    n_trees = max(1, int(area * TREE_DENSITY / 1000))
    log(f"[TREES] surface={area:.1f}px² → {n_trees} arbres à placer")

    np.random.seed(42)
    placed = 0
    attempts = 0
    max_attempts = n_trees * 20

    veg_top_z = mesh_veg.bounds[1][2]

    while placed < n_trees and attempts < max_attempts:
        attempts += 1
        x = np.random.uniform(minx, maxx)
        y = np.random.uniform(miny, maxy)
        if not merged.contains(Point(x, y)):
            continue

        ray_origin = np.array([[x, y, veg_top_z + 1.0]])
        ray_dir = np.array([[0, 0, -1]])
        locs, _, _ = mesh_veg.ray.intersects_location(ray_origin, ray_dir)

        if len(locs) == 0:
            continue

        z_base = locs[:, 2].max()

        cone = trimesh.creation.cone(radius=TREE_RADIUS, height=TREE_HEIGHT)
        cone.apply_translation([x, y, z_base])
        tree_meshes.append(cone)
        placed += 1

    log(f"[TREES] {placed} arbres placés sur {attempts} tentatives")

    if not tree_meshes:
        log("[TREES] aucun arbre généré")
        return trimesh.Trimesh()

    mesh = trimesh.util.concatenate(tree_meshes)
    trimesh.repair.fix_normals(mesh)
    log(f"[TREES] vertices={len(mesh.vertices)} faces={len(mesh.faces)}")
    log(f"[TREES] watertight={mesh.is_watertight}")
    log(f"[TREES] bounds Z=[{mesh.bounds[0][2]:.3f},{mesh.bounds[1][2]:.3f}]")

    return mesh

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

def build_buildings_mask(buildings, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (BUILDINGS_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    if buildings is None:
        log("[BUILDINGS] aucun bâtiment, mesh vide")
        return None, []

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return []
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords] for ring in geom.interiors]
        p = ShPoly(exterior, interiors)
        if not p.is_valid:
            p = p.buffer(0)
        p = p.intersection(ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)]))
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
                    count += 1
                except Exception as e:
                    log(f"[BUILDINGS] extrusion échouée : {e}")
                    skip += 1
                    continue

    log(f"[BUILDINGS] {count} bâtiments traités, {skip} ignorés")

    if not masque_meshes:
        log("[BUILDINGS] aucun masque généré")
        return None, []

    masque_union = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque_union)
    log(f"[BUILDINGS] masque union : faces={len(masque_union.faces)} watertight={masque_union.is_watertight}")

    return masque_union, building_meshes

def apply_buildings_boolean(masque_union, building_meshes, mesh_terrain, mesh_terrain_pristine, monument_meshes=None):
    if masque_union is None or not building_meshes:
        return trimesh.Trimesh(), mesh_terrain

    monument_meshes = monument_meshes or {}

    try:
        mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque_union], engine='manifold')
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

def build_gpx_mask(gpx_points, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * (GPX_Z_BOT_RATIO_PCT / 100)
    z_top = mesh_terrain.bounds[1][2] + 1.0

    def lonlat_to_pixel_xy(lon, lat):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    bbox_pixel = ShPoly([(0,0),(cols-1,0),(cols-1,rows-1),(0,rows-1)])
    pixel_points = [lonlat_to_pixel_xy(lon, lat) for lon, lat in gpx_points]
    pixel_points = [(x, y) for x, y in pixel_points if 0 <= x <= cols-1 and 0 <= y <= rows-1]

    if len(pixel_points) < 2:
        log("[GPX] pas assez de points dans la bbox")
        return None

    line = SLine(pixel_points)
    buffered = line.buffer(GPX_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)

    if buffered.is_empty or buffered.area < 0.1:
        log("[GPX] masque vide")
        return None

    geom_list = list(buffered.geoms) if buffered.geom_type == "MultiPolygon" else [buffered]

    masque_meshes = []
    for poly in geom_list:
        if not poly.is_valid or poly.area < 0.1:
            continue
        try:
            poly = poly.simplify(0.1, preserve_topology=True)
            m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            masque_meshes.append(m)
        except Exception as e:
            log(f"[GPX] extrusion échouée : {e}")

    if not masque_meshes:
        return None

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    log(f"[GPX] masque : faces={len(masque.faces)} watertight={masque.is_watertight}")
    return masque if masque.is_watertight else None

def _gpx_safe_difference(mesh, cutter, label):
    try:
        result = trimesh.boolean.difference([mesh, cutter], engine='manifold')
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
        result = trimesh.boolean.difference([mesh_fixed, cutter], engine='manifold')
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

def _puzzle_cell_polygon(cx0, cx1, cy0, cy1, cell_w, cell_h,
                          tab_right, tab_left, tab_top, tab_bot):
    r = min(cell_w, cell_h) * 0.14
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
            a = -math.pi*i/n
            pts.append((xc + r*math.cos(a), cy0 + r*math.sin(a)))
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
            a = math.pi*i/n
            pts.append((xc + r*math.cos(math.pi - a), cy1 + r*math.sin(math.pi - a)))
        for i in range(8): pts.append((xc-r - i/7*(xc-r-cx0), cy1))
        pts += [(cx0, cy1)]
    else:
        for i in range(8): pts.append((cx1 - i/7*(cx1-(xc+r)), cy1))
        for i in range(n+1):
            a = math.pi*i/n
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

def build_puzzle_pieces(mesh_terrain, n_pieces, extra_meshes=None):
    bounds = mesh_terrain.bounds
    x_min, y_min = bounds[0][0], bounds[0][1]
    x_max, y_max = bounds[1][0], bounds[1][1]
    z_min, z_max = bounds[0][2], bounds[1][2]

    n_cols = math.ceil(math.sqrt(n_pieces))
    n_rows = math.ceil(n_pieces / n_cols)

    cell_w = (x_max - x_min) / n_cols
    cell_h = (y_max - y_min) / n_rows
    z_height = z_max - z_min + 4.0

    tab_x = {col: (col % 2 == 1) for col in range(1, n_cols)}
    tab_y = {row: (row % 2 == 1) for row in range(1, n_rows)}

    pieces = []
    for row in range(n_rows):
        for col in range(n_cols):
            idx = row * n_cols + col
            if idx >= n_pieces:
                break

            cx0 = x_min + col * cell_w
            cx1 = cx0 + cell_w
            cy0 = y_min + row * cell_h
            cy1 = cy0 + cell_h

            right = None if (col == n_cols-1 or idx+1 >= n_pieces) else (not tab_x[col+1])
            left  = None if col == 0 else tab_x[col]
            top   = None if (row == n_rows-1 or idx+n_cols >= n_pieces) else (not tab_y[row+1])
            bot   = None if row == 0 else tab_y[row]

            poly2d = _puzzle_cell_polygon(cx0, cx1, cy0, cy1, cell_w, cell_h,
                                          right, left, top, bot)
            if poly2d.is_empty or poly2d.area < 1:
                continue

            try:
                mask = trimesh.creation.extrude_polygon(poly2d, height=z_height)
                mask.apply_translation([0, 0, z_min - 2])
            except Exception as e:
                log(f"[PUZZLE] masque ({row},{col}) échoué: {e}")
                continue

            piece_meshes = []

            try:
                terrain_piece = trimesh.boolean.intersection([mesh_terrain, mask], engine='manifold')
                if terrain_piece is not None and len(terrain_piece.faces) > 0:
                    if terrain_piece.volume > mesh_terrain.volume * 0.0001:
                        piece_meshes.append(terrain_piece)
            except Exception as e:
                log(f"[PUZZLE] terrain pièce ({row},{col}) échoué: {e}")
                continue

            if not piece_meshes:
                continue

            if extra_meshes:
                for extra in extra_meshes:
                    if extra is None or len(extra.faces) == 0:
                        continue
                    try:
                        ep = trimesh.boolean.intersection([extra, mask], engine='manifold')
                        if ep is not None and len(ep.faces) > 0:
                            piece_meshes.append(ep)
                    except Exception:
                        pass

            piece = trimesh.util.concatenate(piece_meshes) if len(piece_meshes) > 1 else piece_meshes[0]
            log(f"[PUZZLE] pièce ({row},{col}): faces={len(piece.faces)} watertight={piece.is_watertight}")
            pieces.append(piece)

    return pieces

def export_puzzle_3mf(pieces, output_path):

    with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
</Types>''')
        zf.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" Target="/3D/3dmodel.model"/>
</Relationships>''')

        objects_xml = ""
        build_items = ""
        for i, piece in enumerate(pieces):
            oid = i + 1
            verts = piece.vertices
            faces = piece.faces
            vertices_xml = "".join(
                f'<vertex x="{v[0]:.6f}" y="{v[1]:.6f}" z="{v[2]:.6f}"/>'
                for v in verts
            )
            triangles_xml = "".join(
                f'<triangle v1="{f[0]}" v2="{f[1]}" v3="{f[2]}"/>'
                for f in faces
            )
            objects_xml += f'''<object id="{oid}" type="model" name="piece_{i}">
  <mesh>
    <vertices>{vertices_xml}</vertices>
    <triangles>{triangles_xml}</triangles>
  </mesh>
</object>'''
            build_items += f'<item objectid="{oid}"/>'

        model_xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
  <resources>{objects_xml}</resources>
  <build>{build_items}</build>
</model>'''

        zf.writestr("3D/3dmodel.model", model_xml)

    log(f"[PUZZLE] exporté {len(pieces)} pièces → {output_path}")

def save_mesh(mesh, output_path):
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    broken = trimesh.repair.broken_faces(mesh)
    mesh.export(output_path)
    if len(broken) > 0:
        log(f"[AVERT] {len(broken)} faces brisées")
    else:
        log(f"[OK] aucune face brisée")

def save_3mf(meshes, stl_paths, output_path, gpx_list=None):
    LAYER_COLORS = {
        "terrain_base.stl":       "#FFFFFF",
        "terrain_roads.stl":      "#000000",
        "terrain_water.stl":      "#0094FF",
        "terrain_vegetation.stl": "#00D921",
        "terrain_trees.stl":      "#006921",
        "terrain_buildings.stl":  "#898989",
    }

    def hex_to_rgb(h):
        h = h.lstrip("#")
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

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

    obj_model_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
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
        '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p">',
        '<resources>',
        f'<object id="{main_obj_id}" type="model" p:UUID="{main_uuid}">',
        '<components>'
    ]
    for i, (fname, _) in enumerate(meshes_data):
        main_model_lines.append(f'<component p:path="/3D/Objects/object_1.model" objectid="{i+1}" p:UUID="{uuid.uuid4()}" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>')
    main_model_lines += ['</components></object>', '</resources>', f'<build><item objectid="{main_obj_id}"/></build>', '</model>']

    model_settings_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<config>', f'<object id="{main_obj_id}">',
                            f'<metadata key="name" value="topopixel"/>', f'<metadata key="extruder" value="1"/>']
    filament_colors = []
    for i, (fname, _) in enumerate(meshes_data):
        color_hex = LAYER_COLORS.get(fname, "#888888")
        r, g, b = hex_to_rgb(color_hex)
        filament_colors.append(f'#{r:02X}{g:02X}{b:02X}FF')
        label = fname.replace("terrain_", "").replace(".stl", "")
        model_settings_lines += [
            f'<part id="{i+1}" subtype="normal_part">',
            f'<metadata key="name" value="{label}"/>',
            f'<metadata key="extruder" value="{i+1}"/>',
            f'<metadata key="source_file" value="{fname}"/>',
            '</part>'
        ]
    model_settings_lines += ['</object>', '</config>']

    filament_settings = '{\n    "filament_colour": ' + str(filament_colors).replace("'", '"') + '\n}'
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
            return _osm_cache_clip(data, bbox)
    log(f"[OSM CACHE] miss : {data_type}")
    return None

def _osm_cache_clip(data, bbox):
    if data is None:
        return None
    if isinstance(data, tuple):
        return tuple(
            _osm_cache_clip(d, bbox) if d is not None else None
            for d in data
        )
    try:
        if isinstance(data, gpd.GeoDataFrame) and len(data) > 0:
            clipped = data.cx[bbox["west"]:bbox["east"], bbox["south"]:bbox["north"]]
            log(f"[OSM CACHE] clip : {len(data)} → {len(clipped)} features")
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
        water_areas, waterways = cached_water
        forest, other_veg = cached_veg
        return {
            "water": {"water_areas": water_areas, "waterways": waterways},
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

    # --- Emprise ---
    parser.add_argument("--shape", choices=["rect", "circle", "hexagon", "polygon"], default="rect",
                         help="Forme de découpe du modèle")
    parser.add_argument("--radius", type=float, default=RADIUS_M,
                         help="Rayon en mètres (utilisé pour rect/circle/hexagon)")
    parser.add_argument("--polygon", nargs="+", default=None,
                         help="Points du polygone 'lon,lat lon,lat ...' (requis si --shape polygon)")

    # --- DEM / échelle ---
    parser.add_argument("--resolution", type=float, default=RESOLUTION_M, help="Résolution DEM (m/px)")
    parser.add_argument("--size-mm", type=float, default=SIZE_MM, help="Taille du modèle en mm")
    parser.add_argument("--base-thickness", type=float, default=BASE_THICKNESS, help="Épaisseur du socle")
    parser.add_argument("--z-scale", type=float, default=Z_SCALE, help="Facteur d'exagération verticale")

    # --- Couches ---
    parser.add_argument("--layers", nargs="+",
                         default=["terrain", "roads", "water", "vegetation", "trees", "buildings"],
                         choices=["terrain", "roads", "water", "vegetation", "trees", "buildings", "gpx"],
                         help="Couches à générer")
    parser.add_argument("--railways", action="store_true", help="Inclure les voies ferrées")
    parser.add_argument("--bathymetry", action="store_true", help="Activer la bathymétrie")

    # --- Routes ---
    parser.add_argument("--road-width", type=float, default=ROAD_WIDTH_PX, help="Largeur des routes (px)")
    parser.add_argument("--road-height", type=float, default=ROAD_HEIGHT, help="Hauteur des routes")
    parser.add_argument("--roads-z-bot-pct", type=float, default=ROADS_Z_BOT_RATIO_PCT, help="Ratio Z bas routes (%%)")

    # --- Eau ---
    parser.add_argument("--river-width", type=float, default=RIVER_WIDTH_PX, help="Largeur cours d'eau (px)")
    parser.add_argument("--water-height", type=float, default=WATER_HEIGHT, help="Hauteur de l'eau")
    parser.add_argument("--min-water-area", type=float, default=MIN_WATER_AREA_M2, help="Aire min. plan d'eau (m²)")
    parser.add_argument("--min-waterway-length", type=float, default=MIN_WATERWAY_LENGTH_M, help="Longueur min. cours d'eau (m)")
    parser.add_argument("--water-z-bot-pct", type=float, default=WATER_Z_BOT_RATIO_PCT, help="Ratio Z bas eau (%%)")

    # --- Végétation ---
    parser.add_argument("--min-veg-area", type=float, default=MIN_VEG_AREA_M2, help="Aire min. végétation (m²)")
    parser.add_argument("--veg-z-bot-pct", type=float, default=VEG_Z_BOT_RATIO_PCT, help="Ratio Z bas végétation (%%)")
    parser.add_argument("--tree-height", type=float, default=TREE_HEIGHT, help="Hauteur des arbres")
    parser.add_argument("--tree-radius", type=float, default=TREE_RADIUS, help="Rayon des arbres")
    parser.add_argument("--tree-density", type=float, default=TREE_DENSITY, help="Densité des arbres")

    # --- Bâtiments ---
    parser.add_argument("--min-building-area", type=float, default=MIN_BUILDING_AREA_M2, help="Aire min. bâtiment (m²)")
    parser.add_argument("--default-building-height", type=float, default=DEFAULT_BUILDING_HEIGHT_M, help="Hauteur bâtiment par défaut")
    parser.add_argument("--building-height-scale", type=float, default=BUILDING_HEIGHT_SCALE, help="Échelle hauteur bâtiments")
    parser.add_argument("--building-min-height", type=float, default=BUILDING_MIN_HEIGHT, help="Hauteur min. bâtiment")
    parser.add_argument("--building-max-height", type=float, default=BUILDING_MAX_HEIGHT, help="Hauteur max. bâtiment")
    parser.add_argument("--meters-per-level", type=float, default=METERS_PER_LEVEL, help="Mètres par étage")
    parser.add_argument("--buildings-z-bot-pct", type=float, default=BUILDINGS_Z_BOT_RATIO_PCT, help="Ratio Z bas bâtiments (%%)")

    # --- GPX ---
    parser.add_argument("--gpx", nargs="*", default=[], help="Chemins vers des fichiers GPX à tracer")
    parser.add_argument("--gpx-color", type=str, default="#FF0000", help="Couleur des tracés GPX (hex)")
    parser.add_argument("--gpx-width", type=float, default=GPX_WIDTH_PX, help="Largeur du tracé GPX (px)")
    parser.add_argument("--gpx-height", type=float, default=GPX_HEIGHT, help="Hauteur du tracé GPX")
    parser.add_argument("--gpx-z-bot-pct", type=float, default=GPX_Z_BOT_RATIO_PCT, help="Ratio Z bas GPX (%%)")

    # --- Divers / sortie ---
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
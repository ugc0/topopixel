import argparse
import math
import os
import time
import requests
import rasterio
import numpy as np
import struct
import osmnx as ox
from shapely.geometry import Polygon, Point, shape, MultiPolygon, LineString as SLine, box
from shapely.ops import unary_union
from shapely.geometry import Polygon as ShPoly
import geopandas as gpd
import pandas as pd
from rasterio.transform import from_bounds
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
        print(f"[OVERPASS] {name} : {'OK' if status[name] else 'KO'}")
    return status

def apply_overpass_strategy(status):
    if status.get("private.coffee"):
        ox.settings.overpass_url = OVERPASS_ENDPOINTS["private.coffee"]
        print(f"[OVERPASS] stratégie : private.coffee (parallèle)")
        return "parallel"
    elif status.get("gall") and status.get("lambert"):
        ox.settings.overpass_url = "https://overpass-api.de/api"
        print(f"[OVERPASS] stratégie : gall+lambert (séquentiel)")
        return "sequential"
    elif status.get("gall") or status.get("lambert"):
        name = "gall" if status.get("gall") else "lambert"
        ox.settings.overpass_url = OVERPASS_ENDPOINTS[name]
        print(f"[OVERPASS] stratégie : {name} seul (séquentiel)")
        return "sequential"
    else:
        ox.settings.overpass_url = OVERPASS_ENDPOINTS["private.coffee"]
        print(f"[OVERPASS] aucun endpoint disponible")
        return "unavailable"

_overpass_status = {}
_overpass_strategy = "sequential"

warnings.filterwarnings("ignore", category=RuntimeWarning, module="trimesh")

os.environ["CPL_LOG"] = "NUL"

RADIUS_M = 3_000
GPXZ_API_KEY = "ak_0KtNnPbu_v9orPuogXRYmTu0p"
RESOLUTION_M = 5
CACHE_DIR = "cache"
ROAD_LEVELS = ["motorway", "trunk", "primary", "secondary", "tertiary", "residential", "motorway_link", "trunk_link", "primary_link", "secondary_link", "tertiary_link", "residential_link"]
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
TREE_DENSITY = 0.005
BUILDING_MIN_HEIGHT = 3.0
BUILDING_MAX_HEIGHT = 20.0

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

    print(f"Zone {total_km2:.1f}km² découpée en {len(tiles)} tuiles ({n_lat}×{n_lon})")
    return tiles

def download_dem_gpxz_tiled(bbox, api_key, resolution_m=RESOLUTION_M, cache_dir=CACHE_DIR):
    tiles = split_bbox(bbox)

    if len(tiles) == 1:
        return download_dem_gpxz(bbox, api_key, resolution_m, cache_dir)

    tile_arrays = []
    for i, tile in enumerate(tiles):
        print(f"Tuile {i+1}/{len(tiles)}...")
        tile_path = download_dem_gpxz(tile, api_key, resolution_m, cache_dir)
        arr = load_dem(tile_path)
        tile_arrays.append((tile, arr))

    souths = sorted(set(round(t["south"], 6) for t, _ in tile_arrays))
    wests  = sorted(set(round(t["west"],  6) for t, _ in tile_arrays))
    n_lat  = len(souths)
    n_lon  = len(wests)

    south_to_idx = {s: i for i, s in enumerate(souths)}
    west_to_idx  = {w: i for i, w in enumerate(wests)}

    grid = [[None] * n_lon for _ in range(n_lat)]
    for tile, arr in tile_arrays:
        i_lat = south_to_idx[round(tile["south"], 6)]
        i_lon = west_to_idx[round(tile["west"],  6)]
        grid[i_lat][i_lon] = arr

    all_shapes = [grid[i][j].shape for i in range(n_lat) for j in range(n_lon)]
    target_rows = max(s[0] for s in all_shapes)
    target_cols = max(s[1] for s in all_shapes)

    rows_arrays = []
    for i_lat in range(n_lat - 1, -1, -1):
        row_arrs = []
        for i_lon in range(n_lon):
            arr = grid[i_lat][i_lon]
            arr = arr[:target_rows, :] if arr.shape[0] > target_rows \
                  else np.pad(arr, ((0, target_rows - arr.shape[0]), (0, 0)), mode='edge')
            arr = arr[:, :target_cols] if arr.shape[1] > target_cols \
                  else np.pad(arr, ((0, 0), (0, target_cols - arr.shape[1])), mode='edge')
            row_arrs.append(arr)
        rows_arrays.append(np.hstack(row_arrs))

    target_full_cols = rows_arrays[0].shape[1]
    rows_arrays = [r[:, :target_full_cols] if r.shape[1] > target_full_cols
                   else np.pad(r, ((0, 0), (0, target_full_cols - r.shape[1])), mode='edge')
                   for r in rows_arrays]

    elevation = np.vstack(rows_arrays)
    print(f"[DEBUG tiled] Matrice assemblée : {elevation.shape}")
    print(f"[DEBUG tiled] élévation min={elevation.min():.1f} max={elevation.max():.1f} — vérifier cohérence géographique")

    assembled_path = get_cache_path(bbox, resolution_m, cache_dir)
    rows_n, cols_n = elevation.shape
    transform = from_bounds(bbox["west"], bbox["south"], bbox["east"], bbox["north"], cols_n, rows_n)

    with rasterio.open(assembled_path, "w", driver="GTiff",
                       height=rows_n, width=cols_n, count=1,
                       dtype="float32", transform=transform) as dst:
        dst.write(elevation.astype(np.float32), 1)

    print(f"[DEBUG tiled] DEM assemblé sauvegardé : {assembled_path}")
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
            print(f"[CACHE] bbox demandée couverte par {fname}")
            
            print(f"[CACHE] cache couvrant trouvé : {fname}")
            print(f"[CACHE] cache bbox : south={parsed['south']} north={parsed['north']} west={parsed['west']} east={parsed['east']}")
            print(f"[CACHE] demandé   : south={bbox['south']} north={bbox['north']} west={bbox['west']} east={bbox['east']}")
            
            return fpath
    return None

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

    print(f"[CACHE] sous-région extraite → {output_path} ({rows}×{cols} px)")
    return output_path
    
def download_dem_gpxz(bbox, api_key, resolution_m=RESOLUTION_M, cache_dir=CACHE_DIR, max_retries=5):
    output_path = get_cache_path(bbox, resolution_m, cache_dir)

    if os.path.exists(output_path):
        print(f"DEM en cache exact : {output_path}")
        return output_path

    covering = _find_covering_cache(bbox, resolution_m, cache_dir)
    if covering is not None:
        print(f"DEM couvert par cache existant : {covering}")
        return _extract_bbox_from_cache(covering, bbox, output_path)

    print(f"Téléchargement DEM GPXZ ({resolution_m}m)...")
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
            print(f"Rate limit 429 — attente {wait}s...")
            time.sleep(wait)
        else:
            raise RuntimeError(f"Erreur GPXZ : {response.status_code}\n{response.text}")
    else:
        raise RuntimeError("GPXZ : trop de tentatives échouées (429)")

    with open(output_path, "wb") as f:
        f.write(response.content)

    print(f"DEM sauvegardé : {output_path} ({os.path.getsize(output_path)} octets)")
    time.sleep(1.5)
    return output_path
 
def load_dem(dem_path):
    with rasterio.open(dem_path) as src:
        elevation = src.read(1).astype(np.float32)
        nodata = src.nodata

    if nodata is not None:
        elevation[elevation == nodata] = 0

    print(f"Matrice : {elevation.shape} pixels")
    print(f"Altitude min : {elevation.min():.1f}m  max : {elevation.max():.1f}m")

    return elevation

def build_terrain_mesh(elevation, z_min):
    rows, cols = elevation.shape
    z_surface = (elevation - z_min) / RESOLUTION_M

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

    print(f"[TERRAIN] vertices={len(mesh.vertices)} faces={len(mesh.faces)}")
    print(f"[TERRAIN] watertight={mesh.is_watertight}")
    print(f"[TERRAIN] bounds Z=[{mesh.bounds[0][2]:.3f}, {mesh.bounds[1][2]:.3f}]")
    print(f"[TERRAIN] attendu  Z=[{-BASE_THICKNESS}, {z_surface.max():.3f}]")
    if not mesh.is_watertight:
        broken = trimesh.repair.broken_faces(mesh)
        print(f"[TERRAIN] faces brisées : {len(broken)}")

    return mesh

def build_roads_mesh(road_edges, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * 0.33
    z_top = mesh_terrain.bounds[1][2] + 1.0

    if road_edges is None:
        print("[ROADS] aucune edge OSM, mesh vide")
        return trimesh.Trimesh(), mesh_terrain

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

    print(f"[ROADS] {len(pixel_lines)} lignes converties")

    buffered = [line.buffer(ROAD_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                for line in pixel_lines]
    buffered = [p for p in buffered if not p.is_empty and p.area > 0]

    if not buffered:
        print("[ROADS] aucun polygone valide")
        return trimesh.Trimesh(), mesh_terrain

    merged = unary_union(buffered).buffer(0.3, join_style=1).buffer(-0.2, join_style=1)
    print(f"[ROADS] après union+lissage : type={merged.geom_type}")

    geom_list = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    masque_meshes = []
    for poly in geom_list:
        if not poly.is_valid or poly.area < 0.5:
            continue
        try:
            m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            masque_meshes.append(m)
        except Exception as e:
            print(f"[ROADS] extrusion masque échouée : {e}")
            continue

    if not masque_meshes:
        print("[ROADS] aucun masque généré")
        return trimesh.Trimesh(), mesh_terrain

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    print(f"[ROADS] masque brut : faces={len(masque.faces)} watertight={masque.is_watertight}")

    if not masque.is_watertight:
        print("[ROADS] masque non-watertight — abandon")
        return trimesh.Trimesh(), mesh_terrain

    mesh_roads = trimesh.boolean.intersection([mesh_terrain, masque], engine='manifold')
    mesh_roads.apply_translation([0, 0, ROAD_HEIGHT])
    print(f"[ROADS] intersection+translation : faces={len(mesh_roads.faces)} watertight={mesh_roads.is_watertight}")
    print(f"[ROADS] bounds Z=[{mesh_roads.bounds[0][2]:.3f},{mesh_roads.bounds[1][2]:.3f}]")

    masque_trou = masque.copy()
    masque_trou.apply_translation([0, 0, ROAD_HEIGHT])
    trimesh.repair.fix_normals(masque_trou)

    mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque_trou], engine='manifold')
    print(f"[ROADS] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    print(f"[ROADS] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")

    return mesh_roads, mesh_terrain_new

def build_water_mesh(water_areas, waterways, bbox, shape, mesh_terrain):
    rows, cols = shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * 0.33
    z_top = mesh_terrain.bounds[1][2] + 1.0
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

    if water_areas is not None:
        for geom in water_areas.geometry:
            if geom is None or geom.is_empty:
                continue
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                p = convert_polygon(part)
                if p:
                    pixel_polys.append(p)

    if waterways is not None:
        for geom in waterways.geometry:
            if geom is None or geom.is_empty:
                continue
            if geom.geom_type == "Polygon":
                p = convert_polygon(geom)
                if p:
                    pixel_polys.append(p)
            elif geom.geom_type == "MultiPolygon":
                for part in geom.geoms:
                    p = convert_polygon(part)
                    if p:
                        pixel_polys.append(p)
            elif geom.geom_type == "LineString":
                coords = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.coords]
                p = SLine(coords).buffer(RIVER_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                if not p.is_empty and p.area > 0:
                    pixel_polys.append(p)
            elif geom.geom_type == "MultiLineString":
                for line in geom.geoms:
                    coords = [latlon_to_pixel_xy(lat, lon) for lon, lat in line.coords]
                    p = SLine(coords).buffer(RIVER_WIDTH_PX, cap_style=2, join_style=2).intersection(bbox_pixel)
                    if not p.is_empty and p.area > 0:
                        pixel_polys.append(p)

    print(f"[WATER] {len(pixel_polys)} polygones convertis")

    if not pixel_polys:
        print("[WATER] aucun polygone, mesh vide")
        return trimesh.Trimesh(), mesh_terrain

    merged = unary_union(pixel_polys).buffer(0.3, join_style=1).buffer(-0.2, join_style=1)
    print(f"[WATER] après union+lissage : type={merged.geom_type}")

    geom_list = list(merged.geoms) if merged.geom_type == "MultiPolygon" else [merged]

    masque_meshes = []
    for poly in geom_list:
        if not poly.is_valid or poly.area < 0.5:
            continue
        try:
            m = trimesh.creation.extrude_polygon(poly, height=z_top - z_bot)
            m.apply_translation([0, 0, z_bot])
            masque_meshes.append(m)
        except Exception as e:
            print(f"[WATER] extrusion masque échouée : {e}")
            continue

    if not masque_meshes:
        print("[WATER] aucun masque généré")
        return trimesh.Trimesh(), mesh_terrain

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    print(f"[WATER] masque brut : faces={len(masque.faces)} watertight={masque.is_watertight}")

    if not masque.is_watertight:
        print("[WATER] masque non-watertight — abandon")
        return trimesh.Trimesh(), mesh_terrain

    mesh_water = trimesh.boolean.intersection([mesh_terrain, masque], engine='manifold')
    mesh_water.apply_translation([0, 0, WATER_HEIGHT])
    print(f"[WATER] intersection+translation : faces={len(mesh_water.faces)} watertight={mesh_water.is_watertight}")
    print(f"[WATER] bounds Z=[{mesh_water.bounds[0][2]:.3f},{mesh_water.bounds[1][2]:.3f}]")

    masque_trou = masque.copy()
    masque_trou.apply_translation([0, 0, WATER_HEIGHT])
    trimesh.repair.fix_normals(masque_trou)

    mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque_trou], engine='manifold')
    print(f"[WATER] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    print(f"[WATER] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")

    return mesh_water, mesh_terrain_new

def build_vegetation_mesh(forest, other_veg, elevation, z_min, bbox, mesh_terrain):
    rows, cols = elevation.shape
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * 0.33
    z_top = mesh_terrain.bounds[1][2] + 1.0

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

    print(f"[VEG] {len(pixel_polys)} polygones convertis")

    if not pixel_polys:
        print("[VEG] aucun polygone, mesh vide")
        return trimesh.Trimesh(), mesh_terrain

    merged = unary_union(pixel_polys).buffer(0.5, join_style=1).buffer(-0.3, join_style=1)
    print(f"[VEG] après union+lissage : type={merged.geom_type}")

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
            print(f"[VEG] extrusion masque échouée : {e}")
            continue

    if not masque_meshes:
        print("[VEG] aucun masque généré")
        return trimesh.Trimesh(), mesh_terrain

    masque = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque)
    print(f"[VEG] masque : faces={len(masque.faces)} watertight={masque.is_watertight}")
    print(f"[VEG] masque bounds Z=[{masque.bounds[0][2]:.3f},{masque.bounds[1][2]:.3f}]")

    if not masque.is_watertight:
        print("[VEG] masque non-watertight — abandon")
        return trimesh.Trimesh(), mesh_terrain

    mesh_veg = trimesh.boolean.intersection([mesh_terrain, masque], engine='manifold')
    print(f"[VEG] intersection terrain∩masque : faces={len(mesh_veg.faces)} watertight={mesh_veg.is_watertight}")
    print(f"[VEG] bounds Z=[{mesh_veg.bounds[0][2]:.3f},{mesh_veg.bounds[1][2]:.3f}]")

    if not mesh_veg.is_watertight:
        print("[VEG] mesh_veg non-watertight après intersection")
        broken = trimesh.repair.broken_faces(mesh_veg)
        print(f"[VEG] faces brisées : {len(broken)}")

    mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque], engine='manifold')
    print(f"[VEG] terrain après soustraction masque : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")
    print(f"[VEG] terrain Z max avant={mesh_terrain.bounds[1][2]:.4f} après={mesh_terrain_new.bounds[1][2]:.4f}")

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
        print("[TREES] aucune forêt, mesh vide")
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

    print(f"[TREES] {len(forest_polys)} polygones forêt")

    if not forest_polys:
        print("[TREES] aucun polygone valide")
        return trimesh.Trimesh()

    merged = unary_union(forest_polys)

    tree_meshes = []
    minx, miny, maxx, maxy = merged.bounds
    area = merged.area
    n_trees = max(1, int(area * TREE_DENSITY))
    print(f"[TREES] surface={area:.1f}px² → {n_trees} arbres à placer")

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

    print(f"[TREES] {placed} arbres placés sur {attempts} tentatives")

    if not tree_meshes:
        print("[TREES] aucun arbre généré")
        return trimesh.Trimesh()

    mesh = trimesh.util.concatenate(tree_meshes)
    trimesh.repair.fix_normals(mesh)
    print(f"[TREES] vertices={len(mesh.vertices)} faces={len(mesh.faces)}")
    print(f"[TREES] watertight={mesh.is_watertight}")
    print(f"[TREES] bounds Z=[{mesh.bounds[0][2]:.3f},{mesh.bounds[1][2]:.3f}]")

    return mesh

def build_buildings_mesh(buildings, bbox, shape, mesh_terrain):
    rows, cols = shape

    if buildings is None:
        print("[BUILDINGS] aucun bâtiment, mesh vide")
        return trimesh.Trimesh(), mesh_terrain

    def latlon_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

    def convert_polygon(geom):
        if geom.geom_type != "Polygon":
            return []
        exterior = [latlon_to_pixel_xy(lat, lon) for lon, lat in geom.exterior.coords]
        interiors = [[latlon_to_pixel_xy(lat, lon) for lon, lat in ring.coords]
                     for ring in geom.interiors]
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

    veg_top_z = mesh_terrain.bounds[1][2] + 1.0
    z_bot = -BASE_THICKNESS + BASE_THICKNESS * 0.9

    building_meshes = []
    masque_meshes = []
    skip = 0
    count = 0

    for _, row in buildings.iterrows():
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
                    masque = trimesh.creation.extrude_polygon(p, height=veg_top_z - z_bot)
                    masque.apply_translation([0, 0, z_bot])
                    if not masque.is_watertight:
                        skip += 1
                        continue
                    masque_meshes.append(masque)
                    building = trimesh.creation.extrude_polygon(p, height=height_px)
                    trimesh.repair.fix_normals(building)
                    building_meshes.append((building, masque))
                    count += 1
                except Exception as e:
                    print(f"[BUILDINGS] extrusion échouée : {e}")
                    skip += 1
                    continue

    print(f"[BUILDINGS] {count} bâtiments traités, {skip} ignorés")

    if not masque_meshes:
        print("[BUILDINGS] aucun masque généré")
        return trimesh.Trimesh(), mesh_terrain

    masque_union = trimesh.util.concatenate(masque_meshes)
    trimesh.repair.fix_normals(masque_union)
    print(f"[BUILDINGS] masque union : faces={len(masque_union.faces)} watertight={masque_union.is_watertight}")

    mesh_terrain_new = trimesh.boolean.difference([mesh_terrain, masque_union], engine='manifold')
    print(f"[BUILDINGS] terrain après soustraction : faces={len(mesh_terrain_new.faces)} watertight={mesh_terrain_new.is_watertight}")

    final_buildings = []
    for building, masque in building_meshes:
        locs = mesh_terrain.ray.intersects_location(
            np.array([[building.centroid[0], building.centroid[1], veg_top_z]]),
            np.array([[0, 0, -1]])
        )[0]
        z_base = locs[:, 2].max() if len(locs) > 0 else 0.0
        building.apply_translation([0, 0, z_base])
        trimesh.repair.fix_normals(building)
        final_buildings.append(building)

    if not final_buildings:
        print("[BUILDINGS] aucun bâtiment final")
        return trimesh.Trimesh(), mesh_terrain_new

    mesh_buildings = trimesh.util.concatenate(final_buildings)
    trimesh.repair.fix_normals(mesh_buildings)
    print(f"[BUILDINGS] vertices={len(mesh_buildings.vertices)} faces={len(mesh_buildings.faces)}")
    print(f"[BUILDINGS] watertight={mesh_buildings.is_watertight}")
    print(f"[BUILDINGS] bounds Z=[{mesh_buildings.bounds[0][2]:.3f},{mesh_buildings.bounds[1][2]:.3f}]")

    return mesh_buildings, mesh_terrain_new

def save_mesh(mesh, output_path):
    if os.path.dirname(output_path):
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    mesh.export(output_path)
    broken = trimesh.repair.broken_faces(mesh)
    print(f"[MESH] {output_path}")
    print(f"faces={len(mesh.faces)}  vertices={len(mesh.vertices)}")
    print(f"watertight={mesh.is_watertight}  winding_ok={mesh.is_winding_consistent}")
    if mesh.bounds is not None:
        print(f"bounds X=[{mesh.bounds[0][0]:.2f},{mesh.bounds[1][0]:.2f}]"
              f"Y=[{mesh.bounds[0][1]:.2f},{mesh.bounds[1][1]:.2f}]"
              f"Z=[{mesh.bounds[0][2]:.2f},{mesh.bounds[1][2]:.2f}]")
    else:
        print(f"bounds=vide (mesh sans faces)")
    if len(broken) > 0:
        print(f"[AVERT] {len(broken)} faces brisées")
    else:
        print(f"[OK] aucune face brisée")

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
        m = re.match(
            rf"{data_type}_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)_([+-]?\d+\.\d+)\.pkl",
            fname
        )
        if not m:
            continue
        cs, cn, cw, ce = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        if cs <= s and cn >= n and cw <= w and ce >= e:
            print(f"[OSM CACHE] hit : {fname}")
            fpath = os.path.join(osm_dir, fname)
            with open(fpath, "rb") as f:
                data = pickle.load(f)
            return _osm_cache_clip(data, bbox)
    print(f"[OSM CACHE] miss : {data_type}")
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
            print(f"[OSM CACHE] clip : {len(data)} → {len(clipped)} features")
            return clipped
    except Exception:
        pass
    return data

def _osm_cache_save(bbox, data_type, data, cache_dir=CACHE_DIR):
    path = _osm_cache_path(bbox, data_type, cache_dir)
    with open(path, "wb") as f:
        pickle.dump(data, f)
    print(f"[OSM CACHE] sauvegardé : {os.path.basename(path)}")

def download_roads(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "roads", cache_dir)
    if cached is not None:
        return cached

    print("Téléchargement des routes OSM...")
    try:
        graph = ox.graph_from_bbox(
            bbox=(bbox["west"], bbox["south"], bbox["east"], bbox["north"]),
            network_type="all",
            custom_filter=f'["highway"~"^({"|".join(ROAD_LEVELS)})$"]',
            retain_all=True,
            truncate_by_edge=True,
        )
        edges = ox.graph_to_gdfs(graph, nodes=False)
        print(f"{len(edges)} segments de route téléchargés")
        _osm_cache_save(bbox, "roads", edges, cache_dir)
        
        return edges
    except Exception as e:
        print(f"Aucune route : {e}")
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
        "natural": ["water", "wood", "grassland", "scrub"],
        "landuse": ["reservoir", "forest", "meadow", "grass"],
        "waterway": ["river", "canal", "stream"],
        "leisure": "park",
        "building": True,
    })
    print(f"[OSM ALL] {len(all_features)} features en {time.time()-t0:.1f}s")

    nat = all_features["natural"] if "natural" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    luse = all_features["landuse"] if "landuse" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    wway = all_features["waterway"] if "waterway" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    leis = all_features["leisure"] if "leisure" in all_features.columns else pd.Series(dtype=str, index=all_features.index)
    bld = all_features["building"] if "building" in all_features.columns else pd.Series(dtype=str, index=all_features.index)

    water_mask = nat.isin(["water"]) | luse.isin(["reservoir"])
    waterway_mask = wway.isin(["river", "canal", "stream"])
    forest_mask = nat.isin(["wood"]) | luse.isin(["forest"])
    other_veg_mask = nat.isin(["grassland", "scrub"]) | luse.isin(["meadow", "grass"]) | leis.isin(["park"])
    building_mask = bld.notna() & bld.ne("")

    poly_mask = all_features.geometry.geom_type.isin(["Polygon", "MultiPolygon"])
    water_areas = all_features[water_mask & poly_mask]
    waterways = all_features[waterway_mask]
    forest = all_features[forest_mask & poly_mask]
    other_veg = all_features[other_veg_mask & poly_mask]
    buildings = all_features[building_mask & poly_mask]

    water_result = (water_areas if not water_areas.empty else None, waterways if not waterways.empty else None)
    veg_result = (forest if not forest.empty else None, other_veg if not other_veg.empty else None)

    _osm_cache_save(bbox, "water", water_result, cache_dir)
    _osm_cache_save(bbox, "vegetation", veg_result, cache_dir)
    _osm_cache_save(bbox, "buildings", buildings if not buildings.empty else None, cache_dir)

    print(f"[OSM ALL] water={len(water_areas)} waterways={len(waterways)} forest={len(forest)} other_veg={len(other_veg)} buildings={len(buildings)}")

    return {
        "water": {"water_areas": water_result[0], "waterways": water_result[1]},
        "vegetation": {"forest": veg_result[0], "other_veg": veg_result[1]},
        "buildings": {"buildings": buildings if not buildings.empty else None},
    }

def download_water(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "water", cache_dir)
    if cached is not None:
        return cached

    print("Téléchargement des données hydrographiques OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])

    try:
        water_areas = ox.features.features_from_bbox(bounds, {
            "natural": "water", "landuse": "reservoir"
        })
        water_areas = water_areas[water_areas.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        water_areas = water_areas.to_crs("EPSG:3857")
        water_areas = water_areas[water_areas.geometry.area >= MIN_WATER_AREA_M2]
        water_areas = water_areas.to_crs("EPSG:4326")
        print(f"{len(water_areas)} surfaces d'eau (>= {MIN_WATER_AREA_M2}m²)")
    except Exception as e:
        water_areas = None
        print(f"Aucune surface d'eau : {e}")

    try:
        waterways = ox.features.features_from_bbox(bounds, {
            "waterway": ["river", "canal", "stream"]
        })
        lines = waterways[waterways.geometry.geom_type.isin(["LineString", "MultiLineString"])]
        polys = waterways[waterways.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        lines = lines.to_crs("EPSG:3857")
        lines = lines[lines.geometry.length >= MIN_WATERWAY_LENGTH_M]
        lines = lines.to_crs("EPSG:4326")
        waterways = pd.concat([lines, polys])
        print(f"{len(waterways)} cours d'eau (>= {MIN_WATERWAY_LENGTH_M}m)")
    except Exception as e:
        waterways = None
        print(f"Aucun cours d'eau : {e}")

    result = (water_areas, waterways)
    _osm_cache_save(bbox, "water", result, cache_dir)
    return result

def download_vegetation(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "vegetation", cache_dir)
    if cached is not None:
        return cached

    print("Téléchargement de la végétation OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])

    try:
        forest = ox.features.features_from_bbox(bounds, {
            "natural": "wood",
            "landuse": "forest"
        })
        forest = forest[forest.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        print(f"{len(forest)} zones de forêt")
    except Exception:
        forest = None
        print("Aucune forêt trouvée")

    try:
        other_veg = ox.features.features_from_bbox(bounds, {
            "natural": ["grassland", "scrub"],
            "landuse": ["meadow", "grass"],
            "leisure": "park"
        })
        other_veg = other_veg[other_veg.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        print(f"{len(other_veg)} autres zones de végétation")
    except Exception:
        other_veg = None
        print("Aucune autre végétation trouvée")

    result = (forest, other_veg)
    _osm_cache_save(bbox, "vegetation", result, cache_dir)
    return result

def download_buildings(bbox, cache_dir=CACHE_DIR):
    cached = _osm_cache_load(bbox, "buildings", cache_dir)
    if cached is not None:
        return cached

    print("Téléchargement des bâtiments OSM...")
    bounds = (bbox["west"], bbox["south"], bbox["east"], bbox["north"])
    try:
        buildings = ox.features.features_from_bbox(bounds, {"building": True})
        buildings = buildings[buildings.geometry.geom_type.isin(["Polygon", "MultiPolygon"])]
        buildings = buildings.to_crs("EPSG:3857")
        buildings = buildings[buildings.geometry.area >= MIN_BUILDING_AREA_M2]
        buildings = buildings.to_crs("EPSG:4326")
        print(f"{len(buildings)} bâtiments (>= {MIN_BUILDING_AREA_M2}m²)")
        _osm_cache_save(bbox, "buildings", buildings, cache_dir)
        return buildings
    except Exception as e:
        print(f"Aucun bâtiment : {e}")
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

if __name__ == "__main__":

    parser = argparse.ArgumentParser(description="Génère un STL de terrain 3D")
    parser.add_argument("lat", type=float, help="Latitude (ex: 45.8326)")
    parser.add_argument("lon", type=float, help="Longitude (ex: 6.8652)")
    args = parser.parse_args()

    bbox = compute_bbox(args.lat, args.lon)
    dem_path = download_dem_gpxz_tiled(bbox, GPXZ_API_KEY, resolution_m=RESOLUTION_M, cache_dir=CACHE_DIR)
    elevation = load_dem(dem_path)

    road_edges = download_roads(bbox)
    water_areas, waterways = download_water(bbox)
    forest, other_veg = download_vegetation(bbox)
    buildings = download_buildings(bbox)

    z_min = elevation.min()
    rows, cols = elevation.shape

    scale_mm = SIZE_MM / max(cols - 1, rows - 1)
    print(f"[SCALE] 1 pixel = {scale_mm:.4f} mm — modèle {(cols-1)*scale_mm:.1f} x {(rows-1)*scale_mm:.1f} mm")

    mesh_terrain = build_terrain_mesh(elevation, z_min)
    mesh_roads, mesh_terrain = build_roads_mesh(road_edges, bbox, elevation.shape, mesh_terrain)
    mesh_water, mesh_terrain = build_water_mesh(water_areas, waterways, bbox, elevation.shape, mesh_terrain)
    mesh_veg, mesh_terrain = build_vegetation_mesh(forest, other_veg, elevation, z_min, bbox, mesh_terrain)
    mesh_trees = build_trees_mesh(forest, bbox, elevation.shape, mesh_veg)
    mesh_buildings, mesh_terrain = build_buildings_mesh(buildings, bbox, elevation.shape, mesh_terrain)

    mesh_terrain_simplified = mesh_terrain.simplify_quadric_decimation(face_count=50000, aggression=3)
    trimesh.repair.fix_normals(mesh_terrain_simplified)
    print(f"[TERRAIN] après simplification : faces={len(mesh_terrain_simplified.faces)} watertight={mesh_terrain_simplified.is_watertight}")
    if mesh_terrain_simplified.is_watertight:
        mesh_terrain = mesh_terrain_simplified
        print(f"[TERRAIN] simplification OK")
    else:
        print(f"[TERRAIN] AVERT : simplification cassée — mesh original conservé")

    mesh_roads = add_anchor(mesh_roads, cols, rows)
    mesh_water = add_anchor(mesh_water, cols, rows)
    mesh_veg = add_anchor(mesh_veg, cols, rows)
    mesh_trees = add_anchor(mesh_trees, cols, rows)
    mesh_buildings = add_anchor(mesh_buildings, cols, rows)

    scale_matrix = np.diag([scale_mm, scale_mm, scale_mm, 1.0])
    mesh_terrain.apply_transform(scale_matrix)
    mesh_roads.apply_transform(scale_matrix)
    mesh_water.apply_transform(scale_matrix)
    mesh_veg.apply_transform(scale_matrix)
    mesh_trees.apply_transform(scale_matrix)
    mesh_buildings.apply_transform(scale_matrix)
    
    save_mesh(mesh_buildings, "STL/terrain_buildings.stl")
    save_mesh(mesh_trees, "STL/terrain_trees.stl")
    save_mesh(mesh_terrain, "STL/terrain_base.stl")
    save_mesh(mesh_roads, "STL/terrain_roads.stl")
    save_mesh(mesh_water, "STL/terrain_water.stl")
    save_mesh(mesh_veg, "STL/terrain_vegetation.stl")
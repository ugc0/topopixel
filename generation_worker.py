from logger import log
import os
import sys
import math
import traceback
import numpy as np
import trimesh
from shapely.ops import unary_union
from shapely.geometry import Polygon as ShPoly
import json
import glob
import rasterio

from PyQt6.QtCore import QThread, pyqtSignal

from monuments_library import get_monument_entry

if 'topopixel' in sys.modules:
    tp = sys.modules['topopixel']
else:
    import topopixel as tp

def build_clip_mask(shape_kind, shape_params, bbox, rows, cols, z_top=None):

    lonlat_to_pixel_xy = tp.make_latlon_to_pixel(bbox, cols, rows)

    if shape_kind == "rect":
        p = shape_params
        corners = [
            lonlat_to_pixel_xy(p["south"], p["west"]),
            lonlat_to_pixel_xy(p["south"], p["east"]),
            lonlat_to_pixel_xy(p["north"], p["east"]),
            lonlat_to_pixel_xy(p["north"], p["west"]),
        ]
        poly2d = ShPoly(corners)

    elif shape_kind == "circle":
        cx, cy = lonlat_to_pixel_xy(shape_params["center_lat"], shape_params["center_lon"])
        radius_m = shape_params["radius_m"]
        span_lon = bbox["east"] - bbox["west"]
        span_m = span_lon * 111320 * math.cos(math.radians(shape_params["center_lat"]))
        radius_px = radius_m / span_m * (cols - 1)
        n = 64
        pts = [
            (cx + radius_px * math.cos(2 * math.pi * i / n),
             cy + radius_px * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        poly2d = ShPoly(pts)

    elif shape_kind == "hexagon":
        cx, cy = lonlat_to_pixel_xy(shape_params["center_lat"], shape_params["center_lon"])
        radius_m = shape_params["radius_m"]
        span_lon = bbox["east"] - bbox["west"]
        span_m = span_lon * 111320 * math.cos(math.radians(shape_params["center_lat"]))
        radius_px = radius_m / span_m * (cols - 1)
        pts = [
            (cx + radius_px * math.cos(math.radians(60 * i - 30)),
             cy + radius_px * math.sin(math.radians(60 * i - 30)))
            for i in range(6)
        ]
        poly2d = ShPoly(pts)

    elif shape_kind == "polygon":
        pts = [
            lonlat_to_pixel_xy(lat, lon)
            for lon, lat in shape_params["points"]
        ]
        poly2d = ShPoly(pts)
        
    else:
        return None, None

    if not poly2d.is_valid:
        poly2d = poly2d.buffer(0)

    z_bot = -tp.BASE_THICKNESS - 1.0
    if z_top is None:
        z_top = 50.0
        log("[CLIP] AVERT : z_top par défaut (50.0) utilisé — aucune hauteur de terrain fournie, "
              "un relief plus haut que 50 sera coupé")

    mask_mesh = trimesh.creation.extrude_polygon(poly2d, height=z_top - z_bot)
    mask_mesh.apply_translation([0, 0, z_bot])
    trimesh.repair.fix_normals(mask_mesh)

    return mask_mesh, poly2d

def bbox_from_polygon(points):
    if not points:
        return None

    lons = []
    lats = []

    for p in points:
        lon, lat = p[0],p[1]
        lons.append(lon)
        lats.append(lat)

    return {
        "west": min(lons),
        "east": max(lons),
        "south": min(lats),
        "north": max(lats)
    }

def bbox_from_circle(center_lon, center_lat, radius_m):
    return tp.compute_bbox(center_lat, center_lon, radius_m=radius_m)


def bbox_from_hexagon(center_lon, center_lat, radius_m):
    return tp.compute_bbox(center_lat, center_lon, radius_m=radius_m)


def bbox_from_rect(west, south, east, north):
    return {"west": west, "south": south, "east": east, "north": north}


class GenerationWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, shape_kind, shape_params, ui_params, preview_workers, parent=None):
        super().__init__(parent)
        self.shape_kind = shape_kind
        self.shape_params = shape_params
        self.ui_params = ui_params
        self.preview_workers = preview_workers

    def _emit(self, msg):
        self.progress.emit(msg)

    def _apply_ui_params(self):
        for key, value in self.ui_params.items():
            setattr(tp, key, value)

    def run(self):
        try:
            self._apply_ui_params()
            enabled = self.ui_params.get("ENABLED_LAYERS", ["terrain","roads","water","vegetation","trees","buildings"])
            excluded_ids = self.ui_params.get("EXCLUDED_IDS", set())
            excluded_fill_features = self.ui_params.get("EXCLUDED_FILL_FEATURES", [])
            use_osm = self.ui_params.get("USE_OSM", True)
            use_landcover = self.ui_params.get("USE_LANDCOVER", False)
            use_satellite = self.ui_params.get("USE_SATELLITE", False)

            if self.shape_kind == "rect":
                p = self.shape_params
                bbox = bbox_from_rect(p["west"], p["south"], p["east"], p["north"])
            elif self.shape_kind == "circle":
                p = self.shape_params
                bbox = bbox_from_circle(p["center_lon"], p["center_lat"], p["radius_m"])
            elif self.shape_kind == "hexagon":
                p = self.shape_params
                bbox = bbox_from_hexagon(p["center_lon"], p["center_lat"], p["radius_m"])
            elif self.shape_kind == "polygon":
                p = self.shape_params
                bbox = bbox_from_polygon(p["points"])
            else:
                self.failed.emit("Aucune emprise dessinée sur la carte.")
                return

            self._emit(f"Emprise : {bbox}")

            resolution_m = self.ui_params.get("RESOLUTION_M", tp.RESOLUTION_M)
            cache_dir = self.ui_params.get("CACHE_DIR", tp.CACHE_DIR)
            gpxz_key = self.ui_params.get("GPXZ_API_KEY", tp.GPXZ_API_KEY)

            self._emit("Téléchargement du modèle numérique de terrain (DEM)...")
            dem_path = tp.download_dem_gpxz_tiled(bbox, gpxz_key, resolution_m=resolution_m, cache_dir=cache_dir)
            elevation = tp.load_dem(dem_path)

            if "roads" in enabled:
                self._emit("Téléchargement des routes OSM...")
                if "roads" in self.preview_workers and self.preview_workers["roads"].isRunning():
                    self.preview_workers["roads"].wait()
                road_edges = tp.download_roads(bbox, cache_dir=cache_dir)
                include_railways = self.ui_params.get("INCLUDE_RAILWAYS", False)
                if include_railways:
                    rails = tp.download_railways(bbox, cache_dir=cache_dir)
                    if rails is not None:
                        rails = rails.copy()
                        if "osmid" not in rails.columns:
                            rails["osmid"] = rails.index.astype(str)
                        road_edges = pd.concat([road_edges, rails[["geometry", "highway", "osmid"]]], ignore_index=True) if road_edges is not None else rails
            else:
                road_edges = None

            if "_all" in self.preview_workers and self.preview_workers["_all"].isRunning():
                self.preview_workers["_all"].wait()

            if "water" in enabled and use_osm:
                self._emit("Téléchargement de l'hydrographie OSM...")
                if "water" in self.preview_workers and self.preview_workers["water"].isRunning():
                    self.preview_workers["water"].wait()
                if "_all" in self.preview_workers and self.preview_workers["_all"].isRunning():
                    self.preview_workers["_all"].wait()
                water_areas, waterways, coastlines = tp.download_water(bbox, cache_dir=cache_dir)
                areas_list = water_areas.geometry.to_crs("EPSG:3857").area.tolist() if water_areas is not None else []
            else:
                water_areas, waterways = None, None

            if ("vegetation" in enabled or "trees" in enabled) and use_osm:
                self._emit("Téléchargement de la végétation OSM...")
                if "vegetation" in self.preview_workers and self.preview_workers["vegetation"].isRunning():
                    self.preview_workers["vegetation"].wait()
                forest, other_veg = tp.download_vegetation(bbox, cache_dir=cache_dir)
            else:
                forest, other_veg = None, None

            if "buildings" in enabled and use_osm:
                self._emit("Téléchargement des bâtiments OSM...")
                if "buildings" in self.preview_workers and self.preview_workers["buildings"].isRunning():
                    self.preview_workers["buildings"].wait()
                buildings = tp.download_buildings(bbox, cache_dir=cache_dir)
            else:
                buildings = None

            gpx_list = [g for g in self.ui_params.get("GPX_LIST", []) if g.get("enabled") and g.get("path")] if "gpx" in enabled else []

            if road_edges is not None:
                road_levels = self.ui_params.get("ROAD_LEVELS", tp.ROAD_LEVELS)
                road_edges = road_edges[road_edges["highway"].apply(
                    lambda h: any(l in (h if isinstance(h, list) else [h]) for l in road_levels)
                )]

            if water_areas is not None:
                wa = water_areas.to_crs("EPSG:3857")
                water_areas = wa[wa.geometry.area >= tp.MIN_WATER_AREA_M2].to_crs("EPSG:4326")
            if waterways is not None:
                ww = waterways.to_crs("EPSG:3857")
                waterways = ww[ww.geometry.length >= tp.MIN_WATERWAY_LENGTH_M].to_crs("EPSG:4326")
            if buildings is not None:
                b = buildings.to_crs("EPSG:3857")
                buildings = b[b.geometry.area >= tp.MIN_BUILDING_AREA_M2].to_crs("EPSG:4326")

            excluded_roads = {osm_id for t, osm_id in excluded_ids if t == "road"}
            excluded_water = {osm_id for t, osm_id in excluded_ids if t == "water"}
            excluded_veg = {osm_id for t, osm_id in excluded_ids if t == "vegetation"}
            excluded_buildings = {osm_id for t, osm_id in excluded_ids if t == "buildings"}

            if road_edges is not None and excluded_roads:
                road_edges = road_edges[~road_edges["osmid"].apply(lambda x: any(str(i) in excluded_roads for i in (x if isinstance(x, list) else [x])))]
            if water_areas is not None and excluded_water:
                water_areas = water_areas[~water_areas.index.get_level_values(1).map(str).isin(excluded_water)]
            if waterways is not None and excluded_water:
                waterways = waterways[~waterways.index.get_level_values(1).map(str).isin(excluded_water)]
            if forest is not None and excluded_veg:
                forest = forest[~forest.index.get_level_values(1).map(str).isin(excluded_veg)]
            if other_veg is not None and excluded_veg:
                other_veg = other_veg[~other_veg.index.get_level_values(1).map(str).isin(excluded_veg)]
            if buildings is not None and excluded_buildings:
                buildings = buildings[~buildings.index.get_level_values(1).map(str).isin(excluded_buildings)]

            z_min = elevation.min()
            rows, cols = elevation.shape
            scale_mm = tp.SIZE_MM / max(cols - 1, rows - 1)
            self._emit(f"Échelle : 1 pixel = {scale_mm:.4f} mm")

            self._emit("Construction du maillage terrain...")
            mesh_terrain = tp.build_terrain_mesh(elevation, z_min) if "terrain" in enabled else trimesh.Trimesh()

            clip_z_top = mesh_terrain.bounds[1][2] + 1.0 if len(mesh_terrain.faces) > 0 else None
            _, clip_poly2d = build_clip_mask(self.shape_kind, self.shape_params, bbox, rows, cols, z_top=clip_z_top)

            self._emit("Construction des masques...")
            cache_dir = self.ui_params.get("CACHE_DIR", "cache")

            roads_mask, roads_polygon = tp.build_roads_mask(road_edges, bbox, elevation.shape, mesh_terrain) if "roads" in enabled else (None, None)

            water_mask_lacs, water_mask_ocean, water_polygon = tp.build_water_mask(water_areas, waterways, bbox, elevation.shape, mesh_terrain, z_min, self.ui_params.get("ENABLE_BATHYMETRY", False), elevation, roads_polygon) if ("water" in enabled and use_osm) else (None, None, None)

            exclude_buildings = [p for p in [roads_polygon, water_polygon] if p is not None and not p.is_empty]
            exclude_buildings = unary_union(exclude_buildings) if exclude_buildings else None
            buildings_mask, building_meshes, buildings_polygon = tp.build_buildings_mask(buildings, bbox, elevation.shape, mesh_terrain, exclude_buildings) if ("buildings" in enabled and use_osm) else (None, [], None)

            exclude_veg = [p for p in [roads_polygon, water_polygon, buildings_polygon] if p is not None and not p.is_empty]
            exclude_veg = unary_union(exclude_veg) if exclude_veg else None
            veg_mask, veg_polygon = tp.build_veg_mask(forest, other_veg, bbox, elevation.shape, mesh_terrain, exclude_veg) if ("vegetation" in enabled and use_osm) else (None, None)

            landcover_fill = {}
            tree_fill_polygon = None
            if use_landcover:
                self._emit("Téléchargement landcover...")
                tile_paths = tp.download_lcfm_tiles(bbox, self.ui_params.get("CDSE_ACCESS_KEY", ""), self.ui_params.get("CDSE_SECRET_KEY", ""), cache_dir)
                if tile_paths:
                    dst_transform = rasterio.transform.from_bounds(bbox["west"], bbox["south"], bbox["east"], bbox["north"], cols, rows)
                    landcover_grid = tp.reproject_landcover_to_grid(tile_paths, dst_transform, "EPSG:4326", elevation.shape, self.ui_params.get("RESOLUTION_M", 5))
                    if landcover_grid is not None:
                        layer_masks, tree_mask = tp.build_landcover_layer_masks(landcover_grid)
                        landcover_fill, tree_fill_polygon = tp.landcover_masks_to_polygons(layer_masks, tree_mask, bbox, elevation.shape, excluded_ids)
                        if tree_fill_polygon is not None:
                            landcover_fill["trees"] = tree_fill_polygon

            satellite_fill = {}
            if use_satellite:
                self._emit("Téléchargement imagerie satellite...")
                tile_paths = tp.download_worldcover_s2_tiles(bbox, cache_dir)
                if tile_paths:
                    dst_transform = rasterio.transform.from_bounds(bbox["west"], bbox["south"], bbox["east"], bbox["north"], cols, rows)
                    rgb_grid = tp.reproject_satellite_to_grid(tile_paths, dst_transform, "EPSG:4326", elevation.shape)
                    if rgb_grid is not None:
                        calibration = self.ui_params.get("SATELLITE_CALIBRATION", tp.DEFAULT_SATELLITE_CALIBRATION)
                        sat_masks = tp.classify_satellite_pixels(rgb_grid, calibration)
                        satellite_fill = tp.satellite_masks_to_polygons(sat_masks, bbox, elevation.shape, excluded_fill_features, resolution_m)

            osm_coverage = tp.compute_osm_coverage(roads_polygon, water_polygon, buildings_polygon, veg_polygon)

            def _final_fill_for(layer):
                lc_poly = landcover_fill.get(layer)
                if lc_poly is not None and osm_coverage is not None:
                    lc_poly = lc_poly.difference(osm_coverage)
                sat_poly = satellite_fill.get(layer)
                if sat_poly is not None:
                    exclude_from_sat = [p for p in [osm_coverage, lc_poly] if p is not None and not p.is_empty]
                    if exclude_from_sat:
                        sat_poly = sat_poly.difference(unary_union(exclude_from_sat))
                parts = [p for p in [lc_poly, sat_poly] if p is not None and not p.is_empty]
                return unary_union(parts) if parts else None

            water_fill_polygon = _final_fill_for("water") if "water" in enabled else None
            veg_fill_polygon = _final_fill_for("vegetation") if "vegetation" in enabled else None
            buildings_fill_polygon = _final_fill_for("buildings") if "buildings" in enabled else None
            tree_fill_polygon = _final_fill_for("trees") if "trees" in enabled else None

            if water_fill_polygon is not None:
                water_mask_lacs, water_mask_ocean, water_polygon = tp.build_water_mask(
                    water_areas if use_osm else None,
                    waterways if use_osm else None,
                    bbox, elevation.shape, mesh_terrain, z_min,
                    self.ui_params.get("ENABLE_BATHYMETRY", False), elevation, roads_polygon, water_fill_polygon
                )

            if veg_fill_polygon is not None:
                veg_mask, veg_polygon = tp.build_veg_mask(
                    forest if use_osm else None,
                    other_veg if use_osm else None,
                    bbox, elevation.shape, mesh_terrain, exclude_veg, veg_fill_polygon
                )

            buildings_fill_mask = tp.build_fill_mask(buildings_fill_polygon, tp.BUILDINGS_Z_BOT_RATIO_PCT, mesh_terrain) if buildings_fill_polygon is not None else None
        
            gpx_masks = []
            if "gpx" in enabled:
                for gpx in gpx_list:
                    points = tp.parse_gpx_file(gpx["path"], bbox)
                    if points:
                        tp.GPX_WIDTH_PX = self.ui_params.get("GPX_WIDTH_PX", 2.0)
                        tp.GPX_HEIGHT = self.ui_params.get("GPX_HEIGHT", 4.0)
                        mask = tp.build_gpx_mask(points, bbox, elevation.shape, mesh_terrain, clip_poly2d)
                        if mask:
                            gpx_masks.append((mask, gpx["color"]))

            self._emit("Simplification...")
            mesh_terrain = tp.simplify_mesh(mesh_terrain)
            roads_mask = tp.simplify_mesh(roads_mask) if roads_mask is not None else None
            water_mask_lacs = tp.simplify_mesh(water_mask_lacs) if water_mask_lacs is not None else None
            water_mask_ocean = tp.simplify_mesh(water_mask_ocean) if water_mask_ocean is not None else None
            veg_mask = tp.simplify_mesh(veg_mask) if veg_mask is not None else None
            buildings_mask = tp.simplify_mesh(buildings_mask) if buildings_mask is not None else None
            gpx_masks = [(tp.simplify_mesh(m), c) for m, c in gpx_masks]

            self._emit("Application des booléens...")
            mesh_terrain_pristine = mesh_terrain
            mesh_roads, mesh_terrain = tp.apply_roads_boolean(roads_mask, mesh_terrain, mesh_terrain_pristine) if "roads" in enabled else (trimesh.Trimesh(), mesh_terrain)
            mesh_water, mesh_terrain = tp.apply_water_boolean(water_mask_lacs, water_mask_ocean, mesh_terrain, mesh_terrain_pristine) if "water" in enabled else (trimesh.Trimesh(), mesh_terrain)
            mesh_veg, mesh_terrain = tp.apply_veg_boolean(veg_mask, mesh_terrain, mesh_terrain_pristine) if "vegetation" in enabled else (trimesh.Trimesh(), mesh_terrain)
            mesh_trees = tp.build_trees_mesh(forest, bbox, elevation.shape, mesh_veg, tree_fill_polygon) if "trees" in enabled else trimesh.Trimesh()
            
            monument_meshes = {}
            if "buildings" in enabled and building_meshes:
                z_top_monument = mesh_terrain_pristine.bounds[1][2] + 1.0
                for building, masque, osm_id, footprint in building_meshes:
                    entry = get_monument_entry(osm_id)
                    if entry is None or not entry.get("active", True):
                        continue
                    locs = mesh_terrain_pristine.ray.intersects_location(
                        np.array([[building.centroid[0], building.centroid[1], z_top_monument]]),
                        np.array([[0, 0, -1]])
                    )[0]
                    z_base = locs[:, 2].max() if len(locs) > 0 else 0.0
                    try:
                        monument_mesh = tp.build_monument_mesh(entry["stl_path"], entry["rotation_deg"], footprint, z_base, entry.get("scale_factor", 1.0))
                    except Exception as e:
                        log(f"[MONUMENT] échec build_monument_mesh : {e}")
                        monument_mesh = None
                    log(f"[MONUMENT] monument_mesh={monument_mesh}")
                    if monument_mesh is not None:
                        monument_meshes[osm_id] = monument_mesh
            
            mesh_buildings_fill, mesh_terrain = tp.apply_buildings_fill_boolean(buildings_fill_mask, mesh_terrain, mesh_terrain_pristine)
            mesh_buildings = trimesh.util.concatenate([mesh_buildings, mesh_buildings_fill]) if len(mesh_buildings_fill.faces) > 0 else mesh_buildings
            
            if len(mesh_buildings.faces) > 0 and len(mesh_roads.faces) > 0:
                try:
                    mesh_buildings = trimesh.boolean.difference([mesh_buildings, mesh_roads], engine='manifold')
                    trimesh.repair.fix_normals(mesh_buildings)
                except Exception as e:
                    self._emit(f"  découpe bâtiments/routes échouée : {e}")
            
            mesh_gpx_list = []
            for gpx_mask, gpx_color in gpx_masks:
                other = {"roads": mesh_roads, "water": mesh_water, "veg": mesh_veg,
                         "buildings": mesh_buildings, "trees": mesh_trees}
                for i, (prev_mg, _) in enumerate(mesh_gpx_list):
                    other[f"gpx_{i}"] = prev_mg
                mg, mesh_terrain, other, failed_cuts = tp.apply_gpx_boolean(gpx_mask, mesh_terrain, mesh_terrain_pristine, other)
                mesh_roads = other["roads"]
                mesh_water = other["water"]
                mesh_veg = other["veg"]
                mesh_buildings = other["buildings"]
                mesh_trees = other["trees"]
                mesh_gpx_list = [
                    (other[f"gpx_{i}"], color)
                    for i, (_, color) in enumerate(mesh_gpx_list)
                ]
                mesh_gpx_list.append((mg, gpx_color))
                if failed_cuts:
                    self._emit(f"  ATTENTION : découpe GPX échouée pour {', '.join(failed_cuts)} "
                                f"— le GPX pourra être masqué par ces couches à cet endroit")

            faces = mesh_terrain.faces
            unique_faces, counts = np.unique(np.sort(faces, axis=1), axis=0, return_counts=True)
            dup_mask = counts > 1

            if self.shape_kind != "rect":
                self._emit("Découpe à la forme...")
                monument_max_z = max(
                    (m.bounds[1][2] for m in monument_meshes.values()),
                    default=mesh_terrain.bounds[1][2]
                )
                terrain_z_top = max(mesh_terrain.bounds[1][2], monument_max_z) + 1.0
                clip_mask, _ = build_clip_mask(self.shape_kind, self.shape_params, bbox, rows, cols, z_top=terrain_z_top)
                if clip_mask is not None and clip_mask.is_watertight:
                    meshes = {
                        "terrain": mesh_terrain,
                        "roads": mesh_roads,
                        "water": mesh_water,
                        "veg": mesh_veg,
                        "trees": mesh_trees,
                        "buildings": mesh_buildings,
                    }
                    clipped = {}
                    for name, mesh in meshes.items():
                        if len(mesh.faces) == 0:
                            clipped[name] = mesh
                            continue
                        try:
                            result = trimesh.boolean.intersection([mesh, clip_mask], engine='manifold')
                            trimesh.repair.fix_normals(result)
                            components = result.split(only_watertight=False)
                            self._emit(f"  {name} : {len(mesh.faces)} → {len(result.faces)} faces watertight={result.is_watertight}")
                            clipped[name] = result
                        except Exception as e:
                            self._emit(f"  {name} : clip échoué ({e}), mesh original conservé")
                            clipped[name] = mesh
                    mesh_terrain = clipped["terrain"]
                    mesh_roads = clipped["roads"]
                    mesh_water = clipped["water"]
                    mesh_veg = clipped["veg"]
                    mesh_trees = clipped["trees"]
                    mesh_buildings = clipped["buildings"]

                else:
                    self._emit("Masque invalide — découpe ignorée")

            if len(mesh_roads.faces) > 0:
                mesh_roads = tp.add_anchor(mesh_roads, cols, rows)
            if len(mesh_water.faces) > 0:
                mesh_water = tp.add_anchor(mesh_water, cols, rows)
            if len(mesh_veg.faces) > 0:
                mesh_veg = tp.add_anchor(mesh_veg, cols, rows)
            if len(mesh_trees.faces) > 0:
                mesh_trees = tp.add_anchor(mesh_trees, cols, rows)
            if len(mesh_buildings.faces) > 0:
                mesh_buildings = tp.add_anchor(mesh_buildings, cols, rows)

            scale_matrix = np.diag([scale_mm, scale_mm, scale_mm, 1.0])
            mesh_terrain.apply_transform(scale_matrix)
            mesh_roads.apply_transform(scale_matrix)
            mesh_water.apply_transform(scale_matrix)
            mesh_veg.apply_transform(scale_matrix)
            mesh_trees.apply_transform(scale_matrix)
            mesh_buildings.apply_transform(scale_matrix)
            
            mesh_gpx_list = [
                (tp.add_anchor(mg, cols, rows) if len(mg.faces) > 0 else mg, c)
                for mg, c in mesh_gpx_list
            ]
            for mg, _ in mesh_gpx_list:
                if len(mg.faces) > 0:
                    components = mg.split(only_watertight=False)
                mg.apply_transform(scale_matrix)

            self._emit("Export des fichiers STL...")
            stl_dir = self.ui_params.get("STL_DIR", "STL")
            for old_gpx in glob.glob(os.path.join(stl_dir, "terrain_gpx_*.stl")):
                os.remove(old_gpx)
            for old_piece in glob.glob(os.path.join(stl_dir, "puzzle_piece*.stl")):
                os.remove(old_piece)

            project_name = self.ui_params.get("PROJECT_NAME", "topopixel")
            output_3mf = os.path.join(stl_dir, f"{project_name}.3mf")
            n_puzzle = self.ui_params.get("PUZZLE_N_PIECES", 0)

            pieces = None
            if n_puzzle and n_puzzle >= 2:
                self._emit(f"Génération puzzle ({n_puzzle} pièces)...")
                extra = [
                    (name, m) for name, m in [
                        ("roads", mesh_roads),
                        ("water", mesh_water),
                        ("vegetation", mesh_veg),
                        ("trees", mesh_trees),
                        ("buildings", mesh_buildings),
                    ] + [(f"gpx_{i}", m) for i, (m, _) in enumerate(mesh_gpx_list)]
                    if m is not None and len(m.faces) > 0
                ]
                merge_small_pieces = self.ui_params.get("PUZZLE_MERGE_SMALL_PIECES", True)
                tab_radius_pct = self.ui_params.get("PUZZLE_TAB_RADIUS_PCT", 14)
                pieces = tp.build_puzzle_pieces(mesh_terrain, n_puzzle, extra_meshes=extra,merge_small_pieces=merge_small_pieces,tab_radius_ratio=tab_radius_pct / 100.0)

            if pieces:
                piece_files = []
                for i, layers in enumerate(pieces):
                    for name, mesh in layers.items():
                        path = os.path.join(stl_dir, f"puzzle_piece{i}_{name}.stl")
                        tp.save_mesh(mesh, path, name)
                        piece_files.append(path)
                self._emit("Export 3MF...")
                puzzle_gap_mm = self.ui_params.get("PUZZLE_GAP_MM", 2.0)
                tp.export_puzzle_3mf(pieces, output_3mf, puzzle_gap_mm=puzzle_gap_mm)
                self._emit(f"Puzzle exporté : {output_3mf}")
                result_files = piece_files
            else:
                files = [
                    "terrain_base.stl", "terrain_roads.stl", "terrain_water.stl",
                    "terrain_vegetation.stl", "terrain_trees.stl", "terrain_buildings.stl",
                ]
                meshes = [mesh_terrain, mesh_roads, mesh_water, mesh_veg, mesh_trees, mesh_buildings]
                for mesh, fname in zip(meshes, files):
                    tp.save_mesh(mesh, os.path.join(stl_dir, fname), fname[8:-4])
                gpx_files = []
                for i, (mg, _) in enumerate(mesh_gpx_list):
                    path = os.path.join(stl_dir, f"terrain_gpx_{i}.stl")
                    tp.save_mesh(mg, path, "GPX")
                    gpx_files.append(path)

                self._emit("Export 3MF...")
                tp.save_3mf(
                    [mesh_terrain, mesh_roads, mesh_water, mesh_veg, mesh_trees, mesh_buildings],
                    [os.path.join(stl_dir, f) for f in files],
                    output_3mf,
                    gpx_list=gpx_list
                )
                result_files = [os.path.join(stl_dir, f) for f in files] + gpx_files

            layer_stats = tp.compute_layer_stats(road_edges, water_areas, waterways, forest, other_veg, buildings, gpx_list, bbox)
            base_thickness = self.ui_params.get("BASE_THICKNESS", tp.BASE_THICKNESS)

            pla_density_g_mm3 = 1.24 / 1000
            mesh_pla = {
                "terrain": mesh_terrain,
                "roads": mesh_roads,
                "water": mesh_water,
                "vegetation": mesh_veg,
                "buildings": mesh_buildings,
            }
            pla_grams = {}
            for name, mesh in mesh_pla.items():
                if len(mesh.faces) > 0 and mesh.is_watertight:
                    pla_grams[name] = abs(mesh.volume) * pla_density_g_mm3
                else:
                    pla_grams[name] = None
            pla_grams["gpx"] = [
                abs(mg.volume) * pla_density_g_mm3 if len(mg.faces) > 0 and mg.is_watertight else None
                for mg, _ in mesh_gpx_list
            ]

            metadata = {
                "resolution_m": self.ui_params.get("RESOLUTION_M", tp.RESOLUTION_M),
                "cols": cols,
                "rows": rows,
                "scale_mm": scale_mm,
                "base_thickness_mm": base_thickness * scale_mm,
                "altitude_min_m": float(elevation.min()),
                "altitude_max_m": float(elevation.max()),
                "layer_stats": layer_stats,
                "pla_grams": pla_grams,
            }
            with open(os.path.join(stl_dir, "metadata.json"), "w") as f:
                json.dump(metadata, f)

            self._emit("Terminé.")
            
            self.finished_ok.emit({"files": result_files})

        except Exception:
            self.failed.emit(traceback.format_exc())
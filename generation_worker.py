import os
import sys
import math
import traceback
import numpy as np
import trimesh
from shapely.geometry import Polygon as ShPoly

from PyQt6.QtCore import QThread, pyqtSignal

if 'topopixel' in sys.modules:
    tp = sys.modules['topopixel']
else:
    import topopixel as tp

def build_clip_mask(shape_kind, shape_params, bbox, rows, cols):

    def lonlat_to_pixel_xy(lat, lon):
        x = (lon - bbox["west"]) / (bbox["east"] - bbox["west"]) * (cols - 1)
        y = (lat - bbox["south"]) / (bbox["north"] - bbox["south"]) * (rows - 1)
        return x, y

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
        return None

    if not poly2d.is_valid:
        poly2d = poly2d.buffer(0)

    z_bot = -tp.BASE_THICKNESS - 1.0
    z_top = 50.0

    mask_mesh = trimesh.creation.extrude_polygon(poly2d, height=z_top - z_bot)
    mask_mesh.apply_translation([0, 0, z_bot])
    trimesh.repair.fix_normals(mask_mesh)

    print(f"[CLIP] masque {shape_kind} : faces={len(mask_mesh.faces)} watertight={mask_mesh.is_watertight}")
    print(f"[CLIP] bounds X=[{mask_mesh.bounds[0][0]:.2f},{mask_mesh.bounds[1][0]:.2f}]"
          f" Y=[{mask_mesh.bounds[0][1]:.2f},{mask_mesh.bounds[1][1]:.2f}]"
          f" Z=[{mask_mesh.bounds[0][2]:.2f},{mask_mesh.bounds[1][2]:.2f}]")

    return mask_mesh

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
            dem_path = tp.download_dem_gpxz_tiled(
                bbox, gpxz_key, resolution_m=resolution_m, cache_dir=cache_dir
            )
            elevation = tp.load_dem(dem_path)

            if "roads" in enabled:
                self._emit("Téléchargement des routes OSM...")
                if "roads" in self.preview_workers and self.preview_workers["roads"].isRunning():
                    self.preview_workers["roads"].wait()
                road_edges = tp.download_roads(bbox, cache_dir=cache_dir)
            else:
                road_edges = None

            if "_all" in self.preview_workers and self.preview_workers["_all"].isRunning():
                self.preview_workers["_all"].wait()

            if "water" in enabled:
                self._emit("Téléchargement de l'hydrographie OSM...")
                if "water" in self.preview_workers and self.preview_workers["water"].isRunning():
                    self.preview_workers["water"].wait()
                water_areas, waterways = tp.download_water(bbox, cache_dir=cache_dir)
            else:
                water_areas, waterways = None, None

            if "vegetation" in enabled or "trees" in enabled:
                self._emit("Téléchargement de la végétation OSM...")
                if "vegetation" in self.preview_workers and self.preview_workers["vegetation"].isRunning():
                    self.preview_workers["vegetation"].wait()
                forest, other_veg = tp.download_vegetation(bbox, cache_dir=cache_dir)
            else:
                forest, other_veg = None, None

            if "buildings" in enabled:
                self._emit("Téléchargement des bâtiments OSM...")
                if "buildings" in self.preview_workers and self.preview_workers["buildings"].isRunning():
                    self.preview_workers["buildings"].wait()
                buildings = tp.download_buildings(bbox, cache_dir=cache_dir)
            else:
                buildings = None
            
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

            self._emit("Découpe des routes...")
            mesh_roads, mesh_terrain = tp.build_roads_mesh(road_edges, bbox, elevation.shape, mesh_terrain) if "roads" in enabled else (trimesh.Trimesh(), mesh_terrain)

            self._emit("Découpe de l'hydrographie...")
            mesh_water, mesh_terrain = tp.build_water_mesh(water_areas, waterways, bbox, elevation.shape, mesh_terrain) if "water" in enabled else (trimesh.Trimesh(), mesh_terrain)

            self._emit("Découpe de la végétation...")
            mesh_veg, mesh_terrain = tp.build_vegetation_mesh(forest, other_veg, elevation, z_min, bbox, mesh_terrain) if "vegetation" in enabled else (trimesh.Trimesh(), mesh_terrain)

            self._emit("Génération des arbres...")
            mesh_trees = tp.build_trees_mesh(forest, bbox, elevation.shape, mesh_veg) if "trees" in enabled else trimesh.Trimesh()

            self._emit("Génération des bâtiments...")
            mesh_buildings, mesh_terrain = tp.build_buildings_mesh(buildings, bbox, elevation.shape, mesh_terrain) if "buildings" in enabled else (trimesh.Trimesh(), mesh_terrain)
            
            self._emit("Simplification du terrain...")
            target = min(50000, int(len(mesh_terrain.faces) * 0.5))
            if target < len(mesh_terrain.faces):
                mesh_terrain_simplified = mesh_terrain.simplify_quadric_decimation(face_count=target, aggression=3)
                trimesh.repair.fix_normals(mesh_terrain_simplified)
                if mesh_terrain_simplified.is_watertight:
                    mesh_terrain = mesh_terrain_simplified
                    self._emit(f"Simplification OK : {target} faces")
                else:
                    self._emit("Simplification cassée — mesh original conservé")
            else:
                self._emit(f"Simplification ignorée — mesh déjà à {len(mesh_terrain.faces)} faces")
                
            if self.shape_kind != "rect":
                self._emit("Découpe à la forme...")
                clip_mask = build_clip_mask(self.shape_kind, self.shape_params, bbox, rows, cols)
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
                            self._emit(f"  {name} : {len(mesh.faces)} → {len(result.faces)} faces watertight={result.is_watertight}")
                            clipped[name] = result
                        except Exception as e:
                            self._emit(f"  {name} : clip échoué ({e}), mesh original conservé")
                            clipped[name] = mesh
                    mesh_terrain  = clipped["terrain"]
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

            self._emit("Export des fichiers STL...")
            stl_dir = self.ui_params.get("STL_DIR", "STL")
            files = [
                "terrain_base.stl", "terrain_roads.stl", "terrain_water.stl",
                "terrain_vegetation.stl", "terrain_trees.stl", "terrain_buildings.stl",
            ]
            meshes = [mesh_terrain, mesh_roads, mesh_water, mesh_veg, mesh_trees, mesh_buildings]
            for mesh, fname in zip(meshes, files):
                tp.save_mesh(mesh, os.path.join(stl_dir, fname))

            self._emit("Terminé.")
            self.finished_ok.emit({"files": [os.path.join(stl_dir, f) for f in files]})

        except Exception:
            self.failed.emit(traceback.format_exc())

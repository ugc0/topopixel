from logger import log
from PyQt6.QtCore import QThread, pyqtSignal
import topopixel as tp
import pandas as pd
import rasterio
import math

class PreviewWorkerAll(QThread):
    ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    def __init__(self, bbox, cache_dir=None, parent=None):
        super().__init__(parent)
        self.bbox = bbox
        self.cache_dir = cache_dir or tp.CACHE_DIR

    def run(self):
        try:
            result = tp.download_osm_all(self.bbox, cache_dir=self.cache_dir)
            for layer, data in result.items():
                self.ready.emit(layer, data)
        except Exception as e:
            for layer in ("water", "vegetation", "buildings"):
                self.failed.emit(layer, str(e))

class PreviewWorker(QThread):
    ready = pyqtSignal(str, object)
    failed = pyqtSignal(str, str)

    PREVIEW_RESOLUTION_M = 10

    def __init__(self, layer, bbox, cache_dir=None, include_railways=False,
                 cdse_access_key="", cdse_secret_key="", satellite_calibration=None, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.bbox = bbox
        self.cache_dir = cache_dir or tp.CACHE_DIR
        self.include_railways = include_railways
        self.cdse_access_key = cdse_access_key
        self.cdse_secret_key = cdse_secret_key
        self.satellite_calibration = satellite_calibration or tp.DEFAULT_SATELLITE_CALIBRATION

    def _preview_grid(self):
        cols = max(1, round((self.bbox["east"] - self.bbox["west"]) * 111320 * math.cos(math.radians((self.bbox["north"] + self.bbox["south"]) / 2)) / self.PREVIEW_RESOLUTION_M))
        rows = max(1, round((self.bbox["north"] - self.bbox["south"]) * 111320 / self.PREVIEW_RESOLUTION_M))
        transform = rasterio.transform.from_bounds(self.bbox["west"], self.bbox["south"], self.bbox["east"], self.bbox["north"], cols, rows)
        return (rows, cols), transform

    def run(self):
        try:
            if self.layer == "roads":
                edges = tp.download_roads(self.bbox, cache_dir=self.cache_dir)
                if self.include_railways:
                    rails = tp.download_railways(self.bbox, cache_dir=self.cache_dir)
                    if rails is not None:
                        rails = rails.copy()
                        if "osmid" not in rails.columns:
                            rails["osmid"] = rails.index.astype(str)
                        edges = pd.concat([edges, rails[["geometry", "highway", "osmid"]]], ignore_index=True) if edges is not None else rails
                if edges is None:
                    self.ready.emit("roads", {})
                    return
                edges_by_level = {}
                for level in tp.ROAD_LEVELS:
                    subset = edges[edges["highway"].apply(
                        lambda h: level in (h if isinstance(h, list) else [h])
                    )]
                    if len(subset) > 0:
                        edges_by_level[level] = subset
                self.ready.emit("roads", edges_by_level)

            elif self.layer == "water":
                water_areas, waterways, coastlines = tp.download_water(self.bbox, cache_dir=self.cache_dir)
                self.ready.emit("water", {"water_areas": water_areas, "waterways": waterways, "coastlines": coastlines})

            elif self.layer == "vegetation":
                forest, other_veg = tp.download_vegetation(self.bbox, cache_dir=self.cache_dir)
                self.ready.emit("vegetation", {"forest": forest, "other_veg": other_veg})

            elif self.layer == "buildings":
                buildings = tp.download_buildings(self.bbox, cache_dir=self.cache_dir)
                self.ready.emit("buildings", {"buildings": buildings})

            elif self.layer == "landcover":
                tile_paths = tp.download_lcfm_tiles(self.bbox, self.cdse_access_key, self.cdse_secret_key, self.cache_dir)
                if not tile_paths:
                    self.ready.emit("landcover", {"features": []})
                    return
                shape, transform = self._preview_grid()
                grid = tp.reproject_landcover_to_grid(tile_paths, transform, "EPSG:4326", shape, self.PREVIEW_RESOLUTION_M)
                if grid is None:
                    self.ready.emit("landcover", {"features": []})
                    return
                layer_masks, tree_mask = tp.build_landcover_layer_masks(grid)
                features = []
                for target_layer, mask in layer_masks.items():
                    polys = tp._mask_to_polygon(mask)
                    features.extend(tp.polygons_to_geo_features(polys, self.bbox, shape, target_layer, "landcover"))
                tree_polys = tp._mask_to_polygon(tree_mask)
                features.extend(tp.polygons_to_geo_features(tree_polys, self.bbox, shape, "trees", "landcover"))
                self.ready.emit("landcover", {"features": features})

            elif self.layer == "satellite":
                tile_paths = tp.download_worldcover_s2_tiles(self.bbox, self.cache_dir)
                if not tile_paths:
                    self.ready.emit("satellite", {"features": []})
                    return
                shape, transform = self._preview_grid()
                rgb_grid = tp.reproject_satellite_to_grid(tile_paths, transform, "EPSG:4326", shape)
                if rgb_grid is None:
                    self.ready.emit("satellite", {"features": []})
                    return
                layer_masks = tp.classify_satellite_pixels(rgb_grid, self.satellite_calibration)
                features = []
                for target_layer, mask in layer_masks.items():
                    polys = tp._mask_to_polygon(mask)
                    features.extend(tp.polygons_to_geo_features(polys, self.bbox, shape, target_layer, "satellite"))
                self.ready.emit("satellite", {"features": features})

        except Exception as e:
            self.failed.emit(self.layer, str(e))
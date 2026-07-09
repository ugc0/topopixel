from logger import log
from PyQt6.QtCore import QThread, pyqtSignal
import topopixel as tp
import pandas as pd

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

    def __init__(self, layer, bbox, cache_dir=None, include_railways=False, parent=None):
        super().__init__(parent)
        self.layer = layer
        self.bbox = bbox
        self.cache_dir = cache_dir or tp.CACHE_DIR
        self.include_railways = include_railways

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

        except Exception as e:
            self.failed.emit(self.layer, str(e))
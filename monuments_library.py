import os
import json
import trimesh
from shapely.geometry import MultiPoint

MONUMENTS_LIBRARY_PATH = "monuments_library.json"


def load_monuments_library():
    if not os.path.exists(MONUMENTS_LIBRARY_PATH):
        return {}
    with open(MONUMENTS_LIBRARY_PATH, "r") as f:
        return json.load(f)


def save_monument_entry(osm_id, stl_path, rotation_deg, active=True, scale_factor=1.0):
    library = load_monuments_library()
    library[str(osm_id)] = {"stl_path": stl_path, "rotation_deg": rotation_deg, "active": active, "scale_factor": scale_factor}
    with open(MONUMENTS_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)

def set_monument_active(osm_id, active):
    library = load_monuments_library()
    entry = library.get(str(osm_id))
    if entry is None:
        return
    entry["active"] = active
    library[str(osm_id)] = entry
    with open(MONUMENTS_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)

def remove_monument_entry(osm_id):
    library = load_monuments_library()
    library.pop(str(osm_id), None)
    with open(MONUMENTS_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)

def get_monument_entry(osm_id):
    library = load_monuments_library()
    entry = library.get(str(osm_id))
    if entry is None:
        return None
    if not os.path.exists(entry["stl_path"]):
        return None
    return entry
    
def set_monument_scale(osm_id, scale_factor):
    library = load_monuments_library()
    entry = library.get(str(osm_id))
    if entry is None:
        return
    entry["scale_factor"] = scale_factor
    library[str(osm_id)] = entry
    with open(MONUMENTS_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)
        
def set_monument_rotation(osm_id, rotation_deg):
    library = load_monuments_library()
    entry = library.get(str(osm_id))
    if entry is None:
        return
    entry["rotation_deg"] = rotation_deg
    library[str(osm_id)] = entry
    with open(MONUMENTS_LIBRARY_PATH, "w") as f:
        json.dump(library, f, indent=2)

def get_monument_silhouette_2d(stl_path):
    monument = trimesh.load(stl_path)
    if isinstance(monument, trimesh.Scene):
        monument = trimesh.util.concatenate(list(monument.geometry.values()))
    projected = MultiPoint(monument.vertices[:, :2])
    hull = projected.convex_hull
    return list(hull.exterior.coords)
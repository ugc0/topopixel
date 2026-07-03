import os
import math

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtCore import QUrl

TILE_SIZE = 256
OSM_TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_CACHE_DIR = "tile_cache"


def lonlat_to_tile(lon, lat, zoom):
    lat_rad = math.radians(lat)
    n = 2.0 ** zoom
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
    return x, y


def tile_to_lonlat(x, y, zoom):
    n = 2.0 ** zoom
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    lat = math.degrees(lat_rad)
    return lon, lat


def tile_cache_path(z, x, y):
    os.makedirs(TILE_CACHE_DIR, exist_ok=True)
    zdir = os.path.join(TILE_CACHE_DIR, str(z), str(x))
    os.makedirs(zdir, exist_ok=True)
    return os.path.join(zdir, f"{y}.png")


class TileManager(QObject):
    tile_ready = pyqtSignal(int, int, int, QPixmap)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._nam = QNetworkAccessManager(self)
        self._pending = set()
        self._mem_cache = {}
        self._mem_cache_order = []
        self._mem_cache_limit = 600

    def request_tile(self, z, x, y):
        n = 2 ** z
        if not (0 <= y < n):
            return
        x = x % n

        key = (z, x, y)
        if key in self._mem_cache:
            self.tile_ready.emit(z, x, y, self._mem_cache[key])
            return
        if key in self._pending:
            return

        cache_path = tile_cache_path(z, x, y)
        if os.path.exists(cache_path):
            pix = QPixmap(cache_path)
            if not pix.isNull():
                self._store_mem(key, pix)
                self.tile_ready.emit(z, x, y, pix)
                return

        self._pending.add(key)
        url = QUrl(OSM_TILE_URL.format(z=z, x=x, y=y))
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "TopopixelIHM/1.0")
        reply = self._nam.get(req)
        reply.finished.connect(lambda r=reply, k=key: self._on_finished(r, k))

    def _on_finished(self, reply, key):
        z, x, y = key
        self._pending.discard(key)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return
        data = reply.readAll()
        pix = QPixmap()
        if pix.loadFromData(data):
            cache_path = tile_cache_path(z, x, y)
            with open(cache_path, "wb") as f:
                f.write(bytes(data))
            self._store_mem(key, pix)
            self.tile_ready.emit(z, x, y, pix)
        reply.deleteLater()

    def _store_mem(self, key, pix):
        if key not in self._mem_cache:
            self._mem_cache_order.append(key)
        self._mem_cache[key] = pix
        while len(self._mem_cache_order) > self._mem_cache_limit:
            old_key = self._mem_cache_order.pop(0)
            self._mem_cache.pop(old_key, None)

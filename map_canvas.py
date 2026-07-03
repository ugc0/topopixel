import math
import json

from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QWidget, QVBoxLayout, QLabel, QPushButton, QGraphicsLineItem, QGraphicsPolygonItem, QApplication, QLineEdit, QGraphicsProxyWidget, QSpinBox
from PyQt6.QtCore import Qt, QRectF, QPointF, QPoint, pyqtSignal, QTimer, QUrl, QUrlQuery
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtGui import (
    QPainter, QPixmap, QPen, QBrush, QColor, QPolygonF, QWheelEvent,
    QMouseEvent
)

from shapely.geometry import Polygon

from tile_manager import TileManager, TILE_SIZE, lonlat_to_tile, tile_to_lonlat

MIN_ZOOM = 2
MAX_ZOOM = 19

PREVIEW_COLORS = {
    "roads":      "#000000",
    "water":      "#0094FF",
    "vegetation": "#00D921",
    "buildings":  "#898989",
}

class MapTooltip(QWidget):

    exclusion_changed = pyqtSignal(object, bool)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._excluded_ids = set()
        self._current_data = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self._title = QLabel()
        self._title.setStyleSheet("font-weight: bold; font-size: 13px;")
        self._detail = QLabel()
        self._detail.setStyleSheet("color: #555; font-size: 11px;")
        self._exclude_btn = QPushButton("Exclure de la génération")
        self._exclude_btn.setCheckable(True)
        self._exclude_btn.setStyleSheet("""
            QPushButton { background: #f1f3f5; border: 1px solid #ccc; border-radius: 4px; padding: 4px 8px; font-size: 11px; }
            QPushButton:checked { background: #FF4444; color: white; border: 1px solid #FF4444; }
        """)
        layout.addWidget(self._title)
        layout.addWidget(self._detail)
        layout.addWidget(self._exclude_btn)
        self._exclude_btn.toggled.connect(self._on_exclude_toggled)

    def show_data(self, global_pos, osm_id, detail, color, data=None):
        self._current_data = data or {"osm_id": osm_id}
        self._title.setText(str(osm_id))
        self._detail.setText(detail)
        excluded = (self._current_data.get("type", ""), str(osm_id)) in self._excluded_ids
        self._exclude_btn.blockSignals(True)
        self._exclude_btn.setChecked(excluded)
        self._exclude_btn.setText("Exclu de la génération" if excluded else "Exclure de la génération")
        self._exclude_btn.blockSignals(False)
        self.setStyleSheet(f"""
            QWidget {{
                background: white;
                border: 2px solid {color};
                border-radius: 4px;
            }}
        """)
        self.adjustSize()
        self.move(global_pos + QPoint(12, 12))
        self.show()
        self.raise_()
        
    def _on_exclude_toggled(self, checked):
        osm_id = str(self._current_data.get("osm_id", ""))
        layer_type = self._current_data.get("type", "")
        key = (layer_type, osm_id)
        if checked:
            self._excluded_ids.add(key)
            self._exclude_btn.setText("Exclu de la génération")
        else:
            self._excluded_ids.discard(key)
            self._exclude_btn.setText("Exclure de la génération")
        self.exclusion_changed.emit(key, checked)
        
class MapCanvas(QGraphicsView):

    shape_changed = pyqtSignal()

    REF_ZOOM = 18
    
    item_clicked = pyqtSignal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        world_size = TILE_SIZE * (2 ** self.REF_ZOOM)
        self._scene.setSceneRect(0, 0, world_size, world_size)
        self.setScene(self._scene)

        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._tile_manager = TileManager(self)
        self._tile_manager.tile_ready.connect(self._on_tile_ready)
        self._tile_items = {}

        self._current_zoom = 14
        self._center_lonlat = (3.26, 46.92)

        self._panning = False
        self._pan_start = QPointF()

        self._draw_tool = None
        self._draw_start_scene = None
        self._current_shape_item = None
        self._shape_kind = None
        self._shape_params = None
        
        self._pan_start_scene = QPointF()
        self._pan_start_mouse = QPointF()

        self.set_center(*self._center_lonlat, self._current_zoom)
        
        self._search_bar = QLineEdit(self)
        self._search_bar.setPlaceholderText("🔍 Rechercher un lieu...")
        self._search_bar.setFixedHeight(36)
        self._search_bar.setStyleSheet("""
            QLineEdit {
                background: white;
                border: none;
                border-radius: 18px;
                padding: 0 16px;
                font-size: 13px;
                color: #212529;
            }
            QLineEdit:focus {
                border: 2px solid #4C6EF5;
            }
        """)
        self._search_bar.returnPressed.connect(self._on_search)
        self._search_nam = QNetworkAccessManager(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        
        self._polygon_points = []
        self._draw_shape_item = None
        
        self._osm_preview_items = {}
        self._osm_preview_edges = {}
        
        self._tooltip = MapTooltip()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        
        self._tooltip.exclusion_changed.connect(self._on_exclusion_changed)
        
    def _on_exclusion_changed(self, key, excluded):
        layer_type, osm_id = key
        for layer, layer_items in self._osm_preview_items.items():
            for item in layer_items:
                data = item.data(0)
                if not data or data.get("type") != layer_type or str(data.get("osm_id")) != osm_id:
                    continue
                if excluded:
                    color = QColor("#FF4444")
                else:
                    color = QColor(PREVIEW_COLORS.get(layer_type, "#000000"))
                if isinstance(item, QGraphicsLineItem):
                    pen = item.pen()
                    pen.setColor(color)
                    item.setPen(pen)
                elif isinstance(item, QGraphicsPolygonItem):
                    color.setAlphaF(0.7)
                    item.setBrush(QBrush(color))

    def _lonlat_to_scene(self, lon, lat):
        tx, ty = lonlat_to_tile(lon, lat, self.REF_ZOOM)
        return QPointF(tx * TILE_SIZE, ty * TILE_SIZE)

    def _scene_to_lonlat(self, point):
        tx = point.x() / TILE_SIZE
        ty = point.y() / TILE_SIZE
        return tile_to_lonlat(tx, ty, self.REF_ZOOM)

    def _zoom_scale_factor(self):
        return 2.0 ** (self._current_zoom - self.REF_ZOOM)

    def set_center(self, lon, lat, zoom=None):
        if zoom is not None:
            self._current_zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        self._center_lonlat = (lon, lat)
        scale = self._zoom_scale_factor()
        self.resetTransform()
        self.scale(scale, scale)
        self.centerOn(self._lonlat_to_scene(lon, lat))
        self._refresh_tiles()

    def center_lonlat(self):
        center_scene = self.mapToScene(self.viewport().rect().center())
        return self._scene_to_lonlat(center_scene)

    def current_zoom(self):
        return self._current_zoom

    def _apply_pan(self):
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center - self._pan_accum)

    def _refresh_tiles_deferred(self):
        self._refresh_pending = False
        QTimer.singleShot(16, self._refresh_tiles)

    def _refresh_tiles(self):
        if self.viewport().width() <= 0:
            return

        z = self._current_zoom

        rect = self.viewport().rect()
        top_left = self.mapToScene(rect.topLeft())
        bottom_right = self.mapToScene(rect.bottomRight())

        lon1, lat1 = self._scene_to_lonlat(top_left)
        lon2, lat2 = self._scene_to_lonlat(bottom_right)

        x1, y1 = lonlat_to_tile(lon1, lat1, z)
        x2, y2 = lonlat_to_tile(lon2, lat2, z)

        xmin = int(math.floor(min(x1, x2))) - 1
        xmax = int(math.ceil(max(x1, x2))) + 1
        ymin = int(math.floor(min(y1, y2))) - 1
        ymax = int(math.ceil(max(y1, y2))) + 1

        n = 2 ** z
        needed = set()

        for tx in range(xmin, xmax + 1):
            wx = tx % n
            for ty in range(max(0, ymin), min(n, ymax + 1)):
                needed.add((z, wx, ty))
                self._tile_manager.request_tile(z, tx, ty)

        for key in list(self._tile_items.keys()):
            if key not in needed:
                item = self._tile_items.pop(key)
                self._scene.removeItem(item)

    def _on_tile_ready(self, z, x, y, pix: QPixmap):
        if z != self._current_zoom:
            return
        key = (z, x, y)
        if key in self._tile_items:
            return

        ref_scale = 2.0 ** (self.REF_ZOOM - z)
        size = TILE_SIZE * ref_scale
        scene_x = x * size
        scene_y = y * size

        item = self._scene.addPixmap(pix)
        item.setOffset(0, 0)
        item.setZValue(-10)
        item.setPos(scene_x, scene_y)
        item.setScale(ref_scale)
        if getattr(self, '_osm_preview_items', {}):
            item.setOpacity(0.4)
        self._tile_items[key] = item

    def set_preview_opacity(self, has_preview: bool):
        opacity = 0.4 if has_preview else 1.0
        for item in self._tile_items.values():
            item.setOpacity(opacity)

    def mousePressEvent(self, event: QMouseEvent):
    
        self._tooltip.hide()
    
        if self._draw_tool is not None:
            self._draw_mouse_press(event)
            return

        if event.button() == Qt.MouseButton.LeftButton:
            scene_pos = self.mapToScene(event.position().toPoint())
            tolerance = 5 / self._zoom_scale_factor()
            items = self._scene.items(
                QRectF(scene_pos.x() - tolerance, scene_pos.y() - tolerance,
                       tolerance * 2, tolerance * 2)
            )
            for item in items:
                if item.data(0):
                    self._show_tooltip(event.position().toPoint(), item.data(0))
                    event.accept()
                    return

            self._panning = True
            self._pan_start = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._draw_tool is not None:
            self._draw_mouse_move(event)
            return

        if self._panning:
            delta = event.position() - self._pan_start
            self._pan_start = event.position()

            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - int(delta.x())
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - int(delta.y())
            )

            self._refresh_tiles()
            event.accept()
            return

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self._draw_tool is not None:
            self._draw_mouse_release(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        if self._draw_tool is not None:
            self._draw_mouse_double_click_event(event)
            return
        super().mouseDoubleClickEvent(event)

    def _show_tooltip(self, pos, data):
        color = PREVIEW_COLORS.get(data.get("type", ""), "#000000")
        osm_id = str(data.get("osm_id", ""))
        detail = data.get("subtype", "") if data.get("type") == "road" else data.get("detail", "")
        global_pos = self.mapToGlobal(pos)
        self._tooltip.show_data(global_pos, osm_id, detail, color, data)

    def _on_focus_changed(self, old, new):
        if new is None or not self.window().isAncestorOf(new) and new != self.window():
            self._tooltip.hide()

    def translate_view(self, delta_pixels: QPointF):
        self.translate(delta_pixels.x(), delta_pixels.y())

    def wheelEvent(self, event: QWheelEvent):
        anchor_view = event.position().toPoint()
        old_scene_pos = self.mapToScene(anchor_view)

        lon, lat = self._scene_to_lonlat(old_scene_pos)

        steps = event.angleDelta().y() / 120.0
        new_zoom = self._current_zoom + (1 if steps > 0 else -1)
        new_zoom = max(MIN_ZOOM, min(MAX_ZOOM, new_zoom))

        if new_zoom == self._current_zoom:
            event.accept()
            return

        self._current_zoom = new_zoom
        scale = self._zoom_scale_factor()

        self.resetTransform()
        self.scale(scale, scale)

        new_scene_pos = self.mapToScene(anchor_view)

        delta = new_scene_pos - old_scene_pos
        self.translate(delta.x(), delta.y())

        self._refresh_tiles()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_tiles()

    def set_draw_tool(self, tool):
        self._draw_tool = tool
        if tool is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self.setCursor(Qt.CursorShape.CrossCursor)

    def clear_shape(self):
        if self._current_shape_item is not None:
            self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = None
        self._shape_kind = None
        self._shape_params = None
        self.shape_changed.emit()

    def current_shape(self):
        if self._shape_kind is None:
            return None
        return self._shape_kind, self._shape_params

    def _draw_mouse_press(self, event: QMouseEvent):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._draw_start_scene = self.mapToScene(event.position().toPoint())
        if self._current_shape_item is not None:
            self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = None
        if self._draw_tool == "polygon":
            self._polygon_points.append(self.mapToScene(event.pos()))
            return
        event.accept()

    def _draw_mouse_move(self, event: QMouseEvent):
        if self._draw_start_scene is None:
            return
        current_scene = self.mapToScene(event.position().toPoint())
        self._update_preview_shape(self._draw_start_scene, current_scene)
        event.accept()

    def _draw_mouse_release(self, event: QMouseEvent):
        if self._draw_start_scene is None or event.button() != Qt.MouseButton.LeftButton:
            return
        if self._draw_tool == "polygon":
            return
        end_scene = self.mapToScene(event.position().toPoint())
        self._finalize_shape(self._draw_start_scene, end_scene)
        self._draw_start_scene = None
        event.accept()

    def _draw_mouse_double_click_event(self, event):
        if self._draw_tool == "polygon" and len(self._polygon_points) >= 3:
            self._finalize_shape(self._draw_start_scene, None)
            self._draw_start_scene = None
        
        event.accept()

    def _meters_per_scene_unit(self, lat):
        circumference = 40075016.686
        n = 2 ** self.REF_ZOOM
        meters_per_px = circumference * math.cos(math.radians(lat)) / (TILE_SIZE * n)
        return meters_per_px

    def _update_preview_shape(self, start_scene, current_scene):
        pen = QPen(QColor(255, 70, 0), 2 / self._zoom_scale_factor())
        brush = QBrush(QColor(255, 70, 0, 50))

        if self._current_shape_item is not None:
            self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = None

        if self._draw_tool == "rect":
            rect = QRectF(start_scene, current_scene).normalized()
            self._current_shape_item = self._scene.addRect(rect, pen, brush)

        elif self._draw_tool == "circle":
            dx = current_scene.x() - start_scene.x()
            dy = current_scene.y() - start_scene.y()
            r = math.hypot(dx, dy)
            rect = QRectF(start_scene.x() - r, start_scene.y() - r, 2 * r, 2 * r)
            self._current_shape_item = self._scene.addEllipse(rect, pen, brush)

        elif self._draw_tool == "hexagon":
            dx = current_scene.x() - start_scene.x()
            dy = current_scene.y() - start_scene.y()
            r = math.hypot(dx, dy)
            poly = self._hexagon_polygon(start_scene, r)
            self._current_shape_item = self._scene.addPolygon(poly, pen, brush)
            
        elif self._draw_tool == "polygon":
            pts = self._polygon_points

            if len(pts) == 0:
                return

            if len(pts) == 1:
                self._current_shape_item = self._scene.addLine(
                    pts[0].x(), pts[0].y(),
                    current_scene.x(), current_scene.y(),
                    pen
                )
                return

            temp_points = pts + [current_scene]
            poly = QPolygonF(temp_points)
            self._current_shape_item = self._scene.addPolygon(poly, pen, brush)
            
    def _hexagon_polygon(self, center_scene, radius_scene):
        poly = QPolygonF()
        for i in range(6):
            angle = math.radians(60 * i - 30)
            x = center_scene.x() + radius_scene * math.cos(angle)
            y = center_scene.y() + radius_scene * math.sin(angle)
            poly.append(QPointF(x, y))
        return poly

    def _finalize_shape(self, start_scene, end_scene):
        lon_c, lat_c = self._scene_to_lonlat(start_scene)
        m_per_unit = self._meters_per_scene_unit(lat_c)

        if self._draw_tool == "rect":
            rect = QRectF(start_scene, end_scene).normalized()
            lon1, lat1 = self._scene_to_lonlat(rect.topLeft())
            lon2, lat2 = self._scene_to_lonlat(rect.bottomRight())
            dx = end_scene.x() - start_scene.x()
            dy = end_scene.y() - start_scene.y()
            x_m = dx * m_per_unit
            y_m = dy * m_per_unit
            self._shape_kind = "rect"
            self._shape_params = {
                "west": min(lon1, lon2), "east": max(lon1, lon2),
                "south": min(lat1, lat2), "north": max(lat1, lat2),
                "width":x_m, "height": y_m, "area":x_m*y_m/10000
            }
            pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
            brush = QBrush(QColor(0, 150, 60, 60))
            if self._current_shape_item is not None:
                self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = self._scene.addRect(rect, pen, brush)

        elif self._draw_tool in ("circle", "hexagon"):
            dx = end_scene.x() - start_scene.x()
            dy = end_scene.y() - start_scene.y()
            r_scene = math.hypot(dx, dy)
            radius_m = r_scene * m_per_unit
            self._shape_kind = self._draw_tool
            self._shape_params = {
                "center_lon": lon_c, "center_lat": lat_c, "radius_m": radius_m
            }
            if self._draw_tool == "circle":
                self._shape_params["area"] = math.pi*radius_m*radius_m/10000
            else:
                self._shape_params["area"] = 2.5980762*radius_m*radius_m/10000
            
            pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
            brush = QBrush(QColor(0, 150, 60, 60))
            if self._current_shape_item is not None:
                self._scene.removeItem(self._current_shape_item)
            if self._draw_tool == "circle":
                rect = QRectF(start_scene.x() - r_scene, start_scene.y() - r_scene,
                              2 * r_scene, 2 * r_scene)
                self._current_shape_item = self._scene.addEllipse(rect, pen, brush)
            else:
                poly = self._hexagon_polygon(start_scene, r_scene)
                self._current_shape_item = self._scene.addPolygon(poly, pen, brush)
        elif self._draw_tool == "polygon":
            if len(self._polygon_points) < 3:
                return

            poly = QPolygonF(self._polygon_points)
            
            area = 0
            n = len(self._polygon_points)
            for i in range(n):
                x1, y1 = self._polygon_points[i].x(), self._polygon_points[i].y()
                x2, y2 = self._polygon_points[(i + 1) % n].x(), self._polygon_points[(i + 1) % n].y()
                area += x1 * y2 - x2 * y1

            self._shape_kind = "polygon"
            self._shape_params = {
                "area": abs(area)/20000*(m_per_unit**2),
                "points": [
                    self._scene_to_lonlat(p) for p in self._polygon_points
                ]
            }

            pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
            brush = QBrush(QColor(0, 150, 60, 60))

            if self._current_shape_item is not None:
                self._scene.removeItem(self._current_shape_item)

            self._current_shape_item = self._scene.addPolygon(poly, pen, brush)

            self._polygon_points.clear()

        self.set_draw_tool(None)
        self.shape_changed.emit()

    def set_preview_roads(self, edges_by_level: dict):
        self._clear_osm_preview("roads")
        self._osm_preview_edges["roads"] = edges_by_level
        shape = self._shape_as_shapely()
        if shape is not None and edges_by_level:
            clipped = {}
            for level, edges in edges_by_level.items():
                filtered = edges[edges.geometry.intersects(shape)]
                if not filtered.empty:
                    clipped[level] = filtered
            self._draw_osm_preview_roads(list(clipped.keys()))
            self._osm_preview_edges["roads"] = clipped
        else:
            self._draw_osm_preview_roads(list(edges_by_level.keys()))

    def set_preview_water(self, data: dict):
        self._clear_osm_preview("water")
        shape = self._shape_as_shapely()
        if shape is not None:
            clipped = {}
            for k, gdf in data.items():
                if gdf is not None and not gdf.empty:
                    filtered = gdf[gdf.geometry.intersects(shape)]
                    clipped[k] = filtered if not filtered.empty else None
                else:
                    clipped[k] = gdf
            self._osm_preview_edges["water"] = clipped
        else:
            self._osm_preview_edges["water"] = data
        self._draw_osm_preview_water()

    def set_preview_vegetation(self, data: dict):
        self._clear_osm_preview("vegetation")
        shape = self._shape_as_shapely()
        if shape is not None:
            clipped = {}
            for k, gdf in data.items():
                if gdf is not None and not gdf.empty:
                    filtered = gdf[gdf.geometry.intersects(shape)]
                    clipped[k] = filtered if not filtered.empty else None
                else:
                    clipped[k] = gdf
            self._osm_preview_edges["vegetation"] = clipped
        else:
            self._osm_preview_edges["vegetation"] = data
        self._draw_osm_preview_vegetation()

    def set_preview_buildings(self, data: dict):
        self._clear_osm_preview("buildings")
        shape = self._shape_as_shapely()
        if shape is not None:
            buildings = data.get("buildings")
            if buildings is not None and not buildings.empty:
                filtered = buildings[buildings.geometry.intersects(shape)]
                self._osm_preview_edges["buildings"] = {"buildings": filtered if not filtered.empty else None}
            else:
                self._osm_preview_edges["buildings"] = data
        else:
            self._osm_preview_edges["buildings"] = data
        self._draw_osm_preview_buildings()

    def update_preview_visibility(self, visible_levels: list):
        self._clear_osm_preview("roads")
        self._draw_osm_preview_roads(visible_levels)

    def clear_preview_roads(self):
        self._clear_osm_preview("roads")
        self._osm_preview_edges.pop("roads", None)
        
    def clear_preview_water(self):
        self._clear_osm_preview("water")
        self._osm_preview_edges.pop("water", None)
        
    def clear_preview_vegetation(self):
        self._clear_osm_preview("vegetation")
        self._osm_preview_edges.pop("vegetation", None)
        
    def clear_preview_buildings(self):
        self._clear_osm_preview("buildings")
        self._osm_preview_edges.pop("buildings", None)

    def clear_all_osm_preview(self):
        for layer in list(self._osm_preview_items.keys()):
            self._clear_osm_preview(layer)
        self._osm_preview_edges.clear()

    def _clear_osm_preview(self, layer: str):
        for item in self._osm_preview_items.get(layer, []):
            self._scene.removeItem(item)
        self._osm_preview_items.pop(layer, None)

    def _draw_osm_preview_roads(self, visible_levels: list):
        edges_by_level = self._osm_preview_edges.get("roads", {})
        color_hex = PREVIEW_COLORS["roads"]
        pen = QPen(QColor(color_hex))
        pen.setWidth(0)
        items = []
        for level in visible_levels:
            if level not in edges_by_level:
                continue
            edges = edges_by_level[level]
            for idx, row in edges.iterrows():
                geom = row.geometry
                coords = list(geom.coords)
                osm_id = osm_id = row["osmid"][0] if isinstance(row["osmid"], list) else row["osmid"]
                name = row.get("name", "") if "name" in edges.columns else ""
                for i in range(len(coords) - 1):
                    lon1, lat1 = coords[i]
                    lon2, lat2 = coords[i + 1]
                    p1 = self._lonlat_to_scene(lon1, lat1)
                    p2 = self._lonlat_to_scene(lon2, lat2)
                    item = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
                    item.setZValue(6)
                    item.setData(0, {"type": "road", "osm_id": osm_id, "name": name, "subtype": level})
                    items.append(item)
        self._osm_preview_items["roads"] = items

    def _draw_osm_preview_water(self, min_area_m2=0, min_length_m=0):
        data = self._osm_preview_edges.get("water", {})
        color_hex = PREVIEW_COLORS["water"]
        color = QColor(color_hex)
        color.setAlphaF(0.6)
        pen = QPen(Qt.PenStyle.NoPen)
        brush = QBrush(color)
        items = []
        water_areas = data.get("water_areas")
        waterways = data.get("waterways")

        if water_areas is not None:
            wa_proj = water_areas.to_crs("EPSG:3857")
            if min_area_m2 > 0:
                wa_proj = wa_proj[wa_proj.geometry.area >= min_area_m2]
                water_areas = wa_proj.to_crs("EPSG:4326")
            else:
                water_areas = water_areas.loc[wa_proj.index]
            for idx, row in water_areas.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                osm_id = idx[1] if isinstance(idx, tuple) else idx
                name = row.get("name", "") or ""
                subtype = row.get("water", "") or row.get("natural", "") or ""
                area_m2 = round(wa_proj.loc[idx].geometry.area)
                parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
                for part in parts:
                    poly = QPolygonF()
                    for lon, lat in part.exterior.coords:
                        p = self._lonlat_to_scene(lon, lat)
                        poly.append(p)
                    item = self._scene.addPolygon(poly, pen, brush)
                    item.setZValue(5)
                    item.setData(0, {"type": "water", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{area_m2} m²"})
                    items.append(item)

        if waterways is not None:
            ww_proj = waterways.to_crs("EPSG:3857")
            if min_length_m > 0:
                ww_proj = ww_proj[ww_proj.geometry.length >= min_length_m]
                waterways = ww_proj.to_crs("EPSG:4326")
            else:
                waterways = waterways.loc[ww_proj.index]
            line_color = QColor(color_hex)
            line_color.setAlphaF(0.8)
            line_pen = QPen(line_color)
            line_pen.setWidth(0)
            for idx, row in waterways.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                osm_id = idx[1] if isinstance(idx, tuple) else idx
                name = row.get("name", "") or ""
                subtype = row.get("waterway", "") or ""
                length_m = round(ww_proj.loc[idx].geometry.length)
                if geom.geom_type == "LineString":
                    coords = list(geom.coords)
                elif geom.geom_type == "MultiLineString":
                    coords = [pt for line in geom.geoms for pt in line.coords]
                else:
                    continue
                for i in range(len(coords) - 1):
                    lon1, lat1 = coords[i]
                    lon2, lat2 = coords[i+1]
                    p1 = self._lonlat_to_scene(lon1, lat1)
                    p2 = self._lonlat_to_scene(lon2, lat2)
                    item = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), line_pen)
                    item.setZValue(5)
                    item.setData(0, {"type": "water", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{length_m} m"})
                    items.append(item)

        self._osm_preview_items["water"] = items

    def _draw_osm_preview_vegetation(self):
        data = self._osm_preview_edges.get("vegetation", {})
        color_hex = PREVIEW_COLORS["vegetation"]
        color = QColor(color_hex)
        color.setAlphaF(0.5)
        pen = QPen(Qt.PenStyle.NoPen)
        brush = QBrush(color)
        items = []
        for key in ("forest", "other_veg"):
            dataset = data.get(key)
            if dataset is None:
                continue
            dataset_proj = dataset.to_crs("EPSG:3857")
            for idx, row in dataset.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue
                osm_id = idx[1] if isinstance(idx, tuple) else idx
                name = row.get("name", "") or ""
                subtype = row.get("natural", "") or row.get("landuse", "") or row.get("leisure", "") or ""
                area_m2 = round(dataset_proj.loc[idx].geometry.area)
                parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
                for part in parts:
                    poly = QPolygonF()
                    for lon, lat in part.exterior.coords:
                        p = self._lonlat_to_scene(lon, lat)
                        poly.append(p)
                    item = self._scene.addPolygon(poly, pen, brush)
                    item.setZValue(5)
                    item.setData(0, {"type": "vegetation", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{area_m2} m²"})
                    items.append(item)
        self._osm_preview_items["vegetation"] = items
    
    def _draw_osm_preview_buildings(self, min_area_m2=0):
        data = self._osm_preview_edges.get("buildings", {})
        color_hex = PREVIEW_COLORS["buildings"]
        color = QColor(color_hex)
        color.setAlphaF(0.7)
        pen = QPen(Qt.PenStyle.NoPen)
        brush = QBrush(color)
        items = []
        buildings = data.get("buildings")
        if buildings is None:
            self._osm_preview_items["buildings"] = items
            return
        buildings_proj = buildings.to_crs("EPSG:3857")
        if min_area_m2 > 0:
            buildings_proj = buildings_proj[buildings_proj.geometry.area >= min_area_m2]
            buildings = buildings_proj.to_crs("EPSG:4326")
        else:
            buildings = buildings.loc[buildings_proj.index]
        for idx, row in buildings.iterrows():
            geom = row.geometry
            if geom is None or geom.is_empty:
                continue
            osm_id = idx[1] if isinstance(idx, tuple) else idx
            name = row.get("name", "") or ""
            subtype = row.get("building", "") or ""
            area_ha = round(buildings_proj.loc[idx].geometry.area)/10000
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                poly = QPolygonF()
                for lon, lat in part.exterior.coords:
                    p = self._lonlat_to_scene(lon, lat)
                    poly.append(p)
                item = self._scene.addPolygon(poly, pen, brush)
                item.setZValue(5)
                item.setData(0, {"type": "buildings", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{area_ha} ha"})
                items.append(item)
        self._osm_preview_items["buildings"] = items

    def _shape_as_shapely(self):
        if self._shape_kind is None:
            return None
        p = self._shape_params
        if self._shape_kind == "rect":
            return Polygon([(p["west"], p["south"]), (p["east"], p["south"]),
                            (p["east"], p["north"]), (p["west"], p["north"])])
        elif self._shape_kind in ("circle", "hexagon"):
            lon, lat, r = p["center_lon"], p["center_lat"], p["radius_m"]
            r_deg_lat = r / 111320
            r_deg_lon = r / (111320 * math.cos(math.radians(lat)))
            if self._shape_kind == "circle":
                pts = [(lon + r_deg_lon * math.cos(2*math.pi*i/64),
                        lat + r_deg_lat * math.sin(2*math.pi*i/64)) for i in range(64)]
            else:
                pts = [(lon + r_deg_lon * math.cos(math.radians(60*i-30)),
                        lat + r_deg_lat * math.sin(math.radians(60*i-30))) for i in range(6)]
            return Polygon(pts)
        elif self._shape_kind == "polygon":
            return Polygon([(lon, lat) for lon, lat in p["points"]])
        return None

    def get_excluded_ids(self):
        return set(self._tooltip._excluded_ids)
        
    def _on_search(self):
        query = self._search_bar.text().strip()
        if not query:
            return
        url = QUrl("https://nominatim.openstreetmap.org/search")
        params = QUrlQuery()
        params.addQueryItem("q", query)
        params.addQueryItem("format", "json")
        params.addQueryItem("limit", "1")
        url.setQuery(params)
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "topopixel/1.0")
        reply = self._search_nam.get(req)
        reply.finished.connect(lambda: self._on_search_result(reply))

    def _on_search_result(self, reply):
        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return
        data = json.loads(bytes(reply.readAll()))
        reply.deleteLater()
        if not data:
            return
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
        self.set_center(lon, lat, 14)
        self._search_bar.clearFocus()

    def _restore_shape_item(self):
        import math
        if self._shape_kind is None or self._shape_params is None:
            return
        pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
        brush = QBrush(QColor(0, 150, 60, 60))
        p = self._shape_params
        if self._shape_kind == "rect":
            tl = self._lonlat_to_scene(p["west"], p["north"])
            br = self._lonlat_to_scene(p["east"], p["south"])
            self._current_shape_item = self._scene.addRect(QRectF(tl, br), pen, brush)
        elif self._shape_kind == "circle":
            center = self._lonlat_to_scene(p["center_lon"], p["center_lat"])
            m = self._meters_per_scene_unit(p["center_lat"])
            r = p["radius_m"] / m
            rect = QRectF(center.x()-r, center.y()-r, 2*r, 2*r)
            self._current_shape_item = self._scene.addEllipse(rect, pen, brush)
        elif self._shape_kind == "hexagon":
            center = self._lonlat_to_scene(p["center_lon"], p["center_lat"])
            m = self._meters_per_scene_unit(p["center_lat"])
            r = p["radius_m"] / m
            poly = self._hexagon_polygon(center, r)
            self._current_shape_item = self._scene.addPolygon(poly, pen, brush)
        elif self._shape_kind == "polygon":
            pts = [self._lonlat_to_scene(lon, lat) for lon, lat in p["points"]]
            self._current_shape_item = self._scene.addPolygon(QPolygonF(pts), pen, brush)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_tiles()
        w = min(400, self.width() - 40)
        self._search_bar.setFixedWidth(w)
        self._search_bar.move((self.width() - w) // 2, 12)
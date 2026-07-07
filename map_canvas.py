import math
import json
import os
from PyQt6.QtWidgets import QGraphicsView, QGraphicsScene, QWidget, QVBoxLayout, QLabel, QPushButton, QGraphicsLineItem, QGraphicsPolygonItem, QApplication, QLineEdit, QGraphicsProxyWidget, QSpinBox, QListWidget, QListWidgetItem
from PyQt6.QtCore import Qt, QRectF, QPointF, QPoint, pyqtSignal, QTimer, QUrl, QUrlQuery
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from PyQt6.QtGui import (
    QPainter, QPixmap, QPen, QBrush, QColor, QPolygonF, QWheelEvent,
    QMouseEvent
)
from shapely.geometry import Polygon
import geopandas as gpd
from tile_manager import TileManager, TILE_SIZE, lonlat_to_tile, tile_to_lonlat
import xml.etree.ElementTree as ET

from constants import TOPIC_COLORS

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
        self._title.setStyleSheet("font-weight: bold; font-size: 13px; color: #DDDDDD;")
        self._detail = QLabel()
        self._detail.setStyleSheet("color: #999999; font-size: 11px;")
        self._exclude_btn = QPushButton("Exclure de la génération")
        self._exclude_btn.setCheckable(True)
        self._exclude_btn.setStyleSheet("""
            QPushButton { background: #4A4A4A; color: #DDDDDD; border: 1px solid #666666; border-radius: 4px; padding: 4px 8px; font-size: 11px; }
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
                background: #000000;
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
        
        self._search_bar = QLineEdit(self)
        self._search_bar.setPlaceholderText("🔍 Rechercher un lieu...")
        self._search_bar.setFixedHeight(36)
        self._search_bar.setStyleSheet("""
            QLineEdit {
                background: #3C3C3C;
                border: none;
                border-radius: 18px;
                padding: 0 16px;
                font-size: 13px;
                color: #DDDDDD;
            }
            QLineEdit:focus {
                border: 2px solid #4C6EF5;
            }
        """)
        self._search_bar.returnPressed.connect(self._on_suggestion_enter)
        self._search_bar.focusOutEvent = self._on_search_focus_out
        self._search_nam = QNetworkAccessManager(self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        
        self._search_suggestions = QListWidget(self)
        self._search_suggestions.setVisible(False)
        self._search_suggestions.setStyleSheet("""
            QListWidget {
                background: #3C3C3C;
                border: none;
                border-radius: 12px;
                font-size: 10px;
                color: #DDDDDD;
                padding: 4px 0;
            }
            QListWidget::item {
                padding: 8px 16px;
                border-radius: 8px;
            }
            QListWidget::item:hover {
                background: #4A4A4A;
            }
            QListWidget::item:selected {
                background: #555555;
                color: #1971C2;
            }
        """)
        self._search_suggestions.itemClicked.connect(self._on_suggestion_clicked)
        self._search_results = []
        self._search_timer.timeout.connect(self._on_search)
        self._search_bar.textChanged.connect(self._on_search_text_changed)

        self._fit_shape_btn = QPushButton("⛶", self)
        self._fit_shape_btn.setFixedSize(36, 36)
        self._fit_shape_btn.setToolTip("Recentrer sur l'emprise")
        self._fit_shape_btn.setStyleSheet("""
            QPushButton {
                background: #3C3C3C;
                color: #DDDDDD;
                border: 1px solid #555555;
                border-radius: 18px;
                font-size: 16px;
            }
            QPushButton:hover {
                background: #4A4A4A;
            }
        """)
        self._fit_shape_btn.clicked.connect(self.zoom_to_fit_shape)
        self._fit_shape_btn.setVisible(False)
        self._fit_shape_btn.raise_()
        self.setMouseTracking(True)
        self.shape_changed.connect(self._update_fit_btn_visibility)

        self._scale_label = QLabel(self)
        self._scale_label.setStyleSheet("""
            QLabel {
                background: #3C3C3C;
                color: #DDDDDD;
                border: 1px solid #555555;
                border-radius: 3px;
                padding: 2px 8px;
                font-size: 11px;
            }
        """)
        self._scale_label.setFixedHeight(20)
        self._scale_label.raise_()

        self._polygon_points = []
        self._draw_shape_item = None
        
        self._osm_preview_items = {}
        self._osm_preview_edges = {}
        
        self._hovered_vertex = None
        self._vertex_items = []
        self.setMouseTracking(True)
        
        self._dragging_vertex = None
        self._drag_vertex_index = None
        
        self._gpx_items = []
        
        self._tooltip = MapTooltip()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)
        
        self._tooltip.exclusion_changed.connect(self._on_exclusion_changed)
        
        self._vertex_drag_timer = QTimer(self)
        self._vertex_drag_timer.setSingleShot(True)
        self._vertex_drag_timer.timeout.connect(self.shape_changed.emit)

        self.set_center(*self._center_lonlat, self._current_zoom)
        
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
        self._update_scale_label()

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
            item.setOpacity(0.9)
        self._tile_items[key] = item

    def set_preview_opacity(self, has_preview: bool):
        opacity = 0.9 if has_preview else 1.0
        for item in self._tile_items.values():
            item.setOpacity(opacity)

    def mousePressEvent(self, event: QMouseEvent):
    
        self._tooltip.hide()
    
        if self._draw_tool is not None:
            self._draw_mouse_press(event)
            return
            
        if event.button() == Qt.MouseButton.LeftButton and self._hovered_vertex is not None:
            self._vertex_drag_timer.stop()
            self._dragging_vertex = self._hovered_vertex
            if self._hovered_vertex not in self._vertex_items:
                self._hovered_vertex = None
                event.accept()
                return
            self._drag_vertex_index = self._vertex_items.index(self._hovered_vertex)
            event.accept()
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
    
        if self._draw_tool is None and self._vertex_items:
            scene_pos = self.mapToScene(event.position().toPoint())
            tolerance = 8 / self._zoom_scale_factor()
            hovered = None
            for item in self._vertex_items:
                data = item.data(0)
                pt = self._lonlat_to_scene(data["lon"], data["lat"])
                if abs(pt.x() - scene_pos.x()) < tolerance and abs(pt.y() - scene_pos.y()) < tolerance:
                    hovered = item
                    break
            if hovered != self._hovered_vertex:
                self._hovered_vertex = hovered
                if hovered:
                    self.setCursor(Qt.CursorShape.SizeAllCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
    
        if self._dragging_vertex is not None:
            scene_pos = self.mapToScene(event.position().toPoint())
            lon, lat = self._scene_to_lonlat(scene_pos)
            idx = self._drag_vertex_index
            if self._shape_kind == "rect":
                p = self._shape_params
                corners = ["west_north", "east_north", "east_south", "west_south"]
                corner = corners[idx]
                if "west" in corner:
                    p["west"] = lon
                if "east" in corner:
                    p["east"] = lon
                if "north" in corner:
                    p["north"] = lat
                if "south" in corner:
                    p["south"] = lat
            elif self._shape_kind == "polygon":
                self._shape_params["points"][idx] = [lon, lat]
            self._redraw_shape()
            self._draw_vertices()
            event.accept()
            return
    
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
            
        if event.button() == Qt.MouseButton.LeftButton and self._dragging_vertex is not None:
            self._dragging_vertex = None
            self._drag_vertex_index = None
            self._vertex_drag_timer.start(1000)
            p = self._shape_params
            kind = self._shape_kind
            if kind == "rect":
                lat_mid = (p["north"] + p["south"]) / 2
                p["width"] = abs(p["east"] - p["west"]) * 111320 * math.cos(math.radians(lat_mid))
                p["height"] = abs(p["north"] - p["south"]) * 111320
                p["area"] = p["width"] * p["height"] / 10000
            elif kind == "polygon":
                poly = Polygon([(lon, lat) for lon, lat in p["points"]])
                gdf = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326").to_crs("EPSG:3857")
                p["area"] = gdf.geometry.area.iloc[0] / 10000
                
            self._on_shape_changed_internal()
            event.accept()
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
        self._update_scale_label()
        event.accept()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_tiles()
        w = min(400, self.width() - 40)
        self._search_bar.setFixedWidth(w)
        self._search_suggestions.setFixedWidth(w)
        self._search_bar.move((self.width() - w) // 2, 12)
        self._search_suggestions.move((self.width() - w) // 2, 12 + 36 + 4)
        self._fit_shape_btn.move(self.width() - 36 - 12, 12)
        self._update_scale_label()

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
            
            for item in self._vertex_items:
                self._scene.removeItem(item)
            self._vertex_items.clear()
            self._hovered_vertex = None
            
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
            lonlat_points = [self._scene_to_lonlat(p) for p in self._polygon_points]
            gdf = gpd.GeoDataFrame(geometry=[Polygon(lonlat_points)], crs="EPSG:4326").to_crs("EPSG:3857")
            self._shape_kind = "polygon"
            self._shape_params = {
                "area": gdf.geometry.area.iloc[0] / 10000,
                "points": [[lon, lat] for lon, lat in lonlat_points]
            }
            pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
            brush = QBrush(QColor(0, 150, 60, 60))
            if self._current_shape_item is not None:
                self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = self._scene.addPolygon(poly, pen, brush)
            self._polygon_points.clear()

        self.set_draw_tool(None)
        self.shape_changed.emit()
        self._draw_vertices()

    def set_preview_roads(self, edges_by_level: dict):
        self._clear_osm_preview("roads")
        shape = self._shape_as_shapely()
        if shape is not None and edges_by_level:
            clipped = {}
            for level, edges in edges_by_level.items():
                filtered = edges[edges.geometry.intersects(shape)]
                if not filtered.empty:
                    clipped[level] = filtered
            self._osm_preview_edges["roads"] = clipped
        else:
            self._osm_preview_edges["roads"] = edges_by_level
        from PyQt6.QtWidgets import QApplication
        win = QApplication.activeWindow()
        visible = list(self._osm_preview_edges["roads"].keys())
        if hasattr(win, 'param_panel'):
            visible = [l for l in win.param_panel.get_params().get("ROAD_LEVELS", []) if l in self._osm_preview_edges["roads"]]
        self._draw_osm_preview_roads(visible)

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
        from PyQt6.QtWidgets import QApplication
        win = QApplication.activeWindow()
        min_area, min_length = 0, 0
        if hasattr(win, 'param_panel'):
            params = win.param_panel.get_params()
            min_area = params.get("MIN_WATER_AREA_M2", 0)
            min_length = params.get("MIN_WATERWAY_LENGTH_M", 0)
        self._draw_osm_preview_water(min_area_m2=min_area, min_length_m=min_length)

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
        from PyQt6.QtWidgets import QApplication
        win = QApplication.activeWindow()
        min_area = 0
        if hasattr(win, 'param_panel'):
            min_area = win.param_panel.get_params().get("MIN_BUILDING_AREA_M2", 0)
        self._draw_osm_preview_buildings(min_area_m2=min_area)

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

    def show_osm_cache_coverage(self, bboxes):
        self.clear_osm_cache_coverage()
        pen = QPen(QColor(230, 126, 34, 200))
        pen.setWidth(2)
        brush = QBrush(QColor(230, 126, 34, 60))
        items = []
        for bbox in bboxes:
            top_left = self._lonlat_to_scene(bbox["west"], bbox["north"])
            bottom_right = self._lonlat_to_scene(bbox["east"], bbox["south"])
            rect = QRectF(top_left, bottom_right)
            item = self._scene.addRect(rect, pen, brush)
            item.setZValue(5)
            items.append(item)
        self._osm_cache_coverage_items = items

    def clear_osm_cache_coverage(self):
        for item in getattr(self, "_osm_cache_coverage_items", []):
            self._scene.removeItem(item)
        self._osm_cache_coverage_items = []

    def show_cache_coverage(self, bboxes):
        self.clear_cache_coverage()
        pen = QPen(QColor(70, 130, 180, 200))
        pen.setWidth(2)
        brush = QBrush(QColor(70, 130, 180, 60))
        items = []
        for bbox in bboxes:
            top_left = self._lonlat_to_scene(bbox["west"], bbox["north"])
            bottom_right = self._lonlat_to_scene(bbox["east"], bbox["south"])
            rect = QRectF(top_left, bottom_right)
            item = self._scene.addRect(rect, pen, brush)
            item.setZValue(5)
            items.append(item)
        self._cache_coverage_items = items

    def clear_cache_coverage(self):
        for item in getattr(self, "_cache_coverage_items", []):
            self._scene.removeItem(item)
        self._cache_coverage_items = []

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
                    if str(osm_id) in {oid for t, oid in self._tooltip._excluded_ids if t == "road"}:
                        pen = item.pen()
                        pen.setColor(QColor("#FF4444"))
                        item.setPen(pen)
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
                    if str(osm_id) in {oid for t, oid in self._tooltip._excluded_ids if t == "water"}:
                        color = QColor("#FF4444")
                        color.setAlphaF(0.7)
                        item.setBrush(QBrush(color))
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
                    if str(osm_id) in {oid for t, oid in self._tooltip._excluded_ids if t == "water"}:
                        pen = item.pen()
                        pen.setColor(QColor("#FF4444"))
                        item.setPen(pen)
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
                area_ha = round(dataset_proj.loc[idx].geometry.area)/10000
                parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
                for part in parts:
                    poly = QPolygonF()
                    for lon, lat in part.exterior.coords:
                        p = self._lonlat_to_scene(lon, lat)
                        poly.append(p)
                    item = self._scene.addPolygon(poly, pen, brush)
                    item.setZValue(5)
                    item.setData(0, {"type": "vegetation", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{area_ha} ha"})
                    if str(osm_id) in {oid for t, oid in self._tooltip._excluded_ids if t == "vegetation"}:
                        color = QColor("#FF4444")
                        color.setAlphaF(0.7)
                        item.setBrush(QBrush(color))
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
            area_ha = round(buildings_proj.loc[idx].geometry.area)
            parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for part in parts:
                poly = QPolygonF()
                for lon, lat in part.exterior.coords:
                    p = self._lonlat_to_scene(lon, lat)
                    poly.append(p)
                item = self._scene.addPolygon(poly, pen, brush)
                item.setZValue(5)
                item.setData(0, {"type": "buildings", "osm_id": osm_id, "name": name, "subtype": subtype, "detail": f"{area_ha} m²"})
                if str(osm_id) in {oid for t, oid in self._tooltip._excluded_ids if t == "buildings"}:
                    color = QColor("#FF4444")
                    color.setAlphaF(0.7)
                    item.setBrush(QBrush(color))
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

    def zoom_to_fit_shape(self):
        shape = self._shape_as_shapely()
        if shape is None:
            return
        min_lon, min_lat, max_lon, max_lat = shape.bounds
        center_lon = (min_lon + max_lon) / 2
        center_lat = (min_lat + max_lat) / 2
        top_left = self._lonlat_to_scene(min_lon, max_lat)
        bottom_right = self._lonlat_to_scene(max_lon, min_lat)
        rect = QRectF(top_left, bottom_right)
        margin = max(rect.width(), rect.height()) * 0.1
        rect = rect.adjusted(-margin, -margin, margin, margin)
        viewport_size = min(self.viewport().width(), self.viewport().height())
        content_size = max(rect.width(), rect.height())
        raw_zoom = self.REF_ZOOM + math.log2(viewport_size / content_size)
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, round(raw_zoom)))
        self.set_center(center_lon, center_lat, zoom)

    def _update_scale_label(self):
        center_lat = self._center_lonlat[1]
        meters_per_pixel = 156543.03392 * math.cos(math.radians(center_lat)) / (2 ** self._current_zoom)
        target_px = 80
        raw_meters = meters_per_pixel * target_px
        steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000]
        nice_meters = min(steps, key=lambda s: abs(s - raw_meters))
        bar_px = round(nice_meters / meters_per_pixel)
        label_text = f"{nice_meters} m" if nice_meters < 1000 else f"{nice_meters // 1000} km"
        self._scale_label.setText(label_text)
        self._scale_label.setFixedWidth(bar_px)
        self._scale_label.move(self.width() - bar_px - 12, self.height() - 20 - 12)

    def _update_fit_btn_visibility(self):
        self._fit_shape_btn.setVisible(self._shape_kind is not None and self.underMouse())

    def enterEvent(self, event):
        super().enterEvent(event)
        self._update_fit_btn_visibility()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self._fit_shape_btn.setVisible(False)

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
        params.addQueryItem("limit", "3")
        url.setQuery(params)
        req = QNetworkRequest(url)
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "topopixel/1.0")
        reply = self._search_nam.get(req)
        reply.finished.connect(lambda: self._on_search_result(reply))

    def _restore_shape_item(self):
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
            
        self._draw_vertices()

    def _on_search_text_changed(self, text):
        if len(text) < 3:
            self._search_suggestions.setVisible(False)
            return
        self._search_timer.start(300)

    def _on_search_result(self, reply):
        self._search_suggestions.setVisible(True)
        if reply.error() != QNetworkReply.NetworkError.NoError:
            reply.deleteLater()
            return
        raw = bytes(reply.readAll())
        self._search_results = json.loads(raw)
        reply.deleteLater()
        self._search_suggestions.clear()
        if not self._search_results:
            self._search_suggestions.setVisible(False)
            return
        for r in self._search_results:
            self._search_suggestions.addItem(r.get("display_name", ""))
        self._search_suggestions.setFixedHeight(len(self._search_results) * 50)
        self._search_suggestions.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._search_suggestions.setVisible(True)
        self._search_suggestions.raise_()

    def _on_suggestion_clicked(self, item):
        idx = self._search_suggestions.row(item)
        r = self._search_results[idx]
        self._search_bar.setText(r.get("display_name", ""))
        self._search_suggestions.setVisible(False)
        self.set_center(float(r["lon"]), float(r["lat"]), 14)
        self._search_bar.clearFocus()
        self._search_timer.stop()

    def _on_suggestion_enter(self):
        if self._search_results:
            r = self._search_results[0]
            self._search_bar.setText(r.get("display_name", ""))
            self._search_suggestions.setVisible(False)
            self.set_center(float(r["lon"]), float(r["lat"]), 14)
            self._search_bar.clearFocus()
            elf._search_timer.stop()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_tiles()
        w = min(400, self.width() - 40)
        self._search_bar.setFixedWidth(w)
        self._search_suggestions.setFixedWidth(w)
        self._search_bar.move((self.width() - w) // 2, 12)
        self._search_suggestions.move((self.width() - w) // 2, 12 + 36 + 4)
        self._fit_shape_btn.move(self.width() - 36 - 12, 12)
        self._update_scale_label()
        
    def _on_search_focus_out(self, event):
        self._search_suggestions.setVisible(False)
        
    def update_preview_colors(self):
        PREVIEW_COLORS["roads"] = TOPIC_COLORS.get("roads", "#000000")
        PREVIEW_COLORS["water"] = TOPIC_COLORS.get("water", "#0094FF")
        PREVIEW_COLORS["vegetation"] = TOPIC_COLORS.get("vegetation", "#00D921")
        PREVIEW_COLORS["buildings"] = TOPIC_COLORS.get("buildings", "#898989")
        for layer in ("roads", "water", "vegetation", "buildings"):
            if layer in self._osm_preview_edges:
                self._clear_osm_preview(layer)
                if layer == "roads":
                    self._draw_osm_preview_roads(list(self._osm_preview_edges["roads"].keys()))
                elif layer == "water":
                    self._draw_osm_preview_water()
                elif layer == "vegetation":
                    self._draw_osm_preview_vegetation()
                elif layer == "buildings":
                    self._draw_osm_preview_buildings()
                    
    def _draw_vertices(self):
        for item in self._vertex_items:
            self._scene.removeItem(item)
        self._vertex_items = []
        if self._shape_kind is None:
            return
        pen = QPen(QColor(0, 150, 60))
        brush = QBrush(QColor(0, 150, 60))
        r = 6 / self._zoom_scale_factor()
        p = self._shape_params
        if self._shape_kind == "rect":
            corners = [
                (p["west"], p["north"]),
                (p["east"], p["north"]),
                (p["east"], p["south"]),
                (p["west"], p["south"]),
            ]
        elif self._shape_kind == "polygon":
            corners = p["points"]
        else:
            corners = []
        for lon, lat in corners:
            scene_pt = self._lonlat_to_scene(lon, lat)
            item = self._scene.addEllipse(
                scene_pt.x()-r, scene_pt.y()-r, 2*r, 2*r, pen, brush
            )
            item.setZValue(10)
            item.setData(0, {"lon": lon, "lat": lat})
            self._vertex_items.append(item)
            
    def _redraw_shape(self):
        if self._current_shape_item is not None:
            self._scene.removeItem(self._current_shape_item)
            self._current_shape_item = None
        pen = QPen(QColor(0, 150, 60), 2 / self._zoom_scale_factor())
        brush = QBrush(QColor(0, 150, 60, 60))
        p = self._shape_params
        if self._shape_kind == "rect":
            tl = self._lonlat_to_scene(p["west"], p["north"])
            br = self._lonlat_to_scene(p["east"], p["south"])
            self._current_shape_item = self._scene.addRect(QRectF(tl, br), pen, brush)
        elif self._shape_kind == "polygon":
            pts = [self._lonlat_to_scene(lon, lat) for lon, lat in p["points"]]
            self._current_shape_item = self._scene.addPolygon(QPolygonF(pts), pen, brush)
            
    def _highlight_vertex(self, index):
        if index < len(self._vertex_items):
            item = self._vertex_items[index]
            item.setBrush(QBrush(QColor(255, 100, 0)))

    def _unhighlight_vertex(self):
        for item in self._vertex_items:
            item.setBrush(QBrush(QColor(0, 150, 60)))
            
    def _on_shape_changed_internal(self):
        win = QApplication.activeWindow()
        if hasattr(win, 'right_panel'):
            win.right_panel._on_shape_changed()
            
    def set_gpx_tracks(self, gpx_list):
        for item in self._gpx_items:
            self._scene.removeItem(item)
        self._gpx_items = []
        for gpx in gpx_list:
            if not gpx.get("enabled"):
                continue
            path = gpx.get("path", "")
            color = gpx.get("color", "#FF0000")
            if not path or not os.path.exists(path):
                continue
            points = self._parse_gpx(path)
            if len(points) < 2:
                continue
            pen = QPen(QColor(color))
            pen.setWidth(0)
            for i in range(len(points) - 1):
                p1 = self._lonlat_to_scene(points[i][0], points[i][1])
                p2 = self._lonlat_to_scene(points[i+1][0], points[i+1][1])
                item = self._scene.addLine(p1.x(), p1.y(), p2.x(), p2.y(), pen)
                item.setZValue(7)
                self._gpx_items.append(item)

    def _parse_gpx(self, path):
        tree = ET.parse(path)
        root = tree.getroot()
        ns = {'gpx': 'http://www.topografix.com/GPX/1/1'}
        points = []
        for trkpt in root.findall('.//gpx:trkpt', ns):
            lat = float(trkpt.attrib['lat'])
            lon = float(trkpt.attrib['lon'])
            points.append((lon, lat))
        return points
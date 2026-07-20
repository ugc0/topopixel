from logger import log
import os
import math
import sys
import traceback
import json
import copy
import platform
import base64
from app_icon import ICON_BASE64
import ctypes

if platform.system() == "Linux":
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.system("clear")
else:
    os.system("cls")

def excepthook(type, value, tb):
    log("".join(traceback.format_exception(type, value, tb)))

sys.excepthook = excepthook

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QLabel, QTextEdit, QMessageBox, QButtonGroup, QRadioButton,
    QFormLayout, QFrame, QSizePolicy, QTabWidget, QGroupBox, QCheckBox, QGridLayout, 
    QFileDialog, QSpinBox, QDoubleSpinBox, QLineEdit, QSlider, QGraphicsOpacityEffect, QInputDialog, QDialog,
    QListWidget, QStackedWidget, QColorDialog, QScrollArea, QDialogButtonBox
)
from PyQt6.QtGui import QIcon, QPixmap, QGuiApplication, QMovie, QDesktopServices, QColor
from PyQt6.QtCore import Qt, qInstallMessageHandler, QTimer, QPropertyAnimation, QUrl, pyqtSignal, QSettings

from map_canvas import MapCanvas
from param_panel import ParamPanel, DEFAULT_ROAD_LEVELS_CHECKED
from generation_worker import GenerationWorker
from stl_viewer import StlViewerPanel
from preview_worker import PreviewWorker, PreviewWorkerAll
from overpass_status_widget import OverpassStatusWidget
from constants import TOPIC_COLORS, checkbox_style, color_button_style, toggle_button_style
from shape_edit_dialog import ShapeEditDialog
from monuments_library import save_monument_entry, set_monument_active, get_monument_entry, set_monument_scale, set_monument_rotation, remove_monument_entry
import topopixel as tp

DEFAULT_MAP = (3.26, 46.92, 14)

RIGHT_PANEL_STYLESHEET = """
QWidget#rightPanel {
    background-color: #2B2B2B;
}
QLabel#sectionHint {
    color: #999999;
    font-size: 11px;
}
QRadioButton {
    padding: 3px 0;
    color: #DDDDDD;
}
QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 1.5px solid #ADB5BD;
    background-color: #3C3C3C;
}
QRadioButton::indicator:checked {
    background-color: #4C6EF5;
    border: 1.5px solid #4C6EF5;
}
QPushButton {
    background-color: #3C3C3C;
    border: 1px solid #555555;
    border-radius: 6px;
    padding: 6px 10px;
    color: #DDDDDD;
}
QPushButton:hover {
    background-color: #4A4A4A ;
}
QPushButton:pressed {
    background-color: #2F2F2F;
}
QPushButton#generateButton {
    background-color: #2F9E44;
    color: #FFFFFF;
    font-weight: bold;
    font-size: 13px;
    border: none;
    border-radius: 8px;
}
QPushButton#generateButton:hover {
    background-color: #2B8A3E;
}
QPushButton#generateButton:pressed {
    background-color: #237032;
}
QPushButton#generateButton:disabled {
    background-color: #ADB5BD;
}
QTextEdit {
    background-color: #212529;
    color: #E9ECEF;
    border: none;
    border-radius: 6px;
    font-family: Consolas, "Courier New", monospace;
    font-size: 11px;
    padding: 6px;
}
QLabel#kindLabel {
    font-weight: bold;
    color: #1971C2;
}
"""

class OptionsDialog(QDialog):

    sources_changed = pyqtSignal()

    DEFAULT_PARAM_SPECS = [
        ("RESOLUTION_M", "Résolution DEM (m/px)", "int", 5, 1, 30, 1, "general"),
        ("SIZE_MM", "Taille impression (mm)", "int", 120, 10, 500, 1, "general"),
        ("BASE_THICKNESS", "Épaisseur socle", "int", 20, 1, 200, 1, "general"),
        ("Z_SCALE", "Exagération verticale", "float", 1.0, 0.1, 5.0, 0.1, "general"),
        ("PUZZLE_N_PIECES", "Nombre de pièces puzzle", "int", 0, 0, 16, 1, "general"),
        ("INCLUDE_RAILWAYS", "Inclure les voies ferrées", "bool", False, None, None, None, "roads"),
        ("ROAD_WIDTH_PX", "Largeur route (px)", "int", 1, 1, 20, 1, "roads"),
        ("ROAD_HEIGHT", "Surélévation route", "int", 6, 0, 50, 1, "roads"),
        ("ROADS_Z_BOT_RATIO_PCT", "Profondeur socle routes (%)", "int", 33, 0, 100, 1, "roads"),
        ("RIVER_WIDTH_PX", "Largeur cours d'eau (px)", "int", 3, 1, 30, 1, "water"),
        ("WATER_HEIGHT", "Surélévation eau", "int", 3, 0, 50, 1, "water"),
        ("MIN_WATER_AREA_M2", "Surface min plan d'eau (m²)", "int", 5000, 0, 1_000_000, 100, "water"),
        ("MIN_WATERWAY_LENGTH_M", "Longueur min cours d'eau (m)", "int", 500, 0, 100_000, 50, "water"),
        ("WATER_Z_BOT_RATIO_PCT", "Profondeur socle eau (%)", "int", 33, 0, 100, 1, "water"),
        ("ENABLE_BATHYMETRY", "Activer bathymétrie océan", "bool", False, None, None, None, "water"),
        ("TREE_HEIGHT", "Hauteur arbre", "int", 3, 1, 50, 1, "vegetation"),
        ("TREE_RADIUS", "Rayon base arbre", "int", 1, 1, 20, 1, "vegetation"),
        ("TREE_DENSITY", "Densité arbres (‰ par px²)", "int", 8, 0, 500, 1, "vegetation"),
        ("VEG_Z_BOT_RATIO_PCT", "Profondeur socle végétation (%)", "int", 90, 0, 100, 1, "vegetation"),
        ("MIN_BUILDING_AREA_M2", "Surface min bâtiment (m²)", "int", 250, 0, 100_000, 10, "buildings"),
        ("DEFAULT_BUILDING_HEIGHT_M", "Hauteur par défaut (m)", "int", 6, 1, 200, 1, "buildings"),
        ("METERS_PER_LEVEL", "Mètres par étage", "int", 3, 1, 10, 1, "buildings"),
        ("BUILDING_HEIGHT_SCALE", "Échelle hauteur bâtiment", "int", 10, 1, 100, 1, "buildings"),
        ("BUILDING_MIN_HEIGHT", "Hauteur min (modèle)", "int", 2, 0, 100, 1, "buildings"),
        ("BUILDING_MAX_HEIGHT", "Hauteur max (modèle)", "int", 20, 0, 200, 1, "buildings"),
        ("BUILDINGS_Z_BOT_RATIO_PCT", "Profondeur socle bâtiments (%)", "int", 90, 0, 100, 1, "buildings"),
        ("GPX_WIDTH_PX", "Largeur tracé (px)", "int", 2, 1, 20, 1, "gpx"),
        ("GPX_HEIGHT", "Hauteur au-dessus", "int", 4, 0, 50, 1, "gpx"),
        ("GPX_Z_BOT_RATIO_PCT", "Profondeur socle GPX (%)", "int", 95, 0, 100, 1, "gpx"),
    ]

    LAYERS = {
        "terrain":    ("Terrain",     "#FFFFFF"),
        "roads":      ("Routes",      "#000000"),
        "water":      ("Eau",         "#0094FF"),
        "vegetation": ("Végétation",  "#00D921"),
        "trees":      ("Arbres",      "#006921"),
        "buildings":  ("Bâtiments",   "#898989"),
        "gpx":        ("Tracés GPX",  "#FF0000"),
    }

    SECTION_TITLES = {
        "general": "Général",
        "water": "Hydrographie",
    }
    
    GROUP_BASE_COLOR = {"general": "#9C36B5"}
    
    SATELLITE_CALIBRATION_LAYERS = ["water", "vegetation", "buildings", "trees"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Options")
        self.setMinimumSize(720, 520)

        self.setStyleSheet("""
            QCheckBox::indicator {
                width: 14px;
                height: 14px;
                border: 2px solid #FFFFFF;
                border-radius: 3px;
                background: transparent;
            }
            QCheckBox::indicator:checked {
                background: #BBBBBB;
                border: 2px solid #EEEEEE;
            }
        """)

        self._default_fields = {}
        self._layer_checks = {}
        self._color_buttons = {}

        outer = QVBoxLayout(self)

        row = QHBoxLayout()
        outer.addLayout(row, stretch=1)

        self._nav = QListWidget()
        self._nav.setFixedWidth(160)
        self._nav.addItems(["Général", "Paramètres par défaut", "Visuel", "Puzzle", "Sources"])
        row.addWidget(self._nav)

        self._stack = QStackedWidget()
        row.addWidget(self._stack, stretch=1)

        general_page = self._build_general_page()
        visual_page = self._build_visual_page()
        defaults_page = self._build_defaults_page()
        puzzle_page = self._build_puzzle_page()
        sources_page = self._build_sources_page()

        self._stack.addWidget(general_page)
        self._stack.addWidget(defaults_page)
        self._stack.addWidget(visual_page)
        self._stack.addWidget(puzzle_page)
        self._stack.addWidget(sources_page)

        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)

        self._snapshot = None
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        outer.addWidget(button_box)
        
        self._settings = QSettings("TopoPixel", "TopoPixel")
        self._load_persisted_values()

    def _tint_color(self, hex_color, amount=0.85):
        hex_color = hex_color.lstrip("#")
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        r = int(r + (255 - r) * amount)
        g = int(g + (255 - g) * amount)
        b = int(b + (255 - b) * amount)
        return f"#{r:02X}{g:02X}{b:02X}"

    def _wrap_scroll(self, widget):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(widget)
        return scroll

    def _topic_header(self, group_key):
        title = self.SECTION_TITLES.get(group_key) or self.LAYERS[group_key][0]
        color = self._color_buttons[group_key]._current_color if group_key in self._color_buttons else "#495057"
        label = QLabel(title)
        label.setStyleSheet(f"""
            background: {color};
            color: #FFFFFF;
            font-weight: bold;
            padding: 4px 8px;
            border-radius: 3px;
        """)
        return label

    def _section_label(self, text):
        label = QLabel(text)
        label.setStyleSheet("font-size: 15px; font-weight: bold; margin-top: 8px;")
        return label

    def _build_general_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        layout.addLayout(form)

        self._cache_dir = QLineEdit("cache")
        form.addRow("Dossier cache DEM", self._cache_dir)

        self._stl_dir = QLineEdit("STL")
        form.addRow("Dossier STL", self._stl_dir)

        self._gpxz_key = QLineEdit(tp.GPXZ_API_KEY)
        self._gpxz_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Clé API GPXZ", self._gpxz_key)
        
        self._cdse_access_key = QLineEdit("")
        form.addRow("Clé d'accès S3 CDSE", self._cdse_access_key)

        self._cdse_secret_key = QLineEdit("")
        self._cdse_secret_key.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("Clé secrète S3 CDSE", self._cdse_secret_key)

        form.addRow(self._section_label("Gestion du cache"))
        self._btn_clear_dem_cache = QPushButton("Effacer le cache GPXZ (calcul en cours...)")
        self._btn_clear_dem_cache.clicked.connect(self._on_clear_dem_cache)
        form.addRow(self._btn_clear_dem_cache)

        self._btn_clear_osm_cache = QPushButton("Effacer le cache OSM (calcul en cours...)")
        self._btn_clear_osm_cache.clicked.connect(self._on_clear_osm_cache)
        form.addRow(self._btn_clear_osm_cache)
        
        self._btn_clear_landcover_cache = QPushButton("Effacer le cache Landcover (calcul en cours...)")
        self._btn_clear_landcover_cache.clicked.connect(self._on_clear_landcover_cache)
        form.addRow(self._btn_clear_landcover_cache)

        self._btn_clear_satellite_cache = QPushButton("Effacer le cache Satellite (calcul en cours...)")
        self._btn_clear_satellite_cache.clicked.connect(self._on_clear_satellite_cache)
        form.addRow(self._btn_clear_satellite_cache)

        self._update_cache_sizes()
        
        layout.addStretch(1)
        
        form.addRow(self._section_label("Couches activées par défaut"))
        for layer, (label, color) in self.LAYERS.items():
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(checkbox_style(color))
            self._layer_checks[layer] = cb
            form.addRow(cb)
        
        return page

    def _build_defaults_page(self):
        content = QWidget()
        form = QFormLayout(content)

        seen_groups = []
        specs_by_group = {}
        for spec in self.DEFAULT_PARAM_SPECS:
            group = spec[7]
            if group not in specs_by_group:
                specs_by_group[group] = []
                seen_groups.append(group)
            specs_by_group[group].append(spec)

        for group in seen_groups:
            form.addRow(self._topic_header(group))
            base_color = self.LAYERS[group][1] if group in self.LAYERS else self.GROUP_BASE_COLOR.get(group, "#868E96")
            text_color = self._tint_color(base_color, amount=0.55)
            for key, label, kind, default, lo, hi, step, _ in specs_by_group[group]:
                if kind == "int":
                    w = QSpinBox()
                    w.setRange(lo, hi)
                    w.setSingleStep(step)
                    w.setValue(default)
                elif kind == "float":
                    w = QDoubleSpinBox()
                    w.setRange(lo, hi)
                    w.setSingleStep(step)
                    w.setDecimals(2)
                    w.setValue(default)
                else:
                    w = QCheckBox()
                    w.setChecked(default)
                self._default_fields[key] = w
                label_widget = QLabel(label)
                label_widget.setStyleSheet(f"color: {text_color}; background: transparent;")
                form.addRow(label_widget, w)
                
        return self._wrap_scroll(content)

    def _on_pick_default_color(self, key, btn):
        dialog = QColorDialog(QColor(btn._current_color), self)
        if dialog.exec():
            color = dialog.selectedColor().name()
            btn._current_color = color
            btn.setStyleSheet(color_button_style(color))

    def _build_visual_page(self):
        page = QWidget()
        form = QFormLayout(page)

        self._default_explode_factor = QDoubleSpinBox()
        self._default_explode_factor.setRange(0.0, 2.0)
        self._default_explode_factor.setSingleStep(0.05)
        self._default_explode_factor.setValue(1.5)
        form.addRow("Éclatement puzzle par défaut (visualisateur)", self._default_explode_factor)

        form.addRow(self._section_label("Couleurs par défaut"))
        for key, (label, color) in self.LAYERS.items():
            btn = QPushButton()
            btn.setFixedSize(28, 20)
            btn.setStyleSheet(color_button_style(color))
            btn._current_color = color
            btn.clicked.connect(lambda _, k=key, b=btn: self._on_pick_default_color(k, b))
            self._color_buttons[key] = btn
            form.addRow(label, btn)

        return page

    def _build_puzzle_page(self):
        page = QWidget()
        form = QFormLayout(page)

        self._merge_small_pieces = QCheckBox()
        self._merge_small_pieces.setChecked(True)
        form.addRow("Fusionner les pièces trop petites", self._merge_small_pieces)

        self._puzzle_gap_mm = QDoubleSpinBox()
        self._puzzle_gap_mm.setRange(0.0, 20.0)
        self._puzzle_gap_mm.setSingleStep(0.5)
        self._puzzle_gap_mm.setValue(2.0)
        form.addRow("Écart d'impression entre pièces (mm)", self._puzzle_gap_mm)

        self._puzzle_tab_radius_pct = QSpinBox()
        self._puzzle_tab_radius_pct.setRange(5, 30)
        self._puzzle_tab_radius_pct.setValue(14)
        form.addRow("Taille des tenons/encoches (% de la cellule)", self._puzzle_tab_radius_pct)

        return page

    def _update_source_checkboxes_enabled(self):
        boxes = (self._cb_source_osm, self._cb_source_landcover, self._cb_source_satellite)
        checked = [cb for cb in boxes if cb.isChecked()]
        only_one_left = len(checked) == 1
        for cb in boxes:
            cb.setEnabled(not (only_one_left and cb.isChecked()))

    def _on_source_toggled(self, _state):
        self._update_source_checkboxes_enabled()

    def _add_calibration_row(self, layer, color, nir, threshold):
        row = _CalibrationRow(color, nir, threshold)
        row.removed.connect(lambda r, l=layer: self._remove_calibration_row(l, r))
        self._calibration_layouts[layer].addWidget(row)
        self._calibration_rows[layer].append(row)
        self._update_calibration_remove_buttons(layer)

    def _remove_calibration_row(self, layer, row):
        self._calibration_rows[layer].remove(row)
        row.setParent(None)
        row.deleteLater()
        self._update_calibration_remove_buttons(layer)

    def _update_calibration_remove_buttons(self, layer):
        rows = self._calibration_rows[layer]
        only_one_left = len(rows) == 1
        for row in rows:
            row.set_remove_enabled(not only_one_left)

    def _build_sources_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        layout.addLayout(form)

        form.addRow(self._section_label("Sources de données pour combler le terrain"))

        self._cb_source_osm = QCheckBox("OpenStreetMap")
        self._cb_source_osm.setChecked(True)
        self._cb_source_landcover = QCheckBox("Landcover satellite (Copernicus LCFM)")
        self._cb_source_satellite = QCheckBox("Photo satellite (clustering couleur)")

        for cb in (self._cb_source_osm, self._cb_source_landcover, self._cb_source_satellite):
            cb.stateChanged.connect(self._on_source_toggled)
            form.addRow(cb)
            
        self._update_source_checkboxes_enabled()

        form.addRow(self._section_label("Calibration satellite : couleurs de référence par couche"))

        self._calibration_rows = {layer: [] for layer in self.SATELLITE_CALIBRATION_LAYERS}
        self._calibration_layouts = {}

        for layer in self.SATELLITE_CALIBRATION_LAYERS:
            label, _ = self.LAYERS[layer]
            form.addRow(self._topic_header(layer))

            container = QVBoxLayout()
            container.setSpacing(6)
            self._calibration_layouts[layer] = container
            container_widget = QWidget()
            container_widget.setLayout(container)
            form.addRow(container_widget)

            btn_add = QPushButton(f"+ Ajouter une couleur ({label})")
            btn_add.clicked.connect(lambda _, l=layer: self._add_calibration_row(l, (128, 128, 128), 128, 30))
            form.addRow(btn_add)

        for layer, refs in tp.DEFAULT_SATELLITE_CALIBRATION.items():
            for ref in refs:
                self._add_calibration_row(layer, tuple(ref["color"]), ref.get("nir", 128), ref["threshold"])

        layout.addStretch(1)
        return self._wrap_scroll(page)

    def get_values(self):
        result = {
            "CACHE_DIR": self._cache_dir.text(),
            "STL_DIR": self._stl_dir.text(),
            "GPXZ_API_KEY": self._gpxz_key.text(),
            "CDSE_ACCESS_KEY": self._cdse_access_key.text(),
            "CDSE_SECRET_KEY": self._cdse_secret_key.text(),
            "DEFAULT_EXPLODE_FACTOR": self._default_explode_factor.value(),
            "PUZZLE_MERGE_SMALL_PIECES": self._merge_small_pieces.isChecked(),
            "PUZZLE_GAP_MM": self._puzzle_gap_mm.value(),
            "PUZZLE_TAB_RADIUS_PCT": self._puzzle_tab_radius_pct.value(),
            "USE_OSM": self._cb_source_osm.isChecked(),
            "USE_LANDCOVER": self._cb_source_landcover.isChecked(),
            "USE_SATELLITE": self._cb_source_satellite.isChecked(),
        }
        for key, widget in self._default_fields.items():
            if isinstance(widget, QCheckBox):
                result[f"DEFAULT_{key}"] = widget.isChecked()
            else:
                result[f"DEFAULT_{key}"] = widget.value()
        result["DEFAULT_ENABLED_LAYERS"] = [l for l, cb in self._layer_checks.items() if cb.isChecked()]
        result["DEFAULT_COLORS"] = {k: btn._current_color for k, btn in self._color_buttons.items()}
        result["SATELLITE_CALIBRATION"] = {
            layer: [row.get_value() for row in rows]
            for layer, rows in self._calibration_rows.items()
        }
        return result

    def showEvent(self, event):
        super().showEvent(event)
        self._snapshot = self.get_values()

    def accept(self):
        new_values = self.get_values()
        source_keys = ("USE_OSM", "USE_LANDCOVER", "USE_SATELLITE")
        if self._snapshot is not None and any(
            self._snapshot.get(k) != new_values.get(k) for k in source_keys
        ):
            self.sources_changed.emit()
        self._save_persisted_values()
        super().accept()

    def reject(self):
        if self._snapshot is not None:
            self.set_values(self._snapshot)
        super().reject()

    def set_values(self, values):
        self._cache_dir.setText(values.get("CACHE_DIR", self._cache_dir.text()))
        self._stl_dir.setText(values.get("STL_DIR", self._stl_dir.text()))
        self._gpxz_key.setText(values.get("GPXZ_API_KEY", self._gpxz_key.text()))
        self._cdse_access_key.setText(values.get("CDSE_ACCESS_KEY", self._cdse_access_key.text()))
        self._cdse_secret_key.setText(values.get("CDSE_SECRET_KEY", self._cdse_secret_key.text()))
        self._default_explode_factor.setValue(values.get("DEFAULT_EXPLODE_FACTOR", self._default_explode_factor.value()))
        self._merge_small_pieces.setChecked(values.get("PUZZLE_MERGE_SMALL_PIECES", self._merge_small_pieces.isChecked()))
        self._puzzle_gap_mm.setValue(values.get("PUZZLE_GAP_MM", self._puzzle_gap_mm.value()))
        self._puzzle_tab_radius_pct.setValue(values.get("PUZZLE_TAB_RADIUS_PCT", self._puzzle_tab_radius_pct.value()))

        for key, widget in self._default_fields.items():
            value = values.get(f"DEFAULT_{key}")
            if value is None:
                continue
            if isinstance(widget, QCheckBox):
                widget.setChecked(value)
            else:
                widget.setValue(value)

        enabled_layers = values.get("DEFAULT_ENABLED_LAYERS")
        if enabled_layers is not None:
            for layer, cb in self._layer_checks.items():
                cb.setChecked(layer in enabled_layers)

        colors = values.get("DEFAULT_COLORS")
        if colors is not None:
            for key, btn in self._color_buttons.items():
                if key in colors:
                    btn._current_color = colors[key]
                    btn.setStyleSheet(color_button_style(colors[key]))
                    
        for cb, key in (
            (self._cb_source_osm, "USE_OSM"),
            (self._cb_source_landcover, "USE_LANDCOVER"),
            (self._cb_source_satellite, "USE_SATELLITE"),
        ):
            cb.blockSignals(True)
            cb.setChecked(values.get(key, cb.isChecked()))
            cb.blockSignals(False)
        self._update_source_checkboxes_enabled()

        calibration = values.get("SATELLITE_CALIBRATION")
        if calibration is not None:
            for layer in self.SATELLITE_CALIBRATION_LAYERS:
                for row in list(self._calibration_rows[layer]):
                    self._remove_calibration_row(layer, row)
                for ref in calibration.get(layer, []):
                    self._add_calibration_row(layer, tuple(ref["color"]), ref.get("nir", 128), ref["threshold"])

    def _load_persisted_values(self):
        raw = self._settings.value("options_values", "")
        if raw:
            try:
                values = json.loads(raw)
                self.set_values(values)
            except Exception as e:
                log(f"[OPTIONS échec parsing/set_values : {e}")

    def _save_persisted_values(self):
        values = self.get_values()
        self._settings.setValue("options_values", json.dumps(values))
        self._settings.sync()

    def _dir_size(self, path):
        if not os.path.isdir(path):
            return 0
        total = 0
        for dirpath, _, filenames in os.walk(path):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except:
                    pass
        return total

    def _get_dem_cache_size(self):
        cache_dir = self._cache_dir.text()
        if not os.path.isdir(cache_dir):
            return 0
        total = 0
        for entry in os.listdir(cache_dir):
            path = os.path.join(cache_dir, entry)
            if entry == "osm":
                continue
            if os.path.isfile(path):
                try:
                    total += os.path.getsize(path)
                except:
                    pass
            elif os.path.isdir(path):
                total += self._dir_size(path)
        return total

    def _get_osm_cache_size(self):
        return self._dir_size(os.path.join(self._cache_dir.text(), "osm"))

    @staticmethod
    def _format_size(size):
        if size < 1024 * 1024:
            return f"{size // 1024} Ko"
        return f"{size / (1024*1024):.1f} Mo"

    def _update_cache_sizes(self):
        self._btn_clear_dem_cache.setText(f"Effacer le cache GPXZ ({self._format_size(self._get_dem_cache_size())})")
        self._btn_clear_osm_cache.setText(f"Effacer le cache OSM ({self._format_size(self._dir_size(os.path.join(self._cache_dir.text(), 'osm')))})")
        self._btn_clear_landcover_cache.setText(f"Effacer le cache Landcover ({self._format_size(self._dir_size(os.path.join(self._cache_dir.text(), 'landcover')))})")
        self._btn_clear_satellite_cache.setText(f"Effacer le cache Satellite ({self._format_size(self._dir_size(os.path.join(self._cache_dir.text(), 'satellite')))})")

    def _on_clear_dem_cache(self):
        cache_dir = self._cache_dir.text()
        if not os.path.isdir(cache_dir):
            self._update_cache_sizes()
            return
        entries = [e for e in os.listdir(cache_dir) if e != "osm"]
        if entries:
            confirm = QMessageBox.question(
                self,
                "Confirmation",
                "Effacer le cache GPXZ (données d'élévation) ? Cette action est irréversible.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            for entry in entries:
                path = os.path.join(cache_dir, entry)
                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)
        self._update_cache_sizes()

    def _on_clear_osm_cache(self):
        osm_dir = os.path.join(self._cache_dir.text(), "osm")
        is_empty = not os.path.isdir(osm_dir) or not os.listdir(osm_dir)
        if not is_empty:
            confirm = QMessageBox.question(
                self,
                "Confirmation",
                "Effacer le cache OSM (routes, eau, végétation, bâtiments) ? Cette action est irréversible.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            shutil.rmtree(osm_dir)
            os.makedirs(osm_dir)
        self._update_cache_sizes()
        
    def _on_clear_landcover_cache(self):
        self._clear_named_cache_dir("landcover", "Effacer le cache Landcover (Copernicus LCFM) ? Cette action est irréversible.")

    def _on_clear_satellite_cache(self):
        self._clear_named_cache_dir("satellite", "Effacer le cache Satellite (composites Sentinel-2) ? Cette action est irréversible.")

    def _clear_named_cache_dir(self, name, message):
        target_dir = os.path.join(self._cache_dir.text(), name)
        is_empty = not os.path.isdir(target_dir) or not os.listdir(target_dir)
        if not is_empty:
            confirm = QMessageBox.question(
                self, "Confirmation", message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
            if confirm != QMessageBox.StandardButton.Yes:
                return
            shutil.rmtree(target_dir)
            os.makedirs(target_dir)
        self._update_cache_sizes()

class _CalibrationRow(QWidget):
    removed = pyqtSignal(object)

    def __init__(self, color, nir, threshold, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(8)

        self._color = tuple(color)
        self._btn_color = QPushButton()
        self._btn_color.setFixedSize(24, 20)
        self._btn_color.setStyleSheet(color_button_style(self._hex()))
        self._btn_color.clicked.connect(self._pick_color)
        layout.addWidget(self._btn_color)

        layout.addWidget(QLabel("NIR"))
        self._nir = QSpinBox()
        self._nir.setRange(0, 255)
        self._nir.setValue(nir)
        layout.addWidget(self._nir)

        layout.addWidget(QLabel("seuil"))
        self._threshold = QSpinBox()
        self._threshold.setRange(1, 400)
        self._threshold.setValue(threshold)
        layout.addWidget(self._threshold)

        self._btn_remove = QPushButton("✕")
        self._btn_remove.setFixedSize(28, 28)
        self._btn_remove.setStyleSheet("""
            QPushButton {
                font-size: 16px;
                font-weight: bold;
                color: #E03131;
                border: none;
            }
            QPushButton:disabled {
                color: #888888;
            }
        """)
        self._btn_remove.clicked.connect(lambda: self.removed.emit(self))
        layout.addWidget(self._btn_remove)
        
        layout.addStretch(1)

    def _hex(self):
        r, g, b = self._color
        return f"#{r:02X}{g:02X}{b:02X}"

    def _pick_color(self):
        dialog = QColorDialog(QColor(*self._color), self)
        if dialog.exec():
            c = dialog.selectedColor()
            self._color = (c.red(), c.green(), c.blue())
            self._btn_color.setStyleSheet(color_button_style(self._hex()))

    def get_value(self):
        return {"color": list(self._color), "nir": self._nir.value(), "threshold": self._threshold.value()}
        
    def set_remove_enabled(self, enabled):
        self._btn_remove.setEnabled(enabled)

class StaticSection(QFrame):

    def __init__(self, title, accent, hint=None, parent=None):
        super().__init__(parent)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(0)

        header = QLabel(title, self)
        header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.setStyleSheet(f"""
            background-color: {accent};
            color: #FFFFFF;
            border: none;
            border-radius: 8px;
            padding: 9px 12px;
            font-weight: bold;
            font-size: 13px;
        """)

        self.body = QWidget(self)
        self.body.setObjectName("sectionBody")
        self.body.setStyleSheet(f"""
            QWidget#sectionBody {{
                background-color: #2B2B2B;
                border: 2px solid {accent};
                border-top: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
            }}
        """)
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(12, 10, 12, 10)
        self.body_layout.setSpacing(6)

        if hint:
            hint_lbl = QLabel(hint)
            hint_lbl.setObjectName("sectionHint")
            hint_lbl.setWordWrap(True)
            self.body_layout.addWidget(hint_lbl)

        outer.addWidget(header)
        outer.addWidget(self.body)

    def add_widget(self, widget):
        self.body_layout.addWidget(widget)

    def add_layout(self, layout):
        self.body_layout.addLayout(layout)

class RightPanel(QWidget):

    cache_coverage_toggled = pyqtSignal(bool)
    osm_cache_coverage_toggled = pyqtSignal(bool)
    landcover_cache_coverage_toggled = pyqtSignal(bool)
    satellite_cache_coverage_toggled = pyqtSignal(bool)
    matrix_cell_toggled = pyqtSignal(str, str, bool)
    matrix_row_toggled = pyqtSignal(str, bool)
    matrix_column_toggled = pyqtSignal(str, bool)

    def __init__(self, canvas: MapCanvas, parent=None):
        super().__init__(parent)
        self.canvas = canvas
        self._worker = None
        self.setObjectName("rightPanel")
        self.setStyleSheet(RIGHT_PANEL_STYLESHEET)

        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)
        
        extent_box = StaticSection("Emprise du projet", accent="#1971C2")

        self._tool_group = QButtonGroup(self)
        self._rb_rect = QRadioButton("Rectangle")
        self._rb_circle = QRadioButton("Cercle")
        self._rb_hexagon = QRadioButton("Hexagone")
        self._rb_polygon = QRadioButton("Polygone")
        for rb in (self._rb_rect, self._rb_circle, self._rb_hexagon, self._rb_polygon):
            self._tool_group.addButton(rb)
            extent_box.add_widget(rb)

        self._rb_rect.toggled.connect(lambda c: c and self.canvas.set_draw_tool("rect"))
        self._rb_circle.toggled.connect(lambda c: c and self.canvas.set_draw_tool("circle"))
        self._rb_hexagon.toggled.connect(lambda c: c and self.canvas.set_draw_tool("hexagon"))
        self._rb_polygon.toggled.connect(lambda c: c and self.canvas.set_draw_tool("polygon"))

        self._stl_viewer = None

        self.clear_btn = QPushButton("Effacer l'emprise")
        self.clear_btn.setStyleSheet("""
QPushButton {
    background-color: #c0392b;
    color: white;
    border: none;
    padding: 6px 10px;
    border-radius: 4px;
}

QPushButton:hover:!disabled {
    background-color: #e74c3c;
}

QPushButton:pressed:!disabled {
    background-color: #a93226;
}

QPushButton:disabled {
    background-color: #7f8c8d;
    color: #bdc3c7;
}
""")
        self.clear_btn.clicked.connect(self._on_clear)
        self.clear_btn.hide()
        extent_box.add_widget(self.clear_btn)

        info_form = QFormLayout()
        self._lbl_kind = QLabel("—")
        self._lbl_kind.setObjectName("kindLabel")
        self._lbl_details = QLabel("Aucune emprise dessinée")
        self._lbl_details.setWordWrap(True)
        info_form.addRow("Type", self._lbl_kind)
        info_form.addRow("Détails", self._lbl_details)
        extent_box.add_layout(info_form)

        self._btn_edit_shape = QPushButton("✏️")
        self._btn_edit_shape.setVisible(False)
        self._btn_edit_shape.clicked.connect(self._on_edit_shape)
        extent_box.add_widget(self._btn_edit_shape)

        layout.addWidget(extent_box)

        self.canvas.shape_changed.connect(self._on_shape_changed)
        
        preview_section = StaticSection("Prévisualisation carte", accent="#1971C2")
        
        preview_grid = QGridLayout()
        preview_grid.setSpacing(4)
        preview_grid.setColumnMinimumWidth(0, 90)

        topics = ["roads", "water", "vegetation", "buildings"]
        sources = ["osm", "landcover", "satellite"]
        topic_labels = {"roads": "Routes", "water": "Eau", "vegetation": "Végétation", "buildings": "Bâtiments"}
        source_labels = {"osm": "OSM", "landcover": "Landcover", "satellite": "Satellite"}
        applicable_sources = {
            "roads": {"osm"},
            "water": {"osm", "landcover", "satellite"},
            "vegetation": {"osm", "landcover", "satellite"},
            "buildings": {"osm", "landcover", "satellite"},
        }

        self._matrix_state = {
            (topic, source): True
            for topic in topics
            for source in applicable_sources[topic]
        }
        self._matrix_cell_widgets = {}
        self._matrix_row_buttons = {}
        self._matrix_col_buttons = {}
        self._matrix_column_widgets = {source: [] for source in sources}
        self._preview_status = {topic: QLabel("—") for topic in topics}
        self._preview_loading = {topic: QLabel("") for topic in topics}

        preview_grid.addWidget(QLabel(""), 0, 0)
        for j, source in enumerate(sources):
            preview_grid.setColumnMinimumWidth(j + 1, 60)
            btn = QPushButton(source_labels[source])
            btn.setCheckable(True)
            btn.setChecked(True)
            btn.setStyleSheet(toggle_button_style("#555555", True))
            btn.clicked.connect(lambda checked, s=source: self.matrix_column_toggled.emit(s, checked))
            self._matrix_col_buttons[source] = btn
            preview_grid.addWidget(btn, 0, j + 1)
            self._matrix_column_widgets[source].append(btn)

        total_header = QLabel("Total")
        total_header.setAlignment(Qt.AlignmentFlag.AlignRight)
        preview_grid.addWidget(total_header, 0, len(sources) + 1)

        for i, topic in enumerate(topics):
            row = i + 1
            row_btn = QPushButton(topic_labels[topic])
            row_btn.setCheckable(True)
            row_btn.setChecked(True)
            row_btn.setStyleSheet(toggle_button_style(TOPIC_COLORS[topic], True))
            row_btn.clicked.connect(lambda checked, t=topic: self.matrix_row_toggled.emit(t, checked))
            self._matrix_row_buttons[topic] = row_btn
            preview_grid.addWidget(row_btn, row, 0)

            for j, source in enumerate(sources):
                if source not in applicable_sources[topic]:
                    dash = QLabel("—")
                    dash.setAlignment(Qt.AlignmentFlag.AlignCenter)
                    preview_grid.addWidget(dash, row, j + 1)
                    self._matrix_column_widgets[source].append(dash)
                    continue
                cb = QCheckBox()
                cb.setChecked(True)
                cb.setStyleSheet(checkbox_style(TOPIC_COLORS[topic]))
                cb.stateChanged.connect(lambda state, t=topic, s=source: self.matrix_cell_toggled.emit(t, s, state == Qt.CheckState.Checked.value))
                self._matrix_cell_widgets[(topic, source)] = cb
                cell_wrap = QHBoxLayout()
                cell_wrap.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell_widget = QWidget()
                cell_widget.setLayout(cell_wrap)
                cell_wrap.addWidget(cb)
                preview_grid.addWidget(cell_widget, row, j + 1)
                self._matrix_column_widgets[source].append(cell_widget)

            status_wrap = QHBoxLayout()
            status_wrap.setAlignment(Qt.AlignmentFlag.AlignRight)
            loading = self._preview_loading[topic]
            loading.setFixedWidth(16)
            status = self._preview_status[topic]
            status.setStyleSheet("color: #888; font-size: 11px;")
            status_widget = QWidget()
            status_wrap.addWidget(loading)
            status_wrap.addWidget(status)
            status_widget.setLayout(status_wrap)
            preview_grid.addWidget(status_widget, row, len(sources) + 1)

        cache_row = len(topics) + 1
        self._cache_section_expanded = False
        self._cache_toggle_btn = QPushButton("▸ Cache")
        self._cache_toggle_btn.setFlat(True)
        self._cache_toggle_btn.setStyleSheet("text-align: left; color: #999; font-size: 11px; border: none;")
        self._cache_toggle_btn.clicked.connect(self._on_cache_section_toggled)
        preview_grid.addWidget(self._cache_toggle_btn, cache_row, 0, 1, len(sources) + 2)

        self._cb_cache_coverage = QCheckBox("Cache DEM")
        self._cb_cache_coverage.setStyleSheet(checkbox_style("#9C36B5"))
        self._cb_osm_cache_coverage = QCheckBox("Cache OSM")
        self._cb_osm_cache_coverage.setStyleSheet(checkbox_style("#9C36B5"))
        self._cb_landcover_cache_coverage = QCheckBox("Cache Landcover")
        self._cb_landcover_cache_coverage.setStyleSheet(checkbox_style("#9C36B5"))
        self._cb_satellite_cache_coverage = QCheckBox("Cache Satellite")
        self._cb_satellite_cache_coverage.setStyleSheet(checkbox_style("#9C36B5"))

        preview_grid.addWidget(self._cb_cache_coverage, cache_row + 1, 0, 1, 2)
        preview_grid.addWidget(self._cb_osm_cache_coverage, cache_row + 2, 0, 1, 2)
        preview_grid.addWidget(self._cb_landcover_cache_coverage, cache_row + 3, 0, 1, 2)
        preview_grid.addWidget(self._cb_satellite_cache_coverage, cache_row + 4, 0, 1, 2)
        self._cb_cache_coverage.setVisible(False)
        self._cb_osm_cache_coverage.setVisible(False)
        self._cb_landcover_cache_coverage.setVisible(False)
        self._cb_satellite_cache_coverage.setVisible(False)

        self._cb_cache_coverage.toggled.connect(self.cache_coverage_toggled.emit)
        self._cb_osm_cache_coverage.toggled.connect(self.osm_cache_coverage_toggled.emit)
        self._cb_landcover_cache_coverage.toggled.connect(self.landcover_cache_coverage_toggled.emit)
        self._cb_satellite_cache_coverage.toggled.connect(self.satellite_cache_coverage_toggled.emit)

        preview_section.add_layout(preview_grid)
            
        self._btn_stop_preview = QPushButton("⏹ Interrompre")
        self._btn_stop_preview.setStyleSheet("""
            QPushButton {
                background-color: #E03131;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #C92A2A;
            }
            QPushButton:pressed {
                background-color: #B02020;
            }
        """)
        self._btn_stop_preview.setVisible(False)
        self._btn_stop_preview.clicked.connect(self._on_stop_preview)
        preview_section.add_widget(self._btn_stop_preview)
        
        self._btn_start_preview = QPushButton("▶ Lancer la prévisualisation")
        self._btn_start_preview.setStyleSheet("""
            QPushButton {
                background-color: #2F9E44;
                color: white;
                border: none;
                border-radius: 6px;
                padding: 6px 12px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2B8A3E;
            }
            QPushButton:pressed {
                background-color: #237032;
            }
        """)
        self._btn_start_preview.clicked.connect(self._on_start_preview_clicked)
        preview_section.add_widget(self._btn_start_preview)

        layout.addWidget(preview_section)

        gen_box = StaticSection("Génération", accent="#2F9E44")
        self._gen_button = QPushButton("Générer le modèle 3D")
        self._gen_button.setObjectName("generateButton")
        self._gen_button.setMinimumHeight(40)
        self._gen_button.clicked.connect(self._on_generate)
        self._gen_button.setVisible(False)
        gen_box.add_widget(self._gen_button)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setVisible(False)
        gen_box.add_widget(self._log)

        layout.addWidget(gen_box, stretch=1)
        
        self._param_panel_ref = None

    def set_param_panel(self, panel: ParamPanel):
        self._param_panel_ref = panel

    def _on_clear(self):
        self.canvas.clear_shape()
        self.clear_btn.hide()

    def _on_start_preview_clicked(self):
        win = self.window()
        if hasattr(win, '_on_shape_changed_preview'):
            win._on_shape_changed_preview()

    def _on_shape_changed(self):
        shape = self.canvas.current_shape()
        if shape is None:
            self._lbl_kind.setText("—")
            self._lbl_details.setText("Aucune emprise dessinée")
            self._gen_button.setVisible(False)
            return

        kind, params = shape
        if kind == "rect":
            if abs(params["east"] - params["west"]) < 0.001 or abs(params["north"] - params["south"]) < 0.001:
                self.canvas.clear_shape()
                self.canvas.set_draw_tool(kind)
                return
            self._lbl_details.setText(
                f"W={params['west']:.5f} E={params['east']:.5f}\n"
                f"S={params['south']:.5f} N={params['north']:.5f}\n"
                f"{params['width']:.0f} m x {params['height']:.0f} m\n"
                f"Superficie ≈ {params['area']:.0f} ha"
            )
            self._lbl_kind.setText(kind)
            self._tool_group.setExclusive(False)
            self._rb_rect.setChecked(False)
            self._tool_group.setExclusive(True)
            self.clear_btn.show()
            self._gen_button.setVisible(True)
        elif kind in ("circle","hexagon"):
            if params['radius_m']<30:
                self.canvas.clear_shape()
                self.canvas.set_draw_tool(kind)
                return
            self._lbl_details.setText(
                f"Centre lon={params['center_lon']:.5f} lat={params['center_lat']:.5f}\n"
                f"Rayon ≈ {params['radius_m']:.0f} m\n"
                f"Superficie ≈ {params['area']:.0f} ha"
            )
            self._lbl_kind.setText(kind)
            self._tool_group.setExclusive(False)
            self._rb_circle.setChecked(False)
            self._rb_hexagon.setChecked(False)
            self._tool_group.setExclusive(True)
            self.clear_btn.show()
            self._gen_button.setVisible(True)
        else:
            self._lbl_details.setText(
                f"Superficie ≈ {params['area']:.0f} ha"
            )
            self._lbl_kind.setText(kind)
            self._tool_group.setExclusive(False)
            self._rb_polygon.setChecked(False)
            self._tool_group.setExclusive(True)
            self.clear_btn.show()
            self._gen_button.setVisible(True)
            
        self._btn_edit_shape.setVisible(
            shape is not None and kind in ("rect", "polygon")
        )
            
    def _on_generate(self):
    
        win = self.window()
        if hasattr(win, '_gen_locked') and win._gen_locked:
            QMessageBox.warning(self, "Test en cours", "Test des endpoints en cours, veuillez patienter.")
            return
    
        shape = self.canvas.current_shape()
        if shape is None:
            QMessageBox.warning(self, "Emprise manquante",
                                "Dessine d'abord une emprise sur la carte (rectangle, cercle ou hexagone).")
            return
        if self._param_panel_ref is None:
            QMessageBox.critical(self, "Erreur interne", "Panneau de paramètres non lié.")
            return

        kind, params = shape
        ui_params = self._param_panel_ref.get_params()
        ui_params["EXCLUDED_IDS"] = self.canvas.get_excluded_ids()
        ui_params["EXCLUDED_FILL_FEATURES"] = list(self.canvas._tooltip._excluded_fill_features)
        ui_params["PROJECT_NAME"] = getattr(self.window(), '_project_name', 'topopixel')
        ui_params["GPX_LIST"] = self._param_panel_ref.get_gpx_list()

        self._log.clear()
        self._log.setVisible(True)
        self._btn_start_preview.setVisible(False)
        self._gen_button.setText("⏹ Interrompre la génération")
        self._gen_button.setStyleSheet("""
            QPushButton {
                background-color: #E03131;
                color: white;
                border: none;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #C92A2A; }
            QPushButton:pressed { background-color: #B02020; }
        """)
        self._gen_button.clicked.disconnect()
        self._gen_button.clicked.connect(self._on_interrupt_generation)

        self._worker = GenerationWorker(kind, params, ui_params, win._preview_workers)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_finished_ok)
        self._worker.failed.connect(self._on_failed)
        self._worker.start()

    def _on_progress(self, msg):
        self._log.append(msg)

    def _on_finished_ok(self, result):
        self._restore_gen_button()
        if hasattr(self, '_stl_viewer') and self._stl_viewer is not None:
            stl_dir = self._param_panel_ref.get_params().get("STL_DIR", "STL") if self._param_panel_ref else "STL"
            self._stl_viewer.reload_stl_folder(stl_dir)
            win = self.window()
            if hasattr(win, 'tabs'):
                win.tabs.setTabEnabled(1, True)
                win.tabs.setTabVisible(1, True)
            self._stl_viewer._btn_export_3mf.setVisible(True)
            self._stl_viewer._current_stl_dir = stl_dir
        win = self.window()
        if hasattr(win, 'tabs'):
            win.tabs.setCurrentIndex(1)
        else:
            log("Tab prévisualisation 3D non trouvé")

    def _on_failed(self, error_text):
        self._restore_gen_button()
        self._log.setVisible(True)
        self._log.append("ERREUR :\n" + error_text)
        QMessageBox.critical(self, "Échec de la génération","La génération a échoué. Voir le journal pour le détail.")

    def set_stl_viewer(self, viewer: "StlViewerPanel"):
        self._stl_viewer = viewer
                 
    def _on_cache_section_toggled(self):
        self._cache_section_expanded = not self._cache_section_expanded
        self._cache_toggle_btn.setText("▾ Cache" if self._cache_section_expanded else "▸ Cache")
        self._cb_cache_coverage.setVisible(self._cache_section_expanded)
        self._cb_osm_cache_coverage.setVisible(self._cache_section_expanded)
        self._cb_landcover_cache_coverage.setVisible(self._cache_section_expanded)
        self._cb_satellite_cache_coverage.setVisible(self._cache_section_expanded)
                 
    def set_source_columns_visible(self, use_osm, use_landcover, use_satellite):
        visibility = {"osm": use_osm, "landcover": use_landcover, "satellite": use_satellite}
        for source, widgets in self._matrix_column_widgets.items():
            for w in widgets:
                w.setVisible(visibility[source])
                 
    def _on_preview_layer_toggle(self, layer: str, state: int):
        visible = state == Qt.CheckState.Checked.value
        if visible:
            data = self.canvas._osm_preview_edges.get(layer)
            if data is not None:
                if layer == "roads":
                    road_levels = self._param_panel_ref.get_params()["ROAD_LEVELS"] if self._param_panel_ref else []
                    self.canvas.set_preview_roads(data)
                    self.canvas.update_preview_visibility(road_levels)
                elif layer == "water":
                    self.canvas.set_preview_water(data)
                elif layer == "vegetation":
                    self.canvas.set_preview_vegetation(data)
                elif layer == "buildings":
                    self.canvas.set_preview_buildings(data)
        else:
            self.canvas._clear_osm_preview(layer)
            
    def set_preview_layer_loading(self, layer: str):
        lbl = self._preview_loading.get(layer)
        if lbl:
            if not hasattr(self, '_loading_movie'):
                movie_path = os.path.join(os.path.dirname(__file__), "loading.gif")
                self._loading_movie = QMovie(movie_path)
                self._loading_movie.setScaledSize(__import__('PyQt6.QtCore', fromlist=['QSize']).QSize(16, 16))
                self._loading_movie.start()
            lbl.setMovie(self._loading_movie)
        self._preview_status[layer].setText("chargement...")
        
    def reset_preview_status(self):
        for layer in self._preview_status:
            self._preview_loading[layer].clear()
            self._preview_status[layer].setText("—")
    
    def set_preview_layer_done(self, layer: str, count: int):
        lbl = self._preview_loading.get(layer)
        if lbl:
            lbl.setMovie(None)
            lbl.clear()
        if count == 0:
            self._preview_status[layer].setText("aucun élément")
        else:
            self._preview_status[layer].setText(f"{count} éléments affichés")

    def _on_stop_preview(self):
        win = self.window()
        if hasattr(win, '_preview_workers'):
            old_workers = list(win._preview_workers.values())
            for worker in old_workers:
                if worker.isRunning():
                    worker.terminate()
                    worker.wait()
            for worker in old_workers:
                worker.deleteLater()
            win._preview_workers.clear()
        self.reset_preview_status()
        self._btn_stop_preview.setVisible(False)
        self._btn_start_preview.setVisible(True)
    
    def _on_interrupt_generation(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
        self._restore_gen_button()

    def _restore_gen_button(self):
        self._gen_button.setText("Générer le modèle 3D")
        self._gen_button.setStyleSheet("")
        self._gen_button.clicked.disconnect()
        self._gen_button.clicked.connect(self._on_generate)
        self._log.setVisible(False)
        win = self.window()
        if not (hasattr(win, '_preview_workers') and any(w.isRunning() for w in win._preview_workers.values())):
            self._btn_start_preview.setVisible(True)
        
    def _on_edit_shape(self):
        shape = self.canvas.current_shape()
        if shape is None:
            return
        kind, params = shape
        params_backup = copy.deepcopy(params)
        dialog = ShapeEditDialog(kind, params, self)
        dialog.vertex_focused.connect(self.canvas._highlight_vertex)
        dialog.vertex_unfocused.connect(self.canvas._unhighlight_vertex)
        dialog.coords_changed.connect(lambda p: self._apply_shape_preview(kind, p))
        if dialog.exec() == ShapeEditDialog.DialogCode.Accepted:
            result = dialog.get_result()
            p = result
            if kind == "rect":
                lat_mid = (p["north"] + p["south"]) / 2
                p["width"] = abs(p["east"] - p["west"]) * 111320 * math.cos(math.radians(lat_mid))
                p["height"] = abs(p["north"] - p["south"]) * 111320
                p["area"] = p["width"] * p["height"] / 10000
            elif kind == "polygon":
                poly = Polygon([(lon, lat) for lon, lat in p["points"]])
                gdf = gpd.GeoDataFrame(geometry=[poly], crs="EPSG:4326").to_crs("EPSG:3857")
                p["area"] = gdf.geometry.area.iloc[0] / 10000
            self.canvas._shape_params = p
            self.canvas._redraw_shape()
            self.canvas._draw_vertices()
            self.canvas.shape_changed.emit()
        else:
            self.canvas._shape_params = params_backup
            self.canvas._redraw_shape()
            self.canvas._draw_vertices()

    def _apply_shape_preview(self, kind, params):
        self.canvas._shape_params = params
        self.canvas._redraw_shape()
        self.canvas._draw_vertices()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Topopixel — Générateur de terrain 3D")
        icon_data = base64.b64decode(ICON_BASE64)
        pixmap = QPixmap()
        pixmap.loadFromData(icon_data)
        self.setWindowIcon(QIcon(pixmap))
        self.setStyleSheet("QMainWindow { background-color: #E9ECEF; }")
        
        menubar = self.menuBar()
        projet_menu = menubar.addMenu("Projet")
        projet_menu.setStyleSheet("QMenu { min-width: 220px; }")
        
        options_menu = menubar.addMenu("Options")
        action_options = options_menu.addAction("⚙️ Préférences...")
        action_options.setShortcut("Ctrl+,")
        action_options.triggered.connect(lambda: (self._options_dialog.show(), self._options_dialog.raise_(), self._options_dialog.activateWindow()))

        about_menu = menubar.addMenu("?")
        action_doc = about_menu.addAction("📖 Documentation (PDF en ligne)")
        action_doc.triggered.connect(lambda: QDesktopServices.openUrl(QUrl("https://github.com/ugc0/topopixel/blob/main/TopoPixel_Guide_Utilisateur.pdf")))
        
        action_new = projet_menu.addAction("➕ Nouveau projet")
        action_new.setShortcut("Ctrl+N")
        action_new.triggered.connect(self._on_new_project)
        
        action_load = projet_menu.addAction("📂 Charger un projet")
        action_load.setShortcut("Ctrl+O")
        action_load.triggered.connect(self._on_load_project)

        action_save = projet_menu.addAction("💾 Sauvegarder le projet")
        action_save.setShortcut("Ctrl+S")
        action_save.triggered.connect(self._on_save_project)

        action_save_as = projet_menu.addAction("💾 Sauvegarder sous...")
        action_save_as.setShortcut("Ctrl+Alt+S")
        action_save_as.triggered.connect(self._on_save_project_as)
        
        projet_menu.addSeparator()
        
        action_quit = projet_menu.addAction("Quitter")
        action_quit.setShortcut("Ctrl+Q")
        action_quit.triggered.connect(self._on_quit)

        central = QWidget(self)
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        top_widget = QWidget()
        layout = QHBoxLayout(top_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.param_panel = ParamPanel()
        self.param_panel.setMinimumWidth(300)
        self.param_panel.setMaximumWidth(380)
        
        self._options_dialog = OptionsDialog(self)
        self._options_dialog.sources_changed.connect(self._on_shape_changed_preview)
        self._options_dialog.sources_changed.connect(self._refresh_source_columns)
        self.param_panel.set_options_provider(self._options_dialog)
        self.param_panel._fields["RESOLUTION_M"].valueChanged.connect(
            lambda: self.right_panel.cache_coverage_toggled.emit(self.right_panel._cb_cache_coverage.isChecked())
        )

        self.map_canvas = MapCanvas()

        self.right_panel = RightPanel(self.map_canvas)
        self.right_panel.setMinimumWidth(380)
        self.right_panel.setMaximumWidth(460)
        self.right_panel.set_param_panel(self.param_panel)

        layout.addWidget(self.param_panel)
        layout.addWidget(self.map_canvas, stretch=1)
        layout.addWidget(self.right_panel)

        self.tabs = QTabWidget()
        self.tabs.addTab(top_widget, "Carte")

        self.stl_viewer = StlViewerPanel()
        self.tabs.addTab(self.stl_viewer, "Prévisualisation STL")
        self.tabs.setTabVisible(1, False)
        self.tabs.setTabEnabled(1, False)
        self.tabs.currentChanged.connect(self._on_tab_changed)

        main_layout.addWidget(self.tabs)
        
        stl_folder = self.param_panel.get_params().get("STL_DIR", "STL")
        has_stl = os.path.isdir(stl_folder) and any(
            f.endswith(".stl") for f in os.listdir(stl_folder)
        ) if os.path.isdir(stl_folder) else False
        
        self._overpass_status = OverpassStatusWidget()
        self._overpass_status.refresh()
        
        main_layout.addWidget(self._overpass_status)
        
        self.right_panel.set_stl_viewer(self.stl_viewer)
        
        self._apply_default_params_to_panel()
        
        self._refresh_source_columns()

        self._preview_worker = None
        self.map_canvas.shape_changed.connect(self._on_shape_changed_preview)
        self.param_panel.road_levels_changed.connect(self._on_road_levels_changed)
        self.param_panel.railway_option_changed.connect(self._on_railway_option_changed)
        self.param_panel.water_filter_changed.connect(self._on_water_filter_changed)
        self.param_panel.building_filter_changed.connect(self._on_building_filter_changed)
        self.param_panel.color_changed.connect(self._on_color_changed)
        self.map_canvas._tooltip.rotation_preview_changed.connect(self.map_canvas.show_monument_rotation_preview)
        self.map_canvas._tooltip.rotation_committed.connect(self._on_monument_rotation_committed)
        self.map_canvas._tooltip.scale_committed.connect(self._on_monument_scale_committed)
        self.map_canvas._tooltip.scale_preview_changed.connect(self.map_canvas.show_monument_scale_preview)
        self._overpass_status.strategy_changed.connect(self._on_overpass_ready)
        self.map_canvas._tooltip.exclusion_changed.connect(self._on_exclusion_changed_count)
        self.param_panel.gpx_changed.connect(self._on_gpx_changed)
        self.right_panel.cache_coverage_toggled.connect(self._on_cache_coverage_toggled)
        self.right_panel.osm_cache_coverage_toggled.connect(self._on_osm_cache_coverage_toggled)
        self.right_panel.landcover_cache_coverage_toggled.connect(self._on_landcover_cache_coverage_toggled)
        self.right_panel.satellite_cache_coverage_toggled.connect(self._on_satellite_cache_coverage_toggled)
        self.right_panel.matrix_cell_toggled.connect(self._on_matrix_cell_toggled)
        self.right_panel.matrix_row_toggled.connect(self._on_matrix_row_toggled)
        self.right_panel.matrix_column_toggled.connect(self._on_matrix_column_toggled)
        self.map_canvas._tooltip.monument_action.connect(self._on_monument_action)
        self.param_panel.layer_enabled_changed.connect(self._on_layer_enabled_changed)
        
        self.map_canvas.setEnabled(False)
        self._gen_locked = True
        
        self._toast = QLabel("", self)
        self._toast.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._toast.hide()

        self._toast_opacity = QGraphicsOpacityEffect(self._toast)
        self._toast.setGraphicsEffect(self._toast_opacity)

        self._toast_anim = QPropertyAnimation(self._toast_opacity, b"opacity")
        self._toast_anim.setDuration(400)
        self._toast_anim.finished.connect(self._on_toast_fade_finished)
        
    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            canvas = self.right_panel.canvas
            tool_selected = any([
                self.right_panel._rb_rect.isChecked(),
                self.right_panel._rb_circle.isChecked(),
                self.right_panel._rb_hexagon.isChecked(),
                self.right_panel._rb_polygon.isChecked(),
            ])
            drawing_in_progress = (
                canvas._draw_start_scene is not None or
                len(canvas._polygon_points) > 0
            )

            if not tool_selected:
                event.accept()
                return

            self.right_panel._tool_group.setExclusive(False)
            self.right_panel._rb_rect.setChecked(False)
            self.right_panel._rb_circle.setChecked(False)
            self.right_panel._rb_hexagon.setChecked(False)
            self.right_panel._rb_polygon.setChecked(False)
            self.right_panel._tool_group.setExclusive(True)
            canvas.set_draw_tool(None)
            canvas._polygon_points = []
            canvas._draw_shape_item = None
            canvas._draw_start_scene = None

            if drawing_in_progress and canvas._current_shape_item is not None:
                canvas._scene.removeItem(canvas._current_shape_item)
                canvas._current_shape_item = None

            if canvas._shape_kind is None or drawing_in_progress:
                canvas._shape_kind = None
                canvas._shape_params = None
                if canvas._current_shape_item is not None:
                    canvas._scene.removeItem(canvas._current_shape_item)
                    canvas._current_shape_item = None
                canvas.set_preview_opacity(has_preview=False)
                canvas.clear_all_osm_preview()
                canvas.shape_changed.emit()

            event.accept()
            return
        super().keyPressEvent(event)

    def _on_shape_changed_preview(self):
        shape = self.map_canvas.current_shape()
        if shape is None:
            self.map_canvas.clear_all_osm_preview()
            self.map_canvas.clear_preview_fill()
            self.map_canvas.set_preview_opacity(False)
            self.right_panel.reset_preview_status()
            return
        kind, params = shape
        if kind == "rect":
            bbox = {"west": params["west"], "south": params["south"], "east": params["east"], "north": params["north"]}
        elif kind in ('circle', 'hexagon'):
            bbox = tp.compute_bbox(params["center_lat"], params["center_lon"], radius_m=params["radius_m"])
        else:
            lons = [p[0] for p in params['points']]
            lats = [p[1] for p in params['points']]
            bbox = {"west": min(lons), "east": max(lons), "south": min(lats), "north": max(lats)}

        old_workers = list(getattr(self, '_preview_workers', {}).values())
        for worker in old_workers:
            if worker.isRunning():
                worker.terminate()
                worker.wait()
        for worker in old_workers:
            worker.deleteLater()

        params_all = self.param_panel.get_params()
        cache_dir = params_all.get("CACHE_DIR", "cache")
        self._preview_workers = {}
        self._preview_pending = {"water": set(), "vegetation": set(), "buildings": set()}
        self._preview_bbox = bbox
        self._preview_cache_dir = cache_dir
        strategy = tp._overpass_strategy

        enabled = params_all.get("ENABLED_LAYERS", [])
        use_osm = params_all.get("USE_OSM", True)
        use_landcover = params_all.get("USE_LANDCOVER", False)
        use_satellite = params_all.get("USE_SATELLITE", False)

        layers_to_preview = []
        if "roads" in enabled:
            layers_to_preview.append("roads")
        if use_osm and "water" in enabled:
            layers_to_preview.append("water")
            self._preview_pending["water"].add("osm")
        if use_osm and ("vegetation" in enabled or "trees" in enabled):
            layers_to_preview.append("vegetation")
            self._preview_pending["vegetation"].add("osm")
        if use_osm and "buildings" in enabled:
            layers_to_preview.append("buildings")
            self._preview_pending["buildings"].add("osm")

        for target in ("water", "vegetation", "buildings"):
            if target not in layers_to_preview:
                self.map_canvas._clear_osm_preview(target)

        if use_landcover:
            for target in ("water", "vegetation", "buildings"):
                self._preview_pending[target].add("landcover")
        if use_satellite:
            for target in ("water", "vegetation", "buildings"):
                self._preview_pending[target].add("satellite")

        for target in ("water", "vegetation", "buildings"):
            if self._preview_pending[target]:
                self.right_panel.set_preview_layer_loading(target)
            else:
                self.right_panel.set_preview_layer_done(target, self._count_layer(target))

        if strategy == "parallel":
            for layer in layers_to_preview:
                self._launch_preview_worker(layer, bbox, cache_dir)
        elif strategy == "sequential":
            if "roads" in layers_to_preview:
                self._launch_preview_worker("roads", bbox, cache_dir)
            all_layers = [l for l in layers_to_preview if l != "roads"]
            if len(all_layers) == 1:
                self._launch_preview_worker(all_layers[0], bbox, cache_dir)
            elif all_layers:
                w = PreviewWorkerAll(bbox, cache_dir=cache_dir)
                w.ready.connect(self._on_preview_ready)
                w.failed.connect(lambda l, e: self._on_preview_source_done(l))
                self._preview_workers["_all"] = w
                w.start()
        else:
            self.right_panel.reset_preview_status()

        if use_landcover:
            self._launch_preview_worker("landcover", bbox, cache_dir)
        else:
            self._clear_source_fill("landcover")

        if use_satellite:
            self._launch_preview_worker("satellite", bbox, cache_dir)
        else:
            self._clear_source_fill("satellite")
    
    def _clear_source_fill(self, source):
        for target in ("water", "vegetation", "buildings"):
            self.map_canvas.clear_preview_fill(target, source)
            self._preview_pending[target].discard(source)
            if not self._preview_pending[target]:
                self.right_panel.set_preview_layer_done(target, self._count_layer(target))

    def _launch_preview_worker(self, layer, bbox, cache_dir):
        params = self.param_panel.get_params()
        include_railways = params.get("INCLUDE_RAILWAYS", False)
        w = PreviewWorker(
            layer, bbox, cache_dir=cache_dir, include_railways=include_railways,
            cdse_access_key=params.get("CDSE_ACCESS_KEY", ""),
            cdse_secret_key=params.get("CDSE_SECRET_KEY", ""),
            satellite_calibration=params.get("SATELLITE_CALIBRATION", tp.DEFAULT_SATELLITE_CALIBRATION),
        )
        w.ready.connect(self._on_preview_ready)
        w.failed.connect(lambda l=layer: self._on_preview_source_done(l))
        self._preview_workers[layer] = w
        self.right_panel._btn_stop_preview.setVisible(True)
        self.right_panel._btn_start_preview.setVisible(False)
        w.start()
    
    def _on_matrix_cell_toggled(self, topic, source, visible):
        if source == "osm":
            state = Qt.CheckState.Checked.value if visible else Qt.CheckState.Unchecked.value
            self.right_panel._on_preview_layer_toggle(topic, state)
        else:
            self.map_canvas.set_preview_fill_visible(topic, source, visible)
        self.right_panel.set_preview_layer_done(topic, self._count_layer(topic))

    def _on_matrix_row_toggled(self, topic, checked):
        for (t, s), cb in list(self.right_panel._matrix_cell_widgets.items()):
            if t != topic:
                continue
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
            self._on_matrix_cell_toggled(t, s, checked)
        self.right_panel._matrix_row_buttons[topic].setStyleSheet(toggle_button_style(TOPIC_COLORS[topic], checked))

    def _on_matrix_column_toggled(self, source, checked):
        for (t, s), cb in list(self.right_panel._matrix_cell_widgets.items()):
            if s != source:
                continue
            cb.blockSignals(True)
            cb.setChecked(checked)
            cb.blockSignals(False)
            self._on_matrix_cell_toggled(t, s, checked)
        self.right_panel._matrix_col_buttons[source].setStyleSheet(toggle_button_style("#555555", checked))
    
    def _refresh_source_columns(self):
        params = self.param_panel.get_params()
        self.right_panel.set_source_columns_visible(
            params.get("USE_OSM", True),
            params.get("USE_LANDCOVER", False),
            params.get("USE_SATELLITE", False),
        )
    
    def _count_layer(self, layer):
        excluded = self.map_canvas._tooltip._excluded_ids
        excluded_for_layer = {osm_id for t, osm_id in excluded if t == ("road" if layer == "roads" else layer)}
        items = self.map_canvas._osm_preview_items.get(layer, [])
        fill_items = []
        for key, values in getattr(self.map_canvas, "_fill_preview_items", {}).items():
            if key[0] == layer:
                fill_items.extend(values)
        seen_ids = set()
        counted = 0
        all_ids = []
        for item in list(items) + list(fill_items):
            data = item.data(0)
            if not data:
                continue
            osm_id = str(data.get("osm_id", ""))
            all_ids.append(osm_id)
            if osm_id in seen_ids:
                continue
            seen_ids.add(osm_id)
            if osm_id in excluded_for_layer:
                continue
            counted += 1
        return counted

    def _on_preview_source_done(self, source):
        if source == "roads":
            self.right_panel.set_preview_layer_done("roads", self._count_layer("roads"))
            return
        targets = ("water", "vegetation", "buildings") if source in ("landcover", "satellite") else (source,)
        for target in targets:
            self._preview_pending[target].discard("osm" if source in ("water", "vegetation", "buildings") else source)
            if not self._preview_pending[target]:
                self.right_panel.set_preview_layer_done(target, self._count_layer(target))

    def _on_preview_ready(self, layer, data):
        if layer == "roads":
            params = self.param_panel.get_params()
            visible = params["ROAD_LEVELS"]
            if params.get("INCLUDE_RAILWAYS", False):
                visible = visible + ["railway"]
            self.map_canvas.set_preview_roads(data)
            self.map_canvas.update_preview_visibility(visible)
        elif layer == "water":
            water_areas = data.get("water_areas")
            if water_areas is not None:
                areas = water_areas.geometry.to_crs("EPSG:3857").area.tolist()
            self.map_canvas.set_preview_water(data)
        elif layer == "vegetation":
            self.map_canvas.set_preview_vegetation(data)
        elif layer == "buildings":
            self.map_canvas.set_preview_buildings(data)
        elif layer in ("landcover", "satellite"):
            features = data.get("features", [])
            by_target = {}
            for feature in features:
                target = "vegetation" if feature["type"] == "trees" else feature["type"]
                by_target.setdefault(target, []).append(feature)
            for target in ("water", "vegetation", "buildings"):
                self.map_canvas.set_preview_fill(target, layer, by_target.get(target, []))

        self._on_preview_source_done(layer)
        self.map_canvas.set_preview_opacity(True)
        if tp._overpass_strategy == "sequential" and hasattr(self, '_sequential_pending'):
            self._sequential_pending.discard(layer)
            if not self._sequential_pending and self._sequential_next:
                for next_layer in self._sequential_next:
                    self._launch_preview_worker(next_layer, self._preview_bbox, self._preview_cache_dir)
                self._sequential_next = []
        if not any(w.isRunning() for w in self._preview_workers.values()):
            self.right_panel._btn_stop_preview.setVisible(False)
            self.right_panel._btn_start_preview.setVisible(True)
    
    def _on_road_levels_changed(self):
        params = self.param_panel.get_params()
        visible = params["ROAD_LEVELS"]
        if params.get("INCLUDE_RAILWAYS", False):
            visible = visible + ["railway"]
        self.map_canvas.update_preview_visibility(visible)
        self.right_panel.set_preview_layer_done("roads", self._count_layer("roads"))

    def _on_railway_option_changed(self):
        bbox = getattr(self, "_preview_bbox", None)
        if bbox is None:
            return
        cache_dir = getattr(self, "_preview_cache_dir", self.param_panel.get_params().get("CACHE_DIR", "cache"))
        self._launch_preview_worker("roads", bbox, cache_dir)

    def _on_water_filter_changed(self):
        params = self.param_panel.get_params()
        self.map_canvas._clear_osm_preview("water")
        self.map_canvas._draw_osm_preview_water(
            min_area_m2=params["MIN_WATER_AREA_M2"],
            min_length_m=params["MIN_WATERWAY_LENGTH_M"]
        )
        self.right_panel.set_preview_layer_done("water", self._count_layer("water"))

    def _on_building_filter_changed(self):
        params = self.param_panel.get_params()
        self.map_canvas._clear_osm_preview("buildings")
        self.map_canvas._draw_osm_preview_buildings(
            min_area_m2=params["MIN_BUILDING_AREA_M2"]
        )
        self.right_panel.set_preview_layer_done("buildings", self._count_layer("buildings"))

    def _on_tab_changed(self, index):
        if self.tabs.widget(index) == self.stl_viewer:
            if not self.stl_viewer._meshes:
                self.stl_viewer.reload_stl_folder()

    def _on_overpass_ready(self, strategy):
        self.map_canvas.setEnabled(True)
        self._gen_locked = False

    def _on_save_project(self):
        path = getattr(self, "_current_project_path", None)
        if not path:
            path, _ = QFileDialog.getSaveFileName(self, "Sauvegarder le projet", "", "Projet Topopixel (*.topo)")
            if not path:
                return
        self._save_project_to(path)

    def _on_save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Sauvegarder le projet sous", "", "Projet Topopixel (*.topo)")
        if not path:
            return
        self._save_project_to(path)

    def _save_project_to(self, path):
        lon, lat = self.map_canvas.center_lonlat()
        data = {
            "version": 1,
            "map": {"lon": lon, "lat": lat, "zoom": self.map_canvas.current_zoom()},
            "shape_kind": self.map_canvas._shape_kind,
            "shape_params": self.map_canvas._shape_params,
            "params": self.param_panel.get_params(),
            "excluded_ids": list(self.map_canvas._tooltip._excluded_ids),
            "colors": TOPIC_COLORS.copy(),
            "gpx_list": self.param_panel.get_gpx_list(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        project_name = os.path.splitext(os.path.basename(path))[0]
        self.setWindowTitle(f"Topopixel — {project_name}")
        self._project_name = project_name
        self._current_project_path = path
        self.show_toast("Projet sauvegardé")
    
    def _apply_default_params_to_panel(self):
        options_values = self._options_dialog.get_values()
        defaults = {
            key[len("DEFAULT_"):]: value
            for key, value in options_values.items()
            if key.startswith("DEFAULT_") and key not in ("DEFAULT_ENABLED_LAYERS", "DEFAULT_COLORS")
        }

        for key, widget in self.param_panel._fields.items():
            if key not in defaults:
                continue
            value = defaults[key]
            if isinstance(widget, QSlider):
                widget.setValue(int(value * widget.scale) if hasattr(widget, "scale") else value)
            elif isinstance(widget, QLineEdit):
                widget.setText(value)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(value)

        for level, cb in self.param_panel._road_checkboxes.items():
            cb.setChecked(level in DEFAULT_ROAD_LEVELS_CHECKED)

        default_enabled_layers = options_values.get("DEFAULT_ENABLED_LAYERS", [])
        for key, cb in self.param_panel._layer_checkboxes.items():
            cb.setChecked(key in default_enabled_layers)

        default_colors = options_values.get("DEFAULT_COLORS", {})
        for key, color in default_colors.items():
            TOPIC_COLORS[key] = color
            if key in self.param_panel._color_buttons:
                self.param_panel._color_buttons[key].setStyleSheet(color_button_style(color))
                self.param_panel._layer_checkboxes[key].setStyleSheet(checkbox_style(color))
        self.map_canvas.update_preview_colors()
        for key, color in default_colors.items():
            self.stl_viewer.update_layer_color(key, color)

    def _on_new_project(self):
        self.map_canvas.clear_shape()
        self.setWindowTitle("Topopixel — Générateur de terrain 3D")
        self._project_name = "topopixel"
        self._current_project_path = None

        self.map_canvas.set_center(*DEFAULT_MAP)
        self.map_canvas._shape_kind = None
        self.map_canvas._shape_params = None

        self._apply_default_params_to_panel()

        self.map_canvas._tooltip._excluded_ids = set()
        self.map_canvas.shape_changed.emit()

        self.param_panel.clear_gpx_list()
        self.param_panel.gpx_changed.emit()

    def _on_load_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Charger un projet", "", "Projet Topopixel (*.topo)")
        if not path:
            return
        self.load_project_from_path(path)

    def load_project_from_path(self, path):
        self.map_canvas.clear_shape()
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        project_name = os.path.splitext(os.path.basename(path))[0]
        self.setWindowTitle(f"Topopixel — {project_name}")
        self._project_name = project_name
        self._current_project_path = path

        m = data.get("map", {})
        self.map_canvas.set_center(m.get("lon", 3.26), m.get("lat", 46.92), m.get("zoom", 14))

        self.map_canvas._shape_kind = data.get("shape_kind")
        self.map_canvas._shape_params = data.get("shape_params")
        if self.map_canvas._shape_kind:
            self.map_canvas._restore_shape_item()
            self.right_panel._on_shape_changed()
        
        shape_radio_map = {
            "rect": self.right_panel._rb_rect,
            "circle": self.right_panel._rb_circle,
            "hexagon": self.right_panel._rb_hexagon,
            "polygon": self.right_panel._rb_polygon,
        }
        radio = shape_radio_map.get(self.map_canvas._shape_kind)
        if radio is not None:
            radio.setChecked(True)
        
        params = data.get("params", {})
        for key, widget in self.param_panel._fields.items():
            if key in params:
                if isinstance(widget, QSlider):
                    if hasattr(widget, "scale"):
                        widget.setValue(int(params[key] * widget.scale))
                    else:
                        widget.setValue(params[key])
                elif isinstance(widget, QLineEdit):
                    widget.setText(params[key])
                elif isinstance(widget, QCheckBox):
                    widget.setChecked(params[key])

        road_levels = params.get("ROAD_LEVELS", [])
        for level, cb in self.param_panel._road_checkboxes.items():
            cb.setChecked(level in road_levels)

        enabled_layers = params.get("ENABLED_LAYERS", [])
        for key, cb in self.param_panel._layer_checkboxes.items():
            cb.setChecked(key in enabled_layers)

        excluded = data.get("excluded_ids", [])
        self.map_canvas._tooltip._excluded_ids = {tuple(e) for e in excluded}

        if self.map_canvas._shape_kind:
            self._on_shape_changed_preview()
            self.right_panel._gen_button.setVisible(True)
        else:
            self.map_canvas.shape_changed.emit()
            
        colors = data.get("colors", {})
        for key, color in colors.items():
            TOPIC_COLORS[key] = color
            if key in self.param_panel._color_buttons:
                self.param_panel._color_buttons[key].setStyleSheet(color_button_style(color))
                self.param_panel._layer_checkboxes[key].setStyleSheet(checkbox_style(color))
        if colors:
            self.map_canvas.update_preview_colors()
            for key, color in colors.items():
                self.stl_viewer.update_layer_color(key, color)
                
        self.param_panel.clear_gpx_list()
        for gpx in data.get("gpx_list", []):
            self.param_panel._add_gpx_row(gpx["path"], gpx["color"], gpx["enabled"])
        self.param_panel.gpx_changed.emit()

    def _on_exclusion_changed_count(self, key, excluded):
        layer_type = key[0]
        mapping = {"road": "roads", "water": "water", "vegetation": "vegetation", "buildings": "buildings"}
        layer = mapping.get(layer_type)
        if layer:
            self.right_panel.set_preview_layer_done(layer, self._count_layer(layer))

    def _on_color_changed(self, key, color):
        TOPIC_COLORS[key] = color
        self.map_canvas.update_preview_colors()
        self.stl_viewer.update_layer_color(key, color)
        row_btn = self.right_panel._matrix_row_buttons.get(key)
        if row_btn is not None:
            row_btn.setStyleSheet(toggle_button_style(color, row_btn.isChecked()))
        for (topic, source), cb in self.right_panel._matrix_cell_widgets.items():
            if topic == key:
                cb.setStyleSheet(checkbox_style(color))
            
    def _on_gpx_changed(self):
        self.map_canvas.set_gpx_tracks(self.param_panel.get_gpx_list())

    def _on_layer_enabled_changed(self, layer, is_enabled):
        if not is_enabled:
            return
        preview_layer_map = {"water": "water", "vegetation": "vegetation", "trees": "vegetation", "buildings": "buildings", "roads": "roads"}
        preview_layer = preview_layer_map.get(layer)
        if preview_layer is None:
            return
        bbox = getattr(self, "_preview_bbox", None)
        if bbox is None:
            return
        cache_dir = getattr(self, "_preview_cache_dir", self.param_panel.get_params().get("CACHE_DIR", "cache"))
        already_loaded = preview_layer in getattr(self.map_canvas, "_osm_preview_edges", {}) or preview_layer in getattr(self.map_canvas, "_osm_preview_items", {})
        if already_loaded:
            return
        self._launch_preview_worker(preview_layer, bbox, cache_dir)

    def _on_monument_action(self, osm_id, button_text):
        if button_text == "Remplacer par STL":
            path, _ = QFileDialog.getOpenFileName(self, "Choisir un STL", "", "STL (*.stl)")
            if not path:
                return
            save_monument_entry(osm_id, path, 0.0, active=True)
        elif button_text == "Empreinte STL":
            set_monument_active(osm_id, True)
        elif button_text == "Empreinte par défaut":
            set_monument_active(osm_id, False)
        elif button_text == "Changer STL":
            path, _ = QFileDialog.getOpenFileName(self, "Choisir un nouveau STL", "", "STL (*.stl)")
            if not path:
                return
            entry = get_monument_entry(osm_id)
            rotation = entry.get("rotation_deg", 0.0) if entry else 0.0
            scale = entry.get("scale_factor", 1.0) if entry else 1.0
            save_monument_entry(osm_id, path, rotation, active=True, scale_factor=scale)
        elif button_text == "Supprimer STL":
            remove_monument_entry(osm_id)
        self.show_toast("Chargement du STL...", color="#F5C518", text_color="#2B2B2B", persistent=True)
        self.map_canvas.redraw_buildings_preview()
        self.hide_toast()

    def _on_cache_coverage_toggled(self, checked):
        if not checked:
            self.map_canvas.clear_cache_coverage()
            return
        params = self.param_panel.get_params()
        resolution_m = int(params.get("RESOLUTION_M", 5))
        cache_dir = params.get("CACHE_DIR", "cache")
        bboxes = []
        if os.path.isdir(cache_dir):
            for fname in os.listdir(cache_dir):
                if not fname.endswith(".tif"):
                    continue
                parsed = tp._parse_cache_bbox(fname)
                if parsed is not None and parsed["resolution_m"] == resolution_m:
                    bboxes.append(parsed)
        self.map_canvas.show_cache_coverage(bboxes)

    def _on_osm_cache_coverage_toggled(self, checked):
        if not checked:
            self.map_canvas.clear_osm_cache_coverage()
            return
        params = self.param_panel.get_params()
        cache_dir = params.get("CACHE_DIR", "cache")
        osm_dir = os.path.join(cache_dir, "osm")
        bboxes = []
        seen = set()
        if os.path.isdir(osm_dir):
            for fname in os.listdir(osm_dir):
                parsed = tp._parse_osm_cache_bbox(fname)
                if parsed is None:
                    continue
                key = (parsed["south"], parsed["north"], parsed["west"], parsed["east"])
                if key in seen:
                    continue
                seen.add(key)
                bboxes.append(parsed)
        self.map_canvas.show_osm_cache_coverage(bboxes)
        
    def _on_landcover_cache_coverage_toggled(self, checked):
        if not checked:
            self.map_canvas.clear_landcover_cache_coverage()
            return
        params = self.param_panel.get_params()
        cache_dir = params.get("CACHE_DIR", "cache")
        landcover_dir = os.path.join(cache_dir, "landcover")
        bboxes = []
        seen = set()
        if os.path.isdir(landcover_dir):
            for fname in os.listdir(landcover_dir):
                parsed = tp._parse_landcover_cache_bbox(fname)
                if parsed is None:
                    continue
                key = (parsed["south"], parsed["north"], parsed["west"], parsed["east"])
                if key in seen:
                    continue
                seen.add(key)
                bboxes.append(parsed)
        self.map_canvas.show_landcover_cache_coverage(bboxes)

    def _on_satellite_cache_coverage_toggled(self, checked):
        if not checked:
            self.map_canvas.clear_satellite_cache_coverage()
            return
        params = self.param_panel.get_params()
        cache_dir = params.get("CACHE_DIR", "cache")
        satellite_dir = os.path.join(cache_dir, "satellite")
        bboxes = []
        seen = set()
        if os.path.isdir(satellite_dir):
            for fname in os.listdir(satellite_dir):
                parsed = tp._parse_satellite_cache_bbox(fname)
                if parsed is None:
                    continue
                key = (parsed["south"], parsed["north"], parsed["west"], parsed["east"])
                if key in seen:
                    continue
                seen.add(key)
                bboxes.append(parsed)
        self.map_canvas.show_satellite_cache_coverage(bboxes)
        
    def show_toast(self, text, color="#2F9E44", text_color="white", duration_ms=1600, persistent=False):
        self._toast.setText(text)
        self._toast.setStyleSheet(f"""
            QLabel {{
                background: {color};
                color: {text_color};
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
        """)
        self._toast.adjustSize()
        x = (self.width() - self._toast.width()) // 2
        self._toast.move(x, 40)
        self._toast_opacity.setOpacity(1.0)
        self._toast.show()
        self._toast.raise_()
        QApplication.processEvents()
        if not persistent:
            QTimer.singleShot(duration_ms, self._start_toast_fade)

    def hide_toast(self):
        self._toast.hide()

    def _start_toast_fade(self):
        self._toast_anim.setStartValue(1.0)
        self._toast_anim.setEndValue(0.0)
        self._toast_anim.start()

    def _on_toast_fade_finished(self):
        if self._toast_opacity.opacity() == 0.0:
            self._toast.hide()

    def _on_monument_rotation_committed(self, osm_id, rotation_deg):
        set_monument_rotation(osm_id, rotation_deg)
        
    def _on_monument_scale_committed(self, osm_id, scale_factor):
        set_monument_scale(osm_id, scale_factor)
        
    def _on_quit(self):
        confirm = QMessageBox.question(
            self,
            "Quitter",
            "Voulez-vous sauvegarder le projet avant de quitter ?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel
        )
        if confirm == QMessageBox.StandardButton.Cancel:
            return
        if confirm == QMessageBox.StandardButton.Yes:
            self._on_save_project()
        QApplication.quit()

def _qt_message_filter(msg_type, context, message):
    
    if "Point size <= 0" in message:
        return
    sys.stderr.write(message + "\n")

DARK_STYLESHEET = """
QWidget {
    background: #2B2B2B;
    color: #DDDDDD;
}
QMainWindow, QDialog {
    background: #2B2B2B;
}
QLabel {
    color: #DDDDDD;
    background: transparent;
}
QLineEdit, QSpinBox, QDoubleSpinBox {
    background: #3C3C3C;
    color: #DDDDDD;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 2px 4px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #888888;
}
QPushButton {
    background: #3C3C3C;
    color: #DDDDDD;
    border: 1px solid #555555;
    border-radius: 3px;
    padding: 4px 10px;
}
QPushButton:hover {
    background: #4A4A4A;
}
QPushButton:pressed {
    background: #2F2F2F;
}
QPushButton:checked {
    background: #555555;
    border: 1px solid #888888;
}
QSlider::groove:horizontal {
    background: #444444;
    height: 4px;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #999999;
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
}
QScrollArea {
    background: #2B2B2B;
    border: none;
}
QScrollBar:vertical, QScrollBar:horizontal {
    background: #2B2B2B;
    width: 12px;
    height: 12px;
}
QScrollBar::handle {
    background: #555555;
    border-radius: 5px;
}
QTabWidget::pane {
    border: 1px solid #444444;
    background: #2B2B2B;
}
QTabBar::tab {
    background: #3C3C3C;
    color: #DDDDDD;
    padding: 6px 14px;
}
QTabBar::tab:selected {
    background: #4A4A4A;
}
QMenu {
    background: #3C3C3C;
    color: #DDDDDD;
    border: 1px solid #555555;
}
QMenu::item:selected {
    background: #555555;
}
QToolButton {
    background: transparent;
    color: #DDDDDD;
}
QGraphicsView {
    background: #2B2B2B;
    border: none;
}
QListWidget {
    background: #3C3C3C;
    color: #DDDDDD;
    border: 1px solid #555555;
}
QToolTip {
    background-color: #3C3C3C;
    color: #DDDDDD;
    border: 1px solid #555555;
    padding: 4px 8px;
}
"""

def main():
    qInstallMessageHandler(_qt_message_filter)
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    if os.name == "nt":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("topopixel.app")
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    icon_data = base64.b64decode(ICON_BASE64)
    pixmap = QPixmap()
    pixmap.loadFromData(icon_data)
    app.setWindowIcon(QIcon(pixmap))
    app.setStyleSheet(DARK_STYLESHEET)
    
    win = MainWindow()
    win.show()

    def _load_project_deferred():
        project_args = [a for a in sys.argv[1:] if not a.startswith("-")]
        if project_args:
            project_arg = project_args[0]
            if not project_arg.endswith(".topo"):
                project_arg += ".topo"
            if os.path.exists(project_arg):
                win.load_project_from_path(project_arg)
            else:
                log(f"Projet introuvable : {project_arg}")

    def _maximize_then_load():
        win.showMaximized()
        QTimer.singleShot(0, _load_project_deferred)

    QTimer.singleShot(0, _maximize_then_load)

    sys.exit(app.exec())
if __name__ == "__main__":
    main()
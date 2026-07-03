import os
import sys
import traceback
import json

os.system("cls")

def excepthook(type, value, tb):
    print("".join(traceback.format_exception(type, value, tb)))

sys.excepthook = excepthook

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QLabel, QTextEdit, QMessageBox, QButtonGroup, QRadioButton,
    QFormLayout, QFrame, QSizePolicy, QTabWidget, QGroupBox, QCheckBox, QGridLayout, QFileDialog, QSpinBox, QDoubleSpinBox, QLineEdit
)
from PyQt6.QtCore import Qt, qInstallMessageHandler, QTimer
from PyQt6.QtGui import QGuiApplication

from map_canvas import MapCanvas
from param_panel import ParamPanel
from generation_worker import GenerationWorker
from stl_viewer import StlViewerPanel
from preview_worker import PreviewWorker, PreviewWorkerAll
from overpass_status_widget import OverpassStatusWidget
from constants import TOPIC_COLORS, checkbox_style
import topopixel as tp

RIGHT_PANEL_STYLESHEET = """
QWidget#rightPanel {
    background-color: #F1F3F5;
}
QLabel#sectionHint {
    color: #495057;
    font-size: 11px;
}
QRadioButton {
    padding: 3px 0;
    color: #212529;
}
QRadioButton::indicator {
    width: 15px;
    height: 15px;
    border-radius: 8px;
    border: 1.5px solid #ADB5BD;
    background-color: #FFFFFF;
}
QRadioButton::indicator:checked {
    background-color: #4C6EF5;
    border: 1.5px solid #4C6EF5;
}
QPushButton {
    background-color: #E9ECEF;
    border: 1px solid #CED4DA;
    border-radius: 6px;
    padding: 6px 10px;
    color: #212529;
}
QPushButton:hover {
    background-color: #DEE2E6;
}
QPushButton:pressed {
    background-color: #CED4DA;
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
                background-color: #FFFFFF;
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

        tools_box = StaticSection("Outil d'emprise", accent="#1971C2")
        
        preview_section = StaticSection("Prévisualisation carte", accent="#1971C2")
        
        preview_grid = QGridLayout()
        preview_grid.setSpacing(4)

        self._cb_preview_roads = QCheckBox("Routes")
        self._cb_preview_water = QCheckBox("Eau")
        self._cb_preview_veg = QCheckBox("Végétation")
        self._cb_preview_buildings = QCheckBox("Bâtiments")

        self._preview_status = {
            "roads": QLabel("—"),
            "water": QLabel("—"),
            "vegetation": QLabel("—"),
            "buildings": QLabel("—"),
        }

        self._preview_loading = {
            "roads": QLabel(""),
            "water": QLabel(""),
            "vegetation": QLabel(""),
            "buildings": QLabel(""),
        }

        rows = [
            ("roads",self._cb_preview_roads,TOPIC_COLORS["roads"]),
            ("water",self._cb_preview_water,TOPIC_COLORS["water"]),
            ("vegetation", self._cb_preview_veg,TOPIC_COLORS["vegetation"]),
            ("buildings",self._cb_preview_buildings,TOPIC_COLORS["buildings"]),
        ]

        for i, (layer, cb, color) in enumerate(rows):
            cb.setChecked(True)
            cb.setStyleSheet(checkbox_style(color))
            loading = self._preview_loading[layer]
            loading.setFixedWidth(16)
            status = self._preview_status[layer]
            status.setStyleSheet("color: #888; font-size: 11px;")
            preview_grid.addWidget(cb, i, 0)
            preview_grid.addWidget(loading, i, 1)
            preview_grid.addWidget(status, i, 2)

        preview_section.add_layout(preview_grid)

        self._cb_preview_roads.stateChanged.connect(
            lambda s: self._on_preview_layer_toggle("roads", s))
        self._cb_preview_water.stateChanged.connect(
            lambda s: self._on_preview_layer_toggle("water", s))
        self._cb_preview_veg.stateChanged.connect(
            lambda s: self._on_preview_layer_toggle("vegetation", s))
        self._cb_preview_buildings.stateChanged.connect(
            lambda s: self._on_preview_layer_toggle("buildings", s))
            
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

        layout.addWidget(preview_section)

        self._tool_group = QButtonGroup(self)
        self._rb_rect = QRadioButton("Rectangle")
        self._rb_circle = QRadioButton("Cercle")
        self._rb_hexagon = QRadioButton("Hexagone")
        self._rb_polygon = QRadioButton("Polygone")
        for rb in (self._rb_rect, self._rb_circle, self._rb_hexagon, self._rb_polygon):
            self._tool_group.addButton(rb)
            tools_box.add_widget(rb)

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
        tools_box.add_widget(self.clear_btn)

        layout.addWidget(tools_box)

        info_box = StaticSection("Emprise actuelle", accent="#1971C2")
        info_form = QFormLayout()
        self._lbl_kind = QLabel("—")
        self._lbl_kind.setObjectName("kindLabel")
        self._lbl_details = QLabel("Aucune emprise dessinée")
        self._lbl_details.setWordWrap(True)
        info_form.addRow("Type", self._lbl_kind)
        info_form.addRow("Détails", self._lbl_details)
        info_box.add_layout(info_form)
        layout.addWidget(info_box)

        self.canvas.shape_changed.connect(self._on_shape_changed)

        gen_box = StaticSection("Génération", accent="#2F9E44")
        self._gen_button = QPushButton("Générer le modèle 3D")
        self._gen_button.setObjectName("generateButton")
        self._gen_button.setMinimumHeight(40)
        self._gen_button.clicked.connect(self._on_generate)
        self._gen_button.setVisible(False)
        gen_box.add_widget(self._gen_button)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setMaximumHeight(220)
        self._log.setVisible(False)
        gen_box.add_widget(self._log)

        layout.addWidget(gen_box)
        layout.addStretch(1)
        
        self._param_panel_ref = None

    def set_param_panel(self, panel: ParamPanel):
        self._param_panel_ref = panel

    def _on_clear(self):
        self.canvas.clear_shape()
        self.clear_btn.hide()

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

        self._log.clear()
        self._log.setVisible(True)
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
            win.tabs.setCurrentIndex(1)
        else:
            print("Tab prévisualisation 3D non trouvé")

    def _on_failed(self, error_text):
        self._restore_gen_button()
        self._log.setVisible(True)
        self._log.append("ERREUR :\n" + error_text)
        QMessageBox.critical(self, "Échec de la génération","La génération a échoué. Voir le journal pour le détail.")

    def set_stl_viewer(self, viewer: "StlViewerPanel"):
        self._stl_viewer = viewer
                                
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
            from PyQt6.QtGui import QMovie
            if not hasattr(self, '_loading_movie'):
                import os
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
            self._preview_status[layer].setText(f"{count} éléments")

    def _on_stop_preview(self):
        win = self.window()
        if hasattr(win, '_preview_workers'):
            for worker in win._preview_workers.values():
                if worker.isRunning():
                    worker.terminate()
                    worker.wait()
            win._preview_workers.clear()
        self.reset_preview_status()
        self._btn_stop_preview.setVisible(False)
    
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
    
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        
        self.setWindowTitle("Topopixel — Générateur de terrain 3D")
        self.resize(1500, 900)
        self.setStyleSheet("QMainWindow { background-color: #E9ECEF; }")
        
        menubar = self.menuBar()
        projet_menu = menubar.addMenu("Projet")

        action_save = projet_menu.addAction("💾 Sauvegarder le projet")
        action_save.setShortcut("Ctrl+S")
        action_save.triggered.connect(self._on_save_project)

        action_load = projet_menu.addAction("📂 Charger un projet")
        action_load.setShortcut("Ctrl+O")
        action_load.triggered.connect(self._on_load_project)

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

        self.map_canvas = MapCanvas()

        self.right_panel = RightPanel(self.map_canvas)
        self.right_panel.setMinimumWidth(280)
        self.right_panel.setMaximumWidth(340)
        self.right_panel.set_param_panel(self.param_panel)

        layout.addWidget(self.param_panel)
        layout.addWidget(self.map_canvas, stretch=1)
        layout.addWidget(self.right_panel)

        self.tabs = QTabWidget()
        self.tabs.addTab(top_widget, "Carte")

        self.stl_viewer = StlViewerPanel()
        self.tabs.addTab(self.stl_viewer, "Prévisualisation STL")
        self.tabs.currentChanged.connect(self._on_tab_changed)

        main_layout.addWidget(self.tabs)
        
        stl_folder = self.param_panel.get_params().get("STL_DIR", "STL")
        has_stl = os.path.isdir(stl_folder) and any(
            f.endswith(".stl") for f in os.listdir(stl_folder)
        ) if os.path.isdir(stl_folder) else False
        self.tabs.setTabEnabled(1, has_stl)
        
        self._overpass_status = OverpassStatusWidget()
        self._overpass_status.refresh()
        
        main_layout.addWidget(self._overpass_status)
        
        self.right_panel.set_stl_viewer(self.stl_viewer)

        self._preview_worker = None
        self.map_canvas.shape_changed.connect(self._on_shape_changed_preview)
        self.param_panel.road_levels_changed.connect(self._on_road_levels_changed)
        self.param_panel.water_filter_changed.connect(self._on_water_filter_changed)
        self.param_panel.building_filter_changed.connect(self._on_building_filter_changed)
        
        self.map_canvas.setEnabled(False)
        self._gen_locked = True
        self._overpass_status.strategy_changed.connect(self._on_overpass_ready)
        
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

        for worker in getattr(self, '_preview_workers', {}).values():
            if worker.isRunning():
                worker.terminate()
                worker.wait()

        cache_dir = self.param_panel.get_params().get("CACHE_DIR", "cache")
        self._preview_workers = {}
        self._preview_bbox = bbox
        self._preview_cache_dir = cache_dir
        strategy = tp._overpass_strategy

        enabled = self.param_panel.get_params().get("ENABLED_LAYERS", [])
        layers_to_preview = []
        if "roads" in enabled:
            layers_to_preview.append("roads")
        if "water" in enabled:
            layers_to_preview.append("water")
        if "vegetation" in enabled or "trees" in enabled:
            layers_to_preview.append("vegetation")
        if "buildings" in enabled:
            layers_to_preview.append("buildings")

        if strategy == "parallel":
            for layer in layers_to_preview:
                self._launch_preview_worker(layer, bbox, cache_dir)
        elif strategy == "sequential":
            if "roads" in layers_to_preview:
                self._launch_preview_worker("roads", bbox, cache_dir)
            all_layers = [l for l in layers_to_preview if l != "roads"]
            if all_layers:
                w = PreviewWorkerAll(bbox, cache_dir=cache_dir)
                w.ready.connect(self._on_preview_ready)
                w.failed.connect(lambda l, e: self.right_panel.set_preview_layer_done(l, 0))
                self._preview_workers["_all"] = w
                w.start()
                for layer in all_layers:
                    self.right_panel.set_preview_layer_loading(layer)
        else:
            self.right_panel.reset_preview_status()

    def _launch_preview_worker(self, layer, bbox, cache_dir):
        self.right_panel.set_preview_layer_loading(layer)
        w = PreviewWorker(layer, bbox, cache_dir=cache_dir)
        w.ready.connect(self._on_preview_ready)
        w.failed.connect(lambda l=layer: (
            self.right_panel.set_preview_layer_done(l, 0),
        ))
        self._preview_workers[layer] = w
        self.right_panel._btn_stop_preview.setVisible(True)
        w.start()

    def _on_preview_ready(self, layer, data):
        def safe_len(gdf):
            return 0 if gdf is None else len(gdf)

        if layer == "roads":
            count = sum(len(v) for v in data.values())
        elif layer == "water":
            count = safe_len(data.get("water_areas")) + safe_len(data.get("waterways"))
        elif layer == "vegetation":
            count = safe_len(data.get("forest")) + safe_len(data.get("other_veg"))
        elif layer == "buildings":
            count = safe_len(data.get("buildings"))
        else:
            count = 0

        self.right_panel.set_preview_layer_done(layer, count)

        if layer == "roads":
            visible = self.param_panel.get_params()["ROAD_LEVELS"]
            self.map_canvas.set_preview_roads(data)
            self.map_canvas.update_preview_visibility(visible)
        elif layer == "water":
            self.map_canvas.set_preview_water(data)
        elif layer == "vegetation":
            self.map_canvas.set_preview_vegetation(data)
        elif layer == "buildings":
            self.map_canvas.set_preview_buildings(data)

        self.map_canvas.set_preview_opacity(True)

        if tp._overpass_strategy == "sequential" and hasattr(self, '_sequential_pending'):
            self._sequential_pending.discard(layer)
            if not self._sequential_pending and self._sequential_next:
                for next_layer in self._sequential_next:
                    self._launch_preview_worker(next_layer, self._preview_bbox, self._preview_cache_dir)
                self._sequential_next = []
        
        if not any(w.isRunning() for w in self._preview_workers.values()):
            self.right_panel._btn_stop_preview.setVisible(False)

    def _on_road_levels_changed(self):
        visible = self.param_panel.get_params()["ROAD_LEVELS"]
        self.map_canvas.update_preview_visibility(visible)
        
    def _on_tab_changed(self, index):
        if self.tabs.widget(index) == self.stl_viewer:
            if not self.stl_viewer._meshes:
                self.stl_viewer.reload_stl_folder()

    def _on_overpass_ready(self, strategy):
        self.map_canvas.setEnabled(True)
        self._gen_locked = False

    def _on_water_filter_changed(self):
        params = self.param_panel.get_params()
        self.map_canvas._clear_osm_preview("water")
        self.map_canvas._draw_osm_preview_water(
            min_area_m2=params["MIN_WATER_AREA_M2"],
            min_length_m=params["MIN_WATERWAY_LENGTH_M"]
        )

    def _on_building_filter_changed(self):
        params = self.param_panel.get_params()
        self.map_canvas._clear_osm_preview("buildings")
        self.map_canvas._draw_osm_preview_buildings(
            min_area_m2=params["MIN_BUILDING_AREA_M2"]
        )

    def _on_save_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Sauvegarder le projet", "", "Projet Topopixel (*.topo)")
        if not path:
            return
        lon, lat = self.map_canvas.center_lonlat()
        data = {
            "version": 1,
            "map": {"lon": lon, "lat": lat, "zoom": self.map_canvas.current_zoom()},
            "shape_kind": self.map_canvas._shape_kind,
            "shape_params": self.map_canvas._shape_params,
            "params": self.param_panel.get_params(),
            "excluded_ids": list(self.map_canvas._tooltip._excluded_ids),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def _on_load_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Charger un projet", "", "Projet Topopixel (*.topo)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        m = data.get("map", {})
        self.map_canvas.set_center(m.get("lon", 3.26), m.get("lat", 46.92), m.get("zoom", 14))

        self.map_canvas._shape_kind = data.get("shape_kind")
        self.map_canvas._shape_params = data.get("shape_params")
        if self.map_canvas._shape_kind:
            self.map_canvas._restore_shape_item()
        
        params = data.get("params", {})
        for key, widget in self.param_panel._fields.items():
            if key in params:
                if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
                    widget.setValue(params[key])
                elif isinstance(widget, QLineEdit):
                    widget.setText(params[key])

        road_levels = params.get("ROAD_LEVELS", [])
        for level, cb in self.param_panel._road_checkboxes.items():
            cb.setChecked(level in road_levels)

        enabled_layers = params.get("ENABLED_LAYERS", [])
        for key, cb in self.param_panel._layer_checkboxes.items():
            cb.setChecked(key in enabled_layers)

        excluded = data.get("excluded_ids", [])
        self.map_canvas._tooltip._excluded_ids = {tuple(e) for e in excluded}

        self.map_canvas.shape_changed.emit()
        if self.map_canvas._shape_kind:
            self._on_shape_changed_preview()

def _qt_message_filter(msg_type, context, message):
    
    if "Point size <= 0" in message:
        return
    sys.stderr.write(message + "\n")

def main():
    qInstallMessageHandler(_qt_message_filter)

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
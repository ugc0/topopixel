import os
import math
import sys
import traceback
import json
import copy
import platform

if platform.system() == "Linux":
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
    os.system("clear")
else:
    os.system("cls")

def excepthook(type, value, tb):
    print("".join(traceback.format_exception(type, value, tb)))

sys.excepthook = excepthook

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QPushButton,
    QLabel, QTextEdit, QMessageBox, QButtonGroup, QRadioButton,
    QFormLayout, QFrame, QSizePolicy, QTabWidget, QGroupBox, QCheckBox, QGridLayout, QFileDialog, QSpinBox, QDoubleSpinBox, QLineEdit, QSlider
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
from shape_edit_dialog import ShapeEditDialog
import topopixel as tp

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
        
        self._btn_edit_shape = QPushButton("✏️")
        self._btn_edit_shape.setVisible(False)
        self._btn_edit_shape.clicked.connect(self._on_edit_shape)
        info_box.add_widget(self._btn_edit_shape)
        
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
        ui_params["PROJECT_NAME"] = getattr(self.window(), '_project_name', 'topopixel')
        ui_params["GPX_LIST"] = self._param_panel_ref.get_gpx_list()

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
                win.tabs.setTabEnabled(1, True)
            self._stl_viewer._btn_export_3mf.setVisible(True)
            self._stl_viewer._current_stl_dir = stl_dir
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
            self._preview_status[layer].setText(f"{count} éléments affichés")

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
            import math
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

        self._preview_worker = None
        self.map_canvas.shape_changed.connect(self._on_shape_changed_preview)
        self.param_panel.road_levels_changed.connect(self._on_road_levels_changed)
        self.param_panel.water_filter_changed.connect(self._on_water_filter_changed)
        self.param_panel.building_filter_changed.connect(self._on_building_filter_changed)
        self.param_panel.color_changed.connect(self._on_color_changed)
        
        self.map_canvas.setEnabled(False)
        self._gen_locked = True
        
        self._overpass_status.strategy_changed.connect(self._on_overpass_ready)
        self.map_canvas._tooltip.exclusion_changed.connect(self._on_exclusion_changed_count)
        self.param_panel.gpx_changed.connect(self._on_gpx_changed)
        self.param_panel.cache_coverage_toggled.connect(self._on_cache_coverage_toggled)
        self.param_panel.osm_cache_coverage_toggled.connect(self._on_osm_cache_coverage_toggled)
        
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

    def _count_layer(self, layer):
        excluded = self.map_canvas._tooltip._excluded_ids
        excluded_for_layer = {osm_id for t, osm_id in excluded if t == ("road" if layer == "roads" else layer)}
        items = self.map_canvas._osm_preview_items.get(layer, [])
        seen_ids = set()
        counted = 0
        for item in items:
            data = item.data(0)
            if not data:
                continue
            osm_id = str(data.get("osm_id", ""))
            if osm_id in seen_ids:
                continue
            seen_ids.add(osm_id)
            if osm_id in excluded_for_layer:
                continue
            counted += 1
        return counted

    def _on_preview_ready(self, layer, data):
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
        self.right_panel.set_preview_layer_done(layer, self._count_layer(layer))
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
        self.right_panel.set_preview_layer_done("roads", self._count_layer("roads"))

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
            "colors": TOPIC_COLORS.copy(),
            "gpx_list": self.param_panel.get_gpx_list(),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            
        project_name = os.path.splitext(os.path.basename(path))[0]
        self.setWindowTitle(f"Topopixel — {project_name}")
        self._project_name = project_name

    def _on_load_project(self):
        self.map_canvas.clear_shape()
        path, _ = QFileDialog.getOpenFileName(self, "Charger un projet", "", "Projet Topopixel (*.topo)")
        if not path:
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        project_name = os.path.splitext(os.path.basename(path))[0]
        self.setWindowTitle(f"Topopixel — {project_name}")
        self._project_name = project_name

        m = data.get("map", {})
        self.map_canvas.set_center(m.get("lon", 3.26), m.get("lat", 46.92), m.get("zoom", 14))

        self.map_canvas._shape_kind = data.get("shape_kind")
        self.map_canvas._shape_params = data.get("shape_params")
        if self.map_canvas._shape_kind:
            self.map_canvas._restore_shape_item()
            self.right_panel._on_shape_changed()
        
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
                from constants import color_button_style, checkbox_style
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
        cb_map = {
            "roads": self.right_panel._cb_preview_roads,
            "water": self.right_panel._cb_preview_water,
            "vegetation": self.right_panel._cb_preview_veg,
            "buildings": self.right_panel._cb_preview_buildings,
        }
        if key in cb_map:
            cb_map[key].setStyleSheet(checkbox_style(color))
            
    def _on_gpx_changed(self):
        self.map_canvas.set_gpx_tracks(self.param_panel.get_gpx_list())

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
"""

def main():
    qInstallMessageHandler(_qt_message_filter)

    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_STYLESHEET)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
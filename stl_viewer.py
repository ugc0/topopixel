import os
import re
import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel, QApplication, QPushButton, QToolButton, QMenu, QWidgetAction, QScrollArea, QFrame, QToolButton, QMenu, QWidgetAction, QSlider
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QImage, QMouseEvent, QWheelEvent
import trimesh
import subprocess
import glob
import json
from datetime import datetime
from vtkmodules.vtkRenderingCore import (
    vtkRenderer, vtkRenderWindow, vtkActor, vtkPolyDataMapper
)
from vtkmodules.vtkCommonDataModel import vtkPolyData, vtkCellArray
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkInteractionStyle import vtkInteractorStyleTrackballCamera
from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter
import vtkmodules.vtkRenderingOpenGL2

LAYER_COLORS = {
    "terrain_base.stl":       ("#FFFFFF", "Terrain"),
    "terrain_roads.stl":      ("#000000", "Routes"),
    "terrain_water.stl":      ("#0094FF", "Eau"),
    "terrain_vegetation.stl": ("#00D921", "Végétation"),
    "terrain_trees.stl":      ("#006921", "Arbres"),
    "terrain_buildings.stl":  ("#898989", "Bâtiments"),
}

PUZZLE_LAYER_COLORS = {
    "terrain":    "#FFFFFF",
    "roads":      "#000000",
    "water":      "#0094FF",
    "vegetation": "#00D921",
    "trees":      "#006921",
    "buildings":  "#898989",
}

PUZZLE_LAYER_LABELS = {
    "terrain": "Terrain", "roads": "Routes", "water": "Eau",
    "vegetation": "Végétation", "trees": "Arbres", "buildings": "Bâtiments",
}

def hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def trimesh_to_vtk_actor(mesh, hex_color):
    points = vtkPoints()
    for v in mesh.vertices:
        points.InsertNextPoint(float(v[0]), float(v[1]), float(v[2]))

    cells = vtkCellArray()
    for f in mesh.faces:
        cells.InsertNextCell(3)
        cells.InsertCellPoint(int(f[0]))
        cells.InsertCellPoint(int(f[1]))
        cells.InsertCellPoint(int(f[2]))

    polydata = vtkPolyData()
    polydata.SetPoints(points)
    polydata.SetPolys(cells)

    mapper = vtkPolyDataMapper()
    mapper.SetInputData(polydata)

    actor = vtkActor()
    actor.SetMapper(mapper)
    r, g, b = hex_to_rgb(hex_color)
    actor.GetProperty().SetColor(r/255, g/255, b/255)
    actor.GetProperty().SetOpacity(1.0)
    return actor

class VtkOffscreenWidget(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(400, 300)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet("background: #1E1E1E;")

        self._renderer = vtkRenderer()
        self._renderer.SetBackground(0.12, 0.12, 0.12)

        self._render_window = vtkRenderWindow()
        self._render_window.SetOffScreenRendering(1)
        self._render_window.AddRenderer(self._renderer)
        self._render_window.SetSize(800, 600)

        self._last_mouse = QPoint()
        self._mouse_button = None
        self.setMouseTracking(True)

    def add_actor(self, actor):
        self._renderer.AddActor(actor)

    def remove_all_actors(self):
        self._renderer.RemoveAllViewProps()

    def reset_camera(self):
        self._renderer.ResetCamera()

    def render_to_label(self):
        w = max(self.width(), 100)
        h = max(self.height(), 100)
        self._render_window.SetSize(w, h)
        self._render_window.Render()

        w2i = vtkWindowToImageFilter()
        w2i.SetInput(self._render_window)
        w2i.SetInputBufferTypeToRGB()
        w2i.Update()

        img = w2i.GetOutput()
        dims = img.GetDimensions()
        raw = img.GetPointData().GetScalars()
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(dims[1], dims[0], 3)
        arr = np.flipud(arr)

        h_img, w_img, _ = arr.shape
        qimg = QImage(arr.tobytes(), w_img, h_img, w_img * 3, QImage.Format.Format_RGB888)
        pix = QPixmap.fromImage(qimg)
        self.setPixmap(pix)
        self.setScaledContents(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._renderer.GetActors().GetNumberOfItems() > 0:
            self.render_to_label()
        panel = getattr(self.parent(), "_stats_panel", None)
        if panel is not None:
            width = min(320, self.width() - 24)
            panel.setGeometry(12, 56, width, self.height() - 68)

    def mousePressEvent(self, event: QMouseEvent):
        self._last_mouse = event.pos()
        self._mouse_button = event.button()

    def mouseReleaseEvent(self, event: QMouseEvent):
        self._mouse_button = None

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._mouse_button is None:
            return
        dx = event.pos().x() - self._last_mouse.x()
        dy = event.pos().y() - self._last_mouse.y()
        self._last_mouse = event.pos()

        camera = self._renderer.GetActiveCamera()
        if self._mouse_button == Qt.MouseButton.LeftButton:
            camera.Azimuth(-dx * 0.5)
            camera.Elevation(dy * 0.5)
            camera.OrthogonalizeViewUp()
        elif self._mouse_button == Qt.MouseButton.RightButton:
            camera.Dolly(1.0 + dy * 0.01)
            self._renderer.ResetCameraClippingRange()
        elif self._mouse_button == Qt.MouseButton.MiddleButton:
            fp = camera.GetFocalPoint()
            pos = camera.GetPosition()
            scale = camera.GetDistance() * 0.001
            camera.SetFocalPoint(fp[0] - dx*scale, fp[1] + dy*scale, fp[2])
            camera.SetPosition(pos[0] - dx*scale, pos[1] + dy*scale, pos[2])

        self.render_to_label()

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        camera = self._renderer.GetActiveCamera()
        if delta > 0:
            camera.Dolly(1.15)
        else:
            camera.Dolly(0.87)
        self._renderer.ResetCameraClippingRange()
        self.render_to_label()


class StlViewerPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._meshes = {}
        self._actors = {}
        self._checkboxes = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        checks_widget = QWidget()
        self._checks_layout = QHBoxLayout(checks_widget)
        self._checks_layout.setContentsMargins(4, 2, 4, 2)
        layout.addWidget(checks_widget)

        self._vtk_widget = VtkOffscreenWidget(self)
        layout.addWidget(self._vtk_widget, stretch=1)
        
        self._btn_export_3mf = QPushButton("Ouvrir")
        self._btn_export_3mf.setVisible(False)
        self._btn_export_3mf.clicked.connect(self._on_open_3mf)
        self._checks_layout.addWidget(self._btn_export_3mf)
        
        self._lbl_3mf_date = QLabel("")
        self._lbl_3mf_date.setStyleSheet("color: #888; font-size: 10px;")
        layout.addWidget(self._lbl_3mf_date)
        
        self._stats_btn = QPushButton("📊", self._vtk_widget)
        self._stats_btn.setFixedSize(36, 36)
        self._stats_btn.setToolTip("Statistiques")
        self._stats_btn.setStyleSheet("""
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
        self._stats_btn.clicked.connect(self._toggle_stats_panel)
        self._stats_btn.move(12, 12)
        self._stats_btn.raise_()
        
        self._stats_panel = QFrame(self._vtk_widget)
        self._stats_panel.setAttribute(Qt.WidgetAttribute.WA_NoMousePropagation)
        self._stats_panel.setStyleSheet("""
            QFrame {
                background: rgba(43, 43, 43, 235);
                border: 1px solid #555555;
                border-radius: 8px;
            }
            QLabel {
                background: transparent;
                color: #DDDDDD;
            }
        """)
        self._stats_scroll = QScrollArea(self._stats_panel)
        self._stats_scroll.setWidgetResizable(True)
        self._stats_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._stats_content = QWidget()
        self._stats_layout = QVBoxLayout(self._stats_content)
        self._stats_layout.setContentsMargins(10, 10, 10, 10)
        self._stats_layout.setSpacing(6)
        self._stats_scroll.setWidget(self._stats_content)
        stats_panel_layout = QVBoxLayout(self._stats_panel)
        stats_panel_layout.setContentsMargins(0, 0, 0, 0)
        stats_panel_layout.addWidget(self._stats_scroll)
        self._stats_panel.setVisible(False)
        self._stats_panel.raise_()
        
        self._explode_widget = QFrame(self._vtk_widget)
        self._explode_widget.setStyleSheet("""
            QFrame {
                background: rgba(43, 43, 43, 235);
                border: 1px solid #555555;
                border-radius: 8px;
            }
            QLabel { background: transparent; color: #DDDDDD; }
        """)
        explode_layout = QHBoxLayout(self._explode_widget)
        explode_layout.setContentsMargins(10, 6, 10, 6)
        explode_label = QLabel("Éclatement")
        self._explode_slider = QSlider(Qt.Orientation.Horizontal)
        self._explode_slider.setRange(0, 20)
        self._explode_slider.setValue(10)
        self._explode_slider.setFixedWidth(120)
        self._explode_value_lbl = QLabel("1.00")
        self._explode_value_lbl.setStyleSheet("min-width: 32px;")
        self._explode_slider.valueChanged.connect(self._on_explode_changed)
        explode_layout.addWidget(explode_label)
        explode_layout.addWidget(self._explode_slider)
        explode_layout.addWidget(self._explode_value_lbl)
        self._explode_widget.move(12, 60)
        self._explode_widget.adjustSize()
        self._explode_widget.setVisible(False)
        self._explode_widget.raise_()

    def showEvent(self, event):
        super().showEvent(event)
        if not self._meshes:
            main_win = self.window()
            stl_dir = "STL"
            if hasattr(main_win, 'param_panel'):
                stl_dir = main_win.param_panel.get_params().get("STL_DIR", "STL")
            self.reload_stl_folder(stl_dir)
            self._btn_export_3mf.setVisible(True)
        else:
            self._btn_export_3mf.setVisible(True)

    def reload_stl_folder(self, folder="STL"):
        self._meshes.clear()
        self._actors.clear()
        self._layer_groups = None
        self._vtk_widget.remove_all_actors()
        self._explode_widget.setVisible(False)

        puzzle_files = sorted(glob.glob(os.path.join(folder, "puzzle_piece*_*.stl")))
        if puzzle_files:
            self._load_puzzle(folder, puzzle_files)
        else:
            self._load_standard(folder)

        self._vtk_widget.reset_camera()
        self._vtk_widget.render_to_label()
        self._rebuild_checkboxes()

        project_name = "topopixel"
        win = self.window()
        if hasattr(win, '_project_name'):
            project_name = win._project_name
        mf = os.path.join(folder, f"{project_name}.3mf")
        if os.path.exists(mf):
            t = datetime.fromtimestamp(os.path.getmtime(mf))
            self._lbl_3mf_date.setText(f"3MF généré le {t.strftime('%d/%m/%Y %H:%M')}")
        else:
            self._lbl_3mf_date.setText("")
    
    def _load_standard(self, folder):
        for fname, (color_hex, label) in LAYER_COLORS.items():
            path = os.path.join(folder, fname)
            if not os.path.exists(path):
                continue
            try:
                loaded = trimesh.load(path)
                if isinstance(loaded, trimesh.Scene):
                    mesh = trimesh.util.concatenate(list(loaded.geometry.values()))
                else:
                    mesh = loaded
                if len(mesh.faces) == 0:
                    continue
                actor = trimesh_to_vtk_actor(mesh, color_hex)
                self._vtk_widget.add_actor(actor)
                self._meshes[fname] = mesh
                self._actors[fname] = actor
            except Exception as e:
                print(f"[VIEWER] erreur {fname} : {e}")

        for path in sorted(glob.glob(os.path.join(folder, "terrain_gpx_*.stl"))):
            fname = os.path.basename(path)
            try:
                loaded = trimesh.load(path)
                if isinstance(loaded, trimesh.Scene):
                    mesh = trimesh.util.concatenate(list(loaded.geometry.values()))
                else:
                    mesh = loaded
                if len(mesh.faces) == 0:
                    continue
                win = self.window()
                color = "#FF0000"
                if hasattr(win, 'param_panel'):
                    gpx_list = win.param_panel.get_gpx_list()
                    idx = int(fname.replace("terrain_gpx_", "").replace(".stl", ""))
                    if idx < len(gpx_list):
                        color = gpx_list[idx].get("color", "#FF0000")
                actor = trimesh_to_vtk_actor(mesh, color)
                self._vtk_widget.add_actor(actor)
                self._meshes[fname] = mesh
                self._actors[fname] = actor
            except Exception as e:
                print(f"[VIEWER] erreur {fname} : {e}")

    def _load_puzzle(self, folder, puzzle_files):
        self._layer_groups = {}
        self._puzzle_base = {}
        pieces = {}
        for path in puzzle_files:
            fname = os.path.basename(path)
            match = re.match(r"puzzle_piece(\d+)_(.+)\.stl", fname)
            if not match:
                continue
            piece_idx = int(match.group(1))
            layer_name = match.group(2)
            try:
                loaded = trimesh.load(path)
                if isinstance(loaded, trimesh.Scene):
                    mesh = trimesh.util.concatenate(list(loaded.geometry.values()))
                else:
                    mesh = loaded
                if len(mesh.faces) == 0:
                    continue
            except Exception as e:
                print(f"[VIEWER] erreur {fname} : {e}")
                continue
            pieces.setdefault(piece_idx, []).append((layer_name, mesh, fname))
            self._layer_groups.setdefault(layer_name, []).append(fname)

        if not pieces:
            self._explode_widget.setVisible(False)
            return

        piece_centers = {}
        for piece_idx, layers in pieces.items():
            all_verts = np.vstack([mesh.vertices for _, mesh, _ in layers])
            piece_centers[piece_idx] = all_verts[:, :2].mean(axis=0)

        global_center = np.mean(list(piece_centers.values()), axis=0)

        for piece_idx, layers in pieces.items():
            offset_xy = piece_centers[piece_idx] - global_center
            for layer_name, mesh, fname in layers:
                self._puzzle_base[fname] = (mesh, offset_xy, layer_name)

        self._explode_widget.setVisible(True)
        self._apply_explode_factor(self._explode_slider.value() * 0.05)

    def _apply_explode_factor(self, factor):
        if not getattr(self, "_puzzle_base", None):
            return
        self._actors.clear()
        self._meshes.clear()
        self._vtk_widget.remove_all_actors()
        for fname, (mesh, offset_xy, layer_name) in self._puzzle_base.items():
            exploded = mesh.copy()
            exploded.apply_translation([offset_xy[0] * factor, offset_xy[1] * factor, 0])
            color_hex = PUZZLE_LAYER_COLORS.get(layer_name, "#FF0000" if layer_name.startswith("gpx_") else "#888888")
            actor = trimesh_to_vtk_actor(exploded, color_hex)
            cb = self._checkboxes.get(layer_name)
            if cb is not None and not cb.isChecked():
                actor.SetVisibility(0)
            self._vtk_widget.add_actor(actor)
            self._actors[fname] = actor
            self._meshes[fname] = exploded
        self._vtk_widget.reset_camera()
        self._vtk_widget.render_to_label()

    def _on_explode_changed(self, value):
        factor = value * 0.05
        self._explode_value_lbl.setText(f"{factor:.2f}")
        self._apply_explode_factor(factor)
    
    def _on_toggle_group(self, fnames, state):
        visible = state == Qt.CheckState.Checked.value
        for fname in fnames:
            if fname in self._actors:
                self._actors[fname].SetVisibility(1 if visible else 0)
        self._vtk_widget.render_to_label()
    
    def _rebuild_checkboxes(self):
        while self._checks_layout.count():
            item = self._checks_layout.takeAt(0)
            w = item.widget()
            if w and w not in (self._btn_export_3mf,):
                w.deleteLater()
        self._checkboxes.clear()

        if getattr(self, "_layer_groups", None):
            for layer_name, fnames in self._layer_groups.items():
                if layer_name.startswith("gpx_"):
                    continue
                color_hex = PUZZLE_LAYER_COLORS.get(layer_name, "#888888")
                label = PUZZLE_LAYER_LABELS.get(layer_name, layer_name)
                r, g, b = hex_to_rgb(color_hex)
                cb = QCheckBox(label)
                cb.setChecked(True)
                cb.setStyleSheet(f"""
                    QCheckBox {{ color: rgb({r},{g},{b}); font-weight: bold; background: #3C3C3C; padding: 2px 6px; border-radius: 3px; }}
                    QCheckBox::indicator:checked {{ background: rgb({r},{g},{b}); border: 1px solid rgb({r},{g},{b}); }}
                    QCheckBox::indicator:unchecked {{ background: #3C3C3C; border: 1px solid rgb({r},{g},{b}); }}
                """)
                cb.stateChanged.connect(lambda state, names=fnames: self._on_toggle_group(names, state))
                self._checks_layout.addWidget(cb)
                self._checkboxes[layer_name] = cb

            gpx_names = sorted(name for name in self._layer_groups if name.startswith("gpx_"))
            if gpx_names:
                gpx_button = QToolButton()
                gpx_button.setText("GPX")
                gpx_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
                gpx_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
                gpx_button.setStyleSheet("""
                    QToolButton {
                        background: #3C3C3C;
                        border: 1px solid #555555;
                        border-radius: 4px;
                        padding: 2px 22px 2px 8px;
                        font-weight: bold;
                        color: #DDDDDD;
                    }
                    QToolButton:hover { border: 1px solid #888888; }
                    QToolButton::menu-indicator {
                        subcontrol-position: right center;
                        subcontrol-origin: padding;
                        right: 6px;
                    }
                """)
                gpx_menu = QMenu(gpx_button)
                for name in gpx_names:
                    fnames = self._layer_groups[name]
                    cb = QCheckBox(name.upper())
                    cb.setChecked(True)
                    cb.setStyleSheet("QCheckBox { color: #FF0000; font-weight: bold; padding: 4px 8px; }")
                    cb.stateChanged.connect(lambda state, names=fnames: self._on_toggle_group(names, state))
                    action = QWidgetAction(gpx_menu)
                    action.setDefaultWidget(cb)
                    gpx_menu.addAction(action)
                gpx_button.setMenu(gpx_menu)
                self._checks_layout.addWidget(gpx_button)

            self._checks_layout.addStretch()
            self._checks_layout.addWidget(self._btn_export_3mf)
            return

        for fname, (color_hex, label) in LAYER_COLORS.items():
            if fname not in self._actors:
                continue
            mesh = self._meshes.get(fname)
            if mesh is None or len(mesh.faces) == 0:
                continue
            r, g, b = hex_to_rgb(color_hex)
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(f"""
                QCheckBox {{ color: rgb({r},{g},{b}); font-weight: bold; background: #3C3C3C; padding: 2px 6px; border-radius: 3px; }}
                QCheckBox::indicator:checked {{ background: rgb({r},{g},{b}); border: 1px solid rgb({r},{g},{b}); }}
                QCheckBox::indicator:unchecked {{ background: #3C3C3C; border: 1px solid rgb({r},{g},{b}); }}
            """)
            cb.stateChanged.connect(lambda state, f=fname: self._on_toggle(f, state))
            self._checks_layout.addWidget(cb)
            self._checkboxes[fname] = cb

        win = self.window()
        gpx_list_raw = win.param_panel.get_gpx_list() if hasattr(win, 'param_panel') else []
        gpx_list = [g for g in gpx_list_raw if g.get("enabled") and g.get("path")]

        gpx_button = QToolButton()
        gpx_button.setText("GPX")
        gpx_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        gpx_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        gpx_button.setStyleSheet("""
            QToolButton {
                background: #3C3C3C;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 2px 22px 2px 8px;
                font-weight: bold;
                color: #DDDDDD;
            }
            QToolButton:hover { border: 1px solid #888888; }
            QToolButton::menu-indicator {
                subcontrol-position: right center;
                subcontrol-origin: padding;
                right: 6px;
            }
        """)
        gpx_menu = QMenu(gpx_button)
        has_gpx = False

        for i, gpx in enumerate(gpx_list):
            fname = f"terrain_gpx_{i}.stl"
            if fname not in self._actors:
                continue
            has_gpx = True
            color = gpx.get("color", "#FF0000")
            r, g, b = hex_to_rgb(color)
            cb = QCheckBox(f"GPX {i+1}")
            cb.setChecked(True)
            cb.setStyleSheet(f"""
                QCheckBox {{ color: rgb({r},{g},{b}); font-weight: bold; padding: 4px 8px; }}
                QCheckBox::indicator:checked {{ background: rgb({r},{g},{b}); border: 1px solid rgb({r},{g},{b}); }}
                QCheckBox::indicator:unchecked {{ background: #3C3C3C; border: 1px solid rgb({r},{g},{b}); }}
            """)
            cb.stateChanged.connect(lambda state, f=fname: self._on_toggle(f, state))
            action = QWidgetAction(gpx_menu)
            action.setDefaultWidget(cb)
            gpx_menu.addAction(action)
            self._checkboxes[fname] = cb

        if has_gpx:
            gpx_button.setMenu(gpx_menu)
            self._checks_layout.addWidget(gpx_button)

        self._checks_layout.addStretch()
        self._checks_layout.addWidget(self._btn_export_3mf)
    
    def _on_toggle(self, fname, state):
        if fname not in self._actors:
            return
        visible = state == Qt.CheckState.Checked.value
        self._actors[fname].SetVisibility(1 if visible else 0)
        self._vtk_widget.render_to_label()
        
    def _on_open_3mf(self):
        stl_dir = getattr(self, '_current_stl_dir', 'STL')
        win = self.window()
        project_name = getattr(win, '_project_name', 'topopixel')
        path = os.path.join(stl_dir, f"{project_name}.3mf")
        if os.path.exists(path):
            subprocess.Popen(['explorer', path] if os.name == 'nt' else ['xdg-open', path])
            self._lbl_3mf_date.setText(f"3MF généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}")
        else:
            print(f"[3MF] fichier introuvable : {path}")
            
    def update_layer_color(self, key, color):
        fname = f"terrain_{key}.stl"
        if fname not in self._actors:
            return
        r, g, b = hex_to_rgb(color)
        self._actors[fname].GetProperty().SetColor(r/255, g/255, b/255)
        self._vtk_widget.render_to_label()
        LAYER_COLORS[fname] = (color, LAYER_COLORS[fname][1])
        cb = self._checkboxes.get(fname)
        if cb:
            cb.setStyleSheet(f"""
                QCheckBox {{ color: rgb({r},{g},{b}); font-weight: bold; background: #3C3C3C; padding: 2px 6px; border-radius: 3px; }}
                QCheckBox::indicator:checked {{ background: rgb({r},{g},{b}); border: 1px solid rgb({r},{g},{b}); }}
                QCheckBox::indicator:unchecked {{ background: #3C3C3C; border: 1px solid rgb({r},{g},{b}); }}
            """)
            
    def _toggle_stats_panel(self):
        visible = not self._stats_panel.isVisible()
        self._stats_panel.setVisible(visible)
        if visible:
            self._refresh_stats_panel()
            
    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _add_stats_title(self, text):
        lbl = QLabel(text)  
        lbl.setStyleSheet("font-weight: bold; font-size: 13px; color: #FFFFFF; margin-top: 6px;")
        self._stats_layout.addWidget(lbl)

    def _add_stats_line(self, text):
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 11px; color: #CCCCCC;")
        self._stats_layout.addWidget(lbl)

    def _mesh_volume_g(self, mesh):
        if not mesh.is_watertight:
            return None
        volume_mm3 = abs(mesh.volume)
        return volume_mm3 / 1000 * 1.24

    def _refresh_stats_panel(self):
        self._clear_layout(self._stats_layout)

        win = self.window()
        stl_dir = getattr(win, "param_panel", None)
        stl_dir = stl_dir.get_params().get("STL_DIR", "STL") if stl_dir else "STL"

        metadata_path = os.path.join(stl_dir, "metadata.json")
        metadata = {}
        if os.path.exists(metadata_path):
            with open(metadata_path, "r") as f:
                metadata = json.load(f)

        layer_stats = metadata.get("layer_stats", {})
        resolution_m = metadata.get("resolution_m")
        cols = metadata.get("cols")
        rows = metadata.get("rows")

        scale_mm = metadata.get("scale_mm")
        if resolution_m and cols and rows:
            terrain_area_ha = (cols * resolution_m) * (rows * resolution_m) / 10000
            self._add_stats_title("Échelle")
            self._add_stats_line(f"Surface réelle : {terrain_area_ha:.2f} ha")
            if scale_mm:
                meters_per_cm = 10 * resolution_m / scale_mm
                self._add_stats_line(f"1 cm = {meters_per_cm:.1f} m")

        terrain_mesh = self._meshes.get("terrain_base.stl")
        if terrain_mesh is not None and len(terrain_mesh.faces) > 0:
            bounds = terrain_mesh.bounds
            base_thickness_mm = metadata.get("base_thickness_mm")
            self._add_stats_title("Terrain")
            self._add_stats_line(f"Empreinte : {bounds[1][0]-bounds[0][0]:.1f} x {bounds[1][1]-bounds[0][1]:.1f} mm")
            if base_thickness_mm is not None:
                relief_min = 0.0
                relief_max = bounds[1][2] - bounds[0][2] - base_thickness_mm
                self._add_stats_line(f"Hauteur relief : {relief_min:.1f} → {relief_max:.1f} mm")
            altitude_min = metadata.get("altitude_min_m")
            altitude_max = metadata.get("altitude_max_m")
            if altitude_min is not None and altitude_max is not None:
                self._add_stats_line(f"Altitude : {altitude_min:.1f} → {altitude_max:.1f} m")
            pla_grams = metadata.get("pla_grams", {})
            g = pla_grams.get("terrain")
            if g is not None:
                self._add_stats_line(f"PLA : {g:.1f} g")

            all_bounds_min = []
            all_bounds_max = []
            for mesh in self._meshes.values():
                if len(mesh.faces) > 0:
                    all_bounds_min.append(mesh.bounds[0][2])
                    all_bounds_max.append(mesh.bounds[1][2])
            if all_bounds_min:
                self._add_stats_title("Maquette")
                maquette_min = 0.0
                maquette_max = max(all_bounds_max) - min(all_bounds_min)
                self._add_stats_line(f"Hauteur maquette : {maquette_min:.1f} → {maquette_max:.1f} mm")

        topic_labels = {
            "roads": "Routes",
            "water": "Eau",
            "vegetation": "Végétation",
            "buildings": "Bâtiments",
        }
        topic_files = {
            "roads": "terrain_roads.stl",
            "water": "terrain_water.stl",
            "vegetation": "terrain_vegetation.stl",
            "buildings": "terrain_buildings.stl",
        }

        for topic, label in topic_labels.items():
            stats = layer_stats.get(topic)
            if not stats:
                print(topic,"no stats")
                continue
            self._add_stats_title(label)
            if topic == "roads":
                self._add_stats_line(f"Segments : {stats['count']}")
                self._add_stats_line(f"Distance : {stats['distance_m']/1000:.2f} km")
            elif topic == "water":
                if "waterway_count" in stats:
                    self._add_stats_line(f"Cours d'eau : {stats['waterway_count']}")
                    self._add_stats_line(f"Longueur cours d'eau : {stats['waterway_distance_m']/1000:.2f} km")
                if "area_count" in stats:
                    self._add_stats_line(f"Surfaces d'eau : {stats['area_count']}")
                    self._add_stats_line(f"Surface totale : {stats['area_ha']:.2f} ha")
            elif topic in ("vegetation", "buildings"):
                self._add_stats_line(f"Nombre : {stats['count']}")
                self._add_stats_line(f"Surface : {stats['area_ha']:.2f} ha")

            pla_grams = metadata.get("pla_grams", {})
            g = pla_grams.get(topic)
            if g is not None:
                self._add_stats_line(f"PLA : {g:.1f} g")

        gpx_stats = layer_stats.get("gpx", [])
        if gpx_stats:
            self._add_stats_title("GPX")
            for i, gpx in enumerate(gpx_stats):
                name = os.path.basename(gpx["path"])
                if len(name) > 20:
                    name = name[:17] + "..."
                self._add_stats_line(f"{name} : {gpx['distance_m']/1000:.2f} km")
                mesh = self._meshes.get(f"terrain_gpx_{i}.stl")
                if mesh is not None and len(mesh.faces) > 0:
                    g = self._mesh_volume_g(mesh)
                    if g is not None:
                        self._add_stats_line(f"PLA : {g:.1f} g")

        self._stats_layout.addStretch()
import os
import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QCheckBox, QLabel, QApplication, QPushButton, QToolButton, QMenu, QWidgetAction
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QImage, QMouseEvent, QWheelEvent
import trimesh
import subprocess
import glob
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
        self._vtk_widget.remove_all_actors()

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

    def _rebuild_checkboxes(self):
        while self._checks_layout.count():
            item = self._checks_layout.takeAt(0)
            w = item.widget()
            if w and w not in (self._btn_export_3mf,):
                w.deleteLater()
        self._checkboxes.clear()
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
                background: white;
                border: none;
                padding: 2px 8px;
                font-weight: bold;
                color: #333333;
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
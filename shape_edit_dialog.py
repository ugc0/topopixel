from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLineEdit,
    QLabel, QScrollArea, QWidget, QDialogButtonBox
)
from PyQt6.QtCore import Qt, pyqtSignal, QRegularExpression
from PyQt6.QtGui import QRegularExpressionValidator, QFont


class CoordEdit(QLineEdit):
    focused = pyqtSignal(int)
    unfocused = pyqtSignal()

    def __init__(self, text, index):
        super().__init__(text)
        self._index = index

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.focused.emit(self._index)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.unfocused.emit()


class ShapeEditDialog(QDialog):
    vertex_focused = pyqtSignal(int)
    vertex_unfocused = pyqtSignal()
    coords_changed = pyqtSignal(dict)

    def __init__(self, shape_kind, shape_params, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Éditer l'emprise")
        self.setModal(True)
        self.resize(360, 420)
        self._shape_kind = shape_kind
        self._fields = []

        self.setStyleSheet("""
            QDialog { background: #2B2B2B; }
            QLabel#header {
                background: #1971C2; color: white;
                font-weight: bold; font-size: 13px;
                padding: 10px 14px; border-radius: 6px;
            }
            QLineEdit {
                border: 1.5px solid #CED4DA;
                border-radius: 5px;
                padding: 4px 8px;
                font-size: 12px;
                background: black;
                color: #1971C2;
            }
            QLineEdit:focus { border: 1.5px solid #1971C2; }
            QLabel#index {
                color: #1971C2; font-weight: bold;
                font-size: 12px; min-width: 28px;
            }
            QLabel#coord {
                color: #555; font-size: 11px; min-width: 16px;
            }
            QDialogButtonBox QPushButton {
                padding: 6px 18px; border-radius: 5px;
                font-size: 12px; font-weight: bold;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(14, 14, 14, 14)

        header = QLabel("Coordonnées GPS" if shape_kind == "rect" else f"Polygone — {len(shape_params['points'])} sommets")
        header.setObjectName("header")
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        form = QVBoxLayout(content)
        form.setSpacing(4)
        form.setContentsMargins(0, 4, 0, 4)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        validator = QRegularExpressionValidator(QRegularExpression(r"-?\d{0,3}\.?\d{0,8}"), self)

        if shape_kind == "rect":
            p = shape_params
            for key, label, value in [
                ("north", "N", p["north"]),
                ("south", "S", p["south"]),
                ("west",  "W", p["west"]),
                ("east",  "E", p["east"]),
            ]:
                row = QHBoxLayout()
                row.setSpacing(6)
                lbl = QLabel(label)
                lbl.setObjectName("coord")
                edit = QLineEdit(f"{value:.8f}")
                edit.setValidator(validator)
                edit.textChanged.connect(self._emit_coords)
                row.addWidget(lbl)
                row.addWidget(edit)
                form.addLayout(row)
                self._fields.append((key, edit))

        elif shape_kind == "polygon":
            points = shape_params["points"]
            for i, (lon, lat) in enumerate(points):
                row_widget = QWidget()
                row = QHBoxLayout(row_widget)
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(6)
                lbl = QLabel(f"#{i+1}")
                lbl.setObjectName("index")
                lon_lbl = QLabel("E")
                lon_lbl.setObjectName("coord")
                lat_lbl = QLabel("N")
                lat_lbl.setObjectName("coord")
                lon_edit = CoordEdit(f"{lon:.8f}", i)
                lat_edit = CoordEdit(f"{lat:.8f}", i)
                lon_edit.setValidator(validator)
                lat_edit.setValidator(validator)
                lon_edit.focused.connect(self.vertex_focused)
                lat_edit.focused.connect(self.vertex_focused)
                lon_edit.unfocused.connect(self.vertex_unfocused)
                lat_edit.unfocused.connect(self.vertex_unfocused)
                lon_edit.textChanged.connect(self._emit_coords)
                lat_edit.textChanged.connect(self._emit_coords)
                row.addWidget(lbl)
                row.addWidget(lon_lbl)
                row.addWidget(lon_edit)
                row.addWidget(lat_lbl)
                row.addWidget(lat_edit)
                form.addWidget(row_widget)
                self._fields.append((i, lon_edit, lat_edit))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_result(self):
        if self._shape_kind == "rect":
            return {k: float(edit.text()) for k, edit in self._fields}
        elif self._shape_kind == "polygon":
            return {"points": [[float(lon.text()), float(lat.text())] for _, lon, lat in self._fields]}
        return None
        
    def _emit_coords(self):
        try:
            result = self.get_result()
            if result:
                self.coords_changed.emit(result)
        except Exception:
            pass
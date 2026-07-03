import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QDoubleSpinBox, QSpinBox,
    QCheckBox, QLineEdit, QScrollArea, QLabel, QGridLayout, QSizePolicy,
    QToolButton, QFrame, QPushButton
)
from PyQt6.QtCore import Qt, pyqtSignal

from constants import TOPIC_COLORS, checkbox_style

import shutil

ALL_ROAD_LEVELS = [
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link", "residential_link",
]

DEFAULT_ROAD_LEVELS_CHECKED = {
    "motorway", "trunk", "primary", "secondary", "tertiary", "residential",
    "motorway_link", "trunk_link", "primary_link", "secondary_link",
    "tertiary_link", "residential_link",
}

PANEL_BG = "#F1F3F5"
TEXT_COLOR = "#212529"

PANEL_STYLESHEET = f"""
QWidget#paramPanelContent {{
    background-color: {PANEL_BG};
}}
QLabel {{
    color: {TEXT_COLOR};
    font-size: 12px;
}}
QLabel#sectionHint {{
    color: #495057;
    font-size: 11px;
}}
QDoubleSpinBox, QSpinBox, QLineEdit {{
    background-color: #FFFFFF;
    border: 1px solid #CED4DA;
    border-radius: 5px;
    padding: 4px 6px;
    color: {TEXT_COLOR};
    selection-background-color: #4C6EF5;
}}
QDoubleSpinBox:hover, QSpinBox:hover, QLineEdit:hover {{
    border: 1px solid #868E96;
}}
QDoubleSpinBox:focus, QSpinBox:focus, QLineEdit:focus {{
    border: 1.5px solid #4C6EF5;
}}
QCheckBox {{
    spacing: 6px;
    color: {TEXT_COLOR};
    font-size: 12px;
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 4px;
    border: 1.5px solid #ADB5BD;
    background-color: #FFFFFF;
}}
QCheckBox::indicator:checked {{
    background-color: #4C6EF5;
    border: 1.5px solid #4C6EF5;
}}
QCheckBox::indicator:hover {{
    border: 1.5px solid #4C6EF5;
}}
QScrollArea {{
    background-color: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #ADB5BD;
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: #868E96;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}
"""

class CollapsibleSection(QFrame):

    def __init__(self, title, accent, expanded=True, hint=None, parent=None):
        super().__init__(parent)
        self._title = title
        self._accent = accent

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 12)
        outer.setSpacing(0)

        self.header = QToolButton(self)
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.header.clicked.connect(self._on_toggle)
        self.header.setObjectName("sectionHeader")
        self.header.setStyleSheet(f"""
            QToolButton#sectionHeader {{
                background-color: {accent};
                color: #FFFFFF;
                border: none;
                border-radius: 8px;
                padding: 9px 12px;
                font-weight: bold;
                font-size: 13px;
                text-align: left;
            }}
            
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

        outer.addWidget(self.header)
        outer.addWidget(self.body)

        self.body.setVisible(expanded)
        self._refresh_header_text()

    def _refresh_header_text(self):
        arrow = "▾" if self.header.isChecked() else "▸"
        self.header.setText(f"{arrow}  {self._title}")

    def _on_toggle(self):
        expanded = self.header.isChecked()
        self.body.setVisible(expanded)
        self._refresh_header_text()

    def form(self):
        f = QFormLayout()

        f.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        f.setVerticalSpacing(6)

        container = QWidget(self.body)
        container.setLayout(f)
        self.body_layout.addWidget(container)

        return f

    def add_widget(self, widget):
        self.body_layout.addWidget(widget)

    def add_layout(self, layout):
        self.body_layout.addLayout(layout)

class ParamPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(PANEL_STYLESHEET)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 4, 4, 4)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)

        content = QWidget()
        content.setObjectName("paramPanelContent")
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self._fields = {}

        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_roads_group())
        layout.addWidget(self._build_water_group())
        layout.addWidget(self._build_vegetation_group())
        layout.addWidget(self._build_buildings_group())
        layout.addWidget(self._build_advanced_group())

        layout.addStretch(1)
        
    road_levels_changed = pyqtSignal()
    water_filter_changed = pyqtSignal()
    building_filter_changed = pyqtSignal()

    def _build_general_group(self):
        section = CollapsibleSection("Général", accent="#9C36B5")
        form = section.form()

        self._add_int(form, "RESOLUTION_M", "Résolution DEM (m/px)", 1, 1, 30)
        self._add_int(form, "SIZE_MM", "Taille impression (mm)", 120, 10, 500, 1)
        self._add_int(form, "BASE_THICKNESS", "Épaisseur socle", 20, 1, 200, 1)
        
        lbl_layers = QLabel("Couches à générer :")
        form.addRow(lbl_layers)

        self._layer_checkboxes = {}
        layers = [
            ("terrain",    "Terrain"),
            ("roads",      "Routes"),
            ("water",      "Eau"),
            ("vegetation", "Végétation"),
            ("trees",      "Arbres"),
            ("buildings",  "Bâtiments"),
        ]
        for key, label in layers:
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(checkbox_style(TOPIC_COLORS.get(key, "#888888")))
            self._layer_checkboxes[key] = cb
            form.addRow(cb)

        return section

    def _build_roads_group(self):
        section = CollapsibleSection("Routes", accent="#000000", expanded=False)

        lbl = QLabel("Types de voies à modéliser :")
        section.add_widget(lbl)

        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        self._road_checkboxes = {}
        for i, level in enumerate(ALL_ROAD_LEVELS):
            cb = QCheckBox(level)
            cb.setChecked(level in DEFAULT_ROAD_LEVELS_CHECKED)
            self._road_checkboxes[level] = cb
            cb.stateChanged.connect(lambda: self.road_levels_changed.emit())
            grid.addWidget(cb, i, 0)
        section.add_layout(grid)

        form = section.form()
        self._add_int(form, "ROAD_WIDTH_PX", "Largeur route (px)", 1, 1, 20, 1)
        self._add_int(form, "ROAD_HEIGHT", "Surélévation route", 6, 0, 50, 1)

        return section

    def _build_water_group(self):
        section = CollapsibleSection("Hydrographie", accent="#1971C2", expanded=False)
        form = section.form()

        self._add_int(form, "RIVER_WIDTH_PX", "Largeur cours d'eau (px)", 3, 1, 30, 1)
        self._add_int(form, "WATER_HEIGHT", "Surélévation eau", 3, 0, 50, 1)
        self._add_int(form, "MIN_WATER_AREA_M2", "Surface min plan d'eau (m²)", 5000, 0, 1_000_000, 100)
        self._add_int(form, "MIN_WATERWAY_LENGTH_M", "Longueur min cours d'eau (m)", 500, 0, 100_000, 50)
        
        self._fields["MIN_WATER_AREA_M2"].valueChanged.connect(lambda: self.water_filter_changed.emit())
        self._fields["MIN_WATERWAY_LENGTH_M"].valueChanged.connect(lambda: self.water_filter_changed.emit())

        return section

    def _build_vegetation_group(self):
        section = CollapsibleSection("Végatation", accent="#2F9E44", expanded=False)
        form = section.form()

        self._add_int(form, "TREE_HEIGHT", "Hauteur arbre", 3, 1, 50, 1)
        self._add_double(form, "TREE_RADIUS", "Rayon base arbre", 1.5, 0.1, 20, 0.1)
        self._add_double(form, "TREE_DENSITY", "Densité arbres (par px²)", 0.008, 0.0, 1.0, 0.001)

        return section

    def _build_buildings_group(self):
        section = CollapsibleSection("Bâtiments", accent="#495057", expanded=False)
        form = section.form()

        self._add_int(form, "MIN_BUILDING_AREA_M2", "Surface min bâtiment (m²)", 250, 0, 100_000, 10)
        self._add_int(form, "DEFAULT_BUILDING_HEIGHT_M", "Hauteur par défaut (m)", 6, 1, 200, 1)
        self._add_int(form, "METERS_PER_LEVEL", "Mètres par étage", 3, 1, 10, 1)
        self._add_int(form, "BUILDING_HEIGHT_SCALE", "Échelle hauteur bâtiment", 10, 1, 100, 1)
        self._add_int(form, "BUILDING_MIN_HEIGHT", "Hauteur min (modèle)", 2, 0, 100, 1)
        self._add_int(form, "BUILDING_MAX_HEIGHT", "Hauteur max (modèle)", 20, 0, 200, 1)
        
        self._fields["MIN_BUILDING_AREA_M2"].valueChanged.connect(lambda: self.building_filter_changed.emit())

        return section

    def _build_advanced_group(self):
        section = CollapsibleSection(
            "Avancé", accent="#9C36B5", expanded=False,
            hint="Clé API et dossiers de cache et d'export du modèle numérique de terrain."
        )
        form = section.form()

        gpxz_key = QLineEdit("ak_0KtNnPbu_v9orPuogXRYmTu0p")
        gpxz_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._fields["GPXZ_API_KEY"] = gpxz_key
        form.addRow("Clé API GPXZ", gpxz_key)

        cache_dir = QLineEdit("cache")
        self._fields["CACHE_DIR"] = cache_dir
        form.addRow("Dossier cache DEM", cache_dir)
        
        stl_dir = QLineEdit("STL")
        self._fields["STL_DIR"] = stl_dir
        form.addRow("Dossier STL", stl_dir)
        
        self._btn_clear_cache = QPushButton("Effacer le cache (calcul en cours...)")
        self._btn_clear_cache.clicked.connect(self._on_clear_cache)
        form.addRow(self._btn_clear_cache)
        self._update_cache_size()

        return section

    def _add_double(self, form, key, label, default, lo, hi, step):
        spin = QDoubleSpinBox()
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setDecimals(4 if step < 0.01 else (3 if step < 1 else 1))
        spin.setValue(default)
        self._fields[key] = spin
        form.addRow(label, spin)

    def _add_int(self, form, key, label, default, lo, hi, step=1):
        spin = QSpinBox()
        spin.setStyleSheet("QSpinBox::up-button, QSpinBox::down-button { width: 20px; }")
        spin.setRange(lo, hi)
        spin.setSingleStep(step)
        spin.setValue(default)
        self._fields[key] = spin
        form.addRow(label, spin)

    def get_params(self):
        result = {}
        for key, widget in self._fields.items():
            if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                result[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()

        checked_levels = [lvl for lvl, cb in self._road_checkboxes.items() if cb.isChecked()]
        result["ROAD_LEVELS"] = checked_levels
        result["ENABLED_LAYERS"] = [k for k, cb in self._layer_checkboxes.items() if cb.isChecked()]

        return result
        
    def _get_cache_size(self):
        cache_dir = self._fields["CACHE_DIR"].text()
        if not os.path.isdir(cache_dir):
            return 0
        total = 0
        for dirpath, _, filenames in os.walk(cache_dir):
            for f in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, f))
                except:
                    pass
        return total

    def _update_cache_size(self):
        size = self._get_cache_size()
        if size < 1024 * 1024:
            label = f"{size // 1024} Ko"
        else:
            label = f"{size / (1024*1024):.1f} Mo"
        self._btn_clear_cache.setText(f"Effacer le cache ({label})")

    def _on_clear_cache(self):
        cache_dir = self._fields["CACHE_DIR"].text()
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir)
            os.makedirs(cache_dir)
        self._update_cache_size()
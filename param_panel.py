from logger import log
import os
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QDoubleSpinBox, QSpinBox,
    QCheckBox, QLineEdit, QScrollArea, QLabel, QSizePolicy,
    QToolButton, QFrame, QPushButton, QColorDialog, QHBoxLayout, QFileDialog, QSlider, QMenu, QWidgetAction, QMessageBox, QStackedLayout
)
from PyQt6.QtCore import Qt, pyqtSignal, QEvent, QLocale
from PyQt6.QtGui import QColor, QDoubleValidator, QIntValidator

from constants import TOPIC_COLORS, checkbox_style, color_button_style
from tooltips import TOOLTIPS
import topopixel as tp
import shutil

ALL_ROAD_LEVELS = tp.ROAD_LEVELS
DEFAULT_ROAD_LEVELS_CHECKED = set(tp.ROAD_LEVELS_DRIVABLE)

PANEL_BG = "#2B2B2B"
TEXT_COLOR = "#DDDDDD"

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
    background-color: #3C3C3C;
    border: 1px solid #555555;
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
    background-color: #3C3C3C;
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

class ClickableLabel(QLabel):
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


from PyQt6.QtGui import QIntValidator, QDoubleValidator

class EditableValue(QWidget):
    valueChanged = pyqtSignal(object)

    def __init__(self, value, lo, hi, decimals=None, parent=None):
        super().__init__(parent)
        self.lo = lo
        self.hi = hi
        self.decimals = decimals

        self._stack = QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)

        self.label = QLabel(self._fmt(value))
        self.label.setMinimumWidth(50)

        self.edit = QLineEdit(self._fmt(value))
        self.edit.setMinimumWidth(50)
        if decimals is None:
            self.edit.setValidator(QIntValidator(lo, hi, self))
        else:
            validator = QDoubleValidator(lo, hi, decimals, self)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            self.edit.setValidator(validator)

        self._stack.addWidget(self.label)
        self._stack.addWidget(self.edit)
        self._stack.setCurrentWidget(self.label)
        
        if decimals is None:
            self.edit.setValidator(QIntValidator(lo, hi, self))
        else:
            validator = QDoubleValidator(lo, hi, decimals, self)
            validator.setNotation(QDoubleValidator.Notation.StandardNotation)
            validator.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
            self.edit.setValidator(validator)

        self.edit.editingFinished.connect(self._finish_edit)

    def _fmt(self, v):
        return f"{v:.{self.decimals}f}" if self.decimals is not None else str(v)

    def _parse(self, text):
        return float(text) if self.decimals is not None else int(text)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.edit.setText(self.label.text())
            self.edit.selectAll()
            self._stack.setCurrentWidget(self.edit)
            self.edit.setFocus()
        super().mouseDoubleClickEvent(event)

    def _finish_edit(self):
        try:
            v = self._parse(self.edit.text().replace(",", "."))
        except ValueError:
            v = self._parse(self.label.text())
        v = max(self.lo, min(self.hi, v))
        self.set_value(v)
        self.valueChanged.emit(v)

    def set_value(self, v):
        self.label.setText(self._fmt(v))
        self.edit.setText(self._fmt(v))
        self._stack.setCurrentWidget(self.label)

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
        self._options_provider = None

        layout.addWidget(self._build_general_group())
        layout.addWidget(self._build_roads_group())
        layout.addWidget(self._build_water_group())
        layout.addWidget(self._build_vegetation_group())
        layout.addWidget(self._build_buildings_group())
        layout.addWidget(self._build_gpx_group())

        layout.addStretch(1)
        
    road_levels_changed = pyqtSignal()
    railway_option_changed = pyqtSignal()
    water_filter_changed = pyqtSignal()
    building_filter_changed = pyqtSignal()
    color_changed = pyqtSignal(str, str)
    gpx_changed = pyqtSignal()
    layer_enabled_changed = pyqtSignal(str, bool)

    def _build_general_group(self):
        section = CollapsibleSection("Général", accent="#9C36B5")
        form = section.form()

        self._add_int(form, "RESOLUTION_M", "Résolution DEM (m/px)", 5, 1, 30, tooltip=TOOLTIPS.get("RESOLUTION_M"))
        self._add_int(form, "SIZE_MM", "Taille impression (mm)", 120, 10, 500, 1, tooltip=TOOLTIPS.get("SIZE_MM"))
        self._add_int(form, "BASE_THICKNESS", "Épaisseur socle", 20, 1, 200, 1, tooltip=TOOLTIPS.get("BASE_THICKNESS"))
        self._add_double(form, "Z_SCALE", "Exagération verticale", 1.0, 0.1, 5.0, 0.1, tooltip=TOOLTIPS.get("Z_SCALE"))
        self._add_int(form, "PUZZLE_N_PIECES", "Nombre de pièces puzzle", 0, 0, 16, 1, tooltip=TOOLTIPS.get("PUZZLE_N_PIECES"))
        
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
            ("gpx", "Tracés GPX"),
        ]
        self._color_buttons = {}
        for key, label in layers:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(6)
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(checkbox_style(TOPIC_COLORS.get(key, "#888888")))
            cb.stateChanged.connect(lambda state, k=key: self.layer_enabled_changed.emit(k, state == Qt.CheckState.Checked.value))
            self._layer_checkboxes[key] = cb
            btn = QPushButton()
            btn.setStyleSheet(color_button_style(TOPIC_COLORS.get(key, "#888888")))
            btn.clicked.connect(lambda _, k=key, b=btn, c=cb: self._on_pick_color(k, b, c))
            self._color_buttons[key] = btn
            row_layout.addWidget(cb)
            row_layout.addWidget(btn)
            row_layout.addStretch()
            form.addRow(row)

        return section

    def _build_roads_group(self):
        section = CollapsibleSection("Routes", accent="#000000", expanded=False)

        self._road_checkboxes = {}

        drivable_button = QToolButton()
        drivable_button.setText("Voies carrossables")
        drivable_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        drivable_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        drivable_button.setStyleSheet("""
            QToolButton {
                background: #3C3C3C;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 22px 4px 10px;
                color: #DDDDDD;
            }
            QToolButton:hover { border: 1px solid #888888; }
            QToolButton::menu-indicator {
                subcontrol-position: right center;
                subcontrol-origin: padding;
                right: 6px;
            }
        """)
        drivable_menu = QMenu(drivable_button)
        drivable_menu.setStyleSheet("""
            QMenu {
                background: #3C3C3C;
                border: 1px solid #555555;
            }
        """)
        for level in tp.ROAD_LEVELS_DRIVABLE:
            cb = QCheckBox(level)
            cb.setChecked(level in DEFAULT_ROAD_LEVELS_CHECKED)
            cb.setStyleSheet("QCheckBox { color: #DDDDDD; padding: 6px 10px; }")
            cb.stateChanged.connect(lambda: self.road_levels_changed.emit())
            self._road_checkboxes[level] = cb
            action = QWidgetAction(drivable_menu)
            action.setDefaultWidget(cb)
            drivable_menu.addAction(action)
        drivable_button.setMenu(drivable_menu)
        section.add_widget(drivable_button)

        non_drivable_button = QToolButton()
        non_drivable_button.setText("Chemins et sentiers")
        non_drivable_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        non_drivable_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        non_drivable_button.setStyleSheet("""
            QToolButton {
                background: #3C3C3C;
                border: 1px solid #555555;
                border-radius: 4px;
                padding: 4px 22px 4px 10px;
                color: #DDDDDD;
            }
            QToolButton:hover { border: 1px solid #888888; }
            QToolButton::menu-indicator {
                subcontrol-position: right center;
                subcontrol-origin: padding;
                right: 6px;
            }
        """)
        non_drivable_menu = QMenu(non_drivable_button)
        non_drivable_menu.setStyleSheet("""
            QMenu {
                background: #3C3C3C;
                border: 1px solid #555555;
            }
        """)

        for level in tp.ROAD_LEVELS_NON_DRIVABLE:
            cb = QCheckBox(level)
            cb.setChecked(level in DEFAULT_ROAD_LEVELS_CHECKED)
            cb.setStyleSheet("QCheckBox { color: #DDDDDD; padding: 6px 10px; }")
            cb.stateChanged.connect(lambda: self.road_levels_changed.emit())
            self._road_checkboxes[level] = cb
            action = QWidgetAction(non_drivable_menu)
            action.setDefaultWidget(cb)
            non_drivable_menu.addAction(action)
        non_drivable_button.setMenu(non_drivable_menu)
        section.add_widget(non_drivable_button)
        
        railway_cb = QCheckBox("Inclure les voies ferrées")
        railway_cb.setChecked(False)
        railway_cb.setStyleSheet("QCheckBox { color: #DDDDDD; }")
        railway_cb.stateChanged.connect(lambda: self.railway_option_changed.emit())
        self._fields["INCLUDE_RAILWAYS"] = railway_cb
        section.add_widget(railway_cb)

        form = section.form()
        self._add_int(form, "ROAD_WIDTH_PX", "Largeur route (px)", 1, 1, 20, 1, handle_color="#CCCCCC", tooltip=TOOLTIPS.get("ROAD_WIDTH_PX"))
        self._add_int(form, "ROAD_HEIGHT", "Surélévation route", 6, 0, 50, 1, handle_color="#CCCCCC", tooltip=TOOLTIPS.get("ROAD_HEIGHT"))
        self._add_int(form, "ROADS_Z_BOT_RATIO_PCT", "Profondeur socle routes (%)", 33, 0, 100, 1, handle_color="#CCCCCC", tooltip=TOOLTIPS.get("ROADS_Z_BOT_RATIO_PCT"))

        return section

    def _build_water_group(self):
        section = CollapsibleSection("Hydrographie", accent="#1971C2", expanded=False)
        form = section.form()

        self._add_int(form, "RIVER_WIDTH_PX", "Largeur cours d'eau (px)", 3, 1, 30, 1, handle_color=TOPIC_COLORS["water"], tooltip=TOOLTIPS.get("RIVER_WIDTH_PX"))
        self._add_int(form, "WATER_HEIGHT", "Surélévation eau", 3, 0, 50, 1, handle_color=TOPIC_COLORS["water"], tooltip=TOOLTIPS.get("WATER_HEIGHT"))
        self._add_int(form, "MIN_WATER_AREA_M2", "Surface min plan d'eau (m²)", 5000, 0, 1_000_000, 100, handle_color=TOPIC_COLORS["water"], tooltip=TOOLTIPS.get("MIN_WATER_AREA_M2"))
        self._add_int(form, "MIN_WATERWAY_LENGTH_M", "Longueur min cours d'eau (m)", 500, 0, 100_000, 50, handle_color=TOPIC_COLORS["water"], tooltip=TOOLTIPS.get("MIN_WATERWAY_LENGTH_M"))
        self._add_int(form, "WATER_Z_BOT_RATIO_PCT", "Profondeur socle eau (%)", 33, 0, 100, 1, handle_color=TOPIC_COLORS["water"], tooltip=TOOLTIPS.get("WATER_Z_BOT_RATIO_PCT"))
        
        self._fields["MIN_WATER_AREA_M2"].valueChanged.connect(lambda: self.water_filter_changed.emit())
        self._fields["MIN_WATERWAY_LENGTH_M"].valueChanged.connect(lambda: self.water_filter_changed.emit())
        
        bathy_cb = QCheckBox("Activer la bathymétrie")
        bathy_cb.setChecked(False)
        bathy_cb.setStyleSheet(f"QCheckBox {{ color: {TOPIC_COLORS['water']}; }}")
        self._fields["ENABLE_BATHYMETRY"] = bathy_cb
        section.add_widget(bathy_cb)

        return section

    def _build_vegetation_group(self):
        section = CollapsibleSection("Végatation", accent="#2F9E44", expanded=False)
        form = section.form()

        self._add_int(form, "TREE_HEIGHT", "Hauteur arbre", 3, 1, 50, 1, handle_color=TOPIC_COLORS["vegetation"], tooltip=TOOLTIPS.get("TREE_HEIGHT"))
        self._add_int(form, "TREE_RADIUS", "Rayon base arbre", 1, 1, 20, 1, handle_color=TOPIC_COLORS["vegetation"], tooltip=TOOLTIPS.get("TREE_RADIUS"))
        self._add_int(form, "TREE_DENSITY", "Densité arbres (‰ par px²)", 8, 0, 500, 1, handle_color=TOPIC_COLORS["vegetation"], tooltip=TOOLTIPS.get("TREE_DENSITY"))
        self._add_int(form, "VEG_Z_BOT_RATIO_PCT", "Profondeur socle végétation (%)", 90, 0, 100, 1, handle_color=TOPIC_COLORS["vegetation"], tooltip=TOOLTIPS.get("VEG_Z_BOT_RATIO_PCT"))

        return section

    def _build_buildings_group(self):
        section = CollapsibleSection("Bâtiments", accent="#495057", expanded=False)
        form = section.form()

        self._add_int(form, "MIN_BUILDING_AREA_M2", "Surface min bâtiment (m²)", 250, 0, 100_000, 10, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("MIN_BUILDING_AREA_M2"))
        self._add_int(form, "DEFAULT_BUILDING_HEIGHT_M", "Hauteur par défaut (m)", 6, 1, 200, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("DEFAULT_BUILDING_HEIGHT_M"))
        self._add_int(form, "METERS_PER_LEVEL", "Mètres par étage", 3, 1, 10, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("METERS_PER_LEVEL"))
        self._add_int(form, "BUILDING_HEIGHT_SCALE", "Échelle hauteur bâtiment", 10, 1, 100, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("BUILDING_HEIGHT_SCALE"))
        self._add_int(form, "BUILDING_MIN_HEIGHT", "Hauteur min (modèle)", 2, 0, 100, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("BUILDING_MIN_HEIGHT"))
        self._add_int(form, "BUILDING_MAX_HEIGHT", "Hauteur max (modèle)", 20, 0, 200, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("BUILDING_MAX_HEIGHT"))
        self._add_int(form, "BUILDINGS_Z_BOT_RATIO_PCT", "Profondeur socle bâtiments (%)", 90, 0, 100, 1, handle_color=TOPIC_COLORS["buildings"], tooltip=TOOLTIPS.get("BUILDINGS_Z_BOT_RATIO_PCT"))
        
        self._fields["MIN_BUILDING_AREA_M2"].valueChanged.connect(lambda: self.building_filter_changed.emit())

        return section

    def _build_gpx_group(self):
        section = CollapsibleSection("Tracés GPX", accent="#C2163A", expanded=False)

        self._gpx_list_layout = QVBoxLayout()
        self._gpx_list_layout.setSpacing(4)
        self._gpx_entries = []

        section.add_layout(self._gpx_list_layout)

        btn_add = QPushButton("+ Ajouter des tracés GPX")
        btn_add.clicked.connect(self._on_add_gpx)
        section.add_widget(btn_add)

        form = section.form()
        self._add_int(form, "GPX_WIDTH_PX", "Largeur tracé (px)", 2, 1, 20, 1, handle_color=TOPIC_COLORS["gpx"], tooltip=TOOLTIPS.get("GPX_WIDTH_PX"))
        self._add_int(form, "GPX_HEIGHT", "Hauteur au-dessus", 4, 0, 50, 1, handle_color=TOPIC_COLORS["gpx"], tooltip=TOOLTIPS.get("GPX_HEIGHT"))
        self._add_int(form, "GPX_Z_BOT_RATIO_PCT", "Profondeur socle GPX (%)", 95, 0, 100, 1, handle_color=TOPIC_COLORS["gpx"], tooltip=TOOLTIPS.get("GPX_Z_BOT_RATIO_PCT"))

        return section

    def _make_label_with_info(self, label, tooltip, handle_color="#FFFFFF", reset_callback=None):
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        lbl = ClickableLabel(label)
        if reset_callback:
            lbl.setCursor(Qt.CursorShape.PointingHandCursor)
            reset_hint = "Cliquer pour réinitialiser à la valeur par défaut"
            lbl.setToolTip(f"{tooltip}\n\n{reset_hint}" if tooltip else reset_hint)
            lbl.clicked.connect(reset_callback)
        layout.addWidget(lbl)

        if tooltip:
            info = QLabel("ⓘ")
            info.setStyleSheet("color: "+handle_color+"; font-weight: bold;")
            info.setToolTip(tooltip)
            info.setCursor(Qt.CursorShape.WhatsThisCursor)
            layout.addWidget(info)

        layout.addStretch()
        return container

    def _add_double(self, form, key, label, default, lo, hi, step, handle_color="#FFFFFF", tooltip=None):
        decimals = 4 if step < 0.01 else (3 if step < 1 else 1)
        scale = 10 ** decimals

        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(int(lo * scale), int(hi * scale))
        slider.setSingleStep(max(1, int(step * scale)))
        slider.setValue(int(default * scale))
        if handle_color:
            slider.setStyleSheet(f"QSlider::handle:horizontal {{ background: {handle_color}; }}")

        value_widget = EditableValue(default, lo, hi, decimals=decimals)

        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(value_widget)

        slider.valueChanged.connect(lambda v: value_widget.set_value(v / scale))
        value_widget.valueChanged.connect(lambda v: slider.setValue(int(round(v * scale))))

        slider.decimals = decimals
        slider.scale = scale
        self._fields[key] = slider

        label_widget = self._make_label_with_info(
            label, tooltip, handle_color,
            reset_callback=lambda: slider.setValue(int(default * scale))
        )
        form.addRow(label_widget, row)
    
    def _add_int(self, form, key, label, default, lo, hi, step=1, handle_color="#FFFFFF", tooltip=None):
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(lo, hi)
        slider.setSingleStep(step)
        slider.setValue(default)
        if handle_color:
            slider.setStyleSheet(f"QSlider::handle:horizontal {{ background: {handle_color}; }}")

        value_widget = EditableValue(default, lo, hi, decimals=None)

        row = QHBoxLayout()
        row.addWidget(slider)
        row.addWidget(value_widget)

        slider.valueChanged.connect(value_widget.set_value)
        value_widget.valueChanged.connect(slider.setValue)

        self._fields[key] = slider

        label_widget = self._make_label_with_info(
            label, tooltip, handle_color,
            reset_callback=lambda: slider.setValue(default)
        )
        form.addRow(label_widget, row)
  
    def get_params(self):
        result = {}
        for key, widget in self._fields.items():
            if isinstance(widget, QSlider):
                if hasattr(widget, "scale"):
                    result[key] = widget.value() / widget.scale
                else:
                    result[key] = widget.value()
            elif isinstance(widget, QLineEdit):
                result[key] = widget.text()
            elif isinstance(widget, QCheckBox):
                result[key] = widget.isChecked()

        checked_levels = [lvl for lvl, cb in self._road_checkboxes.items() if cb.isChecked()]
        result["ROAD_LEVELS"] = checked_levels
        result["ENABLED_LAYERS"] = [k for k, cb in self._layer_checkboxes.items() if cb.isChecked()]

        if self._options_provider is not None:
            result.update(self._options_provider.get_values())

        return result

    def _on_pick_color(self, key, btn, cb):
        dialog = QColorDialog(QColor(TOPIC_COLORS.get(key, "#888888")), self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        color = dialog.selectedColor()
        if not color.isValid():
            return
        TOPIC_COLORS[key] = color.name()
        btn.setStyleSheet(color_button_style(color.name()))
        cb.setStyleSheet(checkbox_style(color.name()))
        self.color_changed.emit(key, color.name())
        
    def _on_add_gpx(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Charger un tracé GPX", "", "GPX (*.gpx)")
        if not paths:
            return
        for path in paths:
            self._add_gpx_row(path, "#FF0000", True)
        self.gpx_changed.emit()

    def _add_gpx_row(self, path, color, enabled):
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        cb = QCheckBox()
        cb.setChecked(enabled)
        cb.setStyleSheet(checkbox_style(color))
        cb.stateChanged.connect(lambda _: self.gpx_changed.emit())

        path_lbl = QLabel(os.path.basename(path))
        path_lbl.setToolTip(path)
        path_lbl.setProperty("full_path", path)
        path_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        path_lbl.setMinimumWidth(0)

        btn_color = QPushButton()
        btn_color.setFixedSize(20, 20)
        btn_color.setStyleSheet(color_button_style(color))
        btn_color.setProperty("color", color)
        btn_color.clicked.connect(lambda _, b=btn_color: self._on_gpx_color(b))

        btn_del = QPushButton("✕")
        btn_del.setFixedSize(20, 20)
        btn_del.setStyleSheet("""
            QPushButton {
                background: #4A4A4A;
                color: #FF6B6B;
                border: 1px solid #666666;
                border-radius: 3px;
                font-weight: bold;
                padding: 0px;
            }
            QPushButton:hover {
                background: #FF4444;
                color: white;
            }
        """)
        btn_del.clicked.connect(lambda _, r=row: self._on_remove_gpx(r))

        layout.addWidget(cb)
        layout.addWidget(path_lbl, stretch=1)
        layout.addWidget(btn_color)
        layout.addWidget(btn_del)

        self._gpx_list_layout.addWidget(row)
        self._gpx_entries.append((row, cb, path_lbl, btn_color))

    def _on_gpx_color(self, btn):
        dialog = QColorDialog(QColor(btn.property("color")), self)
        dialog.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        if not dialog.exec():
            return
        color = dialog.selectedColor().name()
        btn.setProperty("color", color)
        btn.setStyleSheet(color_button_style(color))
        self.gpx_changed.emit()

    def _on_remove_gpx(self, row):
        self._gpx_entries = [(r, cb, lbl, btn) for r, cb, lbl, btn in self._gpx_entries if r != row]
        row.deleteLater()
        self.gpx_changed.emit()

    def get_gpx_list(self):
        result = []
        for row, cb, lbl, btn_color in self._gpx_entries:
            result.append({
                "path": lbl.toolTip(),
                "color": btn_color.property("color"),
                "enabled": cb.isChecked(),
            })
        return result

    def clear_gpx_list(self):
        for row, cb, lbl, btn in self._gpx_entries:
            row.deleteLater()
        self._gpx_entries = []
        
    def set_options_provider(self, provider):
        self._options_provider = provider
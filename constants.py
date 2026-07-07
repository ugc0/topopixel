from PyQt6.QtGui import QColor

TOPIC_COLORS = {
    "terrain": "#FFFFFF",
    "roads": "#000000",
    "water": "#0094FF",
    "vegetation": "#00D921",
    "trees": "#006921",
    "buildings": "#898989",
    "gpx": "#FF0000",
}

def checkbox_style(color):
    is_white = color.upper() in ("#FFFFFF", "#FFF")
    text_color = "#000000" if is_white else color
    indicator_color = "#000000" if is_white else color
    return f"""
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border-radius: 7px;
            border: 2px solid {indicator_color};
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background: {indicator_color};
            border: 2px solid {indicator_color};
        }}
        QCheckBox {{ color: {text_color}; font-weight: bold; }}
    """

def color_button_style(color):
    return f"""
        QPushButton {{
            background: {color};
            border: 2px solid #555;
            border-radius: 4px;
            min-width: 14px;
            max-width: 14px;
            min-height: 14px;
            max-height: 14px;
        }}
        QPushButton:hover {{ border: 2px solid #000; }}
    """
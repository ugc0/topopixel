TOPIC_COLORS = {
    "terrain":    "#8B7355",
    "roads":      "#000000",
    "water":      "#0094FF",
    "vegetation": "#00D921",
    "trees":      "#006921",
    "buildings":  "#898989",
}

def checkbox_style(color):
    return f"""
        QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border-radius: 7px;
            border: 2px solid {color};
            background: transparent;
        }}
        QCheckBox::indicator:checked {{
            background: {color};
            border: 2px solid {color};
        }}
        QCheckBox {{ color: {color}; font-weight: bold; }}
    """
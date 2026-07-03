from PyQt6.QtWidgets import QWidget, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
import topopixel as tp


class StatusCheckWorker(QThread):
    done = pyqtSignal(dict, str)

    def run(self):
        status = tp.check_overpass_endpoints()
        strategy = tp.apply_overpass_strategy(status)
        self.done.emit(status, strategy)


class OverpassStatusWidget(QWidget):
    strategy_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._strategy = "unavailable"
        self._worker = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(6)

        self._dots = {}
        for name in ("private.coffee", "gall", "lambert"):
            dot = QLabel("●")
            dot.setStyleSheet("color: #888; font-size: 14px;")
            lbl = QLabel(name)
            lbl.setStyleSheet("color: #888; font-size: 11px;")
            layout.addWidget(dot)
            layout.addWidget(lbl)
            self._dots[name] = dot

        self._strategy_lbl = QLabel("—")
        self._strategy_lbl.setStyleSheet("color: #555; font-size: 11px; font-style: italic;")
        layout.addWidget(self._strategy_lbl)

        self._btn = QPushButton("⟳")
        self._btn.setFixedSize(24, 24)
        self._btn.setToolTip("Retester les endpoints")
        self._btn.clicked.connect(self.refresh)
        layout.addWidget(self._btn)
        layout.addStretch()

    def refresh(self):
        self._btn.setEnabled(False)
        self._strategy_lbl.setText("test en cours...")
        for dot in self._dots.values():
            dot.setStyleSheet("color: #888; font-size: 14px;")
        self._worker = StatusCheckWorker()
        self._worker.done.connect(self._on_done)
        self._worker.start()

    def _on_done(self, status, strategy):
        self._strategy = strategy
        for name, ok in status.items():
            color = "#00C851" if ok else "#FF4444"
            self._dots[name].setStyleSheet(f"color: {color}; font-size: 14px;")

        labels = {
            "parallel":    "parallèle",
            "sequential":  "séquentiel ⚠",
            "unavailable": "indisponible ✗",
        }
        colors = {
            "parallel":    "#00C851",
            "sequential":  "#FF8800",
            "unavailable": "#FF4444",
        }
        self._strategy_lbl.setText(labels.get(strategy, strategy))
        self._strategy_lbl.setStyleSheet(f"color: {colors.get(strategy, '#555')}; font-size: 11px; font-style: italic;")
        tp._overpass_strategy = strategy
        tp._overpass_status = status
        self._btn.setEnabled(True)
        self.strategy_changed.emit(strategy)

    def strategy(self):
        return self._strategy
"""Generates lf.ico for the exe (run by build.bat)."""
import sys
from PySide6.QtGui import QGuiApplication

app = QGuiApplication(sys.argv)
from lf_ui import icon_pixmap

sys.exit(0 if icon_pixmap(256).save("lf.ico") else 1)

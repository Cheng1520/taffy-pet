"""启动桌宠。

    python main.py
"""
import sys

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QApplication

from taffy_pet import config as cfgmod
from taffy_pet.pet import PetWindow


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)   # 气泡单独关掉时别把程序带走

    cfg = cfgmod.load()
    pet = PetWindow(cfg)
    pet.show()
    # 等窗口真正摆好位置再弹提示，否则气泡会按还没定位的坐标算
    QTimer.singleShot(600, pet.show_hint_if_first_run)
    return app.exec_()


if __name__ == "__main__":
    sys.exit(main())

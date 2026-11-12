"""程序入口：暨南大学就业信息服务平台岗位信息辅助管理。"""
from __future__ import annotations

import sys

from PyQt6.QtWidgets import QApplication

from database import Database
from main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("暨南大学就业信息服务平台岗位信息辅助管理")

    db = Database()
    try:
        window = MainWindow(db)
        window.show()
        return app.exec()
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

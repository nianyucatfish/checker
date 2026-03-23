import os
import sys

from PyQt6.QtCore import QStandardPaths

APP_DIR_NAME = "AudioQC"


def _unique_existing_or_fallback(paths):
    seen = set()
    for path in paths:
        if not path:
            continue
        normalized = os.path.normpath(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.exists(normalized):
            return normalized
    return None


def asset_root() -> str:
    module_dir = os.path.dirname(os.path.abspath(__file__))
    source_asset_dir = os.path.join(module_dir, "asset")
    candidates = []

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "asset"))

    if getattr(sys, "frozen", False):
        current = os.path.dirname(os.path.abspath(sys.executable))
        for _ in range(5):
            candidates.append(os.path.join(current, "asset"))
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    candidates.append(source_asset_dir)
    resolved = _unique_existing_or_fallback(candidates)
    return resolved or source_asset_dir


def resource_path(*parts: str) -> str:
    return os.path.join(asset_root(), *parts)


def user_documents_dir() -> str:
    path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.DocumentsLocation
    )
    return path or os.path.expanduser("~")


def app_config_dir() -> str:
    path = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppConfigLocation
    )
    if not path:
        path = os.path.join(os.path.expanduser("~"), ".config")
    if os.path.basename(os.path.normpath(path)).lower() != APP_DIR_NAME.lower():
        path = os.path.join(path, APP_DIR_NAME)
    return path


def app_config_path(*parts: str) -> str:
    return os.path.join(app_config_dir(), *parts)

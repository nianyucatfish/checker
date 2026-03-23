import os
import sys

from PyQt6.QtCore import QStandardPaths

APP_DIR_NAME = "AudioQC"


def _unique_existing_or_fallback(paths):
    seen = set()
    fallback = None
    for path in paths:
        if not path:
            continue
        normalized = os.path.normpath(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        if fallback is None:
            fallback = normalized
        if os.path.exists(normalized):
            return normalized
    return fallback


def asset_root() -> str:
    module_dir = os.path.dirname(os.path.abspath(__file__))
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

    candidates.append(os.path.join(module_dir, "asset"))
    resolved = _unique_existing_or_fallback(candidates)
    return resolved or os.path.join(module_dir, "asset")


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

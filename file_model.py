"""
文件模型模块
自定义的文件系统模型，支持错误和未保存状态的显示
"""

import os
import math
from PyQt6.QtGui import QFileSystemModel, QColor, QFont
from PyQt6.QtCore import Qt

try:
    import soundfile as sf
except Exception:  # pragma: no cover
    sf = None


class ProjectModel(QFileSystemModel):
    """
    增强的文件模型：
    1. 错误标红
    2. 未保存状态（小圆点）
    """

    def __init__(self):
        super().__init__()
        self.error_paths = set()  # 有错误的路径（含递归父级）
        self.unsaved_paths = set()  # 未保存的路径（含递归父级）
        self.icon_provider = self.iconProvider()
        self._wav_duration_cache = {}  # {path: (mtime_ns, "mm:ss")}

    # 自定义数据 Role：用于文件树右侧显示 WAV 时长
    DurationRole = Qt.ItemDataRole.UserRole + 101

    def update_status(self, error_map, unsaved_files):
        """
        重新计算高亮路径
        error_map: 全量错误字典
        unsaved_files: 具体的未保存文件路径列表
        """
        self.error_paths.clear()
        self.unsaved_paths.clear()

        # 处理错误路径递归
        for path in error_map.keys():
            p = os.path.normpath(path)
            self.error_paths.add(p)
            self._add_parents(p, self.error_paths)

        # 处理未保存路径递归
        for path in unsaved_files:
            p = os.path.normpath(path)
            self.unsaved_paths.add(p)
            self._add_parents(p, self.unsaved_paths)

        self.layoutChanged.emit()

    def _add_parents(self, path, target_set):
        parent = os.path.dirname(path)
        while parent and parent != path:
            target_set.add(parent)
            old = parent
            parent = os.path.dirname(parent)
            if parent == old:
                break

    def data(self, index, role):
        path = os.path.normpath(self.filePath(index))

        if role == self.DurationRole:
            if not path or not os.path.isfile(path):
                return None
            if os.path.splitext(path)[1].lower() != ".wav":
                return None
            if sf is None:
                return None

            try:
                st = os.stat(path)
                mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))
            except Exception:
                return None

            cached = self._wav_duration_cache.get(path)
            if cached and cached[0] == mtime_ns:
                return cached[1]

            try:
                with sf.SoundFile(path) as f:
                    sr = int(f.samplerate)
                    frames = int(f.frames)
                if sr <= 0 or frames < 0:
                    return None
                total_sec = int(math.floor(frames / float(sr)))
                mm = total_sec // 60
                ss = total_sec % 60
                text = f"{mm:02d}:{ss:02d}"
            except Exception:
                return None

            self._wav_duration_cache[path] = (mtime_ns, text)
            return text

        # 文本颜色：错误优先
        if role == Qt.ItemDataRole.ForegroundRole:
            if path in self.error_paths:
                return QColor("#d32f2f")  # Red
            if path in self.unsaved_paths:
                return QColor("#005fb8")  # Blue

        # 文本显示：增加圆点
        if role == Qt.ItemDataRole.DisplayRole:
            original = super().data(index, role)
            if path in self.unsaved_paths:
                return f"{original} ●"  # 添加小圆点
            return original

        # 字体加粗：未保存
        if role == Qt.ItemDataRole.FontRole:
            if path in self.unsaved_paths:
                font = super().data(index, role) or QFont()
                font.setBold(True)
                return font

        return super().data(index, role)

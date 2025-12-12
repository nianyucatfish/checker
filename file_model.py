"""
文件模型模块
自定义的文件系统模型，支持错误和未保存状态的显示
"""
import os
from PyQt6.QtGui import QFileSystemModel, QColor, QFont
from PyQt6.QtCore import Qt


class ProjectModel(QFileSystemModel):
    """
    增强的文件模型：
    1. 错误标红
    2. 未保存状态（小圆点）
    """
    def __init__(self):
        super().__init__()
        self.error_paths = set()    # 有错误的路径（含递归父级）
        self.unsaved_paths = set()  # 未保存的路径（含递归父级）
        self.icon_provider = self.iconProvider()

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
            if parent == old: break

    def data(self, index, role):
        path = os.path.normpath(self.filePath(index))
        
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

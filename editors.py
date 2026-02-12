"""
编辑器模块
包含文本编辑器、音频播放器、MIDI预览器和编辑器管理器
"""

from PyQt6.QtCore import pyqtSignal, Qt, QThread, QObject, pyqtSlot
import os
import struct
import csv
import re
import base64
import json
import tempfile
from io import StringIO
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QStackedWidget,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QPushButton,
    QHBoxLayout,
    QHeaderView,
    QFileDialog,
)
from PyQt6.QtGui import (
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QColor,
    QKeySequence,
    QTextCursor,
    QPainter,
    QBrush,
    QPen,
)
import numpy as np
import librosa

# 已移除图形 MIDI 预览依赖 (mido, pyqtgraph)。
from audio_player import MediaPlayer


class CsvTableEditor(QWidget):
    """
    CSV表格编辑器，支持基本的单元格编辑和保存
    """

    on_save = pyqtSignal(str, str)  # path, content
    on_change = pyqtSignal(str)  # path

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 操作按钮区
        btn_layout = QHBoxLayout()
        btn_add_row = QPushButton("添加行")
        btn_add_col = QPushButton("添加列")
        btn_del_row = QPushButton("删除行")
        btn_del_col = QPushButton("删除列")
        btn_layout.addWidget(btn_add_row)
        btn_layout.addWidget(btn_add_col)
        btn_layout.addWidget(btn_del_row)
        btn_layout.addWidget(btn_del_col)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.EditTrigger.AllEditTriggers)

        # 允许拖拽调整列宽/行高（类似 Excel 的“拉伸”）
        h_header = self.table.horizontalHeader()
        h_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        h_header.setMinimumSectionSize(24)

        v_header = self.table.verticalHeader()
        v_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        v_header.setMinimumSectionSize(18)

        self.table.itemChanged.connect(self._handle_item_changed)

        # 表格左右留白（避免自适应列宽贴边）
        table_container = QWidget()
        table_layout = QHBoxLayout(table_container)
        table_layout.setContentsMargins(20, 0, 20, 0)
        table_layout.addWidget(self.table)
        layout.addWidget(table_container)

        btn_add_row.clicked.connect(self.add_row)
        btn_add_col.clicked.connect(self.add_column)
        btn_del_row.clicked.connect(self.remove_row)
        btn_del_col.clicked.connect(self.remove_column)

        self.current_path = None
        self.loading = False
        self._changed = False

        # 简易撤销/重做栈（跨单元格/增删行列），避免切换视图时丢失
        self._undo_stack = []
        self._redo_stack = []

        # 记录上一次已知的单元格内容，用于 itemChanged 计算 old/new
        self._cell_cache = {}

    def load_file(self, path):
        """从文件加载"""
        self.current_path = path
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            self.load_from_string(content, reset_history=True, reset_changed=True)
        except Exception as e:
            raise RuntimeError(f"CSV文件读取失败: {e}")

    def load_from_string(self, content, *, reset_history=False, reset_changed=False):
        """从字符串加载数据 (用于视图同步)

        reset_history: 仅在首次打开文件/clear 时为 True，切换视图同步时不要清空撤销栈。
        reset_changed: 仅在首次打开文件时为 True，切换视图同步时不要重置“已修改”状态。
        """
        self.loading = True
        if reset_changed:
            self._changed = False
        if reset_history:
            self._undo_stack.clear()
            self._redo_stack.clear()
            self._cell_cache.clear()
        try:
            f = StringIO(content)
            reader = csv.reader(f)
            data = list(reader)

            if not data:
                data = [[]]

            self.table.clear()
            self.table.setRowCount(len(data))
            self.table.setColumnCount(max(len(row) for row in data) if data else 0)

            for r, row in enumerate(data):
                for c, val in enumerate(row):
                    item = QTableWidgetItem(val)
                    self.table.setItem(r, c, item)

            self._rebuild_cell_cache()

            # 初次加载时按内容做一次基础自适应，后续可手动拖拽微调
            self.table.resizeColumnsToContents()
            self.table.resizeRowsToContents()
        except Exception as e:
            # 解析出错时不崩溃，弹窗提示或者在表格显示错误
            print(f"CSV解析警告: {e}")
        finally:
            self.loading = False

    def clear(self):
        self.loading = True
        self.current_path = None
        self.table.clear()
        self.table.setRowCount(0)
        self.table.setColumnCount(0)
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._cell_cache.clear()
        self._changed = False
        self.loading = False

    def _handle_item_changed(self, item):
        if self.loading or not self.current_path:
            return

        r = item.row()
        c = item.column()
        new_val = item.text()
        old_val = self._cell_cache.get((r, c), "")
        if new_val == old_val:
            return

        self._push_undo(
            {"type": "cell", "row": r, "col": c, "old": old_val, "new": new_val}
        )
        self._cell_cache[(r, c)] = new_val
        self._changed = True
        self.on_change.emit(self.current_path)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Undo):
            self.undo()
            return
        if event.matches(QKeySequence.StandardKey.Redo):
            self.redo()
            return
        if event.matches(QKeySequence.StandardKey.Save):
            if self.current_path:
                self.on_save.emit(self.current_path, self._to_csv_string())
        else:
            super().keyPressEvent(event)

    def _to_csv_string(self):
        # 导出当前表格为CSV字符串
        output = StringIO()
        writer = csv.writer(output, lineterminator="\n")
        for r in range(self.table.rowCount()):
            row = []
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                row.append(item.text() if item else "")
            writer.writerow(row)
        return output.getvalue()

    def add_row(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._push_undo({"type": "insertRow", "row": row})
        self._rebuild_cell_cache()
        self._handle_manual_change()

    def add_column(self):
        col = self.table.columnCount()
        self.table.insertColumn(col)
        self._push_undo({"type": "insertCol", "col": col})
        self._rebuild_cell_cache()
        self._handle_manual_change()

    def remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            snapshot = self._snapshot_row(row)
            self.table.removeRow(row)
            self._push_undo({"type": "removeRow", "row": row, "data": snapshot})
            self._rebuild_cell_cache()
            self._handle_manual_change()

    def remove_column(self):
        col = self.table.currentColumn()
        if col >= 0:
            snapshot = self._snapshot_col(col)
            self.table.removeColumn(col)
            self._push_undo({"type": "removeCol", "col": col, "data": snapshot})
            self._rebuild_cell_cache()
            self._handle_manual_change()

    def _handle_manual_change(self):
        """处理增删行列等非itemChanged触发的修改"""
        if not self.loading and self.current_path:
            self._changed = True
            self.on_change.emit(self.current_path)

    def undo(self):
        if not self._undo_stack:
            return
        cmd = self._undo_stack.pop()
        self._apply_command(cmd, undo=True)
        self._redo_stack.append(cmd)

    def redo(self):
        if not self._redo_stack:
            return
        cmd = self._redo_stack.pop()
        self._apply_command(cmd, undo=False)
        self._undo_stack.append(cmd)

    def _push_undo(self, cmd: dict):
        self._undo_stack.append(cmd)
        self._redo_stack.clear()

    def _apply_command(self, cmd: dict, *, undo: bool):
        """应用撤销/重做命令。undo=True 表示回退到 old 状态。"""
        cmd_type = cmd.get("type")

        def _auto_resize():
            # 仅在结构变更（增删行列/恢复）后做一次内容自适应
            try:
                self.table.resizeColumnsToContents()
                self.table.resizeRowsToContents()
            except Exception:
                pass

        self.loading = True
        try:
            if cmd_type == "cell":
                r = cmd["row"]
                c = cmd["col"]
                text = cmd["old"] if undo else cmd["new"]
                self._set_cell_text(r, c, text)

            elif cmd_type == "insertRow":
                row = cmd["row"]
                if undo:
                    if 0 <= row < self.table.rowCount():
                        self.table.removeRow(row)
                else:
                    self.table.insertRow(row)
                self._rebuild_cell_cache()
                _auto_resize()

            elif cmd_type == "removeRow":
                row = cmd["row"]
                if undo:
                    self.table.insertRow(row)
                    self._restore_row(row, cmd.get("data") or [])
                else:
                    if 0 <= row < self.table.rowCount():
                        self.table.removeRow(row)
                self._rebuild_cell_cache()
                _auto_resize()

            elif cmd_type == "insertCol":
                col = cmd["col"]
                if undo:
                    if 0 <= col < self.table.columnCount():
                        self.table.removeColumn(col)
                else:
                    self.table.insertColumn(col)
                self._rebuild_cell_cache()
                _auto_resize()

            elif cmd_type == "removeCol":
                col = cmd["col"]
                if undo:
                    self.table.insertColumn(col)
                    self._restore_col(col, cmd.get("data") or [])
                else:
                    if 0 <= col < self.table.columnCount():
                        self.table.removeColumn(col)
                self._rebuild_cell_cache()
                _auto_resize()

        finally:
            self.loading = False

        if self.current_path:
            self._changed = True
            self.on_change.emit(self.current_path)

    def _set_cell_text(self, row: int, col: int, text: str):
        if row < 0 or col < 0:
            return
        if row >= self.table.rowCount() or col >= self.table.columnCount():
            return
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem("")
            self.table.setItem(row, col, item)
        item.setText(text)
        self._cell_cache[(row, col)] = text

    def _rebuild_cell_cache(self):
        self._cell_cache.clear()
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                self._cell_cache[(r, c)] = item.text() if item else ""

    def _snapshot_row(self, row: int):
        data = []
        for c in range(self.table.columnCount()):
            item = self.table.item(row, c)
            data.append(item.text() if item else "")
        return data

    def _snapshot_col(self, col: int):
        data = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, col)
            data.append(item.text() if item else "")
        return data

    def _restore_row(self, row: int, data):
        for c, val in enumerate(data):
            if c >= self.table.columnCount():
                break
            self._set_cell_text(row, c, val)

    def _restore_col(self, col: int, data):
        for r, val in enumerate(data):
            if r >= self.table.rowCount():
                break
            self._set_cell_text(r, col, val)


class CsvHighlighter(QSyntaxHighlighter):
    """简单的CSV高亮"""

    def highlightBlock(self, text):
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#008000"))  # 逗号绿色

        # 高亮逗号
        for i, char in enumerate(text):
            if char == ",":
                self.setFormat(i, 1, fmt)


class TextEditor(QWidget):
    """
    支持撤销、保存的文本编辑器
    """

    on_save = pyqtSignal(str, str)  # path, content
    on_change = pyqtSignal(str)  # path

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.editor = QPlainTextEdit()
        self.editor.setFont(QFont("Consolas", 11))
        self.editor.textChanged.connect(self._handle_text_changed)

        self.highlighter = CsvHighlighter(self.editor.document())

        layout.addWidget(self.editor)

        self.current_path = None
        self.loading = False

    def load_file(self, path):
        self.loading = True
        self.current_path = path
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            self.editor.setPlainText(content)
        except Exception as e:
            self.editor.setPlainText(f"无法读取文件: {e}")
        self.loading = False

    def set_content(self, content):
        """用于同步视图内容的设置方法"""
        self.loading = True
        # 不使用 setPlainText：它会清空撤销栈，导致视图切换后无法继续 Ctrl+Z
        doc = self.editor.document()
        cursor = QTextCursor(doc)
        cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document)
        cursor.insertText(content)
        cursor.endEditBlock()
        self.loading = False

    def get_content(self):
        return self.editor.toPlainText()

    def clear(self):
        """清空编辑器且不触发修改信号"""
        self.loading = True
        self.current_path = None
        self.editor.setPlainText("")
        self.loading = False

    def _handle_text_changed(self):
        # 只有在非加载状态且有当前文件路径时才触发修改信号
        if not self.loading and self.current_path:
            self.on_change.emit(self.current_path)

    def keyPressEvent(self, event):
        # 捕获保存快捷键
        if event.matches(QKeySequence.StandardKey.Save):
            if self.current_path:
                self.on_save.emit(self.current_path, self.editor.toPlainText())
        else:
            super().keyPressEvent(event)


class WaveformLoader(QThread):
    """后台线程: 负责读取与降采样音频，避免阻塞 UI"""

    loaded = pyqtSignal(object, int, object)  # data, sr, resampled
    failed = pyqtSignal(str)

    def __init__(self, path, target_points):
        super().__init__()
        self.path = path
        self.target_points = target_points

    def run(self):
        try:
            data, sr = librosa.load(self.path, sr=None, mono=True)
            if len(data) > self.target_points:
                resampled = self._downsample_data(data, self.target_points)
            else:
                resampled = data
            self.loaded.emit(data, sr, resampled)
        except Exception as e:
            self.failed.emit(str(e))

    @staticmethod
    def _downsample_data(data, target_points):
        step = len(data) // target_points
        if step < 1:
            return data
        downsampled = []
        for i in range(0, len(data), step):
            block = data[i : i + step]
            if len(block) > 0:
                max_val = np.max(block)
                min_val = np.min(block)
                downsampled.append(max_val)
                downsampled.append(min_val)
        return np.array(downsampled)


class WaveformWidget(QWidget):
    """
    音频波形图预览组件
    """

    on_seek_request = pyqtSignal(int)

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.data = None
        self.sr = 0
        self.resampled_data = None
        self.current_time_ms = 0
        self._loader = None
        self._loading = False
        self._is_playing = False
        self.beat_data = []
        self.render_beat = False
        self.structure_data = []
        self.render_structure = False
        self.zoom_level = 1.0
        self.view_start_ratio = 0.0

    def set_beat_data(self, beat_data, render=True):
        self.beat_data = beat_data
        self.render_beat = render
        self.update()

    def set_structure_data(self, structure_data, render=True):
        self.structure_data = structure_data
        self.render_structure = render
        self.update()

    def load_file(self, path):
        if self._loader and self._loader.isRunning():
            self._loader.requestInterruption()
            self._loader.wait()

        self.data = None
        self.resampled_data = None
        self.sr = 0
        self.current_time_ms = 0
        self._loading = True
        self.zoom_level = 1.0
        self.view_start_ratio = 0.0
        self.update()

        target_points = 2000
        self._loader = WaveformLoader(path, target_points)
        self._loader.loaded.connect(self._on_loaded)
        self._loader.failed.connect(self._on_failed)
        self._loader.start()

    def _on_loaded(self, data, sr, resampled):
        self.data = data
        self.sr = sr
        self.resampled_data = resampled
        self.current_time_ms = 0
        self._is_playing = True
        self._loading = False
        self.update()

    def _on_failed(self, message):
        QMessageBox.critical(self, "音频加载错误", f"无法加载音频文件: {message}")
        self.data = None
        self.sr = 0
        self.resampled_data = None
        self._is_playing = False
        self._loading = False
        self.update()

    def get_duration_ms(self):
        if self.data is None or self.sr == 0:
            return 0
        return int(len(self.data) / self.sr * 1000)

    def update_play_position(self, current_time_ms):
        self.current_time_ms = current_time_ms
        self.update()

    def set_playing(self, is_playing: bool):
        self._is_playing = is_playing
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)

        if self._loading:
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(255, 255, 255))
            painter.setPen(QPen(QColor(120, 120, 120), 1))
            painter.drawText(
                self.rect(), Qt.AlignmentFlag.AlignCenter, "正在加载波形..."
            )
            return

        if self.data is None or len(self.data) == 0:
            return

        painter = QPainter(self)
        rect = self.rect()
        width = rect.width()
        height = rect.height()

        painter.fillRect(rect, QColor(255, 255, 255))

        center_y = height / 2
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawLine(0, int(center_y), width, int(center_y))

        painter.setPen(QPen(QColor(0, 120, 215), 1))

        max_amplitude = np.max(np.abs(self.data)) or 1.0

        if self.zoom_level == 1.0 and self.resampled_data is not None:
            data_to_draw = self.resampled_data
            num_points = len(data_to_draw) // 2
            for i in range(num_points):
                x = int(i / num_points * width)
                y_max_norm = data_to_draw[i * 2] / max_amplitude
                y_min_norm = data_to_draw[i * 2 + 1] / max_amplitude

                y1 = center_y - (y_max_norm * center_y * 0.9)
                y2 = center_y - (y_min_norm * center_y * 0.9)

                painter.drawLine(x, int(y1), x, int(y2))
        else:
            total_samples = len(self.data)
            start_idx = int(self.view_start_ratio * total_samples)
            view_width_ratio = 1.0 / self.zoom_level
            end_idx = int((self.view_start_ratio + view_width_ratio) * total_samples)

            start_idx = max(0, start_idx)
            end_idx = min(total_samples, end_idx)

            if end_idx > start_idx:
                view_data = self.data[start_idx:end_idx]
                if view_data.ndim > 1:
                    view_data = np.mean(view_data, axis=1)

                step = max(1, len(view_data) // width)
                display_data = view_data[::step]

                for i, val in enumerate(display_data):
                    x = int(i / len(display_data) * width)
                    y_norm = val / max_amplitude
                    y = center_y - (y_norm * center_y * 0.9)
                    painter.drawLine(x, int(center_y), x, int(y))

        total_duration_ms = self.get_duration_ms()
        view_width_ratio = 1.0 / self.zoom_level

        if self.render_structure and self.structure_data and total_duration_ms > 0:
            for time_sec, label in self.structure_data:
                time_ms = time_sec * 1000
                ratio = time_ms / total_duration_ms

                if (
                    self.view_start_ratio
                    <= ratio
                    <= (self.view_start_ratio + view_width_ratio)
                ):
                    view_ratio = (ratio - self.view_start_ratio) / view_width_ratio
                    x = int(view_ratio * width)

                    # Draw Line
                    painter.setPen(
                        QPen(QColor(255, 140, 0), 2, Qt.PenStyle.DashDotLine)
                    )  # Orange
                    painter.drawLine(x, 0, x, height)

                    # Draw Label
                    painter.setPen(QPen(QColor(255, 100, 0), 1))
                    painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
                    painter.drawText(x + 4, 20, label)

        if self.render_beat and self.beat_data and total_duration_ms > 0:
            for time_sec, is_first in self.beat_data:
                time_ms = time_sec * 1000
                ratio = time_ms / total_duration_ms

                if (
                    self.view_start_ratio
                    <= ratio
                    <= (self.view_start_ratio + view_width_ratio)
                ):
                    view_ratio = (ratio - self.view_start_ratio) / view_width_ratio
                    x = int(view_ratio * width)

                    if is_first:
                        painter.setPen(
                            QPen(QColor(0, 200, 0), 2, Qt.PenStyle.SolidLine)
                        )
                    else:
                        painter.setPen(QPen(QColor(0, 200, 0), 1, Qt.PenStyle.DashLine))
                    painter.drawLine(x, 0, x, height)

        if total_duration_ms > 0 and self._is_playing:
            progress_ratio = self.current_time_ms / total_duration_ms
            if (
                self.view_start_ratio
                <= progress_ratio
                <= (self.view_start_ratio + view_width_ratio)
            ):
                view_ratio = (progress_ratio - self.view_start_ratio) / view_width_ratio
                x_pos = int(view_ratio * width)
                painter.setPen(QPen(QColor(255, 0, 0), 2))
                painter.drawLine(x_pos, 0, x_pos, height)

    def mousePressEvent(self, event):
        if self.data is None or self.sr == 0:
            super().mousePressEvent(event)
            return

        width = self.rect().width()
        total_duration_ms = self.get_duration_ms()

        if event.button() == Qt.MouseButton.LeftButton and total_duration_ms > 0:
            x_click = event.position().x()

            view_width_ratio = 1.0 / self.zoom_level
            click_ratio_in_view = x_click / width
            global_ratio = self.view_start_ratio + (
                click_ratio_in_view * view_width_ratio
            )

            target_time_ms = int(global_ratio * total_duration_ms)
            target_time_ms = max(0, min(total_duration_ms, target_time_ms))

            self.on_seek_request.emit(target_time_ms)

        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if self.data is None:
            return

        modifiers = event.modifiers()
        if modifiers & Qt.KeyboardModifier.ShiftModifier:
            # Zoom
            angle = event.angleDelta().y()
            if angle > 0:
                factor = 1.25
            else:
                factor = 0.8

            old_zoom = self.zoom_level
            new_zoom = old_zoom * factor
            if new_zoom < 1.0:
                new_zoom = 1.0
            if new_zoom > 200.0:
                new_zoom = 200.0

            if new_zoom != old_zoom:
                mouse_x = event.position().x()
                width = self.width()
                mouse_ratio = mouse_x / width

                view_width_old = 1.0 / old_zoom
                view_width_new = 1.0 / new_zoom

                self.view_start_ratio += mouse_ratio * (view_width_old - view_width_new)
                self.zoom_level = new_zoom

                max_start = 1.0 - view_width_new
                if self.view_start_ratio < 0:
                    self.view_start_ratio = 0
                if self.view_start_ratio > max_start:
                    self.view_start_ratio = max_start

                self.update()
        else:
            # Horizontal Scroll
            if self.zoom_level > 1.0:
                angle = event.angleDelta().y()
                view_width = 1.0 / self.zoom_level
                scroll_amount = -(angle / 120.0) * (view_width * 0.1)

                self.view_start_ratio += scroll_amount

                max_start = 1.0 - view_width
                if self.view_start_ratio < 0:
                    self.view_start_ratio = 0
                if self.view_start_ratio > max_start:
                    self.view_start_ratio = max_start

                self.update()


class AudioPlayerWithWaveform(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        top_controls = QHBoxLayout()
        self.btn_render_beat = QPushButton("渲染节奏")
        self.btn_render_beat.setCheckable(True)
        self.btn_render_beat.clicked.connect(self._toggle_beat_render)
        top_controls.addWidget(self.btn_render_beat)

        self.btn_render_structure = QPushButton("渲染结构")
        self.btn_render_structure.setCheckable(True)
        self.btn_render_structure.clicked.connect(self._toggle_structure_render)
        top_controls.addWidget(self.btn_render_structure)

        top_controls.addStretch()
        layout.addLayout(top_controls)

        self.waveform_widget = WaveformWidget()
        self.media_player = MediaPlayer()

        self.waveform_widget.on_seek_request.connect(self.media_player.seek_ms)
        self.media_player.position_changed.connect(
            self.waveform_widget.update_play_position
        )
        self.media_player.play_state_changed.connect(self.waveform_widget.set_playing)

        layout.addWidget(self.waveform_widget)
        layout.addWidget(self.media_player)

    def _toggle_beat_render(self, checked):
        if not checked:
            self.waveform_widget.set_beat_data([], False)
            self.media_player.set_metronome_active(False)
            self.btn_render_beat.setText("渲染节奏")
            return

        if not self.media_player.path:
            QMessageBox.warning(self, "提示", "请先加载音频文件")
            self.btn_render_beat.setChecked(False)
            return

        wav_path = self.media_player.path
        song_folder = os.path.dirname(os.path.dirname(wav_path))
        song_folder_name = os.path.basename(song_folder)

        match = re.match(r"^(.+?)_(.+?)_(.+?)$", song_folder_name)
        if match:
            song_name = match.group(2)
        else:
            QMessageBox.warning(
                self,
                "错误",
                f"无法从文件夹名 '{song_folder_name}' 提取歌曲名。\n请确保文件夹命名格式为 '歌曲名_BPM_调号'",
            )
            self.btn_render_beat.setChecked(False)
            return

        csv_path = os.path.join(song_folder, "csv", f"{song_name}_Beat.csv")

        if not os.path.exists(csv_path):
            QMessageBox.warning(self, "错误", f"找不到Beat文件:\n{csv_path}")
            self.btn_render_beat.setChecked(False)
            return

        try:
            beat_data = []
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                if "TIME" not in reader.fieldnames or "LABEL" not in reader.fieldnames:
                    raise ValueError("CSV表头必须包含 TIME 和 LABEL")

                for row in reader:
                    t = float(row["TIME"])
                    label = row["LABEL"]
                    is_first = label.strip().endswith(".1")
                    beat_data.append((t, is_first))

            self.waveform_widget.set_beat_data(beat_data, True)
            self.media_player.set_beat_data(beat_data)
            self.media_player.set_metronome_active(True)
            self.btn_render_beat.setText("取消渲染")

        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取Beat文件失败:\n{e}")
            self.btn_render_beat.setChecked(False)
            self.waveform_widget.set_beat_data([], False)
            self.media_player.set_metronome_active(False)

    def _toggle_structure_render(self, checked):
        if not checked:
            self.waveform_widget.set_structure_data([], False)
            self.btn_render_structure.setText("渲染结构")
            return

        if not self.media_player.path:
            QMessageBox.warning(self, "提示", "请先加载音频文件")
            self.btn_render_structure.setChecked(False)
            return

        wav_path = self.media_player.path
        song_folder = os.path.dirname(os.path.dirname(wav_path))
        song_folder_name = os.path.basename(song_folder)

        match = re.match(r"^(.+?)_(.+?)_(.+?)$", song_folder_name)
        if match:
            song_name = match.group(2)
        else:
            QMessageBox.warning(
                self,
                "错误",
                f"无法从文件夹名 '{song_folder_name}' 提取歌曲名。\n请确保文件夹命名格式为 '歌曲名_BPM_调号'",
            )
            self.btn_render_structure.setChecked(False)
            return

        csv_path = os.path.join(song_folder, "csv", f"{song_name}_Structure.csv")

        if not os.path.exists(csv_path):
            QMessageBox.warning(self, "错误", f"找不到Structure文件:\n{csv_path}")
            self.btn_render_structure.setChecked(False)
            return

        try:
            structure_data = []
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                headers = next(reader, [])  # Labels
                times = next(reader, [])  # Timestamps

                if len(headers) != len(times):
                    raise ValueError("Structure CSV 格式错误: 标题行与时间行长度不一致")

                for i in range(len(headers)):
                    label = headers[i]
                    time_str = times[i]
                    # Parse MM:SS
                    parts = time_str.split(":")
                    if len(parts) == 2:
                        seconds = int(parts[0]) * 60 + float(parts[1])
                        structure_data.append((seconds, label))

            self.waveform_widget.set_structure_data(structure_data, True)
            self.btn_render_structure.setText("取消结构")

        except Exception as e:
            QMessageBox.warning(self, "错误", f"读取Structure文件失败:\n{e}")
            self.btn_render_structure.setChecked(False)
            self.waveform_widget.set_structure_data([], False)

    def load_file(self, path):
        self.waveform_widget.load_file(path)
        self.media_player.load_file(path)

        # Reset beat render state
        self.btn_render_beat.setChecked(False)
        self.btn_render_beat.setText("渲染节奏")
        self.waveform_widget.set_beat_data([], False)
        self.media_player.set_metronome_active(False)

        # Reset structure render state
        self.btn_render_structure.setChecked(False)
        self.btn_render_structure.setText("渲染结构")
        self.waveform_widget.set_structure_data([], False)

    def stop(self):
        self.media_player.stop()
        self.waveform_widget.set_playing(False)
        self.waveform_widget.update_play_position(0)


class MidiPreview(QWebEngineView):
    def __init__(self):
        super().__init__()
        self.pending_midi_data = None
        self.current_midi_path = None
        self.default_export_filename = "export_修改.mid"
        self.compare_wav_dir = None
        self.compare_wav_files = []
        self.default_compare_wav = None

        self.export_bridge = MidiExportBridge(self)
        self.web_channel = QWebChannel(self.page())
        self.web_channel.registerObject("midiExportBridge", self.export_bridge)
        self.page().setWebChannel(self.web_channel)

        self.loadFinished.connect(self._on_load_finished)

        # Load HTML template
        try:
            with open("asset/midi_player.html", "r", encoding="utf-8") as f:
                self.html_template = f.read()
            self.setHtml(self.html_template)
        except Exception as e:
            self.html_template = (
                f"<html><body><h3>Error loading player template: {e}</h3></body></html>"
            )
            self.setHtml(self.html_template)

    def load_file(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
            self.current_midi_path = path
            base_name = os.path.splitext(os.path.basename(path))[0]
            self.default_export_filename = f"{base_name}_修改.mid"
            self._prepare_compare_wavs(path)
            self.pending_midi_data = base64.b64encode(data).decode("ascii")
            # Reload the page to clear state
            self.setHtml(self.html_template)
        except Exception as e:
            print(f"Error loading MIDI: {e}")

    def _prepare_compare_wavs(self, midi_path: str):
        midi_dir = os.path.dirname(midi_path)
        parent_dir = os.path.dirname(midi_dir)
        wav_dir = os.path.join(parent_dir, "分轨wav")

        self.compare_wav_dir = wav_dir if os.path.isdir(wav_dir) else None
        self.compare_wav_files = []
        self.default_compare_wav = None

        if not self.compare_wav_dir:
            return

        wav_files = [
            name
            for name in os.listdir(self.compare_wav_dir)
            if os.path.isfile(os.path.join(self.compare_wav_dir, name))
            and name.lower().endswith(".wav")
        ]
        wav_files.sort()
        self.compare_wav_files = wav_files

        vocal_candidate = next(
            (name for name in wav_files if name.lower().endswith("_vocal_a.wav")),
            None,
        )
        self.default_compare_wav = vocal_candidate or (
            wav_files[0] if wav_files else None
        )

    def _on_load_finished(self, ok):
        if ok and self.pending_midi_data:
            js = f"window.loadMidiContent('{self.pending_midi_data}');"
            self.page().runJavaScript(js)
            default_name_js = json.dumps(self.default_export_filename)
            self.page().runJavaScript(
                f"window.setExportDefaultFilename && window.setExportDefaultFilename({default_name_js});"
            )
            self.pending_midi_data = None


class MidiExportBridge(QObject):
    def __init__(self, preview: "MidiPreview"):
        super().__init__()
        self.preview = preview

    @pyqtSlot(str, str, result=str)
    def saveMidiBase64(self, midi_base64: str, suggested_name: str) -> str:
        if not midi_base64:
            return "ERROR: Empty MIDI data"

        default_name = (
            suggested_name.strip()
            if suggested_name and suggested_name.strip()
            else self.preview.default_export_filename
        )
        if not default_name.lower().endswith((".mid", ".midi")):
            default_name = f"{default_name}.mid"

        if self.preview.current_midi_path:
            default_dir = os.path.dirname(self.preview.current_midi_path)
        else:
            default_dir = os.getcwd()

        default_path = os.path.join(default_dir, default_name)

        save_path, _ = QFileDialog.getSaveFileName(
            self.preview,
            "导出 MIDI",
            default_path,
            "MIDI Files (*.mid *.midi)",
        )
        if not save_path:
            return "CANCELLED"

        try:
            midi_bytes = base64.b64decode(midi_base64)
            with open(save_path, "wb") as f:
                f.write(midi_bytes)
            return save_path
        except Exception as e:
            return f"ERROR: {e}"

    @pyqtSlot(str, result=str)
    def saveMidiToCurrentPath(self, midi_base64: str) -> str:
        if not midi_base64:
            return "ERROR: Empty MIDI data"

        target_path = self.preview.current_midi_path
        if not target_path:
            return "ERROR: 当前未打开MIDI文件"

        target_dir = os.path.dirname(target_path) or os.getcwd()
        try:
            midi_bytes = base64.b64decode(midi_base64)

            with tempfile.NamedTemporaryFile(
                mode="wb", delete=False, dir=target_dir, suffix=".mid"
            ) as tmp:
                tmp.write(midi_bytes)
                tmp_path = tmp.name

            os.replace(tmp_path, target_path)
            return target_path
        except Exception as e:
            try:
                if "tmp_path" in locals() and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return f"ERROR: {e}"

    @pyqtSlot(result=str)
    def getCompareWavList(self) -> str:
        payload = {
            "files": self.preview.compare_wav_files,
            "default": self.preview.default_compare_wav,
            "dir": self.preview.compare_wav_dir,
        }
        return json.dumps(payload, ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def getCompareWavBase64(self, filename: str) -> str:
        if not filename:
            return json.dumps(
                {"ok": False, "error": "empty filename"}, ensure_ascii=False
            )

        if not self.preview.compare_wav_dir:
            return json.dumps(
                {"ok": False, "error": "分轨wav目录不存在"}, ensure_ascii=False
            )

        if filename not in self.preview.compare_wav_files:
            return json.dumps(
                {"ok": False, "error": "文件不在可选列表中"}, ensure_ascii=False
            )

        file_path = os.path.join(self.preview.compare_wav_dir, filename)
        try:
            with open(file_path, "rb") as f:
                data = f.read()
            return json.dumps(
                {
                    "ok": True,
                    "name": filename,
                    "mime": "audio/wav",
                    "data": base64.b64encode(data).decode("ascii"),
                },
                ensure_ascii=False,
            )
        except Exception as e:
            return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


# 已移除 PianoRollWidget（图形 MIDI 预览）。


class EditorManager(QWidget):
    """
    右侧内容区域管理器，负责切换 文本/音频/MIDI 视图
    """

    file_saved = pyqtSignal(str)  # 向上层通知保存
    file_changed = pyqtSignal(str)  # 向上层通知修改

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)

        self.stack = QStackedWidget()

        # Page 0: Empty
        self.empty_lbl = QLabel("请在左侧选择文件")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.stack.addWidget(self.empty_lbl)

        # Page 1: CSV Tab (表格+文本切换)
        self.csv_tab = QTabWidget()
        self.csv_table_editor = CsvTableEditor()
        self.csv_text_editor = TextEditor()

        # 信号连接
        self.csv_table_editor.on_save.connect(self._save_csv_file)
        self.csv_table_editor.on_change.connect(self.file_changed.emit)
        self.csv_text_editor.on_save.connect(self._save_text_file)
        self.csv_text_editor.on_change.connect(self.file_changed.emit)

        # 添加 Tab
        self.csv_tab.addTab(self.csv_table_editor, "表格视图")
        self.csv_tab.addTab(self.csv_text_editor, "纯文本视图")

        # --- 修复缺陷：监听 Tab 切换以同步数据 ---
        self.csv_tab.currentChanged.connect(self._on_csv_tab_changed)

        self.stack.addWidget(self.csv_tab)

        # Page 2: Text Editor (非csv)
        self.text_editor = TextEditor()
        self.text_editor.on_save.connect(self._save_text_file)
        self.text_editor.on_change.connect(self.file_changed.emit)
        self.stack.addWidget(self.text_editor)

        # Page 3: Audio Player
        self.audio_widget = AudioPlayerWithWaveform()
        self.stack.addWidget(self.audio_widget)

        # Page 4: MIDI 元信息预览（只显示文件头信息）
        self.midi_info = MidiPreview()
        self.stack.addWidget(self.midi_info)

        self.layout.addWidget(self.stack)

    @property
    def media_player(self):
        return self.audio_widget.media_player

    def _on_csv_tab_changed(self, index):
        """处理CSV视图切换时的数据同步"""
        # index 0: 表格视图, index 1: 文本视图

        if index == 0:
            # 切换到表格：从文本编辑器获取文本 -> 解析 -> 填入表格
            text_content = self.csv_text_editor.get_content()
            self.csv_table_editor.load_from_string(
                text_content, reset_history=False, reset_changed=False
            )
        elif index == 1:
            # 切换到文本：从表格获取内容 -> 转换为CSV字符串 -> 填入文本编辑器
            csv_string = self.csv_table_editor._to_csv_string()
            self.csv_text_editor.set_content(csv_string)

    def open_file(self, path):
        # 切换前停止播放
        self.media_player.stop()

        ext = os.path.splitext(path)[1].lower()

        if ext == ".csv":
            self.stack.setCurrentIndex(1)
            try:
                # 初始加载时，只加载表格，并尝试切换到表格页
                # 文本页会在用户点击 Tab 切换时自动同步
                self.csv_table_editor.load_file(path)

                # 同时加载文本编辑器（用于纯文本视图）；使用 load_file 以清空其撤销栈
                self.csv_text_editor.load_file(path)

                self.csv_tab.setTabEnabled(0, True)
                self.csv_tab.setTabEnabled(1, True)
                self.csv_tab.setCurrentIndex(0)

            except Exception as e:
                # 解析失败，禁用表格页，仅显示文本
                self.csv_tab.setTabEnabled(0, False)
                self.csv_tab.setTabEnabled(1, True)
                self.csv_tab.setCurrentIndex(1)
                self.csv_text_editor.load_file(path)

        elif ext in [".txt", ".py", ".md", ".log", ".json"]:
            self.stack.setCurrentIndex(2)
            self.text_editor.load_file(path)

        elif ext in [".wav", ".mp3", ".ogg", ".flac"]:
            self.stack.setCurrentIndex(3)
            self.audio_widget.load_file(path)

        elif ext in [".mid", ".midi"]:
            # 仅显示 MIDI 元信息（文件头），不进行图形化预览
            self.stack.setCurrentIndex(4)
            self.midi_info.load_file(path)

        else:
            self.stack.setCurrentIndex(0)
            self.empty_lbl.setText(f"不支持预览此文件类型: {ext}")

    def close_all_tabs(self):
        self.media_player.stop()
        self.text_editor.clear()
        self.csv_table_editor.clear()
        self.csv_text_editor.clear()
        self.stack.setCurrentIndex(0)
        self.empty_lbl.setText("请在左侧选择文件")

    def _save_text_file(self, path, content):
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(content)
            self.file_saved.emit(path)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

    def _save_csv_file(self, path, content):
        try:
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(content)
            self.file_saved.emit(path)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

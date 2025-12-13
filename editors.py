"""
编辑器模块
包含文本编辑器、音频播放器、MIDI预览器和编辑器管理器
"""

from PyQt6.QtCore import pyqtSignal, Qt, QThread
import os
import struct
import csv
from io import StringIO
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
)
from PyQt6.QtGui import (
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QColor,
    QKeySequence,
    QPainter,
    QBrush,
    QPen,
)
import numpy as np
import librosa
import mido
import pyqtgraph as pg
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
        self.table.itemChanged.connect(self._handle_item_changed)
        layout.addWidget(self.table)

        btn_add_row.clicked.connect(self.add_row)
        btn_add_col.clicked.connect(self.add_column)
        btn_del_row.clicked.connect(self.remove_row)
        btn_del_col.clicked.connect(self.remove_column)

        self.current_path = None
        self.loading = False
        self._changed = False

    def load_file(self, path):
        """从文件加载"""
        self.current_path = path
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
            self.load_from_string(content)
        except Exception as e:
            raise RuntimeError(f"CSV文件读取失败: {e}")

    def load_from_string(self, content):
        """从字符串加载数据 (用于视图同步)"""
        self.loading = True
        self._changed = False
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
        self.loading = False

    def _handle_item_changed(self, item):
        if not self.loading and self.current_path:
            self._changed = True
            self.on_change.emit(self.current_path)

    def keyPressEvent(self, event):
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
        self.table.insertRow(self.table.rowCount())
        self._handle_manual_change()

    def add_column(self):
        self.table.insertColumn(self.table.columnCount())
        self._handle_manual_change()

    def remove_row(self):
        row = self.table.currentRow()
        if row >= 0:
            self.table.removeRow(row)
            self._handle_manual_change()

    def remove_column(self):
        col = self.table.currentColumn()
        if col >= 0:
            self.table.removeColumn(col)
            self._handle_manual_change()

    def _handle_manual_change(self):
        """处理增删行列等非itemChanged触发的修改"""
        if not self.loading and self.current_path:
            self.on_change.emit(self.current_path)


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
        self.editor.setPlainText(content)
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

    def load_file(self, path):
        if self._loader and self._loader.isRunning():
            self._loader.requestInterruption()
            self._loader.wait()

        self.data = None
        self.resampled_data = None
        self.sr = 0
        self.current_time_ms = 0
        self._loading = True
        self.update()

        target_points = 1500
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

        if self.resampled_data is None or len(self.resampled_data) == 0:
            return

        painter = QPainter(self)
        rect = self.rect()
        width = rect.width()
        height = rect.height()

        painter.fillRect(rect, QColor(255, 255, 255))

        data_to_draw = self.resampled_data
        max_amplitude = np.max(np.abs(self.data)) or 1.0

        center_y = height / 2
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawLine(0, int(center_y), width, int(center_y))

        painter.setPen(QPen(QColor(0, 120, 215), 1))

        num_points = len(data_to_draw) // 2
        for i in range(num_points):
            x = int(i / num_points * width)
            y_max_norm = data_to_draw[i * 2] / max_amplitude
            y_min_norm = data_to_draw[i * 2 + 1] / max_amplitude

            y1 = center_y - (y_max_norm * center_y * 0.9)
            y2 = center_y - (y_min_norm * center_y * 0.9)

            painter.drawLine(x, int(y1), x, int(y2))

        total_duration_ms = self.get_duration_ms()
        if total_duration_ms > 0 and self._is_playing:
            progress_ratio = self.current_time_ms / total_duration_ms
            x_pos = int(progress_ratio * width)
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
            click_ratio = max(0, min(1, x_click / width))
            target_time_ms = int(click_ratio * total_duration_ms)
            self.on_seek_request.emit(target_time_ms)

        super().mousePressEvent(event)


class AudioPlayerWithWaveform(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.waveform_widget = WaveformWidget()
        self.media_player = MediaPlayer()

        self.waveform_widget.on_seek_request.connect(self.media_player.seek_ms)
        self.media_player.position_changed.connect(
            self.waveform_widget.update_play_position
        )
        self.media_player.play_state_changed.connect(self.waveform_widget.set_playing)

        layout.addWidget(self.waveform_widget)
        layout.addWidget(self.media_player)

    def load_file(self, path):
        self.waveform_widget.load_file(path)
        self.media_player.load_file(path)

    def stop(self):
        self.media_player.stop()
        self.waveform_widget.set_playing(False)
        self.waveform_widget.update_play_position(0)


class MidiPreview(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        self.text_view = QTextEdit()
        self.text_view.setReadOnly(True)
        self.text_view.setStyleSheet("font-family: Consolas; font-size: 12px;")
        layout.addWidget(self.text_view)

    def load_file(self, path):
        info = self._parse_midi_header(path)
        self.text_view.setText(info)

    def _parse_midi_header(self, path):
        try:
            with open(path, "rb") as f:
                chunk_type = f.read(4)
                if chunk_type != b"MThd":
                    return "非标准 MIDI 文件"

                length = struct.unpack(">I", f.read(4))[0]
                data = f.read(length)
                fmt, tracks, division = struct.unpack(">hhh", data[:6])

                info = f"=== MIDI 文件信息 ===\n\n"
                info += f"文件名: {os.path.basename(path)}\n"
                info += f"格式类型 (Format): {fmt}\n"
                info += f"音轨数量 (Tracks): {tracks}\n"
                info += f"时间精度 (Division): {division} ticks/quarter note\n"
                info += f"\n(注: 此预览仅显示文件头信息，暂不支持播放)"
                return info
        except Exception as e:
            return f"解析失败: {e}"


class PianoRollWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setTitle("MIDI 钢琴卷帘预览")
        self.plot_item.setLabel("left", "音高 (MIDI Note)")
        self.plot_item.setLabel("bottom", "时间 (Beats/Ticks)")
        self.plot_item.setYRange(21, 108)
        self.plot_item.hideAxis("bottom")

        self.colors = [
            (255, 0, 0),
            (0, 0, 255),
            (0, 150, 0),
            (255, 128, 0),
            (128, 0, 128),
            (0, 128, 128),
            (150, 150, 0),
            (50, 50, 50),
        ]

        layout.addWidget(self.plot_widget)

    def load_file(self, path):
        self.plot_item.clear()
        try:
            mid = mido.MidiFile(path)
        except Exception as e:
            QMessageBox.critical(self, "MIDI 加载错误", f"无法加载 MIDI 文件: {e}")
            return

        max_time = 0

        for i, track in enumerate(mid.tracks):
            current_time = 0
            open_notes = {}
            track_notes = []

            for msg in track:
                current_time += msg.time

                if msg.type == "note_on" and msg.velocity > 0:
                    open_notes[msg.note] = current_time
                elif msg.type == "note_off" or (
                    msg.type == "note_on" and msg.velocity == 0
                ):
                    note = msg.note
                    if note in open_notes:
                        start_time = open_notes.pop(note)
                        duration = current_time - start_time
                        if duration > 0:
                            track_notes.append(
                                {
                                    "start": start_time,
                                    "end": current_time,
                                    "pitch": note,
                                    "channel": msg.channel,
                                }
                            )

            color = self.colors[i % len(self.colors)]
            brush = QBrush(QColor(*color, 180))
            pen = QPen(QColor(*color, 255), 0.5)

            for note_data in track_notes:
                start = note_data["start"]
                duration = note_data["end"] - start
                pitch = note_data["pitch"]

                # QGraphicsRectItem 绘制矩形
                rect = pg.QtGui.QGraphicsRectItem(start, pitch - 0.5, duration, 1.0)
                rect.setBrush(brush)
                rect.setPen(pen)
                self.plot_item.addItem(rect)

                max_time = max(max_time, note_data["end"])

        if max_time > 0:
            self.plot_item.setXRange(0, max_time * 1.05)
            self.plot_item.setYRange(21, 108)
            self.plot_item.showAxis("bottom")


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

        # Page 4: MIDI Preview
        self.midi_preview = PianoRollWidget()
        self.stack.addWidget(self.midi_preview)

        # Page 5: Old MIDI Info
        self.midi_info_old = MidiPreview()
        self.stack.addWidget(self.midi_info_old)

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
            self.csv_table_editor.load_from_string(text_content)
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

                # 同时静默加载文本编辑器，防止第一次切换闪烁或没数据
                with open(path, "r", encoding="utf-8-sig") as f:
                    self.csv_text_editor.set_content(f.read())
                    # 更新当前路径，确保保存功能正常
                    self.csv_text_editor.current_path = path

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
            self.stack.setCurrentIndex(4)
            self.midi_preview.load_file(path)

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

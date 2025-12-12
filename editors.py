"""
编辑器模块
包含文本编辑器、音频播放器、MIDI预览器和编辑器管理器
"""

import os
import struct
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextEdit,
    QStackedWidget,
    QMessageBox,
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont, QSyntaxHighlighter, QTextCharFormat, QColor, QKeySequence
from audio_player import MediaPlayer
import numpy as np
import librosa  # 用于高效加载音频数据
import mido  # 用于解析 MIDI 文件
import pyqtgraph as pg  # 用于高效的波形和钢琴卷帘绘图
from PyQt6.QtGui import QPainter, QBrush, QPen


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


class WaveformWidget(QWidget):
    """
    音频波形图预览组件
    """

    def __init__(self):
        super().__init__()
        self.setMinimumHeight(200)
        self.data = None  # 音频数据
        self.sr = 0  # 采样率

    def load_file(self, path):
        try:
            # 使用 librosa 加载音频数据，默认转为单声道，以便绘制
            # 设置 sr=None 以保留原始采样率
            self.data, self.sr = librosa.load(path, sr=None, mono=True)
            self.update()  # 触发 paintEvent 重新绘制
        except Exception as e:
            QMessageBox.critical(self, "音频加载错误", f"无法加载音频文件: {e}")
            self.data = None
            self.sr = 0
            self.update()

    def paintEvent(self, event):
        """自定义绘制波形图"""
        super().paintEvent(event)
        if self.data is None or len(self.data) == 0:
            return

        painter = QPainter(self)
        rect = self.rect()
        width = rect.width()
        height = rect.height()

        # 背景和基础设置
        painter.fillRect(rect, QColor(255, 255, 255))
        painter.setPen(QPen(QColor(0, 120, 215), 1))  # 波形颜色

        # 压缩数据以适应像素宽度（最大值抽样）
        step = len(self.data) / width
        max_amplitude = np.max(np.abs(self.data)) or 1.0

        # 绘制中心线 (0幅值)
        center_y = height / 2
        painter.setPen(QPen(QColor(180, 180, 180), 1))
        painter.drawLine(0, int(center_y), width, int(center_y))

        painter.setPen(QPen(QColor(0, 120, 215), 1))

        # 绘制波形
        # 我们只画一条线来代表波形的外轮廓 (上包络线)
        points = []
        for x in range(width):
            start = int(x * step)
            end = int((x + 1) * step)

            # 从数据窗口中采样，取最大幅值（代表波形高度）
            window = self.data[start:end]
            if len(window) > 0:
                # 归一化后映射到绘图区域
                y_max = np.max(window) / max_amplitude
                y_min = np.min(window) / max_amplitude

                # 映射到屏幕坐标
                # 绘制上下包络线
                y1 = center_y - (y_max * center_y * 0.9)  # 90%高度
                y2 = center_y - (y_min * center_y * 0.9)

                # 使用 QPainter.drawLine 绘制垂直的采样线段
                painter.drawLine(x, int(y1), x, int(y2))


class AudioPlayerWithWaveform(QWidget):
    """
    整合了播放器和波形图的容器
    """

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.waveform_widget = WaveformWidget()
        self.media_player = MediaPlayer()  # 假设 MediaPlayer 提供了播放控制 UI

        layout.addWidget(self.waveform_widget)
        layout.addWidget(self.media_player)

    def load_file(self, path):
        self.waveform_widget.load_file(path)
        self.media_player.load_file(path)  # 假设 MediaPlayer 也有 load_file 方法

    def stop(self):
        self.media_player.stop()


class MidiPreview(QWidget):
    """
    MIDI 信息预览（无声，仅解析结构）
    """

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
        # 简单的二进制解析，无需依赖 mido
        try:
            with open(path, "rb") as f:
                chunk_type = f.read(4)
                if chunk_type != b"MThd":
                    return "非标准 MIDI 文件"

                length = struct.unpack(">I", f.read(4))[0]
                data = f.read(length)
                # 格式: format, tracks, division (3个16位无符号整数)
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
    """
    多轨 MIDI 钢琴卷帘预览（使用 PyQtGraph）
    """

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.plot_widget = pg.PlotWidget()
        self.plot_item = self.plot_widget.getPlotItem()
        self.plot_item.setTitle("MIDI 钢琴卷帘预览")
        self.plot_item.setLabel("left", "音高 (MIDI Note)")
        self.plot_item.setLabel("bottom", "时间 (Beats/Ticks)")
        self.plot_item.setYRange(21, 108)  # 标准钢琴音符范围 A0-C8

        # 禁用默认的交互式横轴缩放/拖动，让音符看起来更像矩形
        self.plot_item.hideAxis("bottom")

        # 颜色映射表 (用于区分不同音轨/通道)
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
        self.plot_item.clear()  # 清空旧的绘图

        try:
            mid = mido.MidiFile(path)
        except Exception as e:
            QMessageBox.critical(self, "MIDI 加载错误", f"无法加载 MIDI 文件: {e}")
            return

        max_time = 0

        # 遍历每个音轨
        for i, track in enumerate(mid.tracks):
            current_time = 0
            open_notes = {}  # {note_number: start_time}
            track_notes = []  # [(start_time, duration, pitch, velocity)]

            # 使用 mido 的累积时间
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

            # --- 绘制音符矩形 ---
            # Pyqtgraph 不直接支持高效的矩形绘制，我们使用 BarGraphItem 模拟或使用 fillBetween

            # 采用 QGraphicsRectItem 的方式，这是更准确的钢琴卷帘实现
            color = self.colors[i % len(self.colors)]
            brush = QBrush(QColor(*color, 180))
            pen = QPen(QColor(*color, 255), 0.5)

            # 绘制音符的图形项
            for note_data in track_notes:
                start = note_data["start"]
                duration = note_data["end"] - start
                pitch = note_data["pitch"]

                # 转换为图形坐标 (x, y, width, height)
                x = start
                y = pitch - 0.5  # 音符框占据整个音高行
                width = duration
                height = 1.0

                rect = pg.QtGui.QGraphicsRectItem(x, y, width, height)
                rect.setBrush(brush)
                rect.setPen(pen)
                self.plot_item.addItem(rect)

                max_time = max(max_time, note_data["end"])

        if max_time > 0:
            self.plot_item.setXRange(0, max_time * 1.05)  # X轴范围
            self.plot_item.setYRange(21, 108)
            self.plot_item.showAxis("bottom")  # 有了范围后重新显示X轴


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

        # Page 1: Text Editor
        self.text_editor = TextEditor()
        self.text_editor.on_save.connect(self._save_text_file)
        self.text_editor.on_change.connect(self.file_changed.emit)
        self.stack.addWidget(self.text_editor)

        # Page 2: Audio Player (新的集成组件)
        # 注意: self.media_player 现在是 AudioPlayerWithWaveform 的实例，
        # 它内部包含了旧的 MediaPlayer 实例
        self.audio_widget = AudioPlayerWithWaveform()
        self.stack.addWidget(self.audio_widget)

        # Page 3: MIDI Preview (新的钢琴卷帘组件)
        self.midi_preview = PianoRollWidget()  # 使用新的 PianoRollWidget
        self.stack.addWidget(self.midi_preview)

        # Page 4: Old MIDI Info (作为备用，可以移除)
        self.midi_info_old = MidiPreview()
        self.stack.addWidget(self.midi_info_old)

        self.layout.addWidget(self.stack)

    # 提供对内部播放器的访问，供上层调用 stop()
    @property
    def media_player(self):
        return self.audio_widget.media_player

    def open_file(self, path):
        # 切换前停止播放
        self.media_player.stop()

        ext = os.path.splitext(path)[1].lower()

        if ext in [".txt", ".csv", ".py", ".md", ".log", ".json"]:
            self.stack.setCurrentIndex(1)
            self.text_editor.load_file(path)

        # 使用新的集成音频组件
        elif ext in [".wav", ".mp3", ".ogg", ".flac"]:
            self.stack.setCurrentIndex(2)
            self.audio_widget.load_file(path)  # 调用新组件的 load_file

        # 使用新的钢琴卷帘组件
        elif ext in [".mid", ".midi"]:
            self.stack.setCurrentIndex(3)
            self.midi_preview.load_file(path)  # 调用新组件的 load_file

        else:
            self.stack.setCurrentIndex(0)
            self.empty_lbl.setText(f"不支持预览此文件类型: {ext}")

    def close_all_tabs(self):
        """关闭所有打开的编辑器/预览器，重置为空白状态"""
        # 1. 停止播放
        self.media_player.stop()  # 通过 property 访问内部播放器

        # 2. 清空文本编辑器 (使用 clear 避免触发 change 信号)
        self.text_editor.clear()

        # 3. 切换回空白页
        self.stack.setCurrentIndex(0)
        self.empty_lbl.setText("请在左侧选择文件")

    def _save_text_file(self, path, content):
        try:
            # 使用 newline='' 确保在 Windows 上保存 CSV 文件时不会产生额外的空行
            with open(path, "w", encoding="utf-8-sig", newline="") as f:
                f.write(content)
            self.file_saved.emit(path)
        except Exception as e:
            QMessageBox.critical(self, "保存失败", str(e))

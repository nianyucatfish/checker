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

        # Page 2: Audio Player
        self.media_player = MediaPlayer()
        self.stack.addWidget(self.media_player)

        # Page 3: MIDI Preview
        self.midi_preview = MidiPreview()
        self.stack.addWidget(self.midi_preview)

        self.layout.addWidget(self.stack)

    def open_file(self, path):
        # 切换前停止播放
        self.media_player.stop()

        ext = os.path.splitext(path)[1].lower()

        if ext in [".txt", ".csv", ".py", ".md", ".log", ".json"]:
            self.stack.setCurrentIndex(1)
            self.text_editor.load_file(path)

        elif ext in [".wav", ".mp3", ".ogg", ".flac"]:
            self.stack.setCurrentIndex(2)
            self.media_player.load_file(path)

        elif ext in [".mid", ".midi"]:
            self.stack.setCurrentIndex(3)
            self.midi_preview.load_file(path)

        else:
            self.stack.setCurrentIndex(0)
            self.empty_lbl.setText(f"不支持预览此文件类型: {ext}")

    def close_all_tabs(self):
        """关闭所有打开的编辑器/预览器，重置为空白状态"""
        # 1. 停止播放
        self.media_player.stop()

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

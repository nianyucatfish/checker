"""
音频播放模块
包含音频播放工作线程和音频播放器界面组件
"""

import os
import soundfile as sf
import sounddevice as sd
from PyQt6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QMouseEvent


class ClickableSlider(QSlider):
    """
    一个支持点击任意位置跳转的滑块 (新组件)
    """

    seek_started = pyqtSignal()

    def mousePressEvent(self, event: QMouseEvent):
        # 覆盖默认行为，实现点击跳转
        if event.button() == Qt.MouseButton.LeftButton:
            # 计算点击位置占滑块总长度的比例
            if self.orientation() == Qt.Orientation.Horizontal:
                # 使用 QStyle 辅助函数计算点击位置对应的值
                style = self.style()
                position = style.sliderPositionFromValue(
                    self.minimum(),
                    self.maximum(),
                    event.position().x()
                    / self.width()
                    * (self.maximum() - self.minimum()),
                    self.maximum() - self.minimum(),
                )
            else:
                super().mousePressEvent(event)
                return

            # 发出 seek_started 信号，通知父容器设置 is_seeking=True
            self.seek_started.emit()

            # 设置新值
            self.setValue(position)

            # 立即发出 sliderReleased 信号，模拟拖动/点击结束，触发跳转逻辑
            self.sliderReleased.emit()

        super().mousePressEvent(event)


class AudioPlaybackWorker(QThread):
    """音频播放子线程（使用 sounddevice）"""

    finished = pyqtSignal()
    position_update = pyqtSignal(int)
    duration_update = pyqtSignal(int)  # Duration in ms

    def __init__(self, file_path):
        super().__init__()
        self.file_path = file_path
        self.stop_requested = False
        self.pause_requested = False
        self.current_position = 0  # in frames
        self.seek_position = -1  # in frames, for seeking

    def run(self):
        try:
            with sf.SoundFile(self.file_path, "r") as audio_file:
                samplerate = audio_file.samplerate
                # 计算总时长 (ms)
                duration_ms = int(audio_file.frames * 1000 / samplerate)
                self.duration_update.emit(duration_ms)

                # 使用默认的音频输出设备
                with sd.OutputStream(
                    samplerate=samplerate, channels=audio_file.channels, dtype="float32"
                ) as stream:
                    audio_file.seek(self.current_position)

                    while not self.stop_requested:
                        # 处理暂停
                        while self.pause_requested:
                            self.msleep(100)
                            if self.stop_requested:
                                break

                        if self.stop_requested:
                            break

                        # 处理跳转
                        if self.seek_position != -1:
                            audio_file.seek(self.seek_position)
                            self.current_position = self.seek_position
                            self.seek_position = -1

                        # 读取音频块
                        blocksize = 1024
                        data = audio_file.read(blocksize, dtype="float32")
                        if len(data) == 0:
                            break  # End of file

                        stream.write(data)
                        self.current_position += len(data)

                        # 发送当前位置 (frames to ms)
                        pos_ms = int(self.current_position * 1000 / samplerate)
                        self.position_update.emit(pos_ms)

        except Exception as e:
            # 这里的 print 最好替换成日志记录或向 UI 发送错误信号
            print(f"Audio Playback Error: {e}")
        finally:
            self.stop_requested = True
            self.finished.emit()

    def stop_playback(self):
        self.stop_requested = True
        self.wait()

    def pause_playback(self, state):
        self.pause_requested = state

    def seek_to(self, ms_pos, samplerate):
        """线程内部的跳转方法 (接收 ms 转换为 frames)"""
        self.seek_position = int(ms_pos * samplerate / 1000)


class MediaPlayer(QWidget):
    """
    音频播放器预览组件（使用 sounddevice）
    """

    # 新增公共信号 (对应上一轮 WaveformWidget 中的连接)
    position_changed = pyqtSignal(int)
    # 播放状态变化：True=正在播放，False=未播放/暂停/停止 (新增)
    play_state_changed = pyqtSignal(bool)

    # 外部接口：供波形图调用进行跳转
    def seek_ms(self, ms_pos):
        # 设置 is_seeking=True，防止 position_update 信号覆盖我们的设置
        self.is_seeking = True
        # 无论是否在播放，都更新滑块与时间标签，保持 UI 一致
        try:
            self.slider.setValue(ms_pos)
        except Exception:
            pass
        self._update_time_label(ms_pos, self.slider.maximum())
        # 主动广播位置变化，即使在暂停状态下也更新波形竖线
        self.position_changed.emit(ms_pos)
        # 若正在播放，则执行线程内跳转；未播放时保留为首次播放起点
        self._seek_to_position(ms_pos)
        # 延迟重置 is_seeking
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(100, lambda: self._reset_seeking())

    def __init__(self):
        super().__init__()
        self.playback_worker = None
        self.is_playing = False
        self.path = None
        self.current_samplerate = 0
        self.current_duration_ms = 0
        self.is_seeking = (
            False  # 新增状态，避免在拖动/点击时，被 position_update 信号覆盖滑块值
        )

        self._init_ui()

        # 连接滑块拖动和点击事件 (修改连接)
        self.slider.seek_started.connect(self._handle_seek_started)
        self.slider.sliderMoved.connect(self._handle_slider_moved)
        self.slider.sliderReleased.connect(self._handle_slider_released)

    def _init_ui(self):
        # ... (此方法内容保持不变) ...
        layout = QVBoxLayout(self)

        # Info Area
        self.info_label = QLabel("Ready")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setStyleSheet(
            "font-size: 14px; font-weight: bold; color: #555; margin: 20px;"
        )
        layout.addWidget(self.info_label)

        # Controls
        controls = QHBoxLayout()

        self.btn_play = QPushButton()
        self.btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.clicked.connect(self._toggle_play)
        self.btn_play.setEnabled(False)  # 默认禁用，直到加载文件

        # 使用 ClickableSlider
        self.slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 0)

        self.lbl_time = QLabel("00:00 / 00:00")

        controls.addWidget(self.btn_play)
        controls.addWidget(self.slider)
        controls.addWidget(self.lbl_time)

        layout.addLayout(controls)
        layout.addStretch()

    def load_file(self, path):
        self.stop()  # 确保停止当前播放
        self.path = path
        self.slider.setRange(0, 0)
        self.lbl_time.setText("00:00 / 00:00")
        self.btn_play.setEnabled(False)
        self.current_duration_ms = 0

        # 获取音频信息
        try:
            info = sf.info(path)
            self.current_samplerate = info.samplerate
            # 预先计算总时长，避免等到播放线程才更新
            self.current_duration_ms = int(info.frames * 1000 / info.samplerate)
            self.slider.setRange(0, self.current_duration_ms)
            self._update_time_label(0, self.current_duration_ms)
            details = f"Samplerate: {info.samplerate} Hz | Channels: {info.channels} | Format: {info.subtype}"
            self.info_label.setText(f"{os.path.basename(path)}\n\n{details}")
            self.btn_play.setEnabled(True)
        except Exception as e:
            self.info_label.setText(f"无法读取音频文件 ({os.path.basename(path)}): {e}")

    def _toggle_play(self):
        if not self.path or not self.btn_play.isEnabled():
            return

        if self.is_playing:
            # 当前正在播放 -> 暂停
            if self.playback_worker and self.playback_worker.isRunning():
                self.playback_worker.pause_playback(True)
            self.is_playing = False
            self.btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
            )
            # 暂停时保持竖线可见（仅在停止/结束时隐藏）
            self.play_state_changed.emit(True)
        else:
            # 当前未播放 -> 播放/恢复
            if self.playback_worker and self.playback_worker.isRunning():
                # 从暂停恢复
                self.playback_worker.pause_playback(False)
            else:
                # 启动新的播放线程
                self.playback_worker = AudioPlaybackWorker(self.path)

                # 如果用户在首次播放前已通过滑块/波形设定了起始位置，
                # 则从该位置开始播放（ms -> frames）
                if self.current_samplerate > 0:
                    start_ms = self.slider.value()
                    self.playback_worker.current_position = int(
                        start_ms * self.current_samplerate / 1000
                    )

                # --- 新增/修改的连接逻辑 ---
                # 1. 连接 worker 的 position_update 到内部滑块更新
                self.playback_worker.position_update.connect(self._update_slider)
                # 2. 连接 worker 的 position_update 到外部公共信号 (新增)
                self.playback_worker.position_update.connect(self.position_changed)

                self.playback_worker.duration_update.connect(self._update_duration)
                self.playback_worker.finished.connect(self._playback_finished)
                self.playback_worker.start()

            self.is_playing = True
            self.btn_play.setIcon(
                self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPause)
            )
            # 广播状态：开始播放/恢复
            self.play_state_changed.emit(True)

    def _handle_seek_started(self):
        """当滑块被点击时调用，设置 is_seeking=True 防止 position_update 覆盖"""
        self.is_seeking = True

    def _handle_slider_moved(self, ms_pos):
        self.is_seeking = True
        self._update_time_label(ms_pos, self.slider.maximum())

    def _handle_slider_released(self):
        ms_pos = self.slider.value()
        # 统一走 seek_ms，确保在暂停时也更新波形竖线
        # seek_ms 内部已经处理 is_seeking 的逻辑，包括延迟重置
        self.seek_ms(ms_pos)

    def _reset_seeking(self):
        """延迟重置 is_seeking 标志"""
        self.is_seeking = False

    def _seek_to_position(self, ms_pos):
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.seek_to(ms_pos, self.current_samplerate)

    def _update_slider(self, position_ms):
        if not self.is_seeking:
            self.slider.setValue(position_ms)
        self._update_time_label(position_ms, self.slider.maximum())

    def _update_duration(self, duration_ms):
        self.slider.setRange(0, duration_ms)
        self._update_time_label(0, duration_ms)

    def _playback_finished(self):
        self.is_playing = False
        self.btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.is_seeking = False
        # 不再自动设置滑块到末尾，避免切换文件时进度条卡在末尾
        self.slider.setValue(self.slider.minimum())
        # 广播状态：播放结束
        self.play_state_changed.emit(False)

    def stop(self):
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.stop_playback()
        self.is_playing = False
        self.is_seeking = False
        self.btn_play.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay)
        )
        self.slider.setValue(0)
        # 广播状态：停止
        self.play_state_changed.emit(False)

    def _update_time_label(self, current, total):
        def fmt(ms):
            s = (ms // 1000) % 60
            m = ms // 60000
            return f"{m:02}:{s:02}"

        self.lbl_time.setText(f"{fmt(current)} / {fmt(total)}")

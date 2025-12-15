import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import librosa
import numpy as np
import sounddevice as sd
import soundfile as sf
from PyQt6.QtCore import (
    Qt,
    QEvent,
    QThread,
    QTimer,
    pyqtSignal,
    QObject,
    QRunnable,
    QThreadPool,
)
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

import pyqtgraph as pg
import resampy


# --- 数据结构保持不变 ---
@dataclass
class MixTrack:
    path: str
    name: str
    data: np.ndarray
    samplerate: int
    duration_ms: int
    volume: float = 1.0
    muted: bool = False


# --- MixTrackWidget 保持不变 ---
class MixTrackWidget(QWidget):
    """单轨道控件：左侧信息/控制，右侧波形时间线。"""

    def __init__(
        self,
        track: MixTrack,
        visual_data: np.ndarray,  # 新增：直接接收预处理好的可视化数据
        on_mute_change,
        on_solo_change,
        on_remove,
        on_seek,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.track = track
        self._on_mute_change = on_mute_change
        self._on_solo_change = on_solo_change
        self._on_remove = on_remove
        self._on_seek = on_seek
        self._solo_state = False

        # 仿照 MIDI 播放器：左侧固定宽度信息栏，右侧为时间线(波形)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(4, 4, 4, 4)
        main_layout.setSpacing(6)

        sidebar_layout = QVBoxLayout()
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        sidebar_layout.setSpacing(4)

        self.name_label = QLabel("")
        # Tooltip：完整文件名（含后缀）+ 路径
        self.name_label.setToolTip(f"{track.name}\n{track.path}")
        self.name_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        # 信息栏稍窄一点
        self.name_label.setMinimumWidth(140)
        self.name_label.setMaximumWidth(160)
        # self.name_label.setFixedWidth(100)
        sidebar_layout.addWidget(self.name_label)

        self._update_name_elide()

        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(4)

        self.mute_btn = QPushButton("M")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(track.muted)
        self.mute_btn.setFixedSize(34, 24)
        self.mute_btn.clicked.connect(self._handle_mute)
        controls_row.addWidget(self.mute_btn)

        self.solo_btn = QPushButton("S")
        self.solo_btn.setCheckable(True)
        self.solo_btn.setFixedSize(34, 24)
        self.solo_btn.clicked.connect(self._handle_solo)
        controls_row.addWidget(self.solo_btn)

        self.rm_btn = QPushButton("RM")
        self.rm_btn.setFixedSize(38, 24)
        self.rm_btn.clicked.connect(self._on_remove)
        controls_row.addWidget(self.rm_btn)

        sidebar_layout.addLayout(controls_row)
        sidebar_layout.addStretch(1)
        main_layout.addLayout(sidebar_layout)

        timeline_layout = QVBoxLayout()
        timeline_layout.setContentsMargins(0, 0, 0, 0)
        timeline_layout.setSpacing(0)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=False)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")

        self.plot.setMouseEnabled(False, False)
        self.plot.plotItem.hideButtons()
        # 允许点击事件
        self.plot.scene().sigMouseClicked.connect(self._on_plot_clicked)
        # 忽略滚轮事件，让其冒泡到 QScrollArea
        self.plot.wheelEvent = lambda event: event.ignore()

        self.plot.setMinimumHeight(70)
        self.plot.setMaximumHeight(70)

        # 核心逻辑不变：直接绘制预处理的 visual_data
        self._draw_precalculated_waveform(visual_data, track.duration_ms)

        timeline_layout.addWidget(self.plot)
        main_layout.addLayout(timeline_layout, stretch=1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_name_elide()

    def _update_name_elide(self) -> None:
        # 显示名：隐藏后缀（扩展名），超长时中间省略
        base_name = os.path.splitext(self.track.name)[0]
        metrics = QFontMetrics(self.name_label.font())
        width = max(10, self.name_label.contentsRect().width())
        elided = metrics.elidedText(base_name, Qt.TextElideMode.ElideMiddle, width)
        self.name_label.setText(elided)

    def _draw_precalculated_waveform(
        self, visual_data: np.ndarray, duration_ms: float
    ) -> None:
        """直接绘制预处理好的数据，速度极快"""
        if visual_data.size == 0:
            return

        duration_sec = duration_ms / 1000.0
        times = np.linspace(0, duration_sec, visual_data.size)

        # 使用更高效的绘图参数
        self.plot.plot(times, visual_data, pen=pg.mkPen("#0078d7", width=1))
        self.plot.setYRange(-1.05, 1.05)

        try:
            self.progress_line = pg.InfiniteLine(
                pos=0, angle=90, pen=pg.mkPen("#ff0000", width=1)
            )
            self.progress_line.setZValue(10)
            self.plot.addItem(self.progress_line)
            self.progress_line.setVisible(False)
        except Exception:
            self.progress_line = None

    def _on_plot_clicked(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.scenePos()
            mouse_point = self.plot.plotItem.vb.mapSceneToView(pos)
            time_sec = mouse_point.x()
            if self._on_seek:
                self._on_seek(int(time_sec * 1000))

    # ... (其他方法：_handle_mute, _handle_solo, set_position_ms, reset_position 保持不变) ...
    def _handle_mute(self):
        checked = self.mute_btn.isChecked()
        self.track.muted = checked
        if checked and self.solo_btn.isChecked():
            self.solo_btn.setChecked(False)
        self._on_mute_change(checked)

    def _handle_solo(self):
        checked = self.solo_btn.isChecked()
        self._solo_state = checked
        self._on_solo_change(checked)

    def _on_remove(self):
        self._on_remove()  # call parent callback

    def set_position_ms(self, pos_ms: int) -> None:
        if not hasattr(self, "progress_line") or self.progress_line is None:
            return
        if self.track.duration_ms <= 0:
            self.progress_line.setVisible(False)
            return
        pos_sec = pos_ms / 1000.0
        duration_sec = max(0.0, self.track.duration_ms / 1000.0)
        pos_sec = min(max(0.0, pos_sec), duration_sec)
        try:
            self.progress_line.setPos(pos_sec)
            self.progress_line.setVisible(True)
        except Exception:
            pass

    def reset_position(self) -> None:
        if hasattr(self, "progress_line") and self.progress_line is not None:
            try:
                self.progress_line.setPos(0)
                self.progress_line.setVisible(False)
            except Exception:
                pass


# --- MixPlaybackWorker 保持不变 ---
class MixPlaybackWorker(QThread):
    position_update = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, tracks: List[MixTrack], samplerate: int, start_frame: int = 0):
        super().__init__()
        self.tracks = tracks
        self.samplerate = samplerate
        self.stop_requested = False
        self.pause_requested = False
        self.master_gain = 1.0
        self.current_frame = max(0, int(start_frame))
        self.max_frames = (
            max((track.data.size for track in tracks), default=0) if tracks else 0
        )

    def set_max_frames(self, max_frames: int) -> None:
        self.max_frames = max(0, int(max_frames))

    def set_current_frame(self, frame: int) -> None:
        self.current_frame = max(0, int(frame))

    def run(self) -> None:
        if self.max_frames == 0 or self.samplerate <= 0:
            self.finished.emit()
            return
        blocksize = 2048
        try:
            with sd.OutputStream(
                samplerate=self.samplerate, channels=1, dtype="float32"
            ) as stream:
                while not self.stop_requested and self.current_frame < self.max_frames:
                    if self.pause_requested:
                        self.msleep(80)
                        continue

                    frames_left = self.max_frames - self.current_frame
                    frames_to_process = min(blocksize, frames_left)
                    chunk = np.zeros(frames_to_process, dtype=np.float32)

                    for track in self.tracks:
                        if track.muted or track.data.size == 0:
                            continue
                        if self.current_frame >= track.data.size:
                            continue
                        segment = track.data[
                            self.current_frame : self.current_frame + frames_to_process
                        ]
                        if segment.size == 0:
                            continue
                        chunk[: segment.size] += (
                            segment.astype(np.float32) * track.volume
                        )

                    if chunk.size == 0:
                        break

                    chunk *= self.master_gain
                    np.clip(chunk, -1.0, 1.0, out=chunk)
                    stream.write(chunk.reshape(-1, 1))
                    self.current_frame += frames_to_process
                    pos_ms = int(self.current_frame * 1000 / self.samplerate)
                    self.position_update.emit(pos_ms)
                    if self.stop_requested:
                        break
        except Exception as exc:
            print(f"Mix playback error: {exc}")
        finally:
            self.finished.emit()

    def pause_playback(self, state: bool) -> None:
        self.pause_requested = state

    def stop_playback(self) -> None:
        self.stop_requested = True

    def set_master_gain(self, gain: float) -> None:
        self.master_gain = gain


# --- 优化后的并行加载器 ---


class TrackLoaderSignals(QObject):
    """定义信号，因为QRunnable没有信号"""

    loaded = pyqtSignal(object, object)  # (MixTrack, visual_data_array)
    failed = pyqtSignal(str, str, str)  # path, title, message


class TrackLoaderRunnable(QRunnable):
    """
    使用 QRunnable + QThreadPool 实现真正的并发加载。
    同时在后台完成波形数据的降采样（decimation），减轻主线程负担。
    """

    def __init__(self, path: str, target_samplerate: int):
        super().__init__()
        self.path = path
        self.target_samplerate = target_samplerate
        self.signals = TrackLoaderSignals()
        # 允许自动回收
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            if not os.path.isfile(self.path):
                self.signals.failed.emit(self.path, "文件不存在", self.path)
                return

            ext = os.path.splitext(self.path)[1].lower()
            if ext != ".wav":
                self.signals.failed.emit(
                    self.path, "类型不支持", "仅支持 WAV 文件混音。"
                )
                return

            # 读取音频
            data, sr = sf.read(self.path, dtype="float32", always_2d=True)
            if data.size == 0 or sr <= 0:
                self.signals.failed.emit(
                    self.path, "读取失败", "音频数据为空或采样率无效。"
                )
                return

            mono = data.mean(axis=1).astype(np.float32)

            # 重采样 (最耗时的部分，现在多线程并行执行)
            if self.target_samplerate is not None and sr != self.target_samplerate:
                try:
                    # 使用 kaiser_fast 牺牲极少的质量换取速度，或者 'soxr_vhq' 质量优先
                    mono = librosa.resample(
                        mono,
                        orig_sr=sr,
                        target_sr=self.target_samplerate,
                        res_type="kaiser_fast",
                    ).astype(np.float32)
                except Exception as exc:
                    self.signals.failed.emit(self.path, "采样率转换失败", str(exc))
                    return
                sr = self.target_samplerate

            # --- 关键优化：在后台线程准备可视化数据 ---
            # 直接计算降采样后的数组，这样 UI 线程不用处理百万级的数据
            # 假设波形图宽度不超过 2000-4000 像素，步长取 total // 3000 即可
            step = max(1, mono.size // 3000)
            visual_data = mono[::step].copy()  # copy 确保数据连续且独立

            duration_ms = int(mono.size * 1000 / sr) if sr > 0 else 0

            track = MixTrack(
                path=self.path,
                name=os.path.basename(self.path),
                data=mono,  # 原始高保真数据用于混音
                samplerate=sr,
                duration_ms=duration_ms,
            )

            # 发送 MixTrack 和 极小的 VisualData
            self.signals.loaded.emit(track, visual_data)

        except Exception as exc:
            self.signals.failed.emit(self.path, "读取失败", f"无法读取音频：{exc}")


class MixConsoleWindow(QMainWindow):
    """Standalone mix console window with multi-threaded loading."""

    visibility_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("混音台")
        self.resize(900, 600)

        self.tracks: List[MixTrack] = []
        self.track_widgets: Dict[str, MixTrackWidget] = {}

        # 默认混音采样率。如果为 None，第一个加载的文件决定采样率。
        # 建议设置为固定值(如44100)，以便并发加载时目标统一。
        self.mix_samplerate: int = 44100

        self.playback_worker: Optional[MixPlaybackWorker] = None
        self.is_paused = False
        self.user_seeking = False
        self._was_playing_during_seek = False
        self.total_duration_ms = 0
        self.current_position_ms = 0  # Track current position
        self._minimize_to_hide_pending = False

        self._pending_paths: set[str] = set()
        self._loading_total: int = 0
        self._loading_done: int = 0

        # 初始化线程池
        self.thread_pool = QThreadPool()
        # 设置最大线程数，避免卡死机器 (保留一个核给UI)
        self.thread_pool.setMaxThreadCount(max(1, os.cpu_count() - 1))

        self._init_ui()
        self._update_controls_state()

    # ... ( _init_ui 保持不变 ) ...
    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        # 顶栏：左时间 / 中播放停止 / 右主音量+状态（仿照 MIDI 播放器）
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        self.position_label = QLabel("00:00 / 00:00")
        self.position_label.setMinimumWidth(110)
        top_row.addWidget(self.position_label)

        center_widget = QWidget()
        center_layout = QHBoxLayout(center_widget)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(8)
        center_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.btn_play = QPushButton("播放")
        self.btn_play.clicked.connect(self._toggle_play_pause)
        center_layout.addWidget(self.btn_play)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(lambda: self._stop_playback(reset_position=True))
        center_layout.addWidget(self.btn_stop)

        top_row.addWidget(center_widget, 1)

        right_widget = QWidget()
        right_layout = QHBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        self.master_label = QLabel("主音量: 100%")
        self.master_label.setFixedWidth(120)
        right_layout.addWidget(self.master_label)

        self.master_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_slider.setRange(0, 200)
        self.master_slider.setValue(100)
        self.master_slider.setFixedWidth(160)
        self.master_slider.valueChanged.connect(self._on_master_volume_changed)
        right_layout.addWidget(self.master_slider)

        top_row.addWidget(right_widget)
        layout.addLayout(top_row)

        # position_row removed

        # 加载提示：显示在播放条下、所有轨道之前；加载完成自动隐藏
        self.loading_label = QLabel("")
        self.loading_label.setVisible(False)
        self.loading_label.setAlignment(Qt.AlignmentFlag.AlignLeft)
        # 稍稍明显一点：加粗 + 轻底色 + 内边距
        self.loading_label.setStyleSheet(
            "font-weight: 600; padding: 4px 6px; border-radius: 4px; "
            "background: #f6f8fa; border: 1px solid #d0d7de; color: #0969da;"
        )
        layout.addWidget(self.loading_label)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        self.track_container = QWidget()
        self.scroll.setWidget(self.track_container)
        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setContentsMargins(0, 0, 0, 0)
        self.track_layout.setSpacing(8)
        self.track_layout.addStretch(1)

        # 初始化一次加载提示
        self._update_loading_indicator()

    def _update_loading_indicator(self) -> None:
        pending = len(self._pending_paths)

        # 如果没有正在加载的任务，隐藏并重置计数
        if pending <= 0:
            self.loading_label.setVisible(False)
            self._loading_total = 0
            self._loading_done = 0
            return

        # 有任务在跑：显示 done/total
        total = max(1, int(self._loading_total))
        done = min(int(self._loading_done), total)
        self.loading_label.setText(f"正在加载 {done}/{total}")
        self.loading_label.setVisible(True)

    # ------------------------------------------------------------------
    # 核心修改：并发加载逻辑

    def add_track_from_file(self, path: str) -> None:
        if path in self.track_widgets or path in self._pending_paths:
            QMessageBox.information(self, "已存在", "该轨道已在混音台中。")
            return

        self._pending_paths.add(path)
        self._loading_total += 1
        self._update_loading_indicator()

        # 创建 Runnable Worker
        loader = TrackLoaderRunnable(path, target_samplerate=self.mix_samplerate)

        # 连接信号
        # 注意：QRunnable 所在的线程发出的信号会排队传给 UI 线程 (AutoConnection)
        loader.signals.loaded.connect(self._on_track_loaded)
        loader.signals.failed.connect(self._on_track_load_failed)

        # 丢进线程池，立即并行执行
        self.thread_pool.start(loader)

    def _on_track_loaded(self, track: MixTrack, visual_data: np.ndarray) -> None:
        """当单个轨道加载完毕时调用"""
        self._pending_paths.discard(track.path)
        self._loading_done += 1
        self._update_loading_indicator()

        # --- 新增逻辑：寻找插入位置以保持字典序 ---
        insert_index = len(self.tracks)  # 默认为最后

        for i, existing_track in enumerate(self.tracks):
            # 按 track.name (文件名) 进行字典序比较
            # 如果加载的轨道名小于当前遍历的轨道名，则插在它前面
            if track.name < existing_track.name:
                insert_index = i
                break

        # 1. 插入到数据列表的指定位置
        self.tracks.insert(insert_index, track)

        # 2. 插入到 UI 布局的指定位置
        self._insert_track_widget(track, visual_data, insert_index)

        self._refresh_duration()
        self._update_controls_state()

    def _on_track_load_failed(self, path: str, title: str, message: str) -> None:
        self._pending_paths.discard(path)
        self._loading_done += 1
        self._update_loading_indicator()
        QMessageBox.warning(self, title, message)
        self._update_controls_state()

    def _insert_track_widget(
        self, track: MixTrack, visual_data: np.ndarray, index: int
    ) -> None:
        widget = MixTrackWidget(
            track,
            visual_data,  # 传入预计算波形
            on_mute_change=lambda muted: self._on_track_muted(track, muted),
            on_solo_change=lambda solo: self._on_track_solo(track, solo),
            on_remove=lambda: self._remove_track(track),
            on_seek=self._seek_to_position,
        )

        # 使用 insertWidget 替代原来的 insertWidget(count-1) 逻辑
        # 因为我们已经计算好了 index，直接插入即可。
        # 注意：QVBoxLayout 最后的 stretch item 会自动保持在底部。
        self.track_layout.insertWidget(index, widget)

        self.track_widgets[track.path] = widget

    # ... (其余所有方法保持与原版完全一致) ...
    def _remove_track(self, track: MixTrack) -> None:
        idx = next((i for i, t in enumerate(self.tracks) if t.path == track.path), -1)
        if idx >= 0:
            self.tracks.pop(idx)
        widget = self.track_widgets.pop(track.path, None)
        if widget:
            widget.setParent(None)
            widget.deleteLater()
        if not self.tracks:
            # 重置采样率或保持？建议保持，或重置为 44100
            if self.playback_worker and self.playback_worker.isRunning():
                self._stop_playback(reset_position=True)
        else:
            worker = self.playback_worker
            if worker and worker.isRunning():
                new_max = max((t.data.size for t in self.tracks), default=0)
                worker.set_max_frames(new_max)
                if worker.current_frame >= worker.max_frames:
                    worker.set_current_frame(
                        min(worker.current_frame, worker.max_frames)
                    )
        self._refresh_duration()
        self._update_controls_state()

    def _clear_all_tracks(self) -> None:
        for widget in self.track_widgets.values():
            widget.setParent(None)
            widget.deleteLater()
        self.track_widgets.clear()
        self.tracks.clear()
        self.total_duration_ms = 0
        self._update_time_label(0)
        self._update_controls_state()
        # 记得取消 pending 状态
        self._pending_paths.clear()
        self._update_loading_indicator()

    # ... (播放控制、静音独奏、Seek逻辑保持不变) ...
    def _on_track_muted(self, track: MixTrack, muted: bool) -> None:
        track.muted = muted
        self._update_playback_mute_solo()

    def _on_track_solo(self, track: MixTrack, solo: bool) -> None:
        self._update_playback_mute_solo()

    def _toggle_play_pause(self) -> None:
        if self.playback_worker and self.playback_worker.isRunning():
            self.is_paused = not self.is_paused
            self.playback_worker.pause_playback(self.is_paused)
            self.btn_play.setText("继续" if self.is_paused else "暂停")
            return
        self._start_playback()

    def _start_playback(self) -> None:
        if not self.tracks:
            QMessageBox.information(self, "无轨道", "请先添加至少一个轨道。")
            return
        if all(track.muted for track in self.tracks):
            QMessageBox.information(self, "全部静音", "请取消至少一个轨道的静音。")
            return
        self._stop_playback(reset_position=False)
        start_ms = self.current_position_ms
        start_frame = (
            int(start_ms * self.mix_samplerate / 1000) if self.mix_samplerate else 0
        )
        self.playback_worker = MixPlaybackWorker(
            self.tracks, self.mix_samplerate, start_frame=start_frame
        )
        self.playback_worker.set_master_gain(self.master_slider.value() / 100.0)
        self.playback_worker.position_update.connect(self._on_position_update)
        self.playback_worker.finished.connect(self._on_playback_finished)
        self.playback_worker.start()
        self.btn_play.setText("暂停")
        self.btn_play.setEnabled(True)
        self.is_paused = False

    def _on_position_update(self, position_ms: int) -> None:
        if self.user_seeking:
            return
        self.current_position_ms = min(position_ms, self.total_duration_ms)
        self._update_time_label(self.current_position_ms)
        for widget in self.track_widgets.values():
            try:
                widget.set_position_ms(position_ms)
            except Exception:
                pass

    def _on_playback_finished(self) -> None:
        self._stop_playback(reset_position=True)

    def _stop_playback(self, reset_position: bool) -> None:
        worker = self.playback_worker
        if worker and worker.isRunning():
            worker.stop_playback()
            worker.wait()
        self.playback_worker = None
        self.is_paused = False
        self.btn_play.setText("播放")
        self._update_controls_state()
        if reset_position:
            self.current_position_ms = 0
            self._update_time_label(0)
            for widget in self.track_widgets.values():
                try:
                    widget.reset_position()
                except Exception:
                    pass

    def _on_master_volume_changed(self, value: int) -> None:
        self.master_label.setText(f"主音量: {value}%")
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.set_master_gain(value / 100.0)

    def _refresh_duration(self) -> None:
        self.total_duration_ms = (
            max((track.duration_ms for track in self.tracks), default=0)
            if self.tracks
            else 0
        )
        self._update_time_label(self.current_position_ms)

    def _update_time_label(self, current_ms: int) -> None:
        def fmt(ms: int) -> str:
            total_seconds = int(ms / 1000)
            return f"{total_seconds // 60:02}:{total_seconds % 60:02}"

        self.position_label.setText(
            f"{fmt(current_ms)} / {fmt(self.total_duration_ms)}"
        )

    def _update_controls_state(self) -> None:
        has_tracks = bool(self.tracks)
        self.btn_play.setEnabled(has_tracks)
        self.btn_stop.setEnabled(has_tracks)

    def _update_playback_mute_solo(self):
        solo_paths = [
            p for p, w in self.track_widgets.items() if w.solo_btn.isChecked()
        ]
        if solo_paths:
            for path, widget in self.track_widgets.items():
                widget.track.muted = path not in solo_paths
                widget.mute_btn.setChecked(widget.track.muted)
        else:
            for path, widget in self.track_widgets.items():
                widget.track.muted = widget.mute_btn.isChecked()

    def _seek_to_position(self, pos_ms: int) -> None:
        """Handle seek request from track widgets."""
        pos_ms = max(0, min(pos_ms, self.total_duration_ms))
        self.current_position_ms = pos_ms
        self._update_time_label(pos_ms)

        # Update playback worker if running
        if self.playback_worker and self.playback_worker.isRunning():
            new_frame = int(pos_ms * self.mix_samplerate / 1000)
            self.playback_worker.set_current_frame(new_frame)

        # Update all track widgets
        for widget in self.track_widgets.values():
            try:
                widget.set_position_ms(pos_ms)
            except Exception:
                pass

    # Window events
    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self.visibility_changed.emit(False)

    def changeEvent(self, event) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                if not self._minimize_to_hide_pending:
                    self._minimize_to_hide_pending = True
                    QTimer.singleShot(0, self._handle_minimize_hide)
                return
        super().changeEvent(event)

    def _handle_minimize_hide(self) -> None:
        self._minimize_to_hide_pending = False
        self.setWindowState(Qt.WindowState.WindowNoState)
        self.hide()

    def closeEvent(self, event) -> None:
        self._stop_playback(reset_position=True)
        self._clear_all_tracks()
        event.ignore()
        self.hide()

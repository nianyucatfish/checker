import os
from dataclasses import dataclass
from typing import Dict, List, Optional

import librosa
import numpy as np
import sounddevice as sd
import soundfile as sf
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
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


@dataclass
class MixTrack:
    path: str
    name: str
    data: np.ndarray
    samplerate: int
    duration_ms: int
    volume: float = 1.0
    muted: bool = False


class MixTrackWidget(QWidget):
    """单轨道控件，含静音、独奏、移除按钮，波形右侧纵向排列。"""

    def __init__(
        self,
        track: MixTrack,
        on_mute_change,
        on_solo_change,
        on_remove,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.track = track
        self._on_mute_change = on_mute_change
        self._on_solo_change = on_solo_change
        self._on_remove = on_remove
        self._solo_state = False

        # 主布局：水平 (左边是波形区域，右边是按钮区域)
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(8)

        # --- 左侧区域：垂直布局 (上方歌名，下方波形) ---
        wave_area_layout = QVBoxLayout()
        wave_area_layout.setContentsMargins(0, 0, 0, 0)
        wave_area_layout.setSpacing(2)  # 歌名和波形稍微紧凑一点

        # 1. 歌曲名 (放在波形上方，左对齐)
        name_label = QLabel(track.name)
        name_label.setToolTip(track.path)
        # 设置左对齐
        name_label.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
        )
        wave_area_layout.addWidget(name_label)

        # 2. 波形
        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.setMenuEnabled(False)
        self.plot.showGrid(x=False, y=False)
        self.plot.hideAxis("left")
        self.plot.hideAxis("bottom")
        # 禁用鼠标交互：滚轮缩放、拖动平移等，以防止波形被缩放或移动
        try:
            # 优先使用 PlotWidget 的接口（会转发到内部的 ViewBox）
            self.plot.setMouseEnabled(False, False)
        except Exception:
            # 若不可用，再尝试直接操作 ViewBox（兼容性保底）
            try:
                vb = self.plot.getViewBox()
                vb.setMouseEnabled(False, False)
            except Exception:
                pass
        # 隐藏左下角的自动缩放按钮（去掉“A”）
        try:
            self.plot.plotItem.hideButtons()
        except Exception:
            pass
        self.plot.setMinimumHeight(60)
        self.plot.setMaximumHeight(70)
        self._draw_waveform(track.data, track.samplerate)
        wave_area_layout.addWidget(self.plot)

        # 将左侧波形区域加入主布局，并设 stretch=1 占据主要空间
        main_layout.addLayout(wave_area_layout, stretch=1)

        # --- 右侧区域：按钮纵向排列 ---
        btn_col = QVBoxLayout()
        btn_col.setSpacing(4)
        btn_col.setContentsMargins(0, 0, 0, 0)

        # 静音 M
        self.mute_btn = QPushButton("M")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setChecked(track.muted)
        self.mute_btn.setToolTip("静音 (Mute)")
        self.mute_btn.setFixedWidth(32)
        self.mute_btn.clicked.connect(self._handle_mute)
        btn_col.addWidget(self.mute_btn)

        # 独奏 S
        self.solo_btn = QPushButton("S")
        self.solo_btn.setCheckable(True)
        self.solo_btn.setChecked(False)
        self.solo_btn.setToolTip("独奏 (Solo)")
        self.solo_btn.setFixedWidth(32)
        self.solo_btn.clicked.connect(self._handle_solo)
        btn_col.addWidget(self.solo_btn)

        # 移除 RM
        self.rm_btn = QPushButton("RM")
        self.rm_btn.setToolTip("移除轨道")
        self.rm_btn.setFixedWidth(32)
        self.rm_btn.clicked.connect(self._on_remove)
        btn_col.addWidget(self.rm_btn)

        btn_col.addStretch(1)
        main_layout.addLayout(btn_col)

    def _draw_waveform(self, data: np.ndarray, samplerate: int) -> None:
        if data.size == 0 or samplerate <= 0:
            return
        step = max(1, data.size // 4000)
        reduced = data[::step]
        duration = data.size / samplerate
        times = np.linspace(0, duration, reduced.size)
        self.plot.plot(times, reduced, pen=pg.mkPen("#0078d7"))
        self.plot.setYRange(-1.05, 1.05)

    def _handle_mute(self):
        checked = self.mute_btn.isChecked()
        self.track.muted = checked
        # 若静音被选中，则自动取消独奏
        if checked and self.solo_btn.isChecked():
            self.solo_btn.setChecked(False)
        self._on_mute_change(checked)

    def _handle_solo(self):
        checked = self.solo_btn.isChecked()
        self._solo_state = checked
        self._on_solo_change(checked)


class MixPlaybackWorker(QThread):
    """Background worker that performs realtime mixing and playback."""

    position_update = pyqtSignal(int)
    finished = pyqtSignal()

    def __init__(self, tracks: List[MixTrack], samplerate: int):
        super().__init__()
        self.tracks = tracks
        self.samplerate = samplerate
        self.stop_requested = False
        self.pause_requested = False
        self.master_gain = 1.0
        self.current_frame = 0
        self.max_frames = (
            max((track.data.size for track in tracks), default=0) if tracks else 0
        )

    def run(self) -> None:
        if self.max_frames == 0 or self.samplerate <= 0:
            self.finished.emit()
            return

        blocksize = 2048

        try:
            with sd.OutputStream(  # type: ignore[arg-type]
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
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

        except Exception as exc:  # pragma: no cover - device errors
            print(f"Mix playback error: {exc}")
        finally:
            self.finished.emit()

    def pause_playback(self, state: bool) -> None:
        self.pause_requested = state

    def stop_playback(self) -> None:
        self.stop_requested = True

    def set_master_gain(self, gain: float) -> None:
        self.master_gain = gain


class MixConsoleWindow(QMainWindow):
    """Standalone mix console window with basic multi-track support."""

    visibility_changed = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("混音台")
        self.resize(900, 600)

        self.tracks: List[MixTrack] = []
        self.track_widgets: Dict[str, MixTrackWidget] = {}
        self.mix_samplerate: Optional[int] = None
        self.playback_worker: Optional[MixPlaybackWorker] = None
        self.is_paused = False
        self.total_duration_ms = 0

        self._init_ui()
        self._update_controls_state()

    # ------------------------------------------------------------------
    # UI setup
    def _init_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        control_row = QHBoxLayout()
        control_row.setSpacing(10)

        self.btn_add = QPushButton("添加轨道...")
        self.btn_add.clicked.connect(self._on_add_button_clicked)
        control_row.addWidget(self.btn_add)

        self.btn_play = QPushButton("播放")
        self.btn_play.clicked.connect(self._toggle_play_pause)
        control_row.addWidget(self.btn_play)

        self.btn_stop = QPushButton("停止")
        self.btn_stop.clicked.connect(lambda: self._stop_playback(reset_position=True))
        control_row.addWidget(self.btn_stop)

        control_row.addSpacing(12)

        self.master_label = QLabel("主音量: 100%")
        # --- 关键修改：设置固定宽度，防止文字长短变化导致界面抖动 ---
        self.master_label.setFixedWidth(120)
        # 也可以设置AlignRight或者AlignCenter让数字看起来更规整
        # self.master_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        control_row.addWidget(self.master_label)

        self.master_slider = QSlider(Qt.Orientation.Horizontal)
        self.master_slider.setRange(0, 200)
        self.master_slider.setValue(100)
        self.master_slider.setFixedWidth(180)
        self.master_slider.valueChanged.connect(self._on_master_volume_changed)
        control_row.addWidget(self.master_slider)

        control_row.addStretch(1)

        layout.addLayout(control_row)

        position_row = QHBoxLayout()
        position_row.setSpacing(10)

        self.position_slider = QSlider(Qt.Orientation.Horizontal)
        self.position_slider.setEnabled(False)
        self.position_slider.setRange(0, 0)
        position_row.addWidget(self.position_slider)

        self.position_label = QLabel("00:00 / 00:00")
        position_row.addWidget(self.position_label)

        layout.addLayout(position_row)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        layout.addWidget(self.scroll)

        self.track_container = QWidget()
        self.scroll.setWidget(self.track_container)

        self.track_layout = QVBoxLayout(self.track_container)
        self.track_layout.setContentsMargins(0, 0, 0, 0)
        self.track_layout.setSpacing(12)
        self.track_layout.addStretch(1)

    # ------------------------------------------------------------------
    # Public API
    def add_track_from_file(self, path: str) -> None:
        if not os.path.isfile(path):
            QMessageBox.warning(self, "文件不存在", path)
            return

        ext = os.path.splitext(path)[1].lower()
        if ext != ".wav":
            QMessageBox.warning(self, "类型不支持", "仅支持 WAV 文件混音。")
            return

        if path in self.track_widgets:
            QMessageBox.information(self, "已存在", "该轨道已在混音台中。")
            return

        try:
            data, sr = sf.read(path, dtype="float32", always_2d=True)
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", f"无法读取音频：{exc}")
            return

        mono = data.mean(axis=1).astype(np.float32)

        if self.mix_samplerate is None:
            self.mix_samplerate = sr
        elif sr != self.mix_samplerate:
            try:
                mono = librosa.resample(mono, orig_sr=sr, target_sr=self.mix_samplerate)
            except Exception as exc:
                QMessageBox.critical(self, "采样率转换失败", str(exc))
                return
            sr = self.mix_samplerate

        duration_ms = int(mono.size * 1000 / sr) if sr > 0 else 0
        track = MixTrack(
            path=path,
            name=os.path.basename(path),
            data=mono,
            samplerate=sr,
            duration_ms=duration_ms,
        )

        self.tracks.append(track)
        self._append_track_widget(track)
        self._refresh_duration()
        self._update_controls_state()

    # ------------------------------------------------------------------
    # Internal helpers
    def _append_track_widget(self, track: MixTrack) -> None:
        widget = MixTrackWidget(
            track,
            on_mute_change=lambda muted: self._on_track_muted(track, muted),
            on_solo_change=lambda solo: self._on_track_solo(track, solo),
            on_remove=lambda: self._remove_track(track),
        )
        self.track_layout.insertWidget(self.track_layout.count() - 1, widget)
        self.track_widgets[track.path] = widget

    def _remove_track(self, track: MixTrack) -> None:
        if self.playback_worker and self.playback_worker.isRunning():
            self._stop_playback(reset_position=True)

        idx = next((i for i, t in enumerate(self.tracks) if t.path == track.path), -1)
        if idx >= 0:
            self.tracks.pop(idx)

        widget = self.track_widgets.pop(track.path, None)
        if widget:
            widget.setParent(None)
            widget.deleteLater()

        if not self.tracks:
            self.mix_samplerate = None

        self._refresh_duration()
        self._update_controls_state()

    def _on_track_muted(self, track: MixTrack, muted: bool) -> None:
        # 直接设置track.muted，已在widget中同步
        track.muted = muted
        self._update_playback_mute_solo()

    def _on_track_solo(self, track: MixTrack, solo: bool) -> None:
        # 只要有任意轨道solo，则仅solo轨道发声，其余全部静音
        self._update_playback_mute_solo()

    def _on_add_button_clicked(self) -> None:
        from PyQt6.QtWidgets import QFileDialog

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择 WAV 文件",
            os.getcwd(),
            "WAV Files (*.wav)",
        )
        if file_path:
            self.add_track_from_file(file_path)

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

        if self.mix_samplerate is None:
            QMessageBox.warning(self, "采样率未知", "请重新添加轨道。")
            return

        self._stop_playback(reset_position=False)
        self.position_slider.setValue(0)
        self._update_time_label(0)

        self.playback_worker = MixPlaybackWorker(self.tracks, self.mix_samplerate)
        self.playback_worker.set_master_gain(self.master_slider.value() / 100.0)
        self.playback_worker.position_update.connect(self._on_position_update)
        self.playback_worker.finished.connect(self._on_playback_finished)
        self.playback_worker.start()

        self.btn_play.setText("暂停")
        self.btn_play.setEnabled(True)
        self.is_paused = False

    def _on_position_update(self, position_ms: int) -> None:
        self.position_slider.setValue(min(position_ms, self.total_duration_ms))
        self._update_time_label(position_ms)

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
            self.position_slider.setValue(0)
            self._update_time_label(0)

    def _on_master_volume_changed(self, value: int) -> None:
        self.master_label.setText(f"主音量: {value}%")
        gain = value / 100.0
        if self.playback_worker and self.playback_worker.isRunning():
            self.playback_worker.set_master_gain(gain)

    def _refresh_duration(self) -> None:
        self.total_duration_ms = (
            max((track.duration_ms for track in self.tracks), default=0)
            if self.tracks
            else 0
        )
        self.position_slider.setRange(0, self.total_duration_ms)
        self._update_time_label(self.position_slider.value())

    def _update_time_label(self, current_ms: int) -> None:
        def fmt(ms: int) -> str:
            total_seconds = int(ms / 1000)
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes:02}:{seconds:02}"

        total_text = fmt(self.total_duration_ms)
        current_text = fmt(current_ms)
        self.position_label.setText(f"{current_text} / {total_text}")

    def _update_controls_state(self) -> None:
        has_tracks = bool(self.tracks)
        self.btn_play.setEnabled(has_tracks)
        self.btn_stop.setEnabled(has_tracks)

    def _update_playback_mute_solo(self):
        # 检查所有轨道的独奏状态
        solo_paths = [
            p for p, w in self.track_widgets.items() if w.solo_btn.isChecked()
        ]
        if solo_paths:
            # 只播放solo轨道，其余全部静音
            for path, widget in self.track_widgets.items():
                widget.track.muted = path not in solo_paths
                widget.mute_btn.setChecked(widget.track.muted)
        else:
            # 恢复各自的静音状态
            for path, widget in self.track_widgets.items():
                widget.track.muted = widget.mute_btn.isChecked()

    # ------------------------------------------------------------------
    # Event overrides
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.visibility_changed.emit(True)

    def hideEvent(self, event) -> None:  # type: ignore[override]
        super().hideEvent(event)
        self._stop_playback(reset_position=False)
        self.visibility_changed.emit(False)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_playback(reset_position=True)
        event.ignore()
        self.hide()

import os
import shutil
import json
import math
from datetime import datetime
import webbrowser
from PyQt6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QTreeView,
    QTextEdit,
    QSplitter,
    QListWidget,
    QTabWidget,
    QLabel,
    QToolBar,
    QMessageBox,
    QMenu,
    QInputDialog,
    QListWidgetItem,
    QFileDialog,
    QCheckBox,
    QStyledItemDelegate,
    QStyle,
    QApplication,
    QStackedWidget,
    QPushButton,
    QHBoxLayout,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QAbstractItemView,
)
from PyQt6.QtCore import Qt, QTimer, QFileSystemWatcher, QPoint, QRect
from PyQt6.QtGui import QAction, QKeySequence, QColor, QShortcut

from paths import app_config_path
from file_model import ProjectModel
from editors import EditorManager
from workers import InitialScanWorker
from logic_checker import LogicChecker
from mix_console import MixConsoleWindow

import librosa
import numpy as np
import resampy
import soundfile as sf

# --- 配置常量 ---
CONFIG_FILE = app_config_path("ide_config.json")
RECENT_WORKSPACE_KEY = "last_workspace"
SPLITTER_SIZES_KEY = "splitter_sizes"
SUPPRESS_TRIM_DURATION_PROMPT_KEY = "suppress_trim_duration_prompt"


class WavDurationDelegate(QStyledItemDelegate):
    """在文件树同一列中右对齐显示 WAV 时长(mm:ss)。"""

    def paint(self, painter, option, index):
        if index.column() != 0:
            return super().paint(painter, option, index)

        model = index.model()
        try:
            duration_text = model.data(index, ProjectModel.DurationRole)
        except Exception:
            duration_text = None

        if not duration_text:
            return super().paint(painter, option, index)

        widget = option.widget
        style = widget.style() if widget else QApplication.style()

        opt = option
        self.initStyleOption(opt, index)

        # 如果空间过窄，回退默认绘制
        fm = opt.fontMetrics
        dur_w = fm.horizontalAdvance(str(duration_text))
        text_rect = style.subElementRect(
            QStyle.SubElement.SE_ItemViewItemText, opt, widget
        )
        if text_rect.width() < dur_w + 12:
            return super().paint(painter, option, index)

        # 先让 style 画背景/图标/焦点框；我们自己画文本
        saved_text = opt.text
        opt.text = ""
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, widget)
        opt.text = saved_text

        left_rect = QRect(text_rect)
        left_rect.setRight(text_rect.right() - (dur_w + 12))
        right_rect = QRect(text_rect)
        right_rect.setLeft(left_rect.right() + 1)

        painter.save()

        # 颜色策略：优先使用模型提供的 ForegroundRole（错误红/未保存蓝）；否则用普通 Text 颜色。
        # 不在选中时强制 HighlightedText（白色），避免在浅色选中背景下看不清。
        fg = model.data(index, Qt.ItemDataRole.ForegroundRole)
        if isinstance(fg, QColor):
            text_color = fg
        else:
            # 根据 enabled/active 选择 ColorGroup
            if not (opt.state & QStyle.StateFlag.State_Enabled):
                cg = opt.palette.ColorGroup.Disabled
            elif opt.state & QStyle.StateFlag.State_Active:
                cg = opt.palette.ColorGroup.Active
            else:
                cg = opt.palette.ColorGroup.Inactive
            text_color = opt.palette.color(cg, opt.palette.ColorRole.Text)

        painter.setPen(text_color)
        painter.drawText(
            left_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            str(saved_text),
        )
        painter.drawText(
            right_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight),
            str(duration_text),
        )
        painter.restore()


class AutofixPreviewDialog(QDialog):
    def __init__(self, parent, title, ops, base_path):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        self.ops = list(ops)
        self.base_path = base_path

        layout = QVBoxLayout(self)

        tip = QLabel("以下项目将被修复，可选中后移除不想执行的项。")
        layout.addWidget(tip)

        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
        )
        layout.addWidget(self.list_widget)

        action_row = QHBoxLayout()
        self.remove_btn = QPushButton("移除选中项")
        self.remove_btn.clicked.connect(self._remove_selected)
        action_row.addWidget(self.remove_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setText("确定")
        cancel_btn = self.button_box.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self._refresh_list()

    def _format_op(self, op):
        src = os.path.relpath(op["src"], self.base_path)
        dst = os.path.relpath(op["dst"], self.base_path)
        return f"{src} → {dst}"

    def _refresh_list(self):
        self.list_widget.clear()
        for op in self.ops:
            self.list_widget.addItem(self._format_op(op))
        self.remove_btn.setEnabled(bool(self.ops))
        ok_btn = self.button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn:
            ok_btn.setEnabled(bool(self.ops))

    def _remove_selected(self):
        rows = sorted(
            {self.list_widget.row(item) for item in self.list_widget.selectedItems()},
            reverse=True,
        )
        for row in rows:
            if 0 <= row < len(self.ops):
                self.ops.pop(row)
        self._refresh_list()

    def selected_ops(self):
        return list(self.ops)


class ProjectTreeView(QTreeView):
    def __init__(self, move_handler=None, parent=None):
        super().__init__(parent)
        self.move_handler = move_handler
        self._saved_current_index = None
        self._drop_target_index = None
        self._drag_source_paths = []

    def _selected_source_paths(self):
        selection_model = self.selectionModel()
        model = self.model()
        if not selection_model or not model:
            return []

        paths = []
        seen = set()
        for index in selection_model.selectedRows(0):
            path = os.path.normpath(model.filePath(index))
            if path and path not in seen:
                seen.add(path)
                paths.append(path)

        if not paths:
            index = self.currentIndex()
            if index.isValid():
                path = os.path.normpath(model.filePath(index))
                if path:
                    paths.append(path)
        return paths

    def _resolve_drop_target_index(self, event):
        model = self.model()
        if not model:
            return None

        index = self.indexAt(event.position().toPoint())
        if index.isValid():
            path = os.path.normpath(model.filePath(index))
            if os.path.isdir(path):
                return index
            parent = index.parent()
            if parent.isValid():
                parent_path = os.path.normpath(model.filePath(parent))
                if parent_path and os.path.isdir(parent_path):
                    return parent

        root_index = self.rootIndex()
        if root_index.isValid():
            root_path = os.path.normpath(model.filePath(root_index))
            if root_path and os.path.isdir(root_path):
                return root_index
        return None

    def _resolve_drop_dir(self, event):
        model = self.model()
        target_index = self._resolve_drop_target_index(event)
        if model and target_index and target_index.isValid():
            target_path = os.path.normpath(model.filePath(target_index))
            if target_path and os.path.isdir(target_path):
                return target_path

        root_path = os.path.normpath(model.rootPath()) if model and model.rootPath() else None
        if root_path and os.path.isdir(root_path):
            return root_path
        return None

    def _set_drop_target_index(self, index):
        if index is not None and index.isValid() and self._saved_current_index is None:
            self._saved_current_index = self.currentIndex()
        self._drop_target_index = index if index is not None and index.isValid() else None
        if self._drop_target_index is not None:
            self.setCurrentIndex(self._drop_target_index)
            self.scrollTo(self._drop_target_index)
        elif self._saved_current_index is not None and self._saved_current_index.isValid():
            self.setCurrentIndex(self._saved_current_index)
            self._saved_current_index = None
        else:
            self._saved_current_index = None

    def _begin_drag_session(self):
        self._drag_source_paths = self._selected_source_paths()
        return bool(self._drag_source_paths)

    def _end_drag_session(self):
        self._drag_source_paths = []
        self._set_drop_target_index(None)

    def _can_handle_drag(self, event):
        return (
            event.source() is self
            and self.move_handler is not None
            and bool(self._drag_source_paths)
            and bool(self._resolve_drop_dir(event))
        )

    def startDrag(self, supportedActions):
        if not self._begin_drag_session():
            return
        try:
            super().startDrag(supportedActions)
        finally:
            self._end_drag_session()

    def dragEnterEvent(self, event):
        if self._can_handle_drag(event):
            self._set_drop_target_index(self._resolve_drop_target_index(event))
            event.acceptProposedAction()
            return
        self._set_drop_target_index(None)
        event.ignore()

    def dragMoveEvent(self, event):
        if self._can_handle_drag(event):
            self._set_drop_target_index(self._resolve_drop_target_index(event))
            event.acceptProposedAction()
            return
        self._set_drop_target_index(None)
        event.ignore()

    def dragLeaveEvent(self, event):
        self._set_drop_target_index(None)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        if not self._can_handle_drag(event):
            self._set_drop_target_index(None)
            event.ignore()
            return

        src_paths = list(self._drag_source_paths)
        dst_dir = self._resolve_drop_dir(event)
        if not src_paths or not dst_dir:
            self._set_drop_target_index(None)
            event.ignore()
            return

        self._set_drop_target_index(None)
        if self.move_handler(src_paths, dst_dir):
            event.acceptProposedAction()
            return
        event.ignore()


class MainWindow(QMainWindow):
    """
    主窗口模块
    包含应用程序的主窗口类和所有界面交互逻辑
    """

    def __init__(self):
        super().__init__()
        self.setWindowTitle("音频工程质检工具")
        self.resize(1400, 900)

        # 【关键修复】确保 log_view 和 status_lbl 在 _load_config 调用 log() 之前存在
        self.log_view = None
        self.status_lbl = None

        # 新增：问题显示模式（'all' 或 'current_folder'）
        self.error_display_mode = "all"
        self.current_folder_path = None  # 当前选中文件/文件夹的父目录
        self.last_selected_path = None
        self.mix_console_window = None
        self.suppress_trim_duration_prompt = False
        self.tree_copy_buffer = []

        # 1. 配置加载与工作区初始化
        self.root_dir = None
        self._load_config()
        self._update_window_title()

        # 状态数据
        self.error_data = {}  # {path: [errors]}
        self.unsaved_files = set()  # {path}

        self._init_ui()
        self._init_watcher()
        self._warmup_resampy()
        # 启动即进行一次全量扫描 (仅在加载到有效工作区时)
        if self.root_dir and os.path.isdir(self.root_dir):
            QTimer.singleShot(500, self.run_full_scan)

    # ================= 配置与工作区管理 =================

    def _load_config(self):
        """从配置文件加载上次打开的工作区路径和界面布局"""
        self.saved_splitter_sizes = None  # 初始化保存的分割器尺寸

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    config = json.load(f)
                    path = config.get(RECENT_WORKSPACE_KEY)
                    # 确保路径有效且存在
                    if path and os.path.isdir(path):
                        self.root_dir = path
                        # 此处调用 log() 现在不会报错
                        self.log(f"已加载上次工作区: {self.root_dir}")

                    # 加载分割器尺寸
                    self.saved_splitter_sizes = config.get(SPLITTER_SIZES_KEY)

                    # 是否不再提示“统一时长裁剪”警告
                    self.suppress_trim_duration_prompt = bool(
                        config.get(SUPPRESS_TRIM_DURATION_PROMPT_KEY, False)
                    )
            except (json.JSONDecodeError, IOError) as e:
                self.log(f"加载配置失败，使用默认目录: {e}")

    def _save_config(self):
        """保存当前工作区路径和界面布局到配置文件"""
        try:
            config = {
                RECENT_WORKSPACE_KEY: self.root_dir,
                SPLITTER_SIZES_KEY: (
                    self.splitter_v.sizes() if hasattr(self, "splitter_v") else None
                ),
                SUPPRESS_TRIM_DURATION_PROMPT_KEY: bool(
                    getattr(self, "suppress_trim_duration_prompt", False)
                ),
            }
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=4)
        except IOError as e:
            self.log(f"保存配置失败: {e}")

    def _is_workspace_top_level_dir(self, path: str) -> bool:
        if (
            not path
            or not os.path.isdir(path)
            or not self.root_dir
            or not os.path.isdir(self.root_dir)
        ):
            return False
        parent = os.path.normpath(os.path.dirname(path))
        root = os.path.normpath(self.root_dir)
        return parent == root

    def _is_song_folder(self, path: str) -> bool:
        """判断是否为工作区下的歌曲文件夹（一级子目录）。"""
        if not self._is_workspace_top_level_dir(path):
            return False
        # 至少包含一个目标子目录，避免对普通文件夹误触发
        return os.path.isdir(os.path.join(path, "分轨wav")) or os.path.isdir(
            os.path.join(path, "总轨wav")
        )

    def _confirm_trim_duration_action(self) -> bool:
        """确认统一时长裁剪操作。可通过配置选择不再提示。"""
        if getattr(self, "suppress_trim_duration_prompt", False):
            return True

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("统一时长到最短音频")
        box.setText(
            "该功能会将该歌曲的‘分轨wav’与‘总轨wav’中的 WAV 文件统一裁剪到最短音频时长（仅切掉尾部）。"
        )
        box.setInformativeText(
            "请先手动确认所有音频起始点已对齐后再使用，否则可能导致听感错位。"
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        yes_btn = box.button(QMessageBox.StandardButton.Yes)
        if yes_btn:
            yes_btn.setText("确认裁剪")
        cancel_btn = box.button(QMessageBox.StandardButton.Cancel)
        if cancel_btn:
            cancel_btn.setText("取消")

        cb = QCheckBox("不再提示")
        box.setCheckBox(cb)

        result = box.exec()

        if cb.isChecked():
            self.suppress_trim_duration_prompt = True
            self._save_config()

        return result == QMessageBox.StandardButton.Yes

    def trim_song_wavs_to_shortest(self, song_path: str) -> None:
        """将歌曲文件夹内（分轨wav/总轨wav）所有 WAV 裁剪到最短时长（仅切尾部）。"""
        if not song_path or not os.path.isdir(song_path):
            QMessageBox.warning(self, "无效路径", "无法识别所选歌曲文件夹。")
            return

        if not self._confirm_trim_duration_action():
            return

        self.editor_manager.media_player.stop()

        target_dirs = [
            os.path.join(song_path, "分轨wav"),
            os.path.join(song_path, "总轨wav"),
        ]

        wav_files: list[str] = []
        for d in target_dirs:
            if not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except Exception:
                continue
            for name in names:
                if not name.lower().endswith(".wav"):
                    continue
                fp = os.path.join(d, name)
                if os.path.isfile(fp):
                    wav_files.append(fp)

        if not wav_files:
            QMessageBox.information(self, "无 WAV 文件", "未找到可处理的 WAV 文件。")
            return

        # 1) 扫描最短时长（秒）
        metas: list[tuple[str, int, int, int]] = []  # (path, sr, channels, frames)
        min_dur_sec: float | None = None
        for fp in wav_files:
            try:
                with sf.SoundFile(fp) as f:
                    sr = int(f.samplerate)
                    ch = int(f.channels)
                    frames = int(f.frames)
                    if sr <= 0 or frames <= 0:
                        raise RuntimeError("采样率或帧数无效")
                    dur = float(frames) / float(sr)
            except Exception as e:
                QMessageBox.critical(self, "读取失败", f"无法读取 WAV: {fp}\n\n{e}")
                return

            metas.append((fp, sr, ch, frames))
            if min_dur_sec is None or dur < min_dur_sec:
                min_dur_sec = dur

        if not min_dur_sec or min_dur_sec <= 0:
            QMessageBox.warning(self, "无法处理", "未能获取有效的最短时长。")
            return

        # 2) 执行裁剪（流式写入临时文件再覆盖原文件）
        trimmed = 0
        watcher = getattr(self, "watcher", None)
        if watcher:
            try:
                watcher.blockSignals(True)
            except Exception:
                watcher = None

        try:
            for fp, sr, _ch, frames in metas:
                target_frames = int(math.floor(min_dur_sec * sr))
                target_frames = max(0, min(target_frames, frames))
                if target_frames >= frames:
                    continue

                tmp_path = fp + ".trim_tmp.wav"
                try:
                    with sf.SoundFile(fp, mode="r") as in_f:
                        with sf.SoundFile(
                            tmp_path,
                            mode="w",
                            samplerate=in_f.samplerate,
                            channels=in_f.channels,
                            format=in_f.format,
                            subtype=in_f.subtype,
                        ) as out_f:
                            remaining = target_frames
                            block = 65536
                            while remaining > 0:
                                to_read = min(block, remaining)
                                data = in_f.read(to_read, dtype="int32", always_2d=True)
                                if data.size == 0:
                                    break
                                out_f.write(data)
                                remaining -= data.shape[0]

                    os.replace(tmp_path, fp)
                    trimmed += 1
                except Exception as e:
                    try:
                        if os.path.exists(tmp_path):
                            os.remove(tmp_path)
                    except Exception:
                        pass
                    QMessageBox.critical(
                        self,
                        "裁剪失败",
                        f"处理失败：{fp}\n\n{e}",
                    )
                    return
        finally:
            if watcher:
                try:
                    watcher.blockSignals(False)
                except Exception:
                    pass

        self.log(
            f"统一时长完成：目标 {min_dur_sec:.3f}s，已裁剪 {trimmed} 个文件（仅切尾部）"
        )

        # 3) 触发增量扫描刷新问题
        self.trigger_partial_scan(song_path)
        self.refresh_model()

    def _update_window_title(self):
        """更新窗口标题，显示当前工作区名称"""
        ws_name = os.path.basename(self.root_dir) if self.root_dir else "未打开工作区"
        self.setWindowTitle(f"音频工程质检工具 - [{ws_name}]")

    def _set_root_dir(self, new_root_dir):
        """切换工作区根目录并刷新UI"""
        new_root_dir = os.path.normpath(new_root_dir)
        if not os.path.isdir(new_root_dir):
            QMessageBox.critical(self, "错误", f"路径不是有效目录: {new_root_dir}")
            return

        self.root_dir = new_root_dir
        self._update_window_title()

        # 切换到主界面
        if self.stack.currentWidget() != self.main_widget:
            self.stack.setCurrentWidget(self.main_widget)

        self.editor_manager.close_all_tabs()  # 关闭所有编辑器

        # 刷新文件树
        self.model.setRootPath(self.root_dir)
        self.tree.setRootIndex(self.model.index(self.root_dir))

        # 清空状态数据和视图
        self.error_data.clear()
        self.unsaved_files.clear()
        self.error_list.clear()

        # 【修改点】检查 log_view 是否存在
        if self.log_view:
            self.log_view.clear()

        # 重新初始化 watcher
        current_watching = list(self.watcher.directories())
        for path in current_watching:
            self.watcher.removePath(path)
        self._init_watcher()  # 重新添加路径

        # 重新全量扫描
        self.run_full_scan()
        self._save_config()  # 切换后立即保存配置
        self.log(f"工作区已切换至: {new_root_dir}")

    def open_new_workspace(self):
        """新建工作区：选择一个目录作为根目录"""
        new_dir = QFileDialog.getExistingDirectory(
            self, "选择或创建新的工作区目录", self.root_dir or ""
        )
        if new_dir:
            self._set_root_dir(new_dir)

    def open_workspace_from_folder(self):
        """从文件夹打开工作区：选择一个包含工程的目录作为根目录"""
        open_dir = QFileDialog.getExistingDirectory(
            self, "选择要打开的工作区目录", self.root_dir or ""
        )
        if open_dir:
            self._set_root_dir(open_dir)

    def add_project(self):
        """添加工程：将另一个目录下的内容拷贝到当前工作区根目录下"""
        if not self.root_dir or not os.path.isdir(self.root_dir):
            QMessageBox.warning(
                self, "警告", "当前工作区无效，请先新建或打开一个工作区。"
            )
            return

        project_dir = QFileDialog.getExistingDirectory(
            self, "选择要添加的工程目录", os.path.dirname(self.root_dir)
        )

        # 确保选择的不是当前工作区本身
        if project_dir and os.path.normpath(project_dir) != os.path.normpath(
            self.root_dir
        ):
            target_name = os.path.basename(project_dir)
            target_path = os.path.join(self.root_dir, target_name)

            if os.path.exists(target_path):
                QMessageBox.warning(
                    self, "警告", f"工作区中已存在同名目录: {target_name}"
                )
                return

            if (
                QMessageBox.question(
                    self, "确认", f"确定将 '{project_dir}' 拷贝到工作区根目录下吗？"
                )
                == QMessageBox.StandardButton.Yes
            ):
                try:
                    self.log(f"开始拷贝工程: {target_name}...")
                    # 递归拷贝整个目录
                    shutil.copytree(project_dir, target_path)
                    self.log(f"工程 '{target_name}' 添加成功。")
                    # 拷贝完成后，触发一次扫描
                    self.trigger_partial_scan(target_path)
                except Exception as e:
                    QMessageBox.critical(self, "错误", f"添加工程失败: {e}")

    # ================= UI 初始化 (更新了工具栏) =================

    def _init_ui(self):
        # --- Toolbar ---
        toolbar = QToolBar("Tools")
        self.addToolBar(toolbar)

        # --- 文件菜单动作 (纯文本) ---
        # 将 "文件" 菜单放到工具栏最左侧，所以先创建菜单和对应 QAction
        self.file_menu = QMenu(self)

        # 文件菜单项
        act_open_ws = QAction("从文件夹打开工作区...", self)
        act_open_ws.triggered.connect(self.open_workspace_from_folder)
        self.file_menu.addAction(act_open_ws)

        self.file_menu.addSeparator()

        act_reveal = QAction("在资源管理器中显示根目录", self)
        act_reveal.setToolTip(f"当前根目录: {self.root_dir or '未设置'}")
        act_reveal.triggered.connect(
            lambda: (
                os.startfile(self.root_dir)
                if os.name == "nt" and self.root_dir
                else None
            )
        )
        self.file_menu.addAction(act_reveal)

        # QAction "文件" 触发菜单显示（放到最左侧）
        action_file = QAction("文件", self)
        action_file.triggered.connect(
            lambda: self._show_menu_under_action(action_file, toolbar)
        )
        toolbar.addAction(action_file)

        # 1. 扫描动作 (纯文本)
        action_scan = QAction("扫描", self)
        action_scan.setShortcut(QKeySequence("F5"))
        action_scan.setToolTip("强制重新扫描所有歌曲文件夹 (F5)")
        action_scan.triggered.connect(self.run_full_scan)
        toolbar.addAction(action_scan)

        # 1.1 混音台开关
        self.action_mix_console = QAction("混音台", self)
        self.action_mix_console.setCheckable(True)
        self.action_mix_console.toggled.connect(self.toggle_mix_console)
        toolbar.addAction(self.action_mix_console)

        # --- 帮助菜单 ---
        self.help_menu = QMenu(self)

        # TODO: 加一个数据检查流程链接
        act_workflow = QAction("数据检查流程", self)
        act_workflow.triggered.connect(
            lambda: webbrowser.open(
                "https://wcntr9kdkawk.feishu.cn/wiki/QGILwl3gPiGrhGkIka6cIi4Qncc"
            )
        )
        self.help_menu.addAction(act_workflow)

        act_data_requirements = QAction("数据要求", self)
        act_data_requirements.triggered.connect(
            lambda: webbrowser.open(
                "https://ai.feishu.cn/docx/DbX8dJLcroIamLxRUi8cwarkn3c?from=from_copylink"
            )
        )
        self.help_menu.addAction(act_data_requirements)

        act_work_registration = QAction("分工登记表", self)
        act_work_registration.triggered.connect(
            lambda: webbrowser.open(
                "https://docs.qq.com/sheet/DSUpxbWpOVFZrb3Rx?tab=BB08J2 "
            )
        )
        self.help_menu.addAction(act_work_registration)

        # QAction "帮助" 触发菜单显示
        action_help = QAction("帮助", self)
        action_help.triggered.connect(
            lambda: self._show_menu_under_action(action_help, toolbar)
        )
        toolbar.addAction(action_help)

        # --- Main Layout ---
        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)

        # Page 1: Main UI
        self.main_widget = QWidget()
        layout = QVBoxLayout(self.main_widget)

        # Page 2: Empty State
        self.empty_widget = QWidget()
        empty_layout = QVBoxLayout(self.empty_widget)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        btn_open = QPushButton("从文件夹中打开工作区")
        btn_open.setFixedSize(200, 50)
        font = btn_open.font()
        font.setPointSize(12)
        btn_open.setFont(font)
        btn_open.clicked.connect(self.open_workspace_from_folder)
        empty_layout.addWidget(btn_open)

        self.stack.addWidget(self.main_widget)
        self.stack.addWidget(self.empty_widget)

        if self.root_dir and os.path.isdir(self.root_dir):
            self.stack.setCurrentWidget(self.main_widget)
        else:
            self.stack.setCurrentWidget(self.empty_widget)

        splitter_v = QSplitter(Qt.Orientation.Vertical)
        splitter_h = QSplitter(Qt.Orientation.Horizontal)

        # 左侧：文件树
        self.model = ProjectModel()
        self.model.setRootPath(self.root_dir or "")

        self.tree = ProjectTreeView(move_handler=self._move_paths_via_tree)
        self.tree.setModel(self.model)
        # 检查根目录是否有效
        if self.root_dir and os.path.isdir(self.root_dir):
            self.tree.setRootIndex(self.model.index(self.root_dir))
        else:
            self.tree.setRootIndex(self.model.index(""))

        self.tree.setHeaderHidden(True)
        self.tree.setColumnHidden(1, True)
        self.tree.setColumnHidden(2, True)
        self.tree.setColumnHidden(3, True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setDragEnabled(True)
        self.tree.setAcceptDrops(True)
        self.tree.setDropIndicatorShown(True)
        self.tree.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.tree.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.tree.setItemDelegateForColumn(0, WavDurationDelegate(self.tree))
        self.tree.clicked.connect(self.on_file_clicked)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.open_tree_menu)
        self.tree_copy_shortcut = QShortcut(QKeySequence.StandardKey.Copy, self.tree)
        self.tree_copy_shortcut.activated.connect(self.copy_selected_tree_items)
        self.tree_paste_shortcut = QShortcut(QKeySequence.StandardKey.Paste, self.tree)
        self.tree_paste_shortcut.activated.connect(self.paste_into_tree_target)

        # 右侧：编辑器
        self.editor_manager = EditorManager()
        self.editor_manager.file_changed.connect(self.on_file_modified)
        self.editor_manager.file_saved.connect(self.on_file_saved)

        splitter_h.addWidget(self.tree)
        splitter_h.addWidget(self.editor_manager)
        splitter_h.setStretchFactor(0, 1)
        splitter_h.setStretchFactor(1, 3)

        # 底部：日志/问题面板
        self.bottom_tabs = QTabWidget()
        self.bottom_tabs.tabBar().setExpanding(False)
        self.bottom_tabs.setStyleSheet(
            """
            QTabWidget::pane { border-top: 1px solid #e1e1e1; background: white; }
            QTabBar::tab { background: #f3f3f3; color: #555; padding: 6px 12px; border: none; }
            QTabBar::tab:selected { background: white; color: #333; font-weight: bold; border-top: 2px solid #0078d7; }
            QTabBar::tab:hover { background: #e1e1e1; }
        """
        )

        # 问题显示切换控件
        from PyQt6.QtWidgets import QHBoxLayout, QComboBox

        error_panel = QWidget()
        error_layout = QVBoxLayout(error_panel)
        error_layout.setContentsMargins(0, 0, 0, 0)
        error_layout.setSpacing(2)

        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(4, 4, 4, 4)
        switch_row.setSpacing(8)
        self.error_mode_combo = QComboBox()
        self.error_mode_combo.addItems(["全部问题", "当前文件夹问题"])
        self.error_mode_combo.setToolTip(
            "切换显示全部问题或仅显示当前文件/文件夹所在文件夹的问题"
        )
        self.error_mode_combo.currentIndexChanged.connect(self.on_error_mode_changed)
        switch_row.addWidget(self.error_mode_combo)
        switch_row.addStretch(1)
        error_layout.addLayout(switch_row)

        self.error_list = QListWidget()
        self.error_list.itemClicked.connect(self.on_error_jump)
        error_layout.addWidget(self.error_list)
        self.bottom_tabs.addTab(error_panel, "问题 (Problems)")

        # log_view 在此被创建并赋值给 self.log_view
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.bottom_tabs.addTab(self.log_view, "输出 (Output)")

        self.splitter_v = splitter_v  # 保存引用以便后续保存尺寸
        splitter_v.addWidget(splitter_h)
        splitter_v.addWidget(self.bottom_tabs)
        splitter_v.setStretchFactor(0, 7)
        splitter_v.setStretchFactor(1, 2)

        # 加载保存的分割器尺寸,如果没有则使用默认值
        if self.saved_splitter_sizes and len(self.saved_splitter_sizes) == 2:
            splitter_v.setSizes(self.saved_splitter_sizes)
            self.log(f"已恢复界面布局: {self.saved_splitter_sizes}")
        else:
            # 设置默认初始高度：上部编辑区 700px，下部日志/问题面板 200px
            splitter_v.setSizes([700, 200])

        layout.addWidget(splitter_v)

        # 状态栏
        # status_lbl 在此被创建并赋值给 self.status_lbl
        self.status_lbl = QLabel("就绪")
        self.statusBar().addWidget(self.status_lbl)

    def _show_menu_under_action(self, action, toolbar):
        """显示菜单在指定的 QAction 对应的 ToolButton 下方"""
        widget = toolbar.widgetForAction(action)

        # 根据 action 的文本确定显示哪个菜单
        menu = None
        if action.text() == "文件":
            menu = self.file_menu
        elif action.text() == "帮助":
            menu = self.help_menu

        if not menu:
            return

        if widget:
            # 找到 ToolButton 的左下角位置，并转换为全局坐标
            point = widget.rect().bottomLeft()
            global_pos = widget.mapToGlobal(point)
            menu.exec(global_pos)
        else:
            # 找不到 widget 时，在鼠标位置显示
            menu.exec(self.cursor().pos())

    # ================= 文件系统监听 =================

    def _init_watcher(self):
        """初始化文件监听器"""
        self.watcher = QFileSystemWatcher()

        if not self.root_dir or not os.path.isdir(self.root_dir):
            return

        # 始终监听根目录本身，用于捕获新的一级文件夹创建
        self.watcher.addPath(self.root_dir)

        # 初始添加现有的一级子目录（歌曲文件夹）
        sub_dirs = [
            os.path.join(self.root_dir, d)
            for d in os.listdir(self.root_dir)
            if os.path.isdir(os.path.join(self.root_dir, d))
        ]
        if sub_dirs:
            self.watcher.addPaths(sub_dirs)

        self.watcher.directoryChanged.connect(self.on_dir_changed)
        self.watcher.fileChanged.connect(self.on_file_sys_changed)

    def log(self, msg):
        """
        【关键修复】添加对 self.log_view 和 self.status_lbl 的存在性检查，
        以避免在它们被 _init_ui 创建之前调用时出错。
        """
        time_str = datetime.now().strftime("%H:%M:%S")

        if self.log_view:
            self.log_view.append(f"[{time_str}] {msg}")

        if self.status_lbl:
            self.status_lbl.setText(msg)

    # ================= 业务逻辑 =================

    def run_full_scan(self):
        """全量扫描"""
        if not self.root_dir or not os.path.isdir(self.root_dir):
            self.log("错误：当前工作区无效，无法扫描。")
            return

        self.log("开始全量扫描...")
        self.error_data.clear()

        # 切换到输出 tab
        self.bottom_tabs.setCurrentIndex(1)

        self.worker = InitialScanWorker(self.root_dir)
        self.worker.progress.connect(self.status_lbl.setText)
        self.worker.finished.connect(self.on_scan_finished)
        self.worker.start()

    def on_scan_finished(self, error_map):
        self.error_data = error_map
        self.refresh_model()

        total_problems = sum(len(v) for v in error_map.values())
        if total_problems == 0:
            self.log("扫描完成，未发现问题。")
        else:
            self.log(f"扫描完成，发现 {total_problems} 个问题。")
            self.bottom_tabs.setCurrentIndex(0)  # 切换到问题 tab

        # 重新注册 watch 路径（确保监听了所有一级目录）
        sub_dirs = [
            os.path.join(self.root_dir, d)
            for d in os.listdir(self.root_dir)
            if os.path.isdir(os.path.join(self.root_dir, d))
        ]
        current_watching = self.watcher.directories()
        for d in sub_dirs:
            if d not in current_watching:
                self.watcher.addPath(d)

    def trigger_partial_scan(self, changed_path):
        """
        增量扫描：只扫描变动文件所属的歌曲文件夹
        """
        if not os.path.isdir(self.root_dir):
            return

        # 找到所属的歌曲根目录（假设是一级子目录）
        try:
            rel_path = os.path.relpath(changed_path, self.root_dir)
        except ValueError:
            return

        parts = rel_path.split(os.sep)

        if len(parts) >= 1 and parts[0] != ".":
            song_folder_name = parts[0]
            song_path = os.path.join(self.root_dir, song_folder_name)

            if os.path.isdir(song_path):
                self.log(f"检测到变动，正在重扫: {song_folder_name}")

                # 1. 移除该文件夹旧的错误记录
                keys_to_remove = [
                    k for k in self.error_data.keys() if k.startswith(song_path)
                ]
                for k in keys_to_remove:
                    del self.error_data[k]

                # 2. 重新扫描该文件夹
                new_errors = LogicChecker.check_song_folder(song_path)
                self.error_data.update(new_errors)

                # 3. 刷新 UI
                self.refresh_model()

    def refresh_model(self):
        """刷新树状图颜色和小圆点，刷新错误列表"""
        self.model.update_status(self.error_data, self.unsaved_files)

        # 刷新问题列表，支持过滤
        self.error_list.clear()
        count = 0
        # 过滤逻辑
        if self.error_display_mode == "all" or not self.current_folder_path:
            error_items = list(self.error_data.items())
        else:
            # 只显示当前文件夹及其子文件夹下的错误
            folder = os.path.normpath(self.current_folder_path)
            folder_with_sep = folder + os.sep
            error_items = []
            for p, errs in self.error_data.items():
                p_norm = os.path.normpath(p)
                # 精确匹配：1. 路径等于当前文件夹 2. 路径在当前文件夹下
                if p_norm == folder or p_norm.startswith(folder_with_sep):
                    error_items.append((p, errs))

        for path, errs in error_items:
            folder_name = os.path.basename(os.path.dirname(path))
            name = os.path.basename(path)
            for e in errs:
                item = QListWidgetItem(f"[{folder_name}/{name}] {e}")
                item.setData(Qt.ItemDataRole.UserRole, path)
                item.setForeground(QColor("#d32f2f"))
                self.error_list.addItem(item)
                count += 1
        self.bottom_tabs.setTabText(
            0, f"问题 ({count})" if count > 0 else "问题 (Problems)"
        )

    def on_error_mode_changed(self, idx):
        """切换问题显示模式"""
        if idx == 0:
            self.error_display_mode = "all"
        else:
            self.error_display_mode = "current_folder"
        self.refresh_model()

    # ================= 事件响应 =================

    def on_file_clicked(self, index):
        path = self.model.filePath(index)
        # 记录当前选中文件/文件夹的父目录（用于过滤）
        if os.path.isfile(path):
            self.current_folder_path = os.path.dirname(path)
            self.last_selected_path = path
            self.editor_manager.open_file(path)
        elif os.path.isdir(path):
            self.current_folder_path = path
            self.last_selected_path = None
        else:
            self.current_folder_path = None
            self.last_selected_path = None
        # 切换到“当前文件夹问题”时自动刷新
        if self.error_display_mode == "current_folder":
            self.refresh_model()

    def on_file_modified(self, path):
        """编辑器内容变动"""
        if path not in self.unsaved_files:
            self.unsaved_files.add(path)
            self.refresh_model()  # 触发小圆点更新

    def on_file_saved(self, path):
        """编辑器保存"""
        # 停止播放器，避免保存后 soundfile 无法打开文件
        self.editor_manager.media_player.stop()

        if path in self.unsaved_files:
            self.unsaved_files.remove(path)
            self.refresh_model()  # 移除小圆点
            self.log(f"已保存: {os.path.basename(path)}")
            # 手动触发一次扫描逻辑
            self.trigger_partial_scan(path)

    def on_dir_changed(self, path):
        """Watcher: 目录内容变动（增删文件）"""
        self.editor_manager.media_player.stop()
        self.trigger_partial_scan(path)
        # 如果是根目录变动（加了新歌曲文件夹），需要添加 watch
        if os.path.normpath(path) == os.path.normpath(self.root_dir):
            sub_dirs = [
                os.path.join(self.root_dir, d)
                for d in os.listdir(self.root_dir)
                if os.path.isdir(os.path.join(self.root_dir, d))
            ]
            existing = self.watcher.directories()
            for d in sub_dirs:
                if d not in existing:
                    self.watcher.addPath(d)

    def on_file_sys_changed(self, path):
        """Watcher: 文件属性/内容变动"""
        self.editor_manager.media_player.stop()
        self.trigger_partial_scan(path)

    def on_error_jump(self, item):
        path = item.data(Qt.ItemDataRole.UserRole)
        if os.path.exists(path):
            idx = self.model.index(path)
            self.tree.scrollTo(idx)
            self.tree.setCurrentIndex(idx)
            self.on_file_clicked(idx)

    def open_tree_menu(self, pos):
        idx = self.tree.indexAt(pos)
        if not idx.isValid():
            return

        path = self.model.filePath(idx)
        menu = QMenu()

        act_rename = QAction("重命名", self)
        act_rename.triggered.connect(lambda: self.do_rename(path))
        menu.addAction(act_rename)

        act_del = QAction("删除", self)
        act_del.triggered.connect(lambda: self.do_delete(path))
        menu.addAction(act_del)

        menu.addSeparator()

        act_copy = QAction("复制", self)
        act_copy.setShortcut(QKeySequence.StandardKey.Copy)
        act_copy.triggered.connect(lambda: self.copy_selected_tree_items(path))
        menu.addAction(act_copy)

        act_paste = QAction("粘贴", self)
        act_paste.setShortcut(QKeySequence.StandardKey.Paste)
        act_paste.setEnabled(bool(self.tree_copy_buffer))
        act_paste.triggered.connect(lambda: self.paste_into_tree_target(path))
        menu.addAction(act_paste)

        menu.addSeparator()

        act_reveal = QAction("在资源管理器中显示", self)
        act_reveal.triggered.connect(lambda: self.reveal_in_explorer(path))
        menu.addAction(act_reveal)

        if self._is_workspace_top_level_dir(path):
            menu.addSeparator()
            act_autofix_names = QAction("自动修复本项目命名", self)
            act_autofix_names.triggered.connect(lambda: self.autofix_song_folder_names(path))
            menu.addAction(act_autofix_names)

        if self._is_song_folder(path):
            act_trim = QAction("统一时长到最短音频(裁剪尾部)", self)
            act_trim.triggered.connect(lambda: self.trim_song_wavs_to_shortest(path))
            menu.addAction(act_trim)

        if os.path.isfile(path) and os.path.splitext(path)[1].lower() == ".wav":
            menu.addSeparator()
            act_add_mix = QAction("添加到混音台", self)
            act_add_mix.triggered.connect(lambda: self.add_file_to_mix_console(path))
            menu.addAction(act_add_mix)

        if os.path.isdir(path):
            menu.addSeparator()
            act_add_mix_folder = QAction("添加文件夹到混音台", self)
            act_add_mix_folder.triggered.connect(
                lambda: self.add_folder_to_mix_console(path)
            )
            menu.addAction(act_add_mix_folder)

        menu.exec(self.tree.mapToGlobal(pos))

    def _get_tree_selected_paths(self):
        if not hasattr(self, "tree") or self.tree is None:
            return []
        return self._filter_drag_sources(self.tree._selected_source_paths())

    def copy_selected_tree_items(self, source_path=None):
        explicit_path = self._normalize_path(source_path)
        selected_paths = self._get_tree_selected_paths()
        if explicit_path and explicit_path in selected_paths:
            src_paths = selected_paths
        elif explicit_path:
            src_paths = self._filter_drag_sources([explicit_path])
        else:
            src_paths = selected_paths

        if not src_paths:
            self.log("没有可复制的项目。")
            return False

        self.tree_copy_buffer = list(src_paths)
        if len(src_paths) == 1:
            self.log(f'已复制“{os.path.basename(src_paths[0])}”，可在目标文件夹中粘贴。')
        else:
            self.log(f"已复制 {len(src_paths)} 个项目，可在目标文件夹中粘贴。")
        return True

    def _resolve_tree_paste_target_dir(self, target_path=None):
        target_norm = self._normalize_path(target_path)
        if not target_norm and hasattr(self, "tree") and self.tree is not None:
            index = self.tree.currentIndex()
            if index.isValid():
                target_norm = self._normalize_path(self.model.filePath(index))

        if target_norm:
            if os.path.isdir(target_norm):
                return target_norm
            parent_dir = os.path.dirname(target_norm)
            if parent_dir and os.path.isdir(parent_dir):
                return parent_dir

        root_norm = self._normalize_path(self.root_dir)
        if root_norm and os.path.isdir(root_norm):
            return root_norm
        return None

    def paste_into_tree_target(self, target_path=None):
        if not self.tree_copy_buffer:
            self.log("复制缓冲区为空。")
            return False

        dst_dir = self._resolve_tree_paste_target_dir(target_path)
        if not dst_dir or not os.path.isdir(dst_dir):
            QMessageBox.warning(self, "无法粘贴", "目标不是有效文件夹。")
            return False

        return self._copy_paths_via_tree(self.tree_copy_buffer, dst_dir)

    def ensure_mix_console(self):
        if self.mix_console_window is None:
            self.mix_console_window = MixConsoleWindow(self)
            self.mix_console_window.visibility_changed.connect(
                self._on_mix_console_visibility_change
            )

    def _show_mix_console_window(self):
        self.ensure_mix_console()
        self.mix_console_window.prepare_for_show(self)
        self.mix_console_window.show()
        self.mix_console_window.raise_()
        self.mix_console_window.activateWindow()

    def toggle_mix_console(self, checked):
        self.ensure_mix_console()
        if checked:
            if not self.mix_console_window.isVisible():
                self._show_mix_console_window()
        else:
            if self.mix_console_window.isVisible():
                self.mix_console_window.hide()

    def _on_mix_console_visibility_change(self, visible):
        if hasattr(self, "action_mix_console"):
            self.action_mix_console.setChecked(visible)

    def add_selected_to_mix_console(self):
        if not self.last_selected_path:
            QMessageBox.information(self, "未选择文件", "请先在文件树中选择 WAV 文件。")
            return
        self.add_file_to_mix_console(self.last_selected_path)

    def add_file_to_mix_console(self, path):
        if not path or not os.path.isfile(path):
            QMessageBox.warning(self, "无效路径", "无法识别所选文件。")
            return

        if os.path.splitext(path)[1].lower() != ".wav":
            QMessageBox.warning(self, "类型不支持", "仅支持将 WAV 文件添加到混音台。")
            return

        self._show_mix_console_window()
        self.action_mix_console.setChecked(True)
        self.mix_console_window.add_track_from_file(path)

    def add_folder_to_mix_console(self, path):
        """将指定文件夹下（不递归）的所有 WAV 文件添加到混音台。"""
        if not path or not os.path.isdir(path):
            QMessageBox.warning(self, "无效路径", "无法识别所选文件夹。")
            return

        # 列出目录下的所有文件（非递归），筛选 wav
        try:
            entries = sorted(os.listdir(path))
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法读取目录: {e}")
            return

        wav_files = []
        for name in entries:
            fp = os.path.join(path, name)
            if os.path.isfile(fp) and os.path.splitext(name)[1].lower() == ".wav":
                wav_files.append(fp)

        if not wav_files:
            QMessageBox.information(
                self, "无 WAV 文件", "该文件夹中未发现任何 WAV 文件。"
            )
            return

        # 确保混音台窗口存在并显示
        self._show_mix_console_window()
        if hasattr(self, "action_mix_console"):
            self.action_mix_console.setChecked(True)

        added = 0
        for wf in wav_files:
            try:
                self.mix_console_window.add_track_from_file(wf)
                added += 1
            except Exception:
                # add_track_from_file 内部会弹窗，因此这里只是确保循环继续
                pass

        self.log(
            f"已向混音台添加 {added} 个文件（来自文件夹: {os.path.basename(path)})"
        )

    def reveal_in_explorer(self, path):
        """在资源管理器中显示文件或文件夹"""
        if not os.path.exists(path):
            QMessageBox.warning(self, "错误", f"路径不存在: {path}")
            return

        try:
            if os.name == "nt":  # Windows
                # 使用 explorer /select 命令来选中文件或文件夹
                import subprocess

                subprocess.run(["explorer", "/select,", os.path.normpath(path)])
            else:  # macOS/Linux
                import subprocess

                if os.path.isfile(path):
                    # 打开文件所在目录
                    subprocess.run(["open", "-R", path])  # macOS
                else:
                    # 直接打开文件夹
                    subprocess.run(["open", path])  # macOS
        except Exception as e:
            QMessageBox.warning(self, "错误", f"无法打开资源管理器: {str(e)}")

    def _check_and_stop_player_if_needed(self, target_path):
        """
        检查 target_path 是否被播放器占用（是当前播放文件或其父目录），
        如果是，则停止播放以释放句柄。
        """
        player = self.editor_manager.media_player
        if not player.path:
            return

        try:
            abs_player = os.path.normpath(os.path.abspath(player.path))
            abs_target = os.path.normpath(os.path.abspath(target_path))

            # 1. 目标就是当前播放文件
            if abs_player == abs_target:
                player.stop()
                return

            # 2. 目标是当前播放文件的祖先目录
            # 加上分隔符确保不是部分匹配 (e.g. /foo/bar vs /foo/bar_suffix)
            if abs_player.startswith(abs_target + os.sep):
                player.stop()
                return
        except Exception:
            pass

    def _rename_path(self, src_path, dst_path):
        self._check_and_stop_player_if_needed(src_path)
        os.rename(src_path, dst_path)
        self.log(f"已重命名: {os.path.basename(src_path)} → {os.path.basename(dst_path)}")

    def _normalize_path(self, path):
        if not path:
            return None
        return os.path.normpath(os.path.abspath(path))

    def _is_same_or_child_path(self, path, base_path):
        path_norm = self._normalize_path(path)
        base_norm = self._normalize_path(base_path)
        if not path_norm or not base_norm:
            return False
        return path_norm == base_norm or path_norm.startswith(base_norm + os.sep)

    def _remap_path(self, path, path_map):
        path_norm = self._normalize_path(path)
        if not path_norm:
            return path

        for src, dst in sorted(path_map.items(), key=lambda item: len(item[0]), reverse=True):
            src_norm = self._normalize_path(src)
            dst_norm = self._normalize_path(dst)
            if not src_norm or not dst_norm:
                continue
            if path_norm == src_norm:
                return dst_norm
            src_prefix = src_norm + os.sep
            if path_norm.startswith(src_prefix):
                suffix = path_norm[len(src_prefix) :]
                return os.path.normpath(os.path.join(dst_norm, suffix))
        return path_norm

    def _remap_runtime_paths(self, path_map):
        if not path_map:
            return

        if self.last_selected_path:
            self.last_selected_path = self._remap_path(self.last_selected_path, path_map)
        if self.current_folder_path:
            self.current_folder_path = self._remap_path(self.current_folder_path, path_map)

        self.unsaved_files = {
            self._remap_path(path, path_map) for path in self.unsaved_files if self._remap_path(path, path_map)
        }

        new_error_data = {}
        for path, errors in self.error_data.items():
            new_error_data[self._remap_path(path, path_map)] = errors
        self.error_data = new_error_data

        self.editor_manager.remap_paths(path_map)

    def _refresh_watch_paths(self):
        if not hasattr(self, "watcher"):
            return

        desired = []
        if self.root_dir and os.path.isdir(self.root_dir):
            desired.append(self.root_dir)
            desired.extend(self._collect_song_folders())

        current = [os.path.normpath(p) for p in self.watcher.directories()]
        desired_norm = [os.path.normpath(p) for p in desired]

        for path in list(self.watcher.directories()):
            if os.path.normpath(path) not in desired_norm:
                self.watcher.removePath(path)
        for path in desired:
            if os.path.normpath(path) not in current:
                self.watcher.addPath(path)

    def _collect_affected_song_paths(self, paths):
        affected = []
        seen = set()
        for path in paths:
            path_norm = self._normalize_path(path)
            if not path_norm or not self.root_dir:
                continue
            try:
                rel_path = os.path.relpath(path_norm, self.root_dir)
            except ValueError:
                continue
            parts = rel_path.split(os.sep)
            if not parts or parts[0] == ".":
                continue
            song_path = os.path.normpath(os.path.join(self.root_dir, parts[0]))
            if song_path not in seen:
                seen.add(song_path)
                affected.append(song_path)
        return affected

    def _rescan_affected_paths(self, paths):
        for song_path in self._collect_affected_song_paths(paths):
            self.trigger_partial_scan(song_path)

    def _filter_drag_sources(self, src_paths):
        normalized = []
        seen = set()
        for path in src_paths:
            path_norm = self._normalize_path(path)
            if not path_norm or path_norm in seen:
                continue
            if self.root_dir and not self._is_same_or_child_path(path_norm, self.root_dir):
                continue
            seen.add(path_norm)
            normalized.append(path_norm)

        result = []
        for path in sorted(normalized, key=lambda item: (item.count(os.sep), item)):
            if any(self._is_same_or_child_path(path, parent) for parent in result):
                continue
            result.append(path)
        return result

    def _build_move_plan(self, src_paths, dst_dir):
        dst_dir_norm = self._normalize_path(dst_dir)
        if not dst_dir_norm or not os.path.isdir(dst_dir_norm):
            return [], [], ["目标不是有效文件夹。"]

        filtered = self._filter_drag_sources(src_paths)
        if not filtered:
            return [], [], ["没有可移动的项目。"]

        plan = []
        skip_messages = []
        hard_conflicts = []
        target_counts = {}

        for src_path in filtered:
            if not os.path.exists(src_path):
                hard_conflicts.append(f"源路径不存在: {src_path}")
                continue
            if src_path == dst_dir_norm:
                hard_conflicts.append(f"不能移动到自身: {os.path.basename(src_path)}")
                continue
            if os.path.isdir(src_path) and self._is_same_or_child_path(dst_dir_norm, src_path):
                hard_conflicts.append(f"不能将文件夹移动到其自身或子目录: {os.path.basename(src_path)}")
                continue
            if os.path.dirname(src_path) == dst_dir_norm:
                skip_messages.append(f"原地移动，已跳过: {os.path.basename(src_path)}")
                continue

            dst_path = os.path.normpath(os.path.join(dst_dir_norm, os.path.basename(src_path)))
            target_counts[dst_path] = target_counts.get(dst_path, 0) + 1
            plan.append({"src": src_path, "dst": dst_path, "overwrite": False})

        duplicate_targets = {path for path, count in target_counts.items() if count > 1}
        if duplicate_targets:
            for path in sorted(duplicate_targets):
                hard_conflicts.append(f"拖拽项存在重复目标名: {os.path.basename(path)}")

        valid_plan = []
        overwrite_candidates = []
        for item in plan:
            dst_path = item["dst"]
            if dst_path in duplicate_targets:
                continue
            if os.path.exists(dst_path):
                overwrite_candidates.append(item)
                continue
            valid_plan.append(item)

        valid_plan.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))
        overwrite_candidates.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))
        return valid_plan, overwrite_candidates, skip_messages + hard_conflicts

    def _build_copy_plan(self, src_paths, dst_dir):
        dst_dir_norm = self._normalize_path(dst_dir)
        if not dst_dir_norm or not os.path.isdir(dst_dir_norm):
            return [], [], ["目标不是有效文件夹。"]

        filtered = self._filter_drag_sources(src_paths)
        if not filtered:
            return [], [], ["没有可复制的项目。"]

        plan = []
        skip_messages = []
        hard_conflicts = []
        target_counts = {}

        for src_path in filtered:
            if not os.path.exists(src_path):
                hard_conflicts.append(f"源路径不存在: {src_path}")
                continue
            if src_path == dst_dir_norm:
                hard_conflicts.append(f"不能复制到自身: {os.path.basename(src_path)}")
                continue
            if os.path.isdir(src_path) and self._is_same_or_child_path(dst_dir_norm, src_path):
                hard_conflicts.append(f"不能将文件夹复制到其自身或子目录: {os.path.basename(src_path)}")
                continue

            dst_path = os.path.normpath(os.path.join(dst_dir_norm, os.path.basename(src_path)))
            if src_path == dst_path:
                skip_messages.append(f"原地复制，已跳过: {os.path.basename(src_path)}")
                continue
            target_counts[dst_path] = target_counts.get(dst_path, 0) + 1
            plan.append({"src": src_path, "dst": dst_path, "overwrite": False})

        duplicate_targets = {path for path, count in target_counts.items() if count > 1}
        if duplicate_targets:
            for path in sorted(duplicate_targets):
                hard_conflicts.append(f"复制项存在重复目标名: {os.path.basename(path)}")

        valid_plan = []
        overwrite_candidates = []
        for item in plan:
            dst_path = item["dst"]
            if dst_path in duplicate_targets:
                continue
            if os.path.exists(dst_path):
                overwrite_candidates.append(item)
                continue
            valid_plan.append(item)

        valid_plan.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))
        overwrite_candidates.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))
        return valid_plan, overwrite_candidates, skip_messages + hard_conflicts

    def _confirm_move_plan(self, move_plan, dst_dir, notice_count=0):
        if not move_plan:
            return False

        dst_name = os.path.basename(dst_dir.rstrip(os.sep)) or dst_dir
        if len(move_plan) == 1:
            src_name = os.path.basename(move_plan[0]["src"])
            message = f'是否确定要将“{src_name}”移到“{dst_name}”？'
        else:
            lines = [f'是否确定要将这 {len(move_plan)} 个项目移到“{dst_name}”？']
            for item in move_plan[:4]:
                lines.append(f'- {os.path.basename(item["src"])}')
            if len(move_plan) > 4:
                lines.append(f'- 其余 {len(move_plan) - 4} 个项目')
            if notice_count:
                lines.append(f'另有 {notice_count} 个项目不会移动。')
            message = "\n".join(lines)

        return (
            QMessageBox.question(self, "确认移动", message)
            == QMessageBox.StandardButton.Yes
        )

    def _confirm_overwrite_candidates(self, overwrite_candidates):
        if not overwrite_candidates:
            return True

        if len(overwrite_candidates) == 1:
            name = os.path.basename(overwrite_candidates[0]["dst"])
            message = f'目标文件夹中已存在名称为“{name}”的文件或文件夹。是否要替换它?'
        else:
            lines = [f'目标文件夹中已有 {len(overwrite_candidates)} 个同名文件或文件夹。是否全部替换?']
            for item in overwrite_candidates[:4]:
                lines.append(f'- {os.path.basename(item["dst"])}')
            if len(overwrite_candidates) > 4:
                lines.append(f'- 其余 {len(overwrite_candidates) - 4} 个项目')
            message = "\n".join(lines)

        return (
            QMessageBox.question(self, "替换文件", message)
            == QMessageBox.StandardButton.Yes
        )

    def _make_move_backup_path(self, dst_path):
        candidate = f"{dst_path}.move_backup"
        index = 1
        while os.path.exists(candidate):
            candidate = f"{dst_path}.move_backup_{index}"
            index += 1
        return candidate

    def _cleanup_move_backups(self, executed_moves):
        cleanup_errors = []
        for item in reversed(executed_moves):
            backup_path = item.get("backup_path")
            if not backup_path or not os.path.exists(backup_path):
                continue
            try:
                if os.path.isdir(backup_path):
                    shutil.rmtree(backup_path)
                else:
                    os.remove(backup_path)
            except Exception as e:
                cleanup_errors.append(f"{backup_path}: {e}")
        return cleanup_errors

    def _rollback_move_plan(self, executed_moves):
        rollback_errors = []
        for item in reversed(executed_moves):
            src_path = item["src"]
            dst_path = item["dst"]
            backup_path = item.get("backup_path")
            try:
                if os.path.exists(dst_path):
                    self._check_and_stop_player_if_needed(dst_path)
                    os.rename(dst_path, src_path)
                if backup_path and os.path.exists(backup_path):
                    self._check_and_stop_player_if_needed(backup_path)
                    os.rename(backup_path, dst_path)
            except Exception as e:
                rollback_errors.append(f"{dst_path} -> {src_path}: {e}")
        return rollback_errors

    def _execute_move_plan(self, move_plan):
        executed = []
        path_map = {}
        for item in move_plan:
            src_path = item["src"]
            dst_path = item["dst"]
            overwrite = bool(item.get("overwrite"))
            backup_path = None
            self._check_and_stop_player_if_needed(src_path)
            if overwrite and os.path.exists(dst_path):
                self._check_and_stop_player_if_needed(dst_path)
                backup_path = self._make_move_backup_path(dst_path)
                os.rename(dst_path, backup_path)
            executed_item = {**item, "backup_path": backup_path}
            executed.append(executed_item)
            os.rename(src_path, dst_path)
            path_map[src_path] = dst_path
            self.log(f"已移动: {os.path.basename(src_path)} → {dst_path}")
        return executed, path_map

    def _execute_copy_plan(self, copy_plan):
        executed = []
        for item in copy_plan:
            src_path = item["src"]
            dst_path = item["dst"]
            overwrite = bool(item.get("overwrite"))
            backup_path = None
            if overwrite and os.path.exists(dst_path):
                self._check_and_stop_player_if_needed(dst_path)
                backup_path = self._make_move_backup_path(dst_path)
                os.rename(dst_path, backup_path)
            executed_item = {**item, "backup_path": backup_path}
            executed.append(executed_item)
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dst_path)
            else:
                shutil.copy2(src_path, dst_path)
            self.log(f"已复制: {os.path.basename(src_path)} → {dst_path}")
        return executed

    def _rollback_copy_plan(self, executed_copies):
        rollback_errors = []
        for item in reversed(executed_copies):
            dst_path = item["dst"]
            backup_path = item.get("backup_path")
            try:
                if os.path.exists(dst_path):
                    self._check_and_stop_player_if_needed(dst_path)
                    if os.path.isdir(dst_path):
                        shutil.rmtree(dst_path)
                    else:
                        os.remove(dst_path)
                if backup_path and os.path.exists(backup_path):
                    self._check_and_stop_player_if_needed(backup_path)
                    os.rename(backup_path, dst_path)
            except Exception as e:
                rollback_errors.append(f"{dst_path}: {e}")
        return rollback_errors

    def _move_paths_via_tree(self, src_paths, dst_dir):
        move_plan, overwrite_candidates, notices = self._build_move_plan(src_paths, dst_dir)
        combined_plan = list(move_plan) + list(overwrite_candidates)
        combined_plan.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))

        display_notices = [msg for msg in notices if not msg.startswith("原地移动，已跳过")]

        if display_notices and not combined_plan:
            QMessageBox.warning(self, "无法移动", "\n".join(display_notices[:12]))
            return False
        if not combined_plan:
            return False
        if not self._confirm_move_plan(combined_plan, dst_dir, notice_count=len(display_notices)):
            return False
        if overwrite_candidates and not self._confirm_overwrite_candidates(overwrite_candidates):
            return False

        final_plan = list(move_plan) + [{**item, "overwrite": True} for item in overwrite_candidates]
        final_plan.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))

        before_paths = [item["src"] for item in final_plan]
        after_paths = [item["dst"] for item in final_plan]
        executed = []
        try:
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.blockSignals(True)
            executed, path_map = self._execute_move_plan(final_plan)
            cleanup_errors = self._cleanup_move_backups(executed)
            self._remap_runtime_paths(path_map)
            self._refresh_watch_paths()
            self._rescan_affected_paths(before_paths + after_paths)
            self.refresh_model()
            first_target = final_plan[0]["dst"]
            if os.path.exists(first_target):
                QTimer.singleShot(100, lambda: self._select_path_in_tree(first_target))
            if cleanup_errors:
                QMessageBox.warning(
                    self,
                    "移动完成",
                    "移动已完成，但清理覆盖备份时有残留：\n" + "\n".join(cleanup_errors[:8]),
                )
            return True
        except Exception as e:
            rollback_errors = self._rollback_move_plan(executed)
            if executed:
                rollback_map = {item["dst"]: item["src"] for item in executed}
                self._remap_runtime_paths(rollback_map)
                self._refresh_watch_paths()
                self._rescan_affected_paths(before_paths + after_paths)
                self.refresh_model()
            if rollback_errors:
                QMessageBox.critical(
                    self,
                    "移动失败",
                    f"移动失败: {e}\n\n回滚时仍有失败：\n" + "\n".join(rollback_errors[:12]),
                )
            else:
                QMessageBox.critical(self, "移动失败", f"移动失败，已恢复到移动前状态。\n\n{e}")
            return False
        finally:
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.blockSignals(False)

    def _copy_paths_via_tree(self, src_paths, dst_dir):
        copy_plan, overwrite_candidates, notices = self._build_copy_plan(src_paths, dst_dir)
        combined_plan = list(copy_plan) + list(overwrite_candidates)
        display_notices = [msg for msg in notices if not msg.startswith("原地复制，已跳过")]

        if display_notices and not combined_plan:
            QMessageBox.warning(self, "无法复制", "\n".join(display_notices[:12]))
            return False
        if not combined_plan:
            return False
        if overwrite_candidates and not self._confirm_overwrite_candidates(overwrite_candidates):
            return False

        final_plan = list(copy_plan) + [{**item, "overwrite": True} for item in overwrite_candidates]
        final_plan.sort(key=lambda item: (-item["src"].count(os.sep), item["src"]))

        affected_paths = [item["src"] for item in final_plan] + [item["dst"] for item in final_plan]
        executed = []
        try:
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.blockSignals(True)
            executed = self._execute_copy_plan(final_plan)
            cleanup_errors = self._cleanup_move_backups(executed)
            self._refresh_watch_paths()
            self._rescan_affected_paths(affected_paths)
            self.refresh_model()
            first_target = final_plan[0]["dst"]
            if os.path.exists(first_target):
                QTimer.singleShot(100, lambda: self._select_path_in_tree(first_target))
            if cleanup_errors:
                QMessageBox.warning(
                    self,
                    "复制完成",
                    "复制已完成，但清理覆盖备份时有残留：\n" + "\n".join(cleanup_errors[:8]),
                )
            return True
        except Exception as e:
            rollback_errors = self._rollback_copy_plan(executed)
            if executed:
                self._refresh_watch_paths()
                self._rescan_affected_paths(affected_paths)
                self.refresh_model()
            if rollback_errors:
                QMessageBox.critical(
                    self,
                    "复制失败",
                    f"复制失败: {e}\n\n回滚时仍有失败：\n" + "\n".join(rollback_errors[:12]),
                )
            else:
                QMessageBox.critical(self, "复制失败", f"复制失败，已恢复到复制前状态。\n\n{e}")
            return False
        finally:
            watcher = getattr(self, "watcher", None)
            if watcher:
                watcher.blockSignals(False)

    def _collect_song_folders(self):
        if not self.root_dir or not os.path.isdir(self.root_dir):
            return []
        try:
            names = sorted(os.listdir(self.root_dir))
        except Exception:
            return []
        return [
            os.path.join(self.root_dir, name)
            for name in names
            if os.path.isdir(os.path.join(self.root_dir, name))
        ]

    def _build_autofix_plan(self, song_paths):
        raw_ops = []
        for song_path in song_paths:
            raw_ops.extend(LogicChecker.propose_simple_renames(song_path))

        deduped_ops = []
        seen_pairs = set()
        for op in raw_ops:
            src = os.path.normpath(op["src"])
            dst = os.path.normpath(op["dst"])
            if src == dst:
                continue
            pair = (src, dst)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            deduped_ops.append({**op, "src": src, "dst": dst})

        src_set = {op["src"] for op in deduped_ops}
        target_counts = {}
        for op in deduped_ops:
            target_counts[op["dst"]] = target_counts.get(op["dst"], 0) + 1

        conflicts = []
        valid_ops = []
        duplicate_targets = {dst for dst, count in target_counts.items() if count > 1}

        for dst in sorted(duplicate_targets):
            conflicts.append(f"目标重名，已跳过: {dst}")

        for op in deduped_ops:
            dst = op["dst"]
            if dst in duplicate_targets:
                continue
            if os.path.exists(dst) and dst not in src_set:
                conflicts.append(f"目标已存在，已跳过: {dst}")
                continue
            valid_ops.append(op)

        valid_ops.sort(
            key=lambda op: (
                1 if op["kind"] == "song_folder" else 0,
                -op["src"].count(os.sep),
            )
        )
        return valid_ops, conflicts

    def _show_autofix_summary(self, title, executed_ops, conflicts):
        if not executed_ops and not conflicts:
            QMessageBox.information(self, title, "未发现可自动修复的简单命名问题。")
            return

        lines = []
        if executed_ops:
            lines.append(f"已修复 {len(executed_ops)} 项：")
            lines.extend(
                f"- {os.path.basename(op['src'])} → {os.path.basename(op['dst'])}"
                for op in executed_ops[:15]
            )
            if len(executed_ops) > 15:
                lines.append(f"- 其余 {len(executed_ops) - 15} 项已省略")
        if conflicts:
            if lines:
                lines.append("")
            lines.append(f"已跳过 {len(conflicts)} 项：")
            lines.extend(f"- {msg}" for msg in conflicts[:10])
            if len(conflicts) > 10:
                lines.append(f"- 其余 {len(conflicts) - 10} 项已省略")

        QMessageBox.information(self, title, "\n".join(lines))

    def _choose_autofix_ops(self, valid_ops, conflicts, *, base_path):
        if not valid_ops:
            self._show_autofix_summary("自动修复命名", [], conflicts)
            return None

        dialog = AutofixPreviewDialog(self, "自动修复命名", valid_ops, base_path)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return dialog.selected_ops()

    def _run_autofix(self, song_paths, *, full_rescan=False):
        valid_ops, conflicts = self._build_autofix_plan(song_paths)
        base_path = self.root_dir if full_rescan else song_paths[0]
        selected_ops = self._choose_autofix_ops(valid_ops, conflicts, base_path=base_path)
        if selected_ops is None:
            return

        executed_ops = []
        path_updates = {}

        for op in selected_ops:
            original_src = op["src"]
            current_src = path_updates.get(original_src, original_src)
            original_dst = op["dst"]
            current_dst = path_updates.get(original_dst, original_dst)
            if current_src == current_dst:
                continue
            try:
                self._rename_path(current_src, current_dst)
                executed_ops.append({**op, "src": current_src, "dst": current_dst})
                for old_path, mapped_path in list(path_updates.items()):
                    if mapped_path == current_src:
                        path_updates[old_path] = current_dst
                path_updates[original_src] = current_dst
            except Exception as e:
                conflicts.append(
                    f"重命名失败 {os.path.basename(current_src)} → {os.path.basename(current_dst)}: {e}"
                )

        if executed_ops:
            if full_rescan:
                self.run_full_scan()
            else:
                final_song_paths = [op["dst"] for op in executed_ops if op["kind"] == "song_folder"]
                song_path = final_song_paths[-1] if final_song_paths else path_updates.get(song_paths[-1], song_paths[-1])
                self.trigger_partial_scan(song_path)
                self.refresh_model()
                if os.path.exists(song_path):
                    QTimer.singleShot(100, lambda: self._select_path_in_tree(song_path))

        self._show_autofix_summary("自动修复命名", executed_ops, conflicts)

    def autofix_song_folder_names(self, song_path):
        if not self._is_workspace_top_level_dir(song_path):
            QMessageBox.warning(self, "无效路径", "只能修复工作区一级目录中的项目。")
            return
        self._run_autofix([song_path], full_rescan=False)

    def do_rename(self, path):
        old_name = os.path.basename(path)
        new_name, ok = QInputDialog.getText(self, "重命名", "新名称:", text=old_name)
        if ok and new_name and new_name != old_name:
            new_path = os.path.join(os.path.dirname(path), new_name)

            try:
                old_path_norm = self._normalize_path(path)
                new_path_norm = self._normalize_path(new_path)
                self._rename_path(old_path_norm, new_path_norm)
                self._remap_runtime_paths({old_path_norm: new_path_norm})
                self._refresh_watch_paths()
                self._rescan_affected_paths([old_path_norm, new_path_norm])
                self.refresh_model()

                QTimer.singleShot(
                    100,
                    lambda: self._select_path_in_tree(new_path_norm),
                )

            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

    def _select_path_in_tree(self, path):
        """辅助方法：在树中选中指定路径"""
        idx = self.model.index(path)
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
            self.tree.scrollTo(idx)

    def do_delete(self, path):
        if (
            QMessageBox.question(self, "删除", "确定永久删除?")
            == QMessageBox.StandardButton.Yes
        ):
            try:
                path_norm = self._normalize_path(path)

                should_close_editor = False
                if self.last_selected_path and self._is_same_or_child_path(
                    self.last_selected_path, path_norm
                ):
                    should_close_editor = True

                self._check_and_stop_player_if_needed(path_norm)

                if os.path.isdir(path_norm):
                    shutil.rmtree(path_norm)
                else:
                    os.remove(path_norm)

                self.unsaved_files = {
                    p for p in self.unsaved_files if not self._is_same_or_child_path(p, path_norm)
                }
                self.error_data = {
                    p: errs
                    for p, errs in self.error_data.items()
                    if not self._is_same_or_child_path(p, path_norm)
                }
                if self.current_folder_path and self._is_same_or_child_path(
                    self.current_folder_path, path_norm
                ):
                    self.current_folder_path = None
                if self.last_selected_path and self._is_same_or_child_path(
                    self.last_selected_path, path_norm
                ):
                    self.last_selected_path = None

                self._refresh_watch_paths()
                self._rescan_affected_paths([os.path.dirname(path_norm)])
                self.refresh_model()
                self.log(f"已删除: {os.path.basename(path_norm)}")

                if should_close_editor:
                    self.editor_manager.close_all_tabs()

            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

    def closeEvent(self, event):
        """应用关闭前保存配置并检查未保存文件"""
        self.editor_manager.media_player.stop()

        # 保存当前工作区路径，实现记忆功能
        self._save_config()

        if self.unsaved_files:
            reply = QMessageBox.question(
                self,
                "未保存更改",
                f"有 {len(self.unsaved_files)} 个文件未保存，确定退出吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                event.ignore()
                return
        super().closeEvent(event)

    def _warmup_resampy(self):
        """
        在主线程强制运行一次微小的重采样。
        目的：
        1. 强制 librosa 加载 resampy 模块，防止多线程懒加载导致的 'AttributeError'。
        2. 触发 Numba 的 JIT 编译，缓存机器码，避免后续混音台线程卡顿。
        """
        try:
            # 1. 创建极小的数据 (10个采样点)
            dummy_data = np.zeros(10, dtype=np.float32)

            # 2. 运行一次 kaiser_fast 重采样
            # 注意：这里不需要接收返回值，只要它不报错就行
            librosa.resample(
                dummy_data, orig_sr=44100, target_sr=48000, res_type="kaiser_fast"
            )

            # 3. 记录日志 (确保 self.log_view 已初始化，我们在 init 最后调用是安全的)
            self.log("系统就绪: 音频算法库已预热 (Resampy/Numba initialized).")

        except Exception as e:
            # 如果出错，仅记录警告，不阻断程序启动，但混音台可能会出问题
            self.log(f"警告: 音频库预热失败 - {e}")

"""
工作线程模块
包含后台扫描任务的线程类
"""
import os
from PyQt6.QtCore import QThread, pyqtSignal
from logic_checker import LogicChecker


class InitialScanWorker(QThread):
    """
    初始全量扫描线程
    """
    finished = pyqtSignal(dict)
    progress = pyqtSignal(str)

    def __init__(self, root_dir):
        super().__init__()
        self.root_dir = root_dir

    def run(self):
        full_error_map = {}
        if not os.path.isdir(self.root_dir):
            self.finished.emit({})
            return

        sub_folders = [os.path.join(self.root_dir, d) for d in os.listdir(self.root_dir) 
                       if os.path.isdir(os.path.join(self.root_dir, d))]
        
        for idx, song_path in enumerate(sub_folders):
            self.progress.emit(f"正在初始化 ({idx+1}/{len(sub_folders)}): {os.path.basename(song_path)}")
            errors = LogicChecker.check_song_folder(song_path)
            full_error_map.update(errors)
        
        self.finished.emit(full_error_map)

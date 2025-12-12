import os
import re
import soundfile as sf


class LogicChecker:
    """
    静态逻辑检查类，保持纯函数风格，便于复用。
    """

    @staticmethod
    def collect_files(folder, ext):
        try:
            return [f for f in os.listdir(folder) if f.lower().endswith(ext.lower())]
        except:
            return []

    @staticmethod
    def check_wav_format(wav_path):
        try:
            # 简单的快速检查，不读取全部数据
            with sf.SoundFile(wav_path) as f:
                errors = []
                if f.samplerate != 96000:
                    errors.append(f"采样率 {f.samplerate} != 96000")
                if f.channels != 2:
                    errors.append(f"声道 {f.channels} != 2")
                if f.subtype != "PCM_24":
                    errors.append(f"位深 {f.subtype} != PCM_24")

                if errors:
                    return f"[音频格式错误] ({'; '.join(errors)})"
            return None
        except Exception as e:
            return f"[无法读取WAV] ({e})"

    # =========================================================
    #  新增通用检查逻辑：覆盖原有的重复检查代码
    # =========================================================
    @staticmethod
    def _validate_dir_contents(
        folder_path,
        ext,
        expected_files,
        allowed_files,
        add_error_func,
        file_check_callback=None,
    ):
        """
        通用的文件夹内容检查器
        :param folder_path: 文件夹路径
        :param ext: 目标后缀 (e.g. ".wav")
        :param expected_files: 必须存在的文件名列表 (List[str])
        :param allowed_files: 允许存在的文件名列表 (List[str])
        :param add_error_func: 报错回调函数
        :param file_check_callback: 针对单个文件的额外检查函数 (如 check_wav_format)
        """
        if not os.path.exists(folder_path):
            return  # 文件夹不存在的错误在上一级检查，这里跳过

        # 1. 获取当前文件
        existing_files = LogicChecker.collect_files(folder_path, ext)

        # 2. 检查缺失 (Expected 中的必须存在)
        for exp in expected_files:
            if exp not in existing_files:
                add_error_func(folder_path, f"[缺失文件] {exp}")

        # 3. 检查多余 (既不在 Expected 也不在 Allowed 中)
        valid_set = set(expected_files) | set(allowed_files)
        all_items = os.listdir(folder_path)

        for item in all_items:
            item_path = os.path.join(folder_path, item)

            # 忽略隐藏文件
            if item.startswith("."):
                continue

            # 检查是否为多余文件夹
            if os.path.isdir(item_path):
                add_error_func(item_path, f"[多余文件夹] {item}")
                continue

            # 检查后缀和白名单
            if not item.lower().endswith(ext.lower()):
                add_error_func(item_path, f"[多余文件/格式错误] {item}")
            elif item not in valid_set:
                add_error_func(item_path, f"[多余文件] {item}")
            else:
                # 文件在白名单内，执行额外的格式检查 (如WAV格式)
                if file_check_callback:
                    fmt_err = file_check_callback(item_path)
                    if fmt_err:
                        add_error_func(item_path, fmt_err)

    @staticmethod
    def check_song_folder(song_path):
        """
        检查单个歌曲文件夹，返回 {path: [errors]}
        """
        error_map = {}

        def add_error(path, msg):
            path = os.path.normpath(os.path.abspath(path))
            if path not in error_map:
                error_map[path] = []
            error_map[path].append(msg)

        item = os.path.basename(song_path)

        # 1. 检查文件夹命名
        match = re.match(r"^(.+?)_(.+?)_(.+?)$", item)
        if not match:
            add_error(song_path, f"[命名错误] 文件夹须为 '作者_歌曲名_扒谱者'")
            song_name = item  # Fallback
        else:
            _, song_name, _ = match.groups()

        # 2. 检查一级结构 (目录)
        expected_folders = ["分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"]
        try:
            existing = os.listdir(song_path)
            for folder in expected_folders:
                if folder not in existing:
                    add_error(song_path, f"[缺失目录] 缺少 {folder}")

            # 检查一级目录下的多余项
            for f in existing:
                f_path = os.path.join(song_path, f)
                if f not in expected_folders and not f.startswith("."):
                    add_error(f_path, f"[多余项目] {f}")
        except FileNotFoundError:
            return error_map

        # =========================================================
        #  3. 分轨检查 (WAV)
        # =========================================================
        wav_root = os.path.join(song_path, "分轨wav")
        # 定义后缀规则
        track_required_suffixes = [
            "Vocal_A",
            "Vocal_B",
            "Vocal_A(干声)",
            "Vocal_B(干声)",
        ]
        track_allowed_suffixes = ["BASS", "DR", "GTR", "PNO", "OTHER", "BG", "BG(干声)"]
        # 生成完整文件名列表
        track_expected = [f"{song_name}_{s}.wav" for s in track_required_suffixes]
        track_allowed = [f"{song_name}_{s}.wav" for s in track_allowed_suffixes]

        LogicChecker._validate_dir_contents(
            folder_path=wav_root,
            ext=".wav",
            expected_files=track_expected,
            allowed_files=track_allowed,
            add_error_func=add_error,
            file_check_callback=LogicChecker.check_wav_format,
        )

        # =========================================================
        #  4. 总轨检查 (WAV)
        # =========================================================
        mix_root = os.path.join(song_path, "总轨wav")
        mix_expected = [f"{song_name}_Mix_A.wav", f"{song_name}_Mix_B.wav"]

        LogicChecker._validate_dir_contents(
            folder_path=mix_root,
            ext=".wav",
            expected_files=mix_expected,
            allowed_files=[],  # 不允许其他文件
            add_error_func=add_error,
            file_check_callback=LogicChecker.check_wav_format,
        )

        # =========================================================
        #  5. MIDI 检查 (MID)
        # =========================================================
        midi_root = os.path.join(song_path, "midi")
        midi_expected = [f"{song_name}_Vocal_midi.mid", f"{song_name}_Mix_midi.mid"]
        midi_allowed = [f"{song_name}_BG_midi.mid"]  # 允许存在的额外文件

        LogicChecker._validate_dir_contents(
            folder_path=midi_root,
            ext=".mid",
            expected_files=midi_expected,
            allowed_files=midi_allowed,
            add_error_func=add_error,
            file_check_callback=None,
        )

        # =========================================================
        #  6. CSV 检查 (CSV)
        # =========================================================
        csv_root = os.path.join(song_path, "csv")
        csv_expected = [f"{song_name}_Beat.csv", f"{song_name}_Structure.csv"]

        LogicChecker._validate_dir_contents(
            folder_path=csv_root,
            ext=".csv",
            expected_files=csv_expected,
            allowed_files=[],
            add_error_func=add_error,
            file_check_callback=None,
        )

        # 特殊检查：CSV 内容表头 (保留原有的特殊逻辑)
        beat_path = os.path.join(csv_root, f"{song_name}_Beat.csv")
        if os.path.exists(beat_path):
            try:
                with open(beat_path, "r", encoding="utf-8-sig") as f:
                    line1 = f.readline().strip()
                    if "TIME,LABEL" not in line1.upper():
                        add_error(beat_path, "[表头错误] 需包含 TIME,LABEL")
            except:
                pass

        return error_map

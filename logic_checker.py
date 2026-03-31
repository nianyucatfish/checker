import os
import re
import unicodedata
import soundfile as sf


class LogicChecker:
    """
    静态逻辑检查类，保持纯函数风格，便于复用。
    """

    SONG_FOLDER_PATTERN = re.compile(r"^(.+?)_(.+?)_(.+?)$")
    EXPECTED_FOLDERS = ["分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"]
    TRACK_REQUIRED_SUFFIXES = [
        "Vocal_A",
        "Vocal_B",
        "Vocal_A(干声)",
        "Vocal_B(干声)",
    ]
    TRACK_ALLOWED_SUFFIXES = [
        "BASS",
        "DR",
        "GTR",
        "PNO",
        "OTHER",
        "BG",
        "BG(干声)",
        "BG_A",
        "BG_A(干声)",
        "BG_B",
        "BG_B(干声)",
    ]
    MIX_EXPECTED_SUFFIXES = ["Mix_A", "Mix_B"]
    MIDI_EXPECTED_SUFFIXES = ["Vocal_midi", "Mix_midi"]
    MIDI_ALLOWED_SUFFIXES = ["BG_midi"]
    CSV_EXPECTED_SUFFIXES = ["Beat", "Structure"]

    @staticmethod
    def normalize_simple_name(name):
        if name is None:
            return None
        normalized = unicodedata.normalize("NFKC", str(name))
        normalized = normalized.replace("-", "_")
        return re.sub(r"[\s\u3000]+", "", normalized)

    @staticmethod
    def canonical_simple_name(name):
        if name is None:
            return None
        normalized = unicodedata.normalize("NFKC", str(name))
        return normalized.casefold()

    @staticmethod
    def parse_song_folder_name(folder_name):
        match = LogicChecker.SONG_FOLDER_PATTERN.match(folder_name or "")
        if not match:
            return None
        return match.groups()

    @staticmethod
    def build_expected_structure(song_name):
        return {
            "top_level_dirs": list(LogicChecker.EXPECTED_FOLDERS),
            "dir_valid_names": {
                "分轨wav": [
                    f"{song_name}_{suffix}.wav"
                    for suffix in (
                        LogicChecker.TRACK_REQUIRED_SUFFIXES
                        + LogicChecker.TRACK_ALLOWED_SUFFIXES
                    )
                ],
                "总轨wav": [
                    f"{song_name}_{suffix}.wav"
                    for suffix in LogicChecker.MIX_EXPECTED_SUFFIXES
                ],
                "midi": [
                    f"{song_name}_{suffix}.mid"
                    for suffix in (
                        LogicChecker.MIDI_EXPECTED_SUFFIXES
                        + LogicChecker.MIDI_ALLOWED_SUFFIXES
                    )
                ],
                "csv": [
                    f"{song_name}_{suffix}.csv"
                    for suffix in LogicChecker.CSV_EXPECTED_SUFFIXES
                ],
            },
        }

    @staticmethod
    def resolve_valid_name(current_name, valid_names):
        normalized_name = LogicChecker.normalize_simple_name(current_name)
        if normalized_name is None:
            return None

        if normalized_name != current_name:
            normalized_key = LogicChecker.canonical_simple_name(normalized_name)
            if normalized_key is not None:
                matches = [
                    valid_name
                    for valid_name in valid_names
                    if LogicChecker.canonical_simple_name(valid_name) == normalized_key
                ]
                unique_matches = list(dict.fromkeys(matches))
                if len(unique_matches) == 1:
                    target_name = unique_matches[0]
                    if target_name != current_name:
                        return target_name
            return normalized_name

        current_key = LogicChecker.canonical_simple_name(current_name)
        if current_key is None:
            return None
        matches = [
            valid_name
            for valid_name in valid_names
            if LogicChecker.canonical_simple_name(valid_name) == current_key
        ]
        unique_matches = list(dict.fromkeys(matches))
        if len(unique_matches) != 1:
            return None
        target_name = unique_matches[0]
        if target_name == current_name:
            return None
        return target_name

    @staticmethod
    def propose_simple_renames(song_path):
        rename_ops = []
        if not os.path.isdir(song_path):
            return rename_ops

        folder_name = os.path.basename(song_path)
        normalized_folder_name = LogicChecker.normalize_simple_name(folder_name)
        effective_folder_name = folder_name

        if normalized_folder_name and normalized_folder_name != folder_name:
            rename_ops.append(
                {
                    "src": song_path,
                    "dst": os.path.join(os.path.dirname(song_path), normalized_folder_name),
                    "kind": "song_folder",
                }
            )
            effective_folder_name = normalized_folder_name

        parsed = LogicChecker.parse_song_folder_name(effective_folder_name)
        if not parsed:
            return rename_ops

        _, song_name, _ = parsed
        structure = LogicChecker.build_expected_structure(song_name)
        valid_top_level_dirs = structure["top_level_dirs"]

        try:
            top_level_items = sorted(os.listdir(song_path))
        except Exception:
            return rename_ops

        managed_dirs = []
        for item in top_level_items:
            item_path = os.path.join(song_path, item)
            if not os.path.isdir(item_path):
                continue

            target_dir_name = item if item in valid_top_level_dirs else LogicChecker.resolve_valid_name(item, valid_top_level_dirs)
            if not target_dir_name:
                continue

            managed_dirs.append((item, target_dir_name, item_path))
            if target_dir_name != item:
                rename_ops.append(
                    {
                        "src": item_path,
                        "dst": os.path.join(song_path, target_dir_name),
                        "kind": "managed_dir",
                    }
                )

        for _, target_dir_name, actual_dir_path in managed_dirs:
            valid_file_names = structure["dir_valid_names"].get(target_dir_name)
            if not valid_file_names:
                continue

            try:
                child_items = sorted(os.listdir(actual_dir_path))
            except Exception:
                continue

            for item in child_items:
                item_path = os.path.join(actual_dir_path, item)
                if not os.path.isfile(item_path):
                    continue
                target_file_name = LogicChecker.resolve_valid_name(item, valid_file_names)
                if not target_file_name:
                    continue
                rename_ops.append(
                    {
                        "src": item_path,
                        "dst": os.path.join(actual_dir_path, target_file_name),
                        "kind": "file",
                    }
                )

        return rename_ops

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

    @staticmethod
    def get_wav_duration_seconds(wav_path):
        """读取 WAV 时长（秒）。失败返回 None。"""
        try:
            with sf.SoundFile(wav_path) as f:
                if not f.samplerate or f.samplerate <= 0:
                    return None
                return float(f.frames) / float(f.samplerate)
        except Exception:
            return None

    @staticmethod
    def check_wav_min_duration(wav_path, min_seconds=180.0):
        """检查 WAV 时长是否小于指定阈值；不足则返回错误字符串，否则返回 None。"""
        dur = LogicChecker.get_wav_duration_seconds(wav_path)
        if dur is None:
            return None
        if dur < float(min_seconds):
            return f"[音频时长过短] {dur:.3f}s < {float(min_seconds):.0f}s"
        return None

    @staticmethod
    def check_wav_min_duration_in_folder(
        folder_path, add_error_func, min_seconds=180.0
    ):
        """检查文件夹下所有 WAV 是否满足最小时长；不足则按文件路径报错。"""
        if not os.path.isdir(folder_path):
            return
        try:
            names = os.listdir(folder_path)
        except Exception:
            return
        for name in names:
            if not name.lower().endswith(".wav"):
                continue
            p = os.path.join(folder_path, name)
            if not os.path.isfile(p):
                continue
            err = LogicChecker.check_wav_min_duration(p, min_seconds=min_seconds)
            if err:
                add_error_func(p, err)

    @staticmethod
    def get_wav_duration_inconsistency_summary(
        folder_path,
        tolerance_seconds=0.02,
        label="",
        max_show=5,
    ):
        """返回时长不一致摘要字符串；一致/不可检查则返回 None。"""
        if not os.path.isdir(folder_path):
            return None

        try:
            names = sorted(os.listdir(folder_path))
        except Exception:
            return None

        wav_paths = []
        for name in names:
            if not name.lower().endswith(".wav"):
                continue
            p = os.path.join(folder_path, name)
            if os.path.isfile(p):
                wav_paths.append(p)

        if len(wav_paths) <= 1:
            return None

        ref_path = None
        ref_dur = None
        for p in wav_paths:
            d = LogicChecker.get_wav_duration_seconds(p)
            if d is not None:
                ref_path = p
                ref_dur = d
                break

        if ref_dur is None:
            return None

        mismatches = []
        for p in wav_paths:
            d = LogicChecker.get_wav_duration_seconds(p)
            if d is None:
                continue
            diff = abs(d - ref_dur)
            if diff > tolerance_seconds:
                mismatches.append((os.path.basename(p), d, diff))

        if not mismatches:
            return None

        # 展示部分不一致项，避免信息过长
        show = mismatches[: max_show if max_show and max_show > 0 else len(mismatches)]
        parts = [f"{name}({dur:.3f}s)" for name, dur, _ in show]
        more = ""
        if len(mismatches) > len(show):
            more = f" 等{len(mismatches)}个"

        prefix = f"[{label}] " if label else ""
        return (
            f"{prefix}[时长不一致] 参考 {os.path.basename(ref_path)}({ref_dur:.3f}s)，"
            f"不一致: {', '.join(parts)}{more}"
        )

    @staticmethod
    def is_wav_durations_consistent_between_folders(
        folder_a,
        folder_b,
        tolerance_seconds=0.02,
    ):
        """检查两个文件夹内所有 WAV 的时长是否一致（允许少量容差）。"""
        return LogicChecker.is_wav_durations_consistent_across_folders(
            [folder_a, folder_b],
            tolerance_seconds=tolerance_seconds,
        )

    @staticmethod
    def is_wav_durations_consistent_across_folders(
        folders,
        tolerance_seconds=0.02,
    ):
        """检查多个文件夹内所有 WAV 的时长是否一致（允许少量容差）。"""

        def _list_wavs(folder):
            if not os.path.isdir(folder):
                return []
            try:
                names = sorted(os.listdir(folder))
            except Exception:
                return []
            out = []
            for name in names:
                if not name.lower().endswith(".wav"):
                    continue
                p = os.path.join(folder, name)
                if os.path.isfile(p):
                    out.append(p)
            return out

        wavs = []
        for folder in folders:
            wavs.extend(_list_wavs(folder))

        if len(wavs) <= 1:
            return True

        ref_dur = None
        for p in wavs:
            d = LogicChecker.get_wav_duration_seconds(p)
            if d is not None:
                ref_dur = d
                break

        if ref_dur is None:
            return True

        for p in wavs:
            d = LogicChecker.get_wav_duration_seconds(p)
            if d is None:
                continue
            if abs(d - ref_dur) > tolerance_seconds:
                return False

        return True

    @staticmethod
    def check_wav_durations_consistent(
        folder_path,
        add_error_func,
        tolerance_seconds=0.02,
        label="",
    ):
        """检查指定文件夹下所有 WAV 文件时长是否一致（允许少量容差）。"""
        # 兼容保留：仍可用于直接报错，但现在默认只报一条（挂在 folder_path 上）
        summary = LogicChecker.get_wav_duration_inconsistency_summary(
            folder_path=folder_path,
            tolerance_seconds=tolerance_seconds,
            label=label,
        )
        if summary:
            add_error_func(folder_path, summary)

    # =========================================================
    #  新增通用检查逻辑：覆盖原有的重复检查代码
    # =========================================================
    @staticmethod
    def _validate_bg_track_combination(folder_path, song_name, existing_files, add_error_func):
        bg_files = {
            f"{song_name}_BG.wav",
            f"{song_name}_BG(干声).wav",
        }
        bg_split_files = {
            f"{song_name}_BG_A.wav",
            f"{song_name}_BG_A(干声).wav",
            f"{song_name}_BG_B.wav",
            f"{song_name}_BG_B(干声).wav",
        }

        existing_bg_files = set(existing_files) & (bg_files | bg_split_files)
        if not existing_bg_files:
            return

        has_standard_bg = bool(existing_bg_files & bg_files)
        has_split_bg = bool(existing_bg_files & bg_split_files)

        if has_standard_bg and has_split_bg:
            add_error_func(folder_path, "[伴唱文件错误] 伴唱只能是 BG/BG(干声) 或 BG_A/BG_A(干声)+BG_B/BG_B(干声) 两种形式之一")
            return

        if has_standard_bg:
            if existing_bg_files != bg_files:
                add_error_func(folder_path, "[伴唱文件错误] 使用 BG 形式时，必须同时包含 BG 和 BG(干声)")
            return

        if existing_bg_files != bg_split_files:
            add_error_func(folder_path, "[伴唱文件错误] 使用 BG_A/BG_B 形式时，必须同时包含 BG_A、BG_A(干声)、BG_B、BG_B(干声)")

    @staticmethod
    def _validate_dir_contents(
        folder_path,
        ext,
        expected_files,
        allowed_files,
        add_error_func,
        file_check_callback=None,
        dir_check_callback=None,
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

        if dir_check_callback:
            dir_check_callback(folder_path, existing_files, add_error_func)

        # 3. 检查多余 (既不在 Expected 也不在 Allowed 中)
        valid_set = set(expected_files) | set(allowed_files)
        all_items = os.listdir(folder_path)

        for item in all_items:
            item_path = os.path.join(folder_path, item)

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
                if f not in expected_folders:
                    add_error(f_path, f"[多余项目] {f}")
        except FileNotFoundError:
            return error_map

        # =========================================================
        #  3. 分轨检查 (WAV)
        # =========================================================
        wav_root = os.path.join(song_path, "分轨wav")
        # 定义后缀规则
        track_required_suffixes = list(LogicChecker.TRACK_REQUIRED_SUFFIXES)
        track_allowed_suffixes = list(LogicChecker.TRACK_ALLOWED_SUFFIXES)
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
            dir_check_callback=lambda folder_path, existing_files, add_error_func: LogicChecker._validate_bg_track_combination(
                folder_path=folder_path,
                song_name=song_name,
                existing_files=existing_files,
                add_error_func=add_error_func,
            ),
        )

        # 分轨 WAV 最小时长检查：少于 3 分钟报错
        LogicChecker.check_wav_min_duration_in_folder(
            folder_path=wav_root,
            add_error_func=add_error,
            min_seconds=180.0,
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

        # 总轨 WAV 最小时长检查：少于 3 分钟报错
        LogicChecker.check_wav_min_duration_in_folder(
            folder_path=mix_root,
            add_error_func=add_error,
            min_seconds=180.0,
        )

        # =========================================================
        #  4.x 分轨/总轨/混音工程原文件 WAV 时长一致性检查（每首歌仅报一条）
        # =========================================================
        mix_proj_root = os.path.join(song_path, "混音工程原文件")
        if not LogicChecker.is_wav_durations_consistent_across_folders(
            folders=[wav_root, mix_root, mix_proj_root],
            tolerance_seconds=0.02,
        ):
            add_error(song_path, "[总轨/分轨/混音工程原文件之间音频时长不一致]")

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

        # 更严格的_Beat.csv检查：严格两列，表头必须为TIME,LABEL，且全大写
        beat_path = os.path.join(csv_root, f"{song_name}_Beat.csv")
        if os.path.exists(beat_path):
            try:
                with open(beat_path, "r", encoding="utf-8-sig") as f:
                    lines = f.readlines()
                if not lines:
                    add_error(beat_path, "[内容错误] 文件为空")
                else:
                    header = lines[0].strip()
                    if header != "TIME,LABEL":
                        add_error(
                            beat_path,
                            "[表头错误] 必须严格为 TIME,LABEL（全大写，逗号分隔，不能有多余空格）",
                        )
                    for idx, line in enumerate(lines[1:], start=2):
                        line = line.strip()
                        if not line:
                            continue
                        parts = line.split(",")
                        if len(parts) != 2:
                            add_error(beat_path, f"[列数错误] 第{idx}行不是2列")
                        # 检查是否有多余的列名（如小写、空格等）
                        if idx == 2 and (
                            "time" in line.lower() or "label" in line.lower()
                        ):
                            add_error(
                                beat_path, f"[内容错误] 第{idx}行疑似表头重复或格式不符"
                            )
            except Exception as e:
                add_error(beat_path, f"[读取错误] {e}")

        # 更严格的_Structure.csv检查：只能用Intro,Verse,Chorus,Bridge,Chorus,Outro六种，且时间格式为mm:ss
        structure_path = os.path.join(csv_root, f"{song_name}_Structure.csv")
        allowed_labels = {"Intro", "Verse", "Chorus", "Bridge", "Outro"}
        if os.path.exists(structure_path):
            try:
                with open(structure_path, "r", encoding="utf-8-sig") as f:
                    lines = f.readlines()
                if not lines:
                    add_error(structure_path, "[内容错误] 文件为空")
                else:
                    header = lines[0].strip()
                    header_labels = [h.strip() for h in header.split(",") if h.strip()]
                    if not header_labels:
                        add_error(structure_path, "[表头错误] 不能为空")
                    for h in header_labels:
                        if h not in allowed_labels:
                            add_error(
                                structure_path,
                                f"[表头错误] {h} 不在允许的段落类型 {allowed_labels}",
                            )
                    # 检查内容行
                    for idx, line in enumerate(lines[1:], start=2):
                        line = line.strip()
                        if not line:
                            continue
                        parts = [p.strip() for p in line.split(",") if p.strip()]
                        if len(parts) != len(header_labels):
                            add_error(
                                structure_path,
                                f"[列数错误] 第{idx}行应为{len(header_labels)}列",
                            )
                        for t in parts:
                            # 检查时间格式 mm:ss
                            if not re.match(r"^\d{2}:\d{2}$", t):
                                add_error(
                                    structure_path,
                                    f"[时间格式错误] 第{idx}行 {t} 应为mm:ss格式",
                                )
            except Exception as e:
                add_error(structure_path, f"[读取错误] {e}")

        # =========================================================
        #  7. 混音工程原文件夹检查
        # =========================================================
        # mix_proj_root 已在 4.x 时长一致性检查处定义

        if os.path.exists(mix_proj_root):
            # 获取该目录下所有项目（包括隐藏文件）
            all_items = list(os.listdir(mix_proj_root))

            found_wavs = []
            found_csv = False

            # --- 第一步：分类扫描所有文件 ---
            # 放宽规则：仅对 wav 和 乐器音源对照表.csv 做检查；其他文件/文件夹一律视作工程文件，留给人工检查
            for item in all_items:
                item_path = os.path.join(mix_proj_root, item)
                item_lower = item.lower()

                if item_lower.endswith(".wav"):
                    # 如果是文件夹命名成了 .wav，视为错误
                    if os.path.isdir(item_path):
                        add_error(item_path, f"[多余文件夹] {item} (WAV不应是文件夹)")
                    else:
                        found_wavs.append(item)

                elif item == "乐器音源对照表.csv":
                    if os.path.isdir(item_path):
                        add_error(item_path, f"[类型错误] {item} 不应是文件夹")
                    else:
                        found_csv = True

                else:
                    # 其他文件/文件夹：不报错
                    continue

            # --- 第二步：检查 CSV 内容 (原有逻辑) ---
            instr_map_path = os.path.join(mix_proj_root, "乐器音源对照表.csv")
            if not found_csv:
                # 只有在确实没找到时才报缺失，避免与上面的多余项逻辑冲突（虽然这里通常 expected list 更好，但在混合逻辑下这样写清晰）
                # 这里如果不强求必须有 CSV，可以去掉报错；如果必须有：
                add_error(mix_proj_root, "[缺失文件] 缺少 乐器音源对照表.csv")
            else:
                # 执行 CSV 内容检查
                try:
                    with open(instr_map_path, "r", encoding="utf-8-sig") as f:
                        lines = f.readlines()
                    if not lines:
                        add_error(instr_map_path, "[内容错误] 文件为空")
                    else:
                        header = lines[0].strip()
                        if header != "乐器,音源":
                            add_error(instr_map_path, "[表头错误] 必须严格为 乐器,音源")
                        for idx, line in enumerate(lines[1:], start=2):
                            line = line.strip()
                            if not line:
                                continue
                            parts = line.split(",")
                            if len(parts) != 2:
                                add_error(
                                    instr_map_path, f"[列数错误] 第{idx}行不是2列"
                                )
                except Exception as e:
                    add_error(instr_map_path, f"[读取错误] {e}")

            # --- 第三步：检查 WAV 文件 (格式 + 命名逻辑) ---

            # 用于统计每个乐器的文件信息 { "乐器名": [ {"file": "文件名", "has_num": True/False}, ... ] }
            instrument_groups = {}

            for wav_file in found_wavs:
                wav_full_path = os.path.join(mix_proj_root, wav_file)

                # 4.1 [新增] 音频格式物理检查 (96k/24bit/Stereo)
                fmt_err = LogicChecker.check_wav_format(wav_full_path)
                if fmt_err:
                    add_error(wav_full_path, fmt_err)

                # 4.2 命名与逻辑检查
                # 检查前缀 (歌曲名_...)
                if not wav_file.startswith(song_name + "_"):
                    add_error(
                        wav_full_path,
                        f"[命名错误] 文件必须以歌曲名 '{song_name}_' 开头",
                    )
                    continue

                # 去掉后缀和前面的歌曲名，只剩下 "乐器名" 或 "乐器名_序号"，乐器名可含下划线
                content_part = os.path.splitext(wav_file)[0][len(song_name) + 1 :]
                # 只以最后一个下划线分割
                if "_" in content_part:
                    inst_base, last = content_part.rsplit("_", 1)
                    if last.isdigit():
                        inst_name = inst_base
                        has_num = True
                    else:
                        inst_name = content_part
                        has_num = False
                else:
                    inst_name = content_part
                    has_num = False

                if not inst_name:
                    add_error(
                        wav_full_path,
                        "[格式错误] 乐器名不能为空，命名应为 '歌曲名_乐器名' 或 '歌曲名_乐器名_序号'",
                    )
                    continue

                if inst_name not in instrument_groups:
                    instrument_groups[inst_name] = []
                instrument_groups[inst_name].append(
                    {"file": wav_file, "has_num": has_num}
                )

            # 4.3 校验“单轨无序号，多轨有序号”的逻辑
            for inst_name, items in instrument_groups.items():
                count = len(items)
                if count == 1:
                    # 只有一个轨道 -> 不用写序号
                    item = items[0]
                    if item["has_num"]:
                        add_error(
                            os.path.join(mix_proj_root, item["file"]),
                            f"[命名冗余] 乐器 '{inst_name}' 只有一条轨道，不应包含序号",
                        )
                elif count > 1:
                    # 有多个轨道 -> 必须写序号
                    for item in items:
                        if not item["has_num"]:
                            add_error(
                                os.path.join(mix_proj_root, item["file"]),
                                f"[命名缺失] 乐器 '{inst_name}' 有 {count} 条轨道，必须通过序号区分",
                            )

        return error_map

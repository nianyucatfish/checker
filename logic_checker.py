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
        mix_proj_root = os.path.join(song_path, "混音工程原文件")

        # 定义允许的 DAW 工程后缀
        allowed_daw_exts = (".flp", ".logicx", ".cpr")

        if os.path.exists(mix_proj_root):
            # 获取该目录下所有项目（包括隐藏文件）
            all_items = list(os.listdir(mix_proj_root))

            found_wavs = []
            found_daw_files = []
            found_csv = False

            # --- 第一步：分类扫描所有文件，检查多余项 ---
            for item in all_items:
                item_path = os.path.join(mix_proj_root, item)
                item_lower = item.lower()

                if item_lower.endswith(".wav"):
                    # 如果是文件夹命名成了 .wav，视为错误
                    if os.path.isdir(item_path):
                        add_error(item_path, f"[多余文件夹] {item} (WAV不应是文件夹)")
                    else:
                        found_wavs.append(item)

                elif item_lower.endswith(allowed_daw_exts):
                    # DAW 文件可能是文件夹 (如 .logicx)，所以不限制 isfile
                    found_daw_files.append(item)

                elif item == "乐器音源对照表.csv":
                    if os.path.isdir(item_path):
                        add_error(item_path, f"[类型错误] {item} 不应是文件夹")
                    else:
                        found_csv = True

                else:
                    # 既不是wav，不是daw，也不是指定的csv -> 多余项
                    type_str = "文件夹" if os.path.isdir(item_path) else "文件"
                    add_error(item_path, f"[多余{type_str}] {item}")

            # --- 第二步：检查 DAW 工程文件是否存在 ---
            if not found_daw_files:
                add_error(
                    mix_proj_root,
                    "[缺失文件] 缺少 DAW 工程文件 (需包含 .flp / .logicx / .cpr 任一)",
                )

            # --- 第三步：检查 CSV 内容 (原有逻辑) ---
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

            # --- 第四步：检查 WAV 文件 (格式 + 命名逻辑) ---

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

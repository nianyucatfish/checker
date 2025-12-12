"""
核心逻辑检查模块
提供静态的文件和文件夹检查功能
"""
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
            song_name = item 
        else:
            _, song_name, _ = match.groups()

        # 2. 检查一级结构
        expected = ["分轨wav", "总轨wav", "midi", "csv"]
        try:
            existing = os.listdir(song_path)
            for folder in expected:
                if folder not in existing:
                    add_error(song_path, f"[缺失目录] 缺少 {folder}")
        except FileNotFoundError:
             return error_map

        # 3. 分轨检查
        wav_root = os.path.join(song_path, "分轨wav")
        if os.path.exists(wav_root):
            files = LogicChecker.collect_files(wav_root, ".wav")
            required_suffixes = ["Vocal_A", "Vocal_B"]
            allowed_suffixes = ["BASS", "DR", "GTR", "PNO", "OTHER", "Vocal_A", "Vocal_B", "BG"]
            
            for req in required_suffixes:
                fname = f"{song_name}_{req}.wav"
                if fname not in files:
                    add_error(wav_root, f"[缺失文件] {fname}")
            
            for f in files:
                f_path = os.path.join(wav_root, f)
                if not f.startswith(song_name + "_"):
                    add_error(f_path, f"[命名错误] 未以 {song_name}_ 开头")
                    continue
                # 提取后缀
                try:
                    suffix = f[len(song_name) + 1 : -4]
                    if suffix not in allowed_suffixes:
                        add_error(f_path, f"[非法后缀] {suffix}")
                except:
                    pass
                
                fmt_err = LogicChecker.check_wav_format(f_path)
                if fmt_err: add_error(f_path, fmt_err)
        
        # 4. 总轨检查
        mix_root = os.path.join(song_path, "总轨wav")
        if os.path.exists(mix_root):
            expected_files = [f"{song_name}_Mix_A.wav", f"{song_name}_Mix_B.wav"]
            files = LogicChecker.collect_files(mix_root, ".wav")
            missing = [ef for ef in expected_files if ef not in files]
            if missing:
                 add_error(mix_root, f"[缺失文件] {', '.join(missing)}")
            for f in files:
                f_path = os.path.join(mix_root, f)
                fmt_err = LogicChecker.check_wav_format(f_path)
                if fmt_err: add_error(f_path, fmt_err)

        # 5. MIDI 检查
        midi_root = os.path.join(song_path, "midi")
        if os.path.exists(midi_root):
            files = LogicChecker.collect_files(midi_root, ".mid")
            required = [f"{song_name}_Vocal_midi.mid", f"{song_name}_Mix_midi.mid"]
            for req in required:
                if req not in files: add_error(midi_root, f"[缺失MIDI] {req}")

        # 6. CSV 检查
        csv_root = os.path.join(song_path, "csv")
        if os.path.exists(csv_root):
            files = LogicChecker.collect_files(csv_root, ".csv")
            required = [f"{song_name}_Beat.csv", f"{song_name}_Structure.csv"]
            for req in required:
                if req not in files: add_error(csv_root, f"[缺失CSV] {req}")
            
            # 简单的CSV内容检查
            beat_path = os.path.join(csv_root, f"{song_name}_Beat.csv")
            if os.path.exists(beat_path):
                try:
                    with open(beat_path, "r", encoding="utf-8-sig") as f:
                        line1 = f.readline().strip()
                        if "TIME,LABEL" not in line1.upper():
                            add_error(beat_path, "[表头错误] 需包含 TIME,LABEL")
                except: pass

        return error_map

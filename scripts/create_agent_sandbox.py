"""Create an isolated fake workspace for agent workflow testing.

This script writes only under tmp/agent_sandbox and creates:
- a fake song folder with deliberate QC issues
- a local config file for running sidecar against test LLM settings

Usage:
    python scripts/create_agent_sandbox.py
"""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parent.parent
SANDBOX = ROOT / "tmp" / "agent_sandbox"
WORKSPACE = SANDBOX / "workspace"
CONFIG = SANDBOX / "config.agent-test.toml"
SHEET_FIXTURE = SANDBOX / "sheet_fixture.csv"
SONG = WORKSPACE / "歌手_Agent测试_扒谱者"


def _write_wav(path: Path, frames: int = 96000 * 181) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.zeros((frames, 2), dtype=np.int32)
    sf.write(path, data, 96000, subtype="PCM_24")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_sheet_fixture(path: Path) -> None:
    headers = [""] * 37
    headers[0] = "歌名"
    headers[1] = "扒曲负责人"
    headers[32] = "验收负责人"
    headers[33] = "是否验收"

    row = [""] * 37
    row[0] = "Agent测试"
    row[1] = "张三"
    row[2] = "测试歌手"
    row[3] = "男"
    row[4] = "流行"
    row[5] = "平静"
    row[6] = "2020s"
    row[7] = "完整扒带"
    row[8] = "李四"
    row[9] = "张三"
    row[10] = "王五"
    row[11] = "中等"
    row[12] = "否"
    row[13] = "齐全"
    row[15] = "Neumann U87"
    row[16] = "Apollo Twin"
    row[17] = "Pro Tools"
    row[18] = "Yamaha MG10"
    row[19] = "Yamaha HS5"
    row[20] = "赵六"
    row[21] = "男"
    row[22] = "8"
    row[23] = "孙七"
    row[24] = "女"
    row[25] = "8"
    row[29] = "https://pan.baidu.com/s/test-owner"
    row[32] = "测试员"

    path.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerow(row)


def main() -> None:
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)

    for dirname in ("分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"):
        (SONG / dirname).mkdir(parents=True, exist_ok=True)

    song_name = "Agent测试"

    # Deliberate issue: extra whitespace in file name, fixable by rename.
    _write_wav(SONG / "分轨wav" / f"{song_name}_Vocal_A .wav")
    _write_wav(SONG / "分轨wav" / f"{song_name}_Vocal_A(干声).wav")
    _write_wav(SONG / "分轨wav" / f"{song_name}_Vocal_B.wav")
    _write_wav(SONG / "分轨wav" / f"{song_name}_Vocal_B(干声).wav")

    _write_wav(SONG / "总轨wav" / f"{song_name}_Mix_A.wav")
    _write_wav(SONG / "总轨wav" / f"{song_name}_Mix_B.wav")

    # Deliberate issue: midi is misplaced under csv, so audit can produce MISSING + candidate/EXTRA.
    _write_text(SONG / "csv" / f"{song_name}_Vocal_midi.mid", "fake midi placeholder\n")
    _write_text(SONG / "midi" / f"{song_name}_Mix_midi.mid", "fake midi placeholder\n")

    # Deliberate issue: CSV header/time format need simple text_edit.
    _write_text(SONG / "csv" / f"{song_name}_Beat.csv", "time,label\n0:2,Intro\n")
    _write_text(SONG / "csv" / f"{song_name}_Structure.csv", "Intro,Verse\n0:2,00:30\n")
    _write_text(SONG / "混音工程原文件" / "乐器音源对照表.csv", "乐器,音源\n")
    _write_wav(SONG / "混音工程原文件" / f"{song_name}_Vocal_A.wav")
    _write_wav(SONG / "混音工程原文件" / f"{song_name}_Vocal_B.wav")
    _write_sheet_fixture(SHEET_FIXTURE)

    _write_text(
        CONFIG,
        f"""# Agent sandbox config. Safe to regenerate; contains no real credentials.\n\n[tencent_docs]\nclient_id = \"\"\naccess_token = \"\"\nopen_id = \"\"\nspreadsheet_id = \"\"\nsheet_id = \"\"\n\n[feishu]\napp_id = \"\"\napp_secret = \"\"\n\n[llm]\nprotocol = \"openai\"\nendpoint = \"http://127.0.0.1:8765\"\napi_key = \"\"\nmodel = \"claude-opus-4-7\"\n\n[agent_sandbox]\nsheet_fixture_path = \"{SHEET_FIXTURE.as_posix()}\"\n\n[preferences]\nexecution_mode = \"auto\"\n\n[user]\nreviewer_name = \"测试员\"\n""",
    )

    print(f"sandbox={SANDBOX}")
    print(f"workspace={WORKSPACE}")
    print(f"song={SONG}")
    print(f"config={CONFIG}")
    print("Run sandbox agent with:")
    print(f"  python scripts/run_agent_sandbox.py --song-folder \"歌手_Agent测试_扒谱者\" --song-name \"Agent测试\"")


if __name__ == "__main__":
    main()

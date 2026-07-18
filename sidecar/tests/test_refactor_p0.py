"""P0 重构的回归测试:wav 时长读取去重(logic_checker)+ 原子写去重(fixers)。"""
import os

import numpy as np
import pytest
import soundfile as sf

from sidecar import fixers
from sidecar.logic_checker import LogicChecker


def test_wav_duration_matches_frames_over_rate(tmp_path):
    """get_wav_duration_seconds 现在复用 get_wav_frames_and_rate,二者必须自洽。"""
    p = str(tmp_path / "a.wav")
    sr, frames = 8000, 16000  # 2.0s
    sf.write(p, np.zeros(frames, dtype="float32"), sr)

    assert LogicChecker.get_wav_frames_and_rate(p) == (frames, sr)
    assert LogicChecker.get_wav_duration_seconds(p) == pytest.approx(frames / sr)


def test_wav_readers_fail_gracefully(tmp_path):
    bad = str(tmp_path / "nope.wav")
    assert LogicChecker.get_wav_frames_and_rate(bad) == (None, None)
    assert LogicChecker.get_wav_duration_seconds(bad) is None


def test_atomic_write_text_roundtrip(tmp_path):
    p = str(tmp_path / "x.txt")
    n = fixers._atomic_write_text(p, "héllo\nworld")
    assert open(p, encoding="utf-8").read() == "héllo\nworld"
    assert n == os.path.getsize(p)
    assert not os.path.exists(p + ".__write_tmp__")  # tmp 不残留


def test_atomic_write_text_overwrite(tmp_path):
    p = str(tmp_path / "x.txt")
    fixers._atomic_write_text(p, "old")
    fixers._atomic_write_text(p, "new")
    assert open(p, encoding="utf-8").read() == "new"


def test_atomic_write_text_missing_parent(tmp_path):
    p = str(tmp_path / "nodir" / "x.txt")
    with pytest.raises(FileNotFoundError):
        fixers._atomic_write_text(p, "x")

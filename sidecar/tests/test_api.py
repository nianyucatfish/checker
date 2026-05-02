"""sidecar.api API tests using FastAPI TestClient (httpx-backed)."""

import os
import shutil
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from sidecar.api import app


client = TestClient(app)


@pytest.fixture
def workspace():
    d = tempfile.mkdtemp(prefix="sidecar_api_test_")
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _wav(path, frames, sr=96000, channels=2, subtype="PCM_24"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = np.zeros((frames, channels), dtype=np.int32)
    sf.write(path, data, sr, subtype=subtype)


def _song(ws, name="A_x_y"):
    song = os.path.join(ws, name)
    for d in ("分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"):
        os.makedirs(os.path.join(song, d), exist_ok=True)
    return song


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["service"] == "sidecar"


def test_list_workspace_lists_songs(workspace):
    _song(workspace, "A_x_y")
    _song(workspace, "B_x_y")
    Path(os.path.join(workspace, "loose.txt")).write_text("hi", encoding="utf-8")
    r = client.get("/tools/list_workspace", params={"root": workspace})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = [os.path.basename(p) for p in body["songs"]]
    assert names == ["A_x_y", "B_x_y"]


def test_list_workspace_rejects_bad_path():
    r = client.get("/tools/list_workspace", params={"root": "/nope/missing"})
    assert r.status_code == 400


def test_check_song_returns_structured_errors(workspace):
    song = _song(workspace, "歌手_望春风_扒谱者")
    r = client.get("/tools/check_song", params={"song_path": song})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["scope"] == "song"
    assert body["total_errors"] > 0
    found_codes = set()
    for errs in body["errors"].values():
        for e in errs:
            found_codes.add(e["code"])
    assert "MISSING_FILE" in found_codes


def test_check_workspace_aggregates(workspace):
    _song(workspace, "歌手_a_扒")
    _song(workspace, "歌手_b_扒")
    r = client.get("/tools/check_workspace", params={"root": workspace})
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "workspace"
    assert body["paths_with_errors"] >= 2


def test_get_audio_metadata(workspace):
    p = os.path.join(workspace, "test.wav")
    _wav(p, frames=48000, sr=48000)
    r = client.get("/tools/get_audio_metadata", params={"path": p})
    assert r.status_code == 200
    body = r.json()
    assert body["samplerate"] == 48000
    assert body["frames"] == 48000
    assert body["duration_seconds"] == pytest.approx(1.0, abs=1e-6)


def test_get_audio_metadata_missing():
    r = client.get("/tools/get_audio_metadata", params={"path": "/nope/x.wav"})
    assert r.status_code == 400


def test_get_duration_summary_inconsistent(workspace):
    folder = os.path.join(workspace, "tracks")
    os.makedirs(folder)
    _wav(os.path.join(folder, "a.wav"), frames=48000, sr=48000)
    _wav(os.path.join(folder, "b.wav"), frames=96000, sr=48000)
    r = client.get("/tools/get_duration_summary", params={"folder": folder})
    assert r.status_code == 200
    body = r.json()
    assert body["inconsistent"] is True
    assert body["summary"] is not None


def test_get_duration_summary_consistent(workspace):
    folder = os.path.join(workspace, "tracks")
    os.makedirs(folder)
    _wav(os.path.join(folder, "a.wav"), frames=48000, sr=48000)
    _wav(os.path.join(folder, "b.wav"), frames=48000, sr=48000)
    r = client.get("/tools/get_duration_summary", params={"folder": folder})
    assert r.status_code == 200
    body = r.json()
    assert body["inconsistent"] is False


def test_list_dir_returns_immediate_children(workspace):
    song = _song(workspace, "X_y_Z")
    Path(os.path.join(song, "loose.txt")).write_text("hi", encoding="utf-8")
    r = client.get("/tools/list_dir", params={"path": song})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    names = [e["name"] for e in body["entries"]]
    # 文件夹优先 + 名称序；5 个目录在前，loose.txt 在最后
    assert names[-1] == "loose.txt"
    assert {"分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"}.issubset(set(names))
    dir_entries = [e for e in body["entries"] if e["is_dir"]]
    file_entries = [e for e in body["entries"] if not e["is_dir"]]
    assert len(dir_entries) == 5
    assert len(file_entries) == 1
    txt = file_entries[0]
    assert txt["ext"] == "txt"
    assert txt["size_bytes"] == 2


def test_list_dir_rejects_non_dir(workspace):
    p = os.path.join(workspace, "x.txt")
    Path(p).write_text("hi", encoding="utf-8")
    r = client.get("/tools/list_dir", params={"path": p})
    assert r.status_code == 400


def test_list_song_files_with_audio_meta(workspace):
    song = _song(workspace, "X_y_Z")
    _wav(os.path.join(song, "分轨wav", "X_y_Z_Vocal_A.wav"), frames=48000, sr=48000)
    Path(os.path.join(song, "csv", "X_y_Z_Beat.csv")).write_text("TIME,LABEL\n0.0,1.1\n", encoding="utf-8")
    r = client.get("/tools/list_song_files", params={"song_path": song})
    assert r.status_code == 200
    body = r.json()
    names = [f["name"] for f in body["files"]]
    assert "X_y_Z_Vocal_A.wav" in names
    assert "X_y_Z_Beat.csv" in names
    wav_entry = next(f for f in body["files"] if f["name"] == "X_y_Z_Vocal_A.wav")
    assert wav_entry["is_audio"] is True
    assert wav_entry["audio_meta"]["samplerate"] == 48000


def test_read_csv(workspace):
    p = os.path.join(workspace, "x.csv")
    Path(p).write_text("TIME,LABEL\n0.0,1.1\n0.5,1.2\n", encoding="utf-8")
    r = client.get("/tools/read_csv", params={"path": p})
    assert r.status_code == 200
    body = r.json()
    assert body["total_rows"] == 3
    assert body["rows"][0] == ["TIME", "LABEL"]


def test_read_text_truncation(workspace):
    p = os.path.join(workspace, "big.txt")
    Path(p).write_text("a" * 8192, encoding="utf-8")
    r = client.get("/tools/read_text", params={"path": p, "max_bytes": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert len(body["content"]) <= 100


def test_propose_renames_finds_simple_fixes(workspace):
    song = _song(workspace, "A_望春风_B")
    bad = os.path.join(song, "分轨wav", "A_望春风_B_Vocal_A .wav")
    Path(bad).write_bytes(b"")
    r = client.get("/tools/propose_renames", params={"song_path": song})
    assert r.status_code == 200
    body = r.json()
    assert any("Vocal_A.wav" in op["dst"] for op in body["ops"])


def test_apply_renames_executes(workspace):
    src = os.path.join(workspace, "old.txt")
    Path(src).write_text("hi", encoding="utf-8")
    dst = os.path.join(workspace, "new.txt")
    r = client.post("/tools/apply_renames", json={
        "ops": [{"src": src, "dst": dst, "kind": "file"}]
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["executed"]) == 1
    assert os.path.exists(dst)


def test_pad_song_to_longest(workspace):
    song = _song(workspace, "X_望春风_Y")
    sr = 48000
    _wav(os.path.join(song, "分轨wav", "X_望春风_Y_Vocal_A.wav"), frames=sr * 2, sr=sr)
    _wav(os.path.join(song, "总轨wav", "X_望春风_Y_Mix_A.wav"), frames=sr * 3, sr=sr)
    r = client.post("/tools/pad_song_to_longest", json={"song_path": song})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["padded"] == 1
    assert body["max_duration"] == pytest.approx(3.0, abs=1e-6)


def test_write_csv_atomic(workspace):
    p = os.path.join(workspace, "out.csv")
    rows = [["TIME", "LABEL"], ["0.0", "Intro"], ["12.5", "Chorus"]]
    r = client.post("/tools/write_csv", json={"path": p, "rows": rows})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["bytes_written"] > 0
    rr = client.get("/tools/read_csv", params={"path": p})
    assert rr.json()["rows"] == rows


def test_write_csv_overwrite(workspace):
    p = os.path.join(workspace, "out.csv")
    Path(p).write_text("OLD,DATA\n", encoding="utf-8")
    rows = [["X", "Y"], ["1", "2"]]
    r = client.post("/tools/write_csv", json={"path": p, "rows": rows})
    assert r.status_code == 200
    rr = client.get("/tools/read_csv", params={"path": p})
    assert rr.json()["rows"] == rows


def test_write_csv_rejects_bad_parent():
    r = client.post(
        "/tools/write_csv",
        json={"path": "/nope/nowhere/x.csv", "rows": [["a"]]},
    )
    assert r.status_code == 400


def test_write_text_atomic(workspace):
    p = os.path.join(workspace, "note.md")
    r = client.post(
        "/tools/write_text",
        json={"path": p, "content": "# 标题\n\n正文内容\n"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bytes_written"] > 0
    rr = client.get("/tools/read_text", params={"path": p, "max_bytes": "10000"})
    assert rr.json()["content"] == "# 标题\n\n正文内容\n"


def test_files_raw_serves_bytes(workspace):
    p = os.path.join(workspace, "note.txt")
    Path(p).write_bytes(b"hello-bytes")
    r = client.get("/files/raw", params={"path": p})
    assert r.status_code == 200
    assert r.content == b"hello-bytes"


def test_files_raw_404_when_missing():
    r = client.get("/files/raw", params={"path": "/nope/missing.bin"})
    assert r.status_code == 404


def test_get_audio_peaks(workspace):
    p = os.path.join(workspace, "x.wav")
    sr = 48000
    # 写 1 秒正弦波，便于检查 min/max 大致接近 ±1
    t = np.arange(sr) / sr
    samples = (np.sin(2 * np.pi * 440 * t) * 0.9).astype(np.float32)
    samples_2d = np.stack([samples, samples], axis=1)
    sf.write(p, samples_2d, sr, subtype="FLOAT")
    r = client.get("/tools/get_audio_peaks", params={"path": p, "columns": 100})
    assert r.status_code == 200
    body = r.json()
    assert body["columns"] == 100
    assert len(body["mins"]) == 100
    assert len(body["maxs"]) == 100
    # 振幅 0.9 的正弦应该让某些列接近 ±0.9
    assert max(body["maxs"]) > 0.7
    assert min(body["mins"]) < -0.7


def test_get_audio_peaks_404():
    r = client.get("/tools/get_audio_peaks", params={"path": "/nope/x.wav"})
    assert r.status_code == 400


def test_openapi_schema_available():
    r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json()
    paths = schema["paths"]
    assert "/health" in paths
    assert "/tools/list_workspace" in paths
    assert "/tools/check_song" in paths
    assert "/tools/apply_renames" in paths

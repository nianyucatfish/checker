"""
sidecar.api - FastAPI app exposing A/B class tools as REST endpoints.

All routes under /tools/*. A class (read-only) is GET, B class (write) is POST.
Pydantic schemas in sidecar.schemas keep contracts stable for the renderer.
"""

import csv
import mimetypes
import os
import shutil
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from sidecar import checker, fixers
from sidecar.schemas import (
    AudioMetadata,
    ApplyRenamesIn,
    ApplyRenamesOut,
    CheckErrorOut,
    CheckResult,
    CopyPathsIn,
    DeletePathsIn,
    DirEntry,
    DurationSummary,
    FileEntry,
    FileOpResultOut,
    GetAudioDurationsIn,
    GetAudioDurationsOut,
    ListDirOut,
    ListSongFilesOut,
    ListWorkspaceOut,
    MovePathsIn,
    PadResultOut,
    PadSongIn,
    ProposeRenamesOut,
    ReadCsvOut,
    ReadTextOut,
    RenameOpModel,
    RenamePathIn,
    AudioPeaksOut,
    WriteCsvIn,
    WriteResultOut,
    WriteTextIn,
)
from logic_checker import LogicChecker


app = FastAPI(title="Audio QC Sidecar", version="0.1.0")

# Local sidecar; renderer hits 127.0.0.1, CORS open is fine.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "service": "sidecar", "version": app.version}


@app.get("/tools/list_workspace", response_model=ListWorkspaceOut)
def tool_list_workspace(root: str = Query(..., description="workspace root absolute path")):
    if not os.path.isdir(root):
        raise HTTPException(status_code=400, detail=f"not a directory: {root}")
    songs = fixers.collect_song_folders(root)
    return ListWorkspaceOut(songs=songs)


@app.get("/tools/check_song", response_model=CheckResult)
def tool_check_song(song_path: str = Query(...)):
    if not os.path.isdir(song_path):
        raise HTTPException(status_code=400, detail=f"not a directory: {song_path}")
    raw = checker.check_song_folder(song_path)
    errors = {p: [CheckErrorOut(**e.to_dict()) for e in errs] for p, errs in raw.items()}
    return CheckResult(
        scope="song",
        errors=errors,
        paths_with_errors=len(errors),
        total_errors=sum(len(v) for v in errors.values()),
    )


@app.get("/tools/check_workspace", response_model=CheckResult)
def tool_check_workspace(root: str = Query(...)):
    if not os.path.isdir(root):
        raise HTTPException(status_code=400, detail=f"not a directory: {root}")
    raw = checker.check_workspace(root)
    errors = {p: [CheckErrorOut(**e.to_dict()) for e in errs] for p, errs in raw.items()}
    return CheckResult(
        scope="workspace",
        errors=errors,
        paths_with_errors=len(errors),
        total_errors=sum(len(v) for v in errors.values()),
    )


@app.get("/tools/get_audio_metadata", response_model=AudioMetadata)
def tool_get_audio_metadata(path: str = Query(...)):
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"file not found: {path}")
    import soundfile as sf
    try:
        with sf.SoundFile(path) as f:
            sr = int(f.samplerate)
            ch = int(f.channels)
            subtype = str(f.subtype)
            frames = int(f.frames)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read failed: {e}")
    return AudioMetadata(
        path=path, samplerate=sr, channels=ch, subtype=subtype, frames=frames,
        duration_seconds=frames / sr if sr else 0.0,
    )


@app.get("/tools/get_duration_summary", response_model=DurationSummary)
def tool_get_duration_summary(folder: str = Query(...), tolerance_seconds: float = 0.02):
    if not os.path.isdir(folder):
        raise HTTPException(status_code=400, detail=f"not a directory: {folder}")
    summary = LogicChecker.get_wav_duration_inconsistency_summary(
        folder, tolerance_seconds=tolerance_seconds
    )
    return DurationSummary(folder=folder, inconsistent=summary is not None, summary=summary)


@app.get("/tools/list_dir", response_model=ListDirOut)
def tool_list_dir(path: str = Query(..., description="absolute directory path")):
    """列举单层目录，供前端文件树懒加载。文件夹优先 + 名称序。"""
    if not os.path.isdir(path):
        raise HTTPException(status_code=400, detail=f"not a directory: {path}")
    try:
        names = os.listdir(path)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"list failed: {e}")
    entries: List[DirEntry] = []
    for name in names:
        full = os.path.join(path, name)
        try:
            is_dir = os.path.isdir(full)
            size = 0 if is_dir else os.path.getsize(full)
        except OSError:
            continue
        ext = "" if is_dir else os.path.splitext(name)[1].lstrip(".").lower()
        entries.append(DirEntry(
            path=full, name=name, is_dir=is_dir, size_bytes=size, ext=ext,
        ))
    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return ListDirOut(path=path, entries=entries)


@app.get("/tools/list_song_files", response_model=ListSongFilesOut)
def tool_list_song_files(song_path: str = Query(...)):
    if not os.path.isdir(song_path):
        raise HTTPException(status_code=400, detail=f"not a directory: {song_path}")
    import soundfile as sf
    files: List[FileEntry] = []
    for dirpath, _, filenames in os.walk(song_path):
        for name in sorted(filenames):
            full = os.path.join(dirpath, name)
            try:
                size = os.path.getsize(full)
            except OSError:
                continue
            rel = os.path.relpath(full, song_path)
            is_audio = name.lower().endswith((".wav", ".mp3", ".ogg", ".flac"))
            audio_meta = None
            if is_audio:
                try:
                    with sf.SoundFile(full) as f:
                        sr = int(f.samplerate)
                        frames = int(f.frames)
                        audio_meta = AudioMetadata(
                            path=full, samplerate=sr, channels=int(f.channels),
                            subtype=str(f.subtype), frames=frames,
                            duration_seconds=frames / sr if sr else 0.0,
                        )
                except Exception:
                    pass
            files.append(FileEntry(
                path=full, name=name, rel_path=rel,
                size_bytes=size, is_audio=is_audio, audio_meta=audio_meta,
            ))
    return ListSongFilesOut(song_path=song_path, files=files)


@app.get("/tools/read_csv", response_model=ReadCsvOut)
def tool_read_csv(path: str = Query(...), start: int = 0, end: int = 1000):
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"file not found: {path}")
    rows: List[List[str]] = []
    total = 0
    truncated = False
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(f)
            for i, row in enumerate(reader):
                total += 1
                if i < start:
                    continue
                if len(rows) >= (end - start):
                    truncated = True
                    continue
                rows.append(row)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read failed: {e}")
    return ReadCsvOut(path=path, rows=rows, total_rows=total, truncated=truncated)


@app.get("/tools/read_text", response_model=ReadTextOut)
def tool_read_text(path: str = Query(...), max_bytes: int = 4096):
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"file not found: {path}")
    try:
        with open(path, "rb") as f:
            raw = f.read(max_bytes + 1)
        truncated = len(raw) > max_bytes
        if truncated:
            raw = raw[:max_bytes]
        try:
            content = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            content = raw.decode("utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read failed: {e}")
    return ReadTextOut(path=path, content=content, truncated=truncated)


@app.get("/tools/propose_renames", response_model=ProposeRenamesOut)
def tool_propose_renames(song_path: str = Query(...)):
    if not os.path.isdir(song_path):
        raise HTTPException(status_code=400, detail=f"not a directory: {song_path}")
    plan = fixers.build_autofix_plan([song_path])
    return ProposeRenamesOut(
        ops=[RenameOpModel(**op.to_dict()) for op in plan.ops],
        conflicts=plan.conflicts,
    )


@app.post("/tools/apply_renames", response_model=ApplyRenamesOut)
def tool_apply_renames(body: ApplyRenamesIn):
    ops = [fixers.RenameOp(src=o.src, dst=o.dst, kind=o.kind) for o in body.ops]
    result = fixers.execute_autofix_plan(ops)
    return ApplyRenamesOut(
        executed=[RenameOpModel(src=op.src, dst=op.dst, kind=op.kind) for op in result.executed],
        errors=result.errors,
        path_updates=result.path_updates,
    )


@app.post("/tools/pad_song_to_longest", response_model=PadResultOut)
def tool_pad_song_to_longest(body: PadSongIn):
    if not os.path.isdir(body.song_path):
        raise HTTPException(status_code=400, detail=f"not a directory: {body.song_path}")
    r = fixers.pad_song_to_longest(body.song_path)
    return PadResultOut(
        ok=r.error is None, padded=r.padded,
        max_duration=r.max_duration, error=r.error,
    )


def _atomic_write(path: str, write_fn) -> int:
    """tmp 写 + os.replace 原子覆盖；失败时清理 tmp。"""
    parent = os.path.dirname(path) or "."
    if not os.path.isdir(parent):
        raise HTTPException(status_code=400, detail=f"parent not a directory: {parent}")
    tmp = path + ".__write_tmp__"
    try:
        write_fn(tmp)
        os.replace(tmp, path)
    except Exception as e:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise HTTPException(status_code=400, detail=f"write failed: {e}")
    return os.path.getsize(path)


@app.post("/tools/write_csv", response_model=WriteResultOut)
def tool_write_csv(body: WriteCsvIn):
    def _do(tmp: str):
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerows(body.rows)
    size = _atomic_write(body.path, _do)
    return WriteResultOut(path=body.path, bytes_written=size)


@app.post("/tools/write_text", response_model=WriteResultOut)
def tool_write_text(body: WriteTextIn):
    def _do(tmp: str):
        with open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(body.content)
    size = _atomic_write(body.path, _do)
    return WriteResultOut(path=body.path, bytes_written=size)


@app.get("/files/raw")
def files_raw(path: str = Query(..., description="absolute file path")):
    """流式返回任意本地文件的字节内容。FileResponse 自带 Range 支持，
    供前端 <audio>/<video> / fetch decodeAudioData 等使用。
    """
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"file not found: {path}")
    media_type, _ = mimetypes.guess_type(path)
    if not media_type:
        ext = os.path.splitext(path)[1].lower()
        if ext in (".mid", ".midi"):
            media_type = "audio/midi"
        else:
            media_type = "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=os.path.basename(path))


@app.get("/tools/get_audio_peaks", response_model=AudioPeaksOut)
def tool_get_audio_peaks(path: str = Query(...), columns: int = 4000):
    """服务端预算 min/max 波形包络。前端只画图，不再 decodeAudioData，避免 OOM。

    columns 默认 4000；过大没意义（屏幕宽度撑死 ~3000px），过小波形不准。
    """
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"file not found: {path}")
    columns = max(1, min(8000, int(columns)))

    import numpy as np
    import soundfile as sf

    try:
        with sf.SoundFile(path) as f:
            sr = int(f.samplerate)
            ch = int(f.channels)
            frames = int(f.frames)
            data = f.read(dtype="float32", always_2d=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"read failed: {e}")

    if data.size == 0 or frames <= 0:
        return AudioPeaksOut(
            path=path, samplerate=sr, channels=ch, frames=frames,
            duration_seconds=0.0, columns=0, mins=[], maxs=[],
        )

    chan0 = data[:, 0]
    samples_per_col = max(1, frames // columns)
    actual_cols = min(columns, frames // samples_per_col)
    if actual_cols <= 0:
        actual_cols = 1
    trim = samples_per_col * actual_cols
    arr = chan0[:trim].reshape(actual_cols, samples_per_col)
    mins = arr.min(axis=1).astype(float).tolist()
    maxs = arr.max(axis=1).astype(float).tolist()
    return AudioPeaksOut(
        path=path, samplerate=sr, channels=ch, frames=frames,
        duration_seconds=float(frames) / float(sr),
        columns=actual_cols, mins=mins, maxs=maxs,
    )


# ====================================================
#  音频时长批量查询(给文件树用)
# ====================================================

_AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")


@app.post("/tools/get_audio_durations", response_model=GetAudioDurationsOut)
def tool_get_audio_durations(body: GetAudioDurationsIn):
    """批量返回路径 → {frames, samplerate, duration_seconds}。读不出的、非音频的为 None。
    前端拿 frames+samplerate 做整数级别同帧比较。"""
    import soundfile as sf
    from sidecar.schemas import AudioDurationItem
    out: dict[str, AudioDurationItem | None] = {}
    for p in body.paths:
        if not p or not os.path.isfile(p):
            out[p] = None
            continue
        if not p.lower().endswith(_AUDIO_EXTS):
            out[p] = None
            continue
        try:
            with sf.SoundFile(p) as f:
                sr = int(f.samplerate)
                frames = int(f.frames)
            if sr <= 0:
                out[p] = None
            else:
                out[p] = AudioDurationItem(
                    frames=frames,
                    samplerate=sr,
                    duration_seconds=frames / sr,
                )
        except Exception:
            out[p] = None
    return GetAudioDurationsOut(durations=out)


# ====================================================
#  文件操作:rename / delete / copy / move
# ====================================================


def _ensure_isdir(p: str):
    if not os.path.isdir(p):
        raise HTTPException(status_code=400, detail=f"not a directory: {p}")


def _ensure_exists(p: str):
    if not os.path.exists(p):
        raise HTTPException(status_code=400, detail=f"path not found: {p}")


@app.post("/tools/rename_path", response_model=FileOpResultOut)
def tool_rename_path(body: RenamePathIn):
    """重命名/移动单个文件或目录。src 必须存在;dst 父目录必须存在;dst 不能已存在。"""
    src = body.src
    dst = body.dst
    _ensure_exists(src)
    parent = os.path.dirname(dst) or "."
    _ensure_isdir(parent)
    if os.path.exists(dst):
        # 同名冲突;同名只是大小写不同的 case 在某些 FS 上(Windows)需要先改一个临时名
        if os.path.normcase(os.path.normpath(src)) != os.path.normcase(os.path.normpath(dst)):
            raise HTTPException(status_code=400, detail=f"destination exists: {dst}")
    try:
        if os.path.normcase(os.path.normpath(src)) == os.path.normcase(os.path.normpath(dst)):
            # Windows 大小写改名:走两步
            tmp = src + ".__rename_tmp__"
            os.rename(src, tmp)
            os.rename(tmp, dst)
        else:
            os.rename(src, dst)
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"rename failed: {e}")
    return FileOpResultOut(executed=[dst])


@app.post("/tools/delete_paths", response_model=FileOpResultOut)
def tool_delete_paths(body: DeletePathsIn):
    """把若干路径送进系统回收站(send2trash)。失败的逐条收集到 errors 里。"""
    from send2trash import send2trash
    executed: List[str] = []
    errors: List[str] = []
    for p in body.paths:
        if not os.path.exists(p):
            errors.append(f"{p}: not found")
            continue
        try:
            send2trash(p)
            executed.append(p)
        except Exception as e:
            errors.append(f"{p}: {e}")
    return FileOpResultOut(ok=not errors, executed=executed, errors=errors)


def _resolve_copy_dst(dst_dir: str, src: str) -> str:
    """目标 = dst_dir / basename(src)。若同名已存在,追加 ` (n)` 直到不冲突。"""
    base = os.path.basename(src.rstrip("\\/"))
    candidate = os.path.join(dst_dir, base)
    if not os.path.exists(candidate):
        return candidate
    name, ext = os.path.splitext(base)
    n = 2
    while True:
        candidate = os.path.join(dst_dir, f"{name} ({n}){ext}")
        if not os.path.exists(candidate):
            return candidate
        n += 1


@app.post("/tools/copy_paths", response_model=FileOpResultOut)
def tool_copy_paths(body: CopyPathsIn):
    _ensure_isdir(body.dst_dir)
    executed: List[str] = []
    errors: List[str] = []
    for src in body.srcs:
        if not os.path.exists(src):
            errors.append(f"{src}: not found")
            continue
        try:
            dst = _resolve_copy_dst(body.dst_dir, src)
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            executed.append(dst)
        except Exception as e:
            errors.append(f"{src}: {e}")
    return FileOpResultOut(ok=not errors, executed=executed, errors=errors)


@app.post("/tools/move_paths", response_model=FileOpResultOut)
def tool_move_paths(body: MovePathsIn):
    _ensure_isdir(body.dst_dir)
    executed: List[str] = []
    errors: List[str] = []
    for src in body.srcs:
        if not os.path.exists(src):
            errors.append(f"{src}: not found")
            continue
        try:
            dst = _resolve_copy_dst(body.dst_dir, src)
            shutil.move(src, dst)
            executed.append(dst)
        except Exception as e:
            errors.append(f"{src}: {e}")
    return FileOpResultOut(ok=not errors, executed=executed, errors=errors)

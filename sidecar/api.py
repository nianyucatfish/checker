"""
sidecar.api - FastAPI app exposing A/B class tools as REST endpoints.

All routes under /tools/*. A class (read-only) is GET, B class (write) is POST.
Pydantic schemas in sidecar.schemas keep contracts stable for the renderer.
"""

import csv
import os
from typing import List

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from sidecar import checker, fixers
from sidecar.schemas import (
    AudioMetadata,
    ApplyRenamesIn,
    ApplyRenamesOut,
    CheckErrorOut,
    CheckResult,
    DurationSummary,
    FileEntry,
    ListSongFilesOut,
    ListWorkspaceOut,
    PadResultOut,
    PadSongIn,
    ProposeRenamesOut,
    ReadCsvOut,
    ReadTextOut,
    RenameOpModel,
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

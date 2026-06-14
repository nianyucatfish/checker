"""
sidecar.api - FastAPI app exposing A/B class tools as REST endpoints.

All routes under /tools/*. A class (read-only) is GET, B class (write) is POST.
Pydantic schemas in sidecar.schemas keep contracts stable for the renderer.
"""

import csv
import json
import mimetypes
import os
import shutil
import time
from typing import List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from sidecar import checker, fixers
from sidecar import workspace as _ws
from sidecar.config import LLMConfig, reload_config, write_llm_config
from sidecar import llm_providers as llm
from sidecar.schemas import (
    AudioMetadata,
    ApplyRenamesIn,
    ApplyRenamesOut,
    ChatIn,
    ChatOut,
    CheckErrorOut,
    CheckResult,
    CopyPathsIn,
    DeletePathsIn,
    DirEntry,
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
from sidecar.logic_checker import LogicChecker


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


@app.post("/chat", response_model=ChatOut)
def chat(inp: ChatIn):
    cfg = reload_config().llm
    if not cfg.endpoint or not cfg.api_key:
        raise HTTPException(status_code=400, detail="llm.endpoint/api_key 未配置")

    body = {"messages": [m.model_dump() for m in inp.messages]}
    protocol = (cfg.protocol or "openai").lower()
    if protocol == "anthropic":
        url = llm.anthropic_url(cfg.endpoint)
        payload = llm.to_anthropic_request(body, cfg.model, 1024)
        headers = llm.anthropic_headers(cfg.api_key)
    else:
        url = llm.openai_endpoint_url(cfg.endpoint)
        payload = {
            "model": cfg.model,
            "messages": body["messages"],
            "stream": False,
        }
        headers = llm.openai_headers(cfg.api_key)
    try:
        with httpx.Client(timeout=60, trust_env=False) as client:
            r = client.post(url, json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"LLM HTTP {e.response.status_code}: {e.response.text[:500]}") from e
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"LLM request failed: {e}") from e

    try:
        if protocol == "anthropic":
            content = llm.from_anthropic_response(data)["message"].get("content") or ""
        else:
            content = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as e:
        raise HTTPException(status_code=502, detail=f"LLM response schema invalid: {data}") from e
    return ChatOut(message={"role": "assistant", "content": content}, model=cfg.model)


@app.post("/agent/workspace")
def agent_workspace(body: dict):
    """Electron 切工作区时推一次,sidecar 之后用它解析相对路径。

    Body: {root: str | null}. root=null/空 → 清空(后续相对路径调用会被拒)。
    """
    root = body.get("root")
    _ws.set_workspace(root if isinstance(root, str) else None)
    return {"ok": True, "current": _ws.get_workspace()}


def _mask_key(key: str) -> str:
    if not key:
        return ""
    return ("•" * 4 + key[-4:]) if len(key) >= 4 else "•" * len(key)


def _llm_config_view() -> dict:
    c = reload_config().llm
    return {
        "protocol": c.protocol,
        "endpoint": c.endpoint,
        "model": c.model,
        "api_key": c.api_key,
        "key_set": bool(c.api_key),
        "key_masked": _mask_key(c.api_key),
    }


@app.get("/config/llm")
def get_llm_config():
    """当前 LLM 配置。api_key 仅供本地设置界面回显,由前端默认打码显示。"""
    return _llm_config_view()


@app.post("/config/llm")
def set_llm_config(body: dict):
    """写 LLM 配置到 config.toml 的 [llm] 段。

    Body: {protocol?, endpoint?, model?, api_key?}。api_key 缺失则保留现有;空字符串会清空。
    写完即生效(/agent/completion 每次 reload_config)。
    """
    current = reload_config().llm
    next_cfg = LLMConfig(
        protocol=body["protocol"].strip() if isinstance(body.get("protocol"), str) else current.protocol,
        endpoint=body["endpoint"].strip() if isinstance(body.get("endpoint"), str) else current.endpoint,
        model=body["model"].strip() if isinstance(body.get("model"), str) else current.model,
        api_key=body["api_key"].strip() if isinstance(body.get("api_key"), str) else current.api_key,
    )
    try:
        path = write_llm_config(next_cfg)
        reload_config()
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=500, detail=f"写配置失败: {e}") from e
    return {"ok": True, "config_path": str(path), **_llm_config_view()}


@app.post("/agent/completion")
def agent_completion(body: dict):
    """Proxy LLM call with tool support for the Electron agent loop.

    Body: {messages, tools, tool_choice?}. Returns the raw assistant message dict
    (`content` + optional `tool_calls`). Keeps the llm api_key in sidecar,
    Electron main never sees it.
    """
    cfg = reload_config().llm
    if not cfg.endpoint or not cfg.api_key:
        raise HTTPException(status_code=400, detail="llm.endpoint/api_key 未配置")

    # 按 protocol 分发(适配在 llm_providers.py);agent 永远只发 OpenAI 形状,差异全在这翻译。
    protocol = (cfg.protocol or "openai").lower()
    max_tokens = body.get("max_tokens", 4096)  # 显式给上限,避免代理默认值过小导致中段截断
    if protocol == "anthropic":
        payload = llm.to_anthropic_request(body, cfg.model, max_tokens)
        headers = llm.anthropic_headers(cfg.api_key)
        url = llm.anthropic_url(cfg.endpoint)
    else:
        payload = {
            "model": cfg.model,
            "messages": body.get("messages", []),
            "tools": body.get("tools", []),
            "tool_choice": body.get("tool_choice", "auto"),
            "stream": False,
            "max_tokens": max_tokens,
        }
        headers = llm.openai_headers(cfg.api_key)
        url = llm.openai_endpoint_url(cfg.endpoint)

    # GA llmcore.py:296-349 同款重试策略:
    # - 5xx/429/超时 → 指数退避 1.5 * 2^attempt(封顶 30s),honor Retry-After
    # - connect=10s / read=300s 分别给(代理慢时不会假死)
    RETRYABLE = {408, 409, 425, 429, 500, 502, 503, 504, 529}
    max_retries = 3
    last_err: str | None = None
    data: dict | None = None
    for attempt in range(max_retries + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(300.0, connect=10.0), trust_env=False) as client:
                r = client.post(url, json=payload, headers=headers)
                if r.status_code >= 400:
                    if r.status_code in RETRYABLE and attempt < max_retries:
                        ra_hdr = r.headers.get("retry-after")
                        try:
                            ra = float(ra_hdr) if ra_hdr else None
                        except ValueError:
                            ra = None
                        delay = max(0.5, ra if ra is not None else min(30.0, 1.5 * (2 ** attempt)))
                        print(f"[agent_completion] HTTP {r.status_code}, retry in {delay:.1f}s ({attempt+1}/{max_retries+1})", flush=True)
                        time.sleep(delay)
                        continue
                    raise HTTPException(status_code=502, detail=f"LLM HTTP {r.status_code}: {r.text[:500]}")
                data = r.json()
                break
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_err = f"{type(e).__name__}: {e}"
            if attempt < max_retries:
                delay = min(30.0, 1.5 * (2 ** attempt))
                print(f"[agent_completion] {type(e).__name__}, retry in {delay:.1f}s ({attempt+1}/{max_retries+1})", flush=True)
                time.sleep(delay)
                continue
            raise HTTPException(status_code=502, detail=f"LLM request failed: {last_err}") from e
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"LLM request failed: {e}") from e
    if data is None:
        raise HTTPException(status_code=502, detail=f"LLM request failed after {max_retries+1} attempts: {last_err}")

    if protocol == "anthropic":
        try:
            parsed = llm.from_anthropic_response(data)
        except (KeyError, IndexError, TypeError) as e:
            raise HTTPException(status_code=502, detail=f"LLM response schema invalid: {data}") from e
        msg = parsed["message"]
        finish_reason = parsed["finish_reason"]
        usage = parsed["usage"]
        cached = parsed["cached_tokens"]
    else:
        try:
            choice = data["choices"][0]
            msg = choice["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise HTTPException(status_code=502, detail=f"LLM response schema invalid: {data}") from e
        finish_reason = choice.get("finish_reason", "")
        usage = data.get("usage", {})
        # 抽取 cache 命中数:覆盖 3 种主流 schema
        cached = (
            usage.get("prompt_cache_hit_tokens")
            or (usage.get("prompt_tokens_details") or {}).get("cached_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        )
    print(
        f"[agent_completion] finish={finish_reason} cached={cached} usage={usage}",
        flush=True,
    )
    # 把代理原始 JSON 落盘 tmp/agent_upstream.jsonl,排查 native token 泄漏 / tool_calls
    # 解析失败这类代理 bug —— 看的是 sidecar 拿到的完全未处理的 upstream 数据。
    try:
        import json as _json
        import time as _time
        from pathlib import Path as _Path
        dump_path = _Path(__file__).resolve().parent.parent / "tmp" / "agent_upstream.jsonl"
        dump_path.parent.mkdir(parents=True, exist_ok=True)
        with dump_path.open("a", encoding="utf-8") as f:
            f.write(_json.dumps({"ts": _time.time(), "finish": finish_reason, "data": data}, ensure_ascii=False) + "\n")
    except Exception as _e:
        print(f"[agent_completion] dump failed: {_e}", flush=True)
    return {
        "message": msg,
        "model": cfg.model,
        "usage": usage,
        "cached_tokens": cached,
        "finish_reason": finish_reason,
    }


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


@app.post("/tools/write_midi", response_model=WriteResultOut)
def tool_write_midi(body: dict):
    """把 base64 编码的 MIDI 字节写到指定 path。MidiViewer 保存按钮走这条。
    Body: {path: str, base64: str}
    """
    import base64 as _b64
    path = body.get("path")
    b64 = body.get("base64")
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=400, detail="path required")
    if not isinstance(b64, str):
        raise HTTPException(status_code=400, detail="base64 required")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".mid", ".midi"):
        raise HTTPException(status_code=400, detail=f"only .mid/.midi allowed, got {ext}")
    try:
        data = _b64.b64decode(b64, validate=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid base64: {e}")
    # 调试副本:每次写 midi 也存一份到 tmp/last_midi_save.mid,方便事后对比
    try:
        from pathlib import Path as _Path
        dbg_dir = _Path(__file__).resolve().parent.parent / "tmp"
        dbg_dir.mkdir(parents=True, exist_ok=True)
        (dbg_dir / "last_midi_save.mid").write_bytes(data)
        (dbg_dir / "last_midi_save.meta.txt").write_text(
            f"target_path={path}\nsize={len(data)}\nfirst_30_hex={data[:30].hex()}\n",
            encoding="utf-8",
        )
    except Exception as _e:
        print(f"[write_midi] dbg dump failed: {_e}", flush=True)
    def _do(tmp: str):
        with open(tmp, "wb") as f:
            f.write(data)
    size = _atomic_write(path, _do)
    return WriteResultOut(path=path, bytes_written=size)


@app.post("/tools/shift_midi_per_track_save")
def tool_shift_midi_per_track_save(body: dict):
    """读原 MIDI bytes → 按 magenta instrument id 做 tick 级平移 → 原子写回 path。

    Body: {path: str, shifts: [{instrument: int, shift: float seconds}]}

    替代 webview 端 mm.sequenceProtoToMidi(magenta-music 1.x 在多声部 / 同 channel
    overlap notes 时重写会出错,见 2e05704)。Python 用 mido 在 tick 层平移,
    保 raw event 顺序 / velocity / tempo 不变。
    """
    from sidecar import midi_shifter
    path = body.get("path")
    shifts_list = body.get("shifts") or []
    if not isinstance(path, str) or not path:
        raise HTTPException(status_code=400, detail="path required")
    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"midi file not found: {path}")
    ext = os.path.splitext(path)[1].lower()
    if ext not in (".mid", ".midi"):
        raise HTTPException(status_code=400, detail=f"only .mid/.midi allowed, got {ext}")
    # shifts: list[{instrument, shift}] → dict[int, float]
    shifts: dict[int, float] = {}
    for item in shifts_list:
        if not isinstance(item, dict): continue
        try:
            shifts[int(item["instrument"])] = float(item.get("shift", 0))
        except (KeyError, TypeError, ValueError):
            continue
    try:
        with open(path, "rb") as f:
            orig_bytes = f.read()
        new_bytes = midi_shifter.shift_midi_bytes_per_magenta_instrument(orig_bytes, shifts)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"midi shift failed: {e}")
    # 调试副本
    try:
        from pathlib import Path as _Path
        dbg_dir = _Path(__file__).resolve().parent.parent / "tmp"
        dbg_dir.mkdir(parents=True, exist_ok=True)
        (dbg_dir / "last_midi_save.mid").write_bytes(new_bytes)
        (dbg_dir / "last_midi_save.meta.txt").write_text(
            f"target_path={path}\nsize={len(new_bytes)}\nshifts={shifts}\nfirst_30_hex={new_bytes[:30].hex()}\n",
            encoding="utf-8",
        )
    except Exception as _e:
        print(f"[shift_midi] dbg dump failed: {_e}", flush=True)
    def _do(tmp: str):
        with open(tmp, "wb") as f:
            f.write(new_bytes)
    size = _atomic_write(path, _do)
    return WriteResultOut(path=path, bytes_written=size)


@app.get("/files/raw")
def files_raw(path: str = Query(..., description="absolute file path")):
    """流式返回任意本地文件的字节内容。FileResponse 自带 Range 支持，
    供前端 <audio>/<video> / fetch decodeAudioData 等使用。

    返回 Cache-Control: no-store —— Chromium webview 默认会缓存普通 GET 响应,
    保存 midi/wav 后重新打开看到旧字节就是这个 cache 在搞鬼,显式禁用。
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
    resp = FileResponse(path, media_type=media_type, filename=os.path.basename(path))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    resp.headers["Pragma"] = "no-cache"
    return resp


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


def _rename_with_retry(src: str, dst: str, retries: int = 4, delay: float = 0.08) -> None:
    """os.rename + Windows 文件锁瞬态重试。

    Win 上播放器/HTTP stream 释放 fd 是异步的,前端 audio:release 后服务端可能还
    handle 着;直接 rename 会 PermissionError。指数退避 0.08/0.16/0.32/0.64s 共 ~1.2s
    给浏览器和 sidecar 自己的 FileResponse 完成清理,99% 瞬态锁都能熬过。
    """
    import time as _time
    last: Exception | None = None
    for i in range(retries + 1):
        try:
            os.rename(src, dst)
            return
        except (PermissionError, OSError) as e:
            last = e
            if i < retries:
                _time.sleep(delay * (2 ** i))
            else:
                raise


@app.post("/tools/rename_path", response_model=FileOpResultOut)
def tool_rename_path(body: RenamePathIn):
    """重命名/移动单个文件或目录。src 必须存在;dst 父目录必须存在;dst 不能已存在。
    Win 文件锁瞬态自动重试(~1.2s 窗口),避免外部播放器/sidecar 自身 stream 没释放
    fd 时硬失败。
    """
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
            _rename_with_retry(src, tmp)
            _rename_with_retry(tmp, dst)
        else:
            _rename_with_retry(src, dst)
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


# ====================================================
#  Dev 面板专用端点 (/dev/*) — Toolbar 调试用,不进 agent 工具集
#
#  - 不写 Pydantic schema,返回 dict 即可,降低改接口的成本。
#  - 不进 OpenAPI 工具列表(给 agent 暴露的是 /tools/* + MCP)。
#  - 错误统一捕成 HTTP 400 + 文本 message,给前端 alert 显示。
# ====================================================


def _dev_error(e: Exception) -> HTTPException:
    """把 sidecar 各种异常包成 400 + 可读文本,前端直接 alert。"""
    from sidecar.tencent_sheet import TencentSheetError
    if isinstance(e, TencentSheetError):
        return HTTPException(
            status_code=400,
            detail=f"{type(e).__name__}: {e} "
                   f"(http_status={e.http_status}, api_code={e.api_code})",
        )
    return HTTPException(status_code=400, detail=f"{type(e).__name__}: {e}")


@app.get("/dev/sheet_status")
def dev_sheet_status():
    """看缓存当前状态 —— 不触发任何 API 调用。

    返回内存 cache + 磁盘 cache 两层状态,方便确认"现在到底从哪读"。
    """
    from sidecar.tencent_sheet import get_client, _disk_cache_path
    try:
        client = get_client()
    except Exception as e:
        raise _dev_error(e)
    cache = client._cache  # 仅 dev 端点直读私有属性
    disk_path = _disk_cache_path()
    disk_exists = disk_path.is_file()
    disk_size_kb = round(disk_path.stat().st_size / 1024, 1) if disk_exists else None
    return {
        "mem_cached": cache is not None,
        "mem_rows": len(cache) if cache is not None else 0,
        "fetched_at": client.fetched_at.isoformat() if client.fetched_at else None,
        "disk_cached": disk_exists,
        "disk_path": str(disk_path),
        "disk_size_kb": disk_size_kb,
        "spreadsheet_id": client.spreadsheet_id,
        "sheet_id": client.sheet_id,
    }


@app.post("/dev/refresh_sheet")
def dev_refresh_sheet():
    """强制重拉整表,刷新缓存。返回行数 + 耗时。"""
    import time
    from sidecar.tencent_sheet import get_client
    try:
        client = get_client()
        t0 = time.monotonic()
        rows = client.fetch_all(force=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
    except Exception as e:
        raise _dev_error(e)
    return {
        "rows": len(rows),
        "elapsed_ms": elapsed_ms,
        "fetched_at": client.fetched_at.isoformat() if client.fetched_at else None,
    }


@app.get("/dev/list_my_pending")
def dev_list_my_pending():
    """列出当前用户未验收的歌 —— 整行 37 列 + 表头,给开发者直接看原始数据。

    身份在 sidecar 内部读取,不接收任何参数 —— 与正式工具的隐私边界一致。
    """
    from sidecar.assignment_sheet import list_my_pending_rows
    try:
        headers, items = list_my_pending_rows()
    except Exception as e:
        raise _dev_error(e)
    return {"count": len(items), "headers": headers, "items": items}


@app.get("/dev/list_my_accepted")
def dev_list_my_accepted():
    """列出当前用户已验收的歌(col 34 == "1") —— 整行 37 列 + 表头。"""
    from sidecar.assignment_sheet import list_my_accepted_rows
    try:
        headers, items = list_my_accepted_rows()
    except Exception as e:
        raise _dev_error(e)
    return {"count": len(items), "headers": headers, "items": items}

"""Checker adapters for GenericAgent atomic tools."""
from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from pathlib import Path

from sidecar import paths, workspace
from sidecar.vendor.ga.code_runner import code_run as ga_code_run
from sidecar.vendor.ga import file_ops as ga_files

_browser_lock = threading.Lock()
_driver = None


async def code_run(script: str, type: str = "python", timeout: int = 60, cwd: str = "") -> dict:
    """Execute a script with the sidecar Python; use existing QC tools whenever they cover the task.
    NEVER edit QC states/chat DB/approvals or bypass fix_execute_plan. Default cwd and scratch outputs are temp; returns stdout, exit_code, cwd, temp_dir.
    """
    if not script.strip() or not 1 <= timeout <= 600:
        return {"ok": False, "code": "INVALID_ARG", "message": "script required; timeout must be 1..600"}
    temp = paths.temp_dir().resolve()
    temp.mkdir(parents=True, exist_ok=True)
    target = (temp / cwd).resolve() if cwd else temp
    if not target.is_dir():
        return {"ok": False, "code": "INVALID_CWD", "message": str(target)}
    stopped = []
    def execute():
        generator = ga_code_run(script, type, timeout, str(target), code_cwd=str(temp),
                                stop_signal=stopped, myprint=lambda *a, **k: None)
        while True:
            try:
                next(generator)
            except StopIteration as end:
                result = end.value or {"status": "error"}
                return {**result, "ok": result.get("status") == "success",
                        "cwd": str(target), "temp_dir": str(temp), "python": sys.executable,
                        "workspace": workspace.get_workspace()}
    task = asyncio.create_task(asyncio.to_thread(execute))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stopped.append(True)
        await asyncio.shield(task)
        raise


def _file_path(path: str) -> Path:
    if not path or not path.strip():
        raise ValueError("path required")
    # Like code_run, relative file paths are based on the scratch directory.
    temp = paths.temp_dir().resolve()
    return (temp / path).resolve()


def _scratch_write_path(path: str) -> Path:
    target = _file_path(path)
    temp = paths.temp_dir().resolve()
    if target == temp or not target.is_relative_to(temp):
        raise ValueError("file_write/file_patch only accept paths inside temp; use fix_execute_plan for song files")
    return target


def file_read(path: str, start: int = 1, keyword: str = "", count: int = 200,
              show_linenos: bool = True) -> dict:
    """Read a text file with GA line numbers or keyword context. Relative paths use temp.
    For song CSV/text prefer read_text_file. Returns {ok, path, content, truncated}; count 1..1000.
    """
    if start < 1 or not 1 <= count <= 1000:
        return {"ok": False, "code": "INVALID_ARG", "message": "start >= 1 and count 1..1000 required"}
    try:
        target = _file_path(path)
        if not target.is_file():
            return {"ok": False, "code": "FILE_NOT_FOUND", "message": str(target)}
        content = ga_files.file_read(str(target), start, keyword or None, count, show_linenos)
        if content.startswith("Error:"):
            return {"ok": False, "code": "READ_FAILED", "message": content}
        limit = 35000
        return {"ok": True, "path": str(target), "content": content[:limit],
                "truncated": len(content) > limit}
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "READ_FAILED", "message": str(exc)}


def file_write(path: str, content: str, mode: str = "overwrite") -> dict:
    """Create, overwrite, append or prepend a UTF-8 scratch file under temp.
    For song delivery text use fix_execute_plan simulate→execute. Returns {ok, path, mode, written_chars}.
    """
    if mode not in ("overwrite", "append", "prepend") or len(content) > 2_000_000:
        return {"ok": False, "code": "INVALID_ARG", "message": "invalid mode or content exceeds 2,000,000 characters"}
    try:
        target = _scratch_write_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        result = ga_files.file_write(str(target), content, mode)
        return {"ok": result["status"] == "success", "path": str(target), **result}
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "INVALID_PATH", "message": str(exc)}


def file_patch(path: str, old_content: str, new_content: str) -> dict:
    """Replace one unique exact text block in an existing UTF-8 scratch file under temp.
    For song delivery text use fix_execute_plan text_edit. Returns {ok, path, status, msg}.
    """
    if not old_content or len(old_content) + len(new_content) > 2_000_000:
        return {"ok": False, "code": "INVALID_ARG", "message": "old_content required; patch exceeds 2,000,000 characters"}
    try:
        target = _scratch_write_path(path)
        result = ga_files.file_patch(str(target), old_content, new_content)
        return {"ok": result["status"] == "success", "path": str(target), **result}
    except (OSError, ValueError) as exc:
        return {"ok": False, "code": "INVALID_PATH", "message": str(exc)}


def _browser():
    global _driver
    from sidecar.vendor.ga.TMWebDriver import TMWebDriver
    if _driver is None:
        _driver = TMWebDriver()
    return _driver


def _select(driver, tab_id):
    tabs = driver.get_all_sessions()
    if not tabs:
        raise ValueError("浏览器未连接。请加载 plugin/browser-bridge 扩展并保持普通网页打开。")
    if tab_id is None or str(tab_id) not in {str(t["id"]) for t in tabs}:
        raise ValueError("请先 web_scan(tabs_only=true)，再传入有效的 switch_tab_id。")
    driver.default_session_id = str(tab_id)
    return tabs


def web_scan(tabs_only: bool = False, switch_tab_id: str | None = None, text_only: bool = False) -> dict:
    """Scan browser tabs or simplified page HTML through GA's signed-in browser bridge.
    Use only for web tasks not covered by QC tools. List tabs first, then pass an explicit switch_tab_id.
    """
    try:
        with _browser_lock:
            driver = _browser()
            tabs = driver.get_all_sessions()
            if tabs_only:
                # MV3 extension reconnects a few seconds after the bridge starts.
                # Avoid reporting a false disconnection on the first scan.
                deadline = time.monotonic() + 10
                while not tabs and time.monotonic() < deadline:
                    time.sleep(0.25)
                    tabs = driver.get_all_sessions()
                return {"ok": True, "tabs": tabs, "connected": bool(tabs)}
            _select(driver, switch_tab_id)
            from sidecar.vendor.ga import simphtml
            content = simphtml.get_html(driver, cutlist=True, maxchars=35000, text_only=text_only)
            return {"ok": True, "tabs": tabs, "tab_id": str(switch_tab_id), "content": content[:35000]}
    except Exception as exc:
        return {"ok": False, "code": "BROWSER_ERROR", "message": str(exc)}


def web_execute_js(script: str, switch_tab_id: str, no_monitor: bool = False) -> dict:
    """Execute page JavaScript and report changes using GA; requires a tab ID observed by web_scan.
    Only for web tasks; never bypass QC tools, confirmations or state updates. Verify page state after actions.
    """
    if not script.strip():
        return {"ok": False, "code": "INVALID_ARG", "message": "script required"}
    try:
        with _browser_lock:
            driver = _browser()
            _select(driver, switch_tab_id)
            from sidecar.vendor.ga import simphtml
            result = simphtml.execute_js_rich(script, driver, no_monitor=no_monitor)
            encoded = json.dumps(result, ensure_ascii=False, default=str)
            if len(encoded) > 35000:
                return {"ok": result.get("status") == "success", "truncated": True, "content": encoded[:35000]}
            return {**result, "ok": result.get("status") == "success"}
    except Exception as exc:
        return {"ok": False, "code": "BROWSER_ERROR", "message": str(exc)}

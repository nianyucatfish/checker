"""File primitives extracted from GenericAgent ga.py (MIT; see LICENSE).

Checker passes resolved paths and limits writes to its scratch directory in the
adapter. The GA response-body fallback and file-reference expansion are omitted:
MCP tools receive their content explicitly as a structured argument.
"""

import collections
import itertools
import os
import re
from pathlib import Path


def _file_newline(path):
    endings = set(re.findall(rb'\r\n|[\r\n]', Path(path).read_bytes())) if os.path.exists(path) else set()
    return next(iter(endings)).decode() if len(endings) == 1 else None


def file_read(path, start=1, keyword=None, count=200, show_linenos=True):
    """GA line-oriented file read, with keyword context and numbered output."""
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            stream = ((i, line.rstrip('\r\n')) for i, line in enumerate(f, 1))
            stream = itertools.dropwhile(lambda item: item[0] < start, stream)
            if keyword:
                before = collections.deque(maxlen=count // 3)
                for i, line in stream:
                    if keyword.lower() in line.lower():
                        res = list(before) + [(i, line)] + list(itertools.islice(stream, count - len(before) - 1))
                        break
                    before.append((i, line))
                else:
                    return (f"Keyword '{keyword}' not found after line {start}. Falling back to content from line {start}:\n\n"
                            + file_read(path, start, None, count, show_linenos))
            else:
                res = list(itertools.islice(stream, count))
            realcnt = len(res)
            line_max = min(max(100, 256000 // max(realcnt, 1)), 8000)
            remaining = sum(1 for _ in itertools.islice(stream, 5000))
            total_lines = (res[0][0] - 1 if res else start - 1) + realcnt + remaining
            total_str = f"{total_lines}+" if remaining >= 5000 else str(total_lines)
            partial = total_lines > realcnt
            total_tag = f"[FILE] {total_str} lines" + (f" | PARTIAL showing {realcnt}; assess need for more" if partial else "") + "\n"
            res = [(i, line if len(line) <= line_max else line[:line_max] + " ... [TRUNCATED]") for i, line in res]
            result = "\n".join(f"{i}|{line}" if show_linenos else line for i, line in res)
            if show_linenos:
                result = total_tag + result
            elif partial:
                result += f"\n\n[FILE PARTIAL: showing {realcnt}/{total_str} lines; assess need for more]"
            return result
    except Exception as exc:
        return f"Error: {exc}"


def file_patch(path: str, old_content: str, new_content: str):
    """Replace one exact unique text block, preserving homogeneous newlines."""
    path = str(Path(path).resolve())
    try:
        if not os.path.exists(path):
            return {"status": "error", "msg": "file not found"}
        with open(path, 'r', encoding='utf-8') as file:
            full_text = file.read()
        if not old_content:
            return {"status": "error", "msg": "old_content is blank"}
        matches = full_text.count(old_content)
        if matches == 0:
            return {"status": "error", "msg": "old_content is not found. Use file_read to check current file content."}
        if matches > 1:
            return {"status": "error", "msg": f"find {matches} matches, unable to determine unique position"}
        updated_text = full_text.replace(old_content, new_content)
        with open(path, 'w', encoding='utf-8', newline=_file_newline(path)) as file:
            file.write(updated_text)
        return {"status": "success", "msg": "file patched successfully"}
    except Exception as exc:
        return {"status": "error", "msg": str(exc)}


def file_write(path: str, content: str, mode: str = "overwrite"):
    """GA overwrite/append/prepend modes with explicit MCP content."""
    if mode not in ("overwrite", "append", "prepend"):
        return {"status": "error", "msg": "mode must be overwrite, append or prepend"}
    try:
        if mode == "prepend":
            old = Path(path).read_text(encoding='utf-8') if os.path.exists(path) else ""
            with open(path, 'w', encoding='utf-8', newline=_file_newline(path)) as file:
                file.write(content + old)
        else:
            with open(path, 'a' if mode == "append" else 'w', encoding='utf-8', newline=_file_newline(path)) as file:
                file.write(content)
        return {"status": "success", "written_chars": len(content), "mode": mode}
    except Exception as exc:
        return {"status": "error", "msg": str(exc)}

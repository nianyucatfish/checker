import asyncio
import sys

from sidecar import advanced_tools as tools


def test_python_environment_and_temp(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKER_TEMP_DIR", str(tmp_path))
    result = asyncio.run(tools.code_run("import sys, os; print(sys.executable); print(os.getcwd()); print('中文')"))
    assert result["ok"]
    assert sys.executable in result["stdout"]
    assert str(tmp_path) in result["stdout"]
    assert "中文" in result["stdout"]
    assert not list(tmp_path.glob("*.ai.py"))


def test_error_timeout_and_output_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKER_TEMP_DIR", str(tmp_path))
    failed = asyncio.run(tools.code_run("raise ValueError('test')"))
    assert not failed["ok"] and failed["exit_code"] != 0
    timed = asyncio.run(tools.code_run("import time; time.sleep(30)", timeout=1))
    assert not timed["ok"] and "Timeout" in timed["stdout"]
    large = asyncio.run(tools.code_run("print('x' * 100000)"))
    assert large["ok"] and len(large["stdout"]) < 11000


def test_cancel_stops_script(tmp_path, monkeypatch):
    monkeypatch.setenv("CHECKER_TEMP_DIR", str(tmp_path))
    async def run():
        task = asyncio.create_task(tools.code_run("import time; time.sleep(30)"))
        await asyncio.sleep(0.3)
        task.cancel()
        try:
            await asyncio.wait_for(task, 3)
        except asyncio.CancelledError:
            pass
    asyncio.run(run())
    assert not list(tmp_path.glob("*.ai.py"))


def test_browser_requires_observed_tab(monkeypatch):
    class Driver:
        default_session_id = None
        def get_all_sessions(self):
            return [{"id": "7", "title": "test"}]
    monkeypatch.setattr(tools, "_driver", Driver())
    monkeypatch.setattr(tools, "_browser", lambda: tools._driver)
    assert tools.web_scan(tabs_only=True)["tabs"][0]["id"] == "7"
    assert not tools.web_scan(switch_tab_id="8")["ok"]
    assert not tools.web_execute_js("return 1", "8")["ok"]


def test_ga_file_tools_use_temp_and_keyword_lines(tmp_path, monkeypatch):
    scratch = tmp_path / "temp"
    monkeypatch.setenv("CHECKER_TEMP_DIR", str(scratch))
    assert tools.file_write("notes.txt", "first\nneedle\nlast\n")["ok"]
    found = tools.file_read("notes.txt", keyword="NEEDLE", count=2)
    assert found["ok"] and "2|needle" in found["content"]
    assert tools.file_patch("notes.txt", "needle", "matched")["ok"]
    assert tools.file_write("notes.txt", "tail\n", mode="append")["ok"]
    assert tools.file_write("notes.txt", "head\n", mode="prepend")["ok"]
    assert tools.file_read("notes.txt", show_linenos=False)["content"] == "head\nfirst\nmatched\nlast\ntail"
    assert not tools.file_patch("notes.txt", "missing", "new")["ok"]


def test_advanced_writes_cannot_bypass_song_fixer(tmp_path, monkeypatch):
    scratch = tmp_path / "temp"
    song = tmp_path / "song" / "Beat.csv"
    song.parent.mkdir()
    song.write_text("beat", encoding="utf-8")
    monkeypatch.setenv("CHECKER_TEMP_DIR", str(scratch))
    assert not tools.file_write(str(song), "changed")["ok"]
    assert not tools.file_patch(str(song), "beat", "changed")["ok"]
    assert song.read_text(encoding="utf-8") == "beat"
    scratch.mkdir()
    (scratch / "outside.txt").symlink_to(song)
    assert not tools.file_write("outside.txt", "changed")["ok"]
    assert tools.file_read(str(song))["ok"]

"""Render a reusable side-by-side HTML diff for two text files.

Usage:
    python scripts/render_text_diff_html.py OLD_FILE NEW_FILE -o tmp/diff.html

The output is a standalone HTML file that can be opened locally in a browser.
"""

from __future__ import annotations

import argparse
import difflib
import html
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def _rel_label(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def _build_html(
    old_path: Path,
    new_path: Path,
    output_path: Path,
    *,
    old_label: str | None,
    new_label: str | None,
    title: str | None,
    context: int | None,
) -> str:
    old_lines = _read_lines(old_path)
    new_lines = _read_lines(new_path)
    fromdesc = old_label or _rel_label(old_path)
    todesc = new_label or _rel_label(new_path)
    page_title = title or f"Diff: {fromdesc} ↔ {todesc}"

    diff = difflib.HtmlDiff(tabsize=2, wrapcolumn=120).make_table(
        old_lines,
        new_lines,
        fromdesc=html.escape(fromdesc),
        todesc=html.escape(todesc),
        context=context is not None,
        numlines=context if context is not None else 5,
    )

    old_count = len(old_lines)
    new_count = len(new_lines)
    line_delta = new_count - old_count

    return f"""<!doctype html>
<html lang=\"zh-CN\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>{html.escape(page_title)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1115;
      --panel: #161a22;
      --text: #e6edf3;
      --muted: #9aa4b2;
      --border: #303746;
      --add: #12351f;
      --add-strong: #1f6f3a;
      --del: #3f171b;
      --del-strong: #8a2f35;
      --chg: #3a300f;
      --chg-strong: #8a6f1d;
      --link: #7cb7ff;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #f6f8fa;
        --panel: #ffffff;
        --text: #24292f;
        --muted: #57606a;
        --border: #d0d7de;
        --add: #dafbe1;
        --add-strong: #aceebb;
        --del: #ffebe9;
        --del-strong: #ffcecb;
        --chg: #fff8c5;
        --chg-strong: #fae17d;
        --link: #0969da;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    header {{
      position: sticky;
      top: 0;
      z-index: 5;
      padding: 14px 18px;
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border-bottom: 1px solid var(--border);
      backdrop-filter: blur(8px);
    }}
    h1 {{ margin: 0 0 6px; font-size: 17px; font-weight: 650; }}
    .meta {{ color: var(--muted); display: flex; flex-wrap: wrap; gap: 12px; }}
    main {{ padding: 16px; }}
    .hint {{ margin: 0 0 12px; color: var(--muted); }}
    .diff-wrap {{
      overflow: auto;
      border: 1px solid var(--border);
      border-radius: 10px;
      background: var(--panel);
    }}
    table.diff {{
      width: 100%;
      border-collapse: collapse;
      font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
      table-layout: fixed;
    }}
    .diff_header {{
      background: color-mix(in srgb, var(--panel) 82%, var(--border));
      color: var(--muted);
      font-weight: 600;
      border-bottom: 1px solid var(--border);
    }}
    td, th {{ vertical-align: top; }}
    .diff_next {{ width: 22px; color: var(--muted); text-align: center; }}
    .diff_next a {{ color: var(--link); text-decoration: none; }}
    .diff_header {{ width: 56px; text-align: right; padding: 0 8px; user-select: none; }}
    td:nth-child(3), td:nth-child(6) {{
      width: calc((100% - 156px) / 2);
      padding: 0 10px;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }}
    tr:hover td {{ background: color-mix(in srgb, var(--border) 18%, transparent); }}
    .diff_add {{ background: var(--add); }}
    .diff_sub {{ background: var(--del); }}
    .diff_chg {{ background: var(--chg); }}
    .diff_add .diff_chg {{ background: var(--add-strong); }}
    .diff_sub .diff_chg {{ background: var(--del-strong); }}
    .diff_chg .diff_chg {{ background: var(--chg-strong); }}
    .summary {{ display: none; }}
    .legend {{ margin-top: 12px; color: var(--muted); display: flex; gap: 14px; flex-wrap: wrap; }}
    .swatch {{ display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 5px; vertical-align: -1px; }}
  </style>
</head>
<body>
  <header>
    <h1>{html.escape(page_title)}</h1>
    <div class=\"meta\">
      <span>旧版: {html.escape(fromdesc)} ({old_count} 行)</span>
      <span>新版: {html.escape(todesc)} ({new_count} 行)</span>
      <span>行数变化: {line_delta:+d}</span>
      <span>输出: {html.escape(_rel_label(output_path))}</span>
    </div>
  </header>
  <main>
    <p class=\"hint\">左右并排展示文本差异;顶部锚点可在差异块之间跳转。此文件为离线 HTML,可直接发给别人打开。</p>
    <div class=\"diff-wrap\">
      {diff}
    </div>
    <div class=\"legend\">
      <span><i class=\"swatch\" style=\"background: var(--del)\"></i>删除</span>
      <span><i class=\"swatch\" style=\"background: var(--add)\"></i>新增</span>
      <span><i class=\"swatch\" style=\"background: var(--chg)\"></i>行内修改</span>
    </div>
  </main>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a side-by-side HTML diff for two UTF-8 text files.")
    parser.add_argument("old_file", type=Path, help="Old text file")
    parser.add_argument("new_file", type=Path, help="New text file")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output HTML file path")
    parser.add_argument("--old-label", help="Left-side label; defaults to the file path")
    parser.add_argument("--new-label", help="Right-side label; defaults to the file path")
    parser.add_argument("--title", help="Page title")
    parser.add_argument(
        "--context",
        type=int,
        default=None,
        help="Only show changed blocks with N context lines; default shows the full file. Example: --context 3",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    old_path = args.old_file.resolve()
    new_path = args.new_file.resolve()
    output_path = args.output.resolve()

    if not old_path.is_file():
        raise SystemExit(f"旧版文件不存在: {old_path}")
    if not new_path.is_file():
        raise SystemExit(f"新版文件不存在: {new_path}")

    html_text = _build_html(
        old_path,
        new_path,
        output_path,
        old_label=args.old_label,
        new_label=args.new_label,
        title=args.title,
        context=args.context,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8", newline="\n")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()

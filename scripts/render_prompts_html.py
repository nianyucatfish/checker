"""Render doc/prompts/*.md into a single readable HTML page for review.

Usage:
    # 工作区当前版本
    python scripts/render_prompts_html.py -o tmp/prompts_review.html

    # 指定 git ref(分支 / commit)上的版本
    python scripts/render_prompts_html.py --ref origin/frank/workflow-rework -o tmp/prompts_frank.html

Output is a standalone HTML file (sidebar TOC + readable typography) that can
be opened locally in a browser or sent to others.
"""

from __future__ import annotations

import argparse
import html
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent

# 按运行时注入顺序排列:phase_a 是闲聊阶段,phase_b_header 是工作流头,后接完整手册
PROMPT_FILES = [
    ("doc/prompts/phase_a.md", "Phase A — 未进入质检流程"),
    ("doc/prompts/phase_b_header.md", "Phase B header — 工作流头部"),
    ("doc/prompts/agent_workflow.md", "Agent 工作手册(完整 SOP)"),
]

CHECKBOX_UNCHECKED = re.compile(r"^(\s*[-*]\s+)\[ \]", re.MULTILINE)
CHECKBOX_CHECKED = re.compile(r"^(\s*[-*]\s+)\[[xX]\]", re.MULTILINE)


@dataclass
class Section:
    slug: str
    title: str
    rel_path: str
    body_html: str
    toc_tokens: list
    line_count: int
    char_count: int


def _read_source(rel_path: str, ref: str | None) -> str:
    if ref is None:
        return (ROOT / rel_path).read_text(encoding="utf-8")
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    return result.stdout.decode("utf-8")


def _ref_commit(ref: str | None) -> str:
    args = ["git", "log", "-1", "--format=%h %s"] + ([ref] if ref else [])
    result = subprocess.run(args, cwd=ROOT, capture_output=True, check=True)
    return result.stdout.decode("utf-8").strip()


def _preprocess(text: str) -> str:
    """python-markdown 不认 task list,替换成 HTML checkbox。"""
    text = CHECKBOX_UNCHECKED.sub(r'\1<input type="checkbox" disabled> ', text)
    text = CHECKBOX_CHECKED.sub(r'\1<input type="checkbox" disabled checked> ', text)
    return text


def _render_section(rel_path: str, title: str, ref: str | None, index: int) -> Section:
    raw = _read_source(rel_path, ref)
    slug = f"file{index}"
    md = markdown.Markdown(
        extensions=["extra", "toc", "sane_lists"],
        extension_configs={"toc": {"toc_depth": "1-3", "slugify": _make_slugify(slug)}},
    )
    body = md.convert(_preprocess(raw))
    return Section(
        slug=slug,
        title=title,
        rel_path=rel_path,
        body_html=body,
        toc_tokens=md.toc_tokens,
        line_count=raw.count("\n") + 1,
        char_count=len(raw),
    )


def _make_slugify(prefix: str):
    from markdown.extensions.toc import slugify_unicode

    def _slug(value: str, separator: str) -> str:
        return f"{prefix}-{slugify_unicode(value, separator)}"

    return _slug


def _toc_html(sections: list[Section]) -> str:
    parts: list[str] = []
    for sec in sections:
        parts.append(
            f'<div class="toc-file"><a href="#sec-{sec.slug}">{html.escape(sec.title)}</a></div>'
        )
        parts.append(_toc_tokens_html(sec.toc_tokens))
    return "\n".join(parts)


def _toc_tokens_html(tokens: list) -> str:
    if not tokens:
        return ""
    items = []
    for tok in tokens:
        children = _toc_tokens_html(tok.get("children", []))
        items.append(
            f'<li><a href="#{tok["id"]}">{html.escape(tok["name"])}</a>{children}</li>'
        )
    return "<ul>" + "".join(items) + "</ul>"


def _build_html(sections: list[Section], *, source_label: str, commit: str) -> str:
    toc = _toc_html(sections)
    body_parts = []
    for sec in sections:
        body_parts.append(
            f"""
    <section id="sec-{sec.slug}">
      <div class="file-banner">
        <span class="file-path">{html.escape(sec.rel_path)}</span>
        <span class="file-meta">{sec.line_count} 行 / {sec.char_count} 字符</span>
      </div>
      <div class="md-body">{sec.body_html}</div>
    </section>"""
        )
    body = "\n".join(body_parts)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Prompts 审阅 — {html.escape(source_label)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #0f1115; --panel: #161a22; --text: #e6edf3; --muted: #9aa4b2;
      --border: #303746; --accent: #7cb7ff; --code-bg: #1d2330;
      --banner: #1b2130;
    }}
    @media (prefers-color-scheme: light) {{
      :root {{
        --bg: #f6f8fa; --panel: #ffffff; --text: #24292f; --muted: #57606a;
        --border: #d0d7de; --accent: #0969da; --code-bg: #f0f2f5;
        --banner: #eef2f7;
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; background: var(--bg); color: var(--text);
      font: 15px/1.75 ui-sans-serif, system-ui, -apple-system, "Segoe UI",
        "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    header {{
      position: sticky; top: 0; z-index: 5; padding: 12px 20px;
      background: color-mix(in srgb, var(--panel) 92%, transparent);
      border-bottom: 1px solid var(--border); backdrop-filter: blur(8px);
    }}
    header h1 {{ margin: 0 0 4px; font-size: 16px; font-weight: 650; }}
    header .meta {{ color: var(--muted); font-size: 13px; display: flex; gap: 14px; flex-wrap: wrap; }}
    .layout {{ display: flex; align-items: flex-start; max-width: 1280px; margin: 0 auto; }}
    nav {{
      position: sticky; top: 64px; width: 300px; flex: none;
      max-height: calc(100vh - 80px); overflow: auto;
      padding: 16px 8px 24px 20px; font-size: 13px;
    }}
    nav .toc-file {{ margin: 14px 0 4px; font-weight: 650; }}
    nav ul {{ list-style: none; margin: 0; padding-left: 14px; }}
    nav li {{ margin: 2px 0; }}
    nav a {{ color: var(--muted); text-decoration: none; }}
    nav a:hover {{ color: var(--accent); }}
    nav .toc-file a {{ color: var(--text); }}
    main {{ flex: 1; min-width: 0; padding: 20px 28px 80px; }}
    @media (max-width: 960px) {{ nav {{ display: none; }} }}

    section {{ margin-bottom: 48px; }}
    .file-banner {{
      display: flex; justify-content: space-between; align-items: baseline; gap: 12px;
      padding: 8px 14px; margin-bottom: 18px;
      background: var(--banner); border: 1px solid var(--border); border-radius: 8px;
    }}
    .file-path {{ font: 600 13px ui-monospace, Consolas, monospace; color: var(--accent); }}
    .file-meta {{ color: var(--muted); font-size: 12px; white-space: nowrap; }}

    .md-body {{ max-width: 860px; }}
    .md-body h1 {{ font-size: 26px; border-bottom: 2px solid var(--border); padding-bottom: 8px; margin: 28px 0 16px; }}
    .md-body h2 {{ font-size: 21px; border-bottom: 1px solid var(--border); padding-bottom: 6px; margin: 32px 0 12px; }}
    .md-body h3 {{ font-size: 17px; margin: 26px 0 10px; }}
    .md-body h4 {{ font-size: 15px; margin: 20px 0 8px; color: var(--muted); }}
    .md-body p {{ margin: 10px 0; }}
    .md-body ul, .md-body ol {{ padding-left: 26px; margin: 8px 0; }}
    .md-body li {{ margin: 4px 0; }}
    .md-body li input[type="checkbox"] {{ margin-right: 4px; vertical-align: -2px; }}
    .md-body code {{
      font: 13px ui-monospace, SFMono-Regular, Consolas, monospace;
      background: var(--code-bg); padding: 1px 5px; border-radius: 4px;
    }}
    .md-body pre {{
      background: var(--code-bg); border: 1px solid var(--border); border-radius: 8px;
      padding: 12px 14px; overflow: auto; line-height: 1.55;
    }}
    .md-body pre code {{ background: none; padding: 0; }}
    .md-body table {{ border-collapse: collapse; margin: 12px 0; width: 100%; font-size: 14px; }}
    .md-body th, .md-body td {{ border: 1px solid var(--border); padding: 6px 10px; text-align: left; }}
    .md-body th {{ background: var(--banner); }}
    .md-body blockquote {{
      margin: 12px 0; padding: 4px 16px; border-left: 3px solid var(--accent);
      color: var(--muted); background: color-mix(in srgb, var(--panel) 60%, transparent);
    }}
    .md-body hr {{ border: none; border-top: 1px solid var(--border); margin: 28px 0; }}
    .md-body a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <header>
    <h1>Prompts 审阅 — {html.escape(source_label)}</h1>
    <div class="meta">
      <span>来源: {html.escape(source_label)}</span>
      <span>最新提交: {html.escape(commit)}</span>
      <span>共 {len(sections)} 个文件</span>
    </div>
  </header>
  <div class="layout">
    <nav>{toc}</nav>
    <main>{body}</main>
  </div>
</body>
</html>
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render doc/prompts/*.md to a readable standalone HTML.")
    parser.add_argument("--ref", help="git ref(分支/commit);缺省用工作区当前文件")
    parser.add_argument("-o", "--output", type=Path, required=True, help="输出 HTML 路径")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_label = args.ref or "工作区 (working tree)"
    sections = [
        _render_section(rel_path, title, args.ref, i)
        for i, (rel_path, title) in enumerate(PROMPT_FILES)
    ]
    html_text = _build_html(sections, source_label=source_label, commit=_ref_commit(args.ref))
    output_path = args.output if args.output.is_absolute() else ROOT / args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html_text, encoding="utf-8", newline="\n")
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()

"""
sidecar.cli — 命令行入口，方便在 sidecar 还没接 HTTP 层时直接验证检查与修复逻辑。

用法：
    python -m sidecar.cli check <workspace>           # 全工作区结构化检查（JSON）
    python -m sidecar.cli check <song>                # 单首歌检查
    python -m sidecar.cli plan-autofix <song>         # 干跑：列出可修的命名问题
    python -m sidecar.cli pad <song>                  # 时长统一（写文件，慎用）

输出统一为 UTF-8 JSON，便于 pipe 给其他工具。
"""

import argparse
import json
import os
import sys

from sidecar import checker, fixers


def _err(msg):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    return 1


def cmd_check(path):
    if not os.path.isdir(path):
        return _err(f"路径不存在或不是目录: {path}")

    typical = {"分轨wav", "总轨wav", "midi", "csv", "混音工程原文件"}
    children = set(os.listdir(path)) if os.path.isdir(path) else set()
    is_song = bool(typical & children)

    if is_song:
        result = checker.check_song_folder(path)
    else:
        result = checker.check_workspace(path)

    payload = {
        "ok": True,
        "scope": "song" if is_song else "workspace",
        "errors": {p: [e.to_dict() for e in errs] for p, errs in result.items()},
        "summary": {
            "paths_with_errors": len(result),
            "total_errors": sum(len(v) for v in result.values()),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def cmd_plan_autofix(song):
    if not os.path.isdir(song):
        return _err(f"歌曲文件夹不存在: {song}")
    plan = fixers.build_autofix_plan([song])
    print(
        json.dumps(
            {
                "ok": True,
                "ops": [op.to_dict() for op in plan.ops],
                "conflicts": plan.conflicts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def cmd_pad(song):
    if not os.path.isdir(song):
        return _err(f"歌曲文件夹不存在: {song}")
    result = fixers.pad_song_to_longest(song)
    print(
        json.dumps(
            {
                "ok": result.error is None,
                "padded": result.padded,
                "max_duration": result.max_duration,
                "error": result.error,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.error is None else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m sidecar.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="结构化检查（自动判别工作区/单首歌）")
    p_check.add_argument("path")

    p_plan = sub.add_parser("plan-autofix", help="干跑命名修复计划，不写盘")
    p_plan.add_argument("song")

    p_pad = sub.add_parser("pad", help="对一首歌的三个目录做尾部补静音对齐（写文件）")
    p_pad.add_argument("song")

    args = parser.parse_args(argv)
    if args.cmd == "check":
        return cmd_check(args.path)
    if args.cmd == "plan-autofix":
        return cmd_plan_autofix(args.song)
    if args.cmd == "pad":
        return cmd_pad(args.song)
    return _err(f"未知命令: {args.cmd}")


if __name__ == "__main__":
    sys.exit(main())

import { useMemo, useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
import { clsx } from "../utils";
import type { CheckErrorOut } from "../api";

interface Props {
  errorsBySong: Record<string, CheckErrorOut[]>;
  // 当前选中所属"目录":file 取其 parent dir,dir 取自身。null = 没选中。
  // mode='current' 时只显示路径在该 dir 下(含子目录)的错误。
  selectedDir: string | null;
  onJumpTo?: (path: string) => void;
}

type Mode = "all" | "current";

function basename(p: string) {
  const m = p.split(/[\\/]/);
  return m[m.length - 1] || p;
}

// errPath 是否在 dir 之下(含 dir 本身)
function inDir(errPath: string, dir: string) {
  return (
    errPath === dir ||
    errPath.startsWith(dir + "\\") ||
    errPath.startsWith(dir + "/")
  );
}

export function ProblemsPanel({ errorsBySong, selectedDir, onJumpTo }: Props) {
  const [mode, setMode] = useState<Mode>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const visible = useMemo<Record<string, CheckErrorOut[]>>(() => {
    if (mode !== "current" || !selectedDir) return errorsBySong;
    const out: Record<string, CheckErrorOut[]> = {};
    for (const [song, errs] of Object.entries(errorsBySong)) {
      const filtered = errs.filter((e) => inDir(e.path, selectedDir));
      if (filtered.length > 0) out[song] = filtered;
    }
    return out;
  }, [mode, selectedDir, errorsBySong]);

  const totalCount = Object.values(visible).reduce((acc, arr) => acc + arr.length, 0);
  const isCurrent = mode === "current";

  function toggle(song: string) {
    const next = new Set(collapsed);
    if (next.has(song)) next.delete(song);
    else next.add(song);
    setCollapsed(next);
  }

  return (
    <div className="pane">
      <div className="pane-header flex justify-between gap-2">
        <span>问题{totalCount > 0 && <span className="ml-1 text-danger">({totalCount})</span>}</span>
        <button
          type="button"
          role="switch"
          aria-checked={isCurrent}
          onClick={() => setMode((m) => (m === "current" ? "all" : "current"))}
          disabled={!selectedDir}
          title={
            selectedDir
              ? "切换是否仅显示当前目录(及其子目录)的问题"
              : "先在左侧选中一项"
          }
          className={clsx(
            "inline-flex items-center gap-2 normal-case tracking-normal",
            "text-[11px] disabled:opacity-40 disabled:cursor-not-allowed",
            isCurrent ? "text-fg" : "text-fg-muted hover:text-fg",
          )}
        >
          <span>仅查看当前目录问题</span>
          {/* 药丸轨道 + 圆点旋钮(off=灰底,on=accent 蓝底);旋钮在轨道里左右滑 */}
          <span
            className={clsx(
              "relative inline-block w-7 h-4 rounded-full transition-colors shrink-0",
              isCurrent ? "bg-accent" : "bg-fg-subtle/40",
            )}
          >
            <span
              className={clsx(
                "absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white shadow-sm transition-transform",
                isCurrent ? "translate-x-3" : "translate-x-0",
              )}
            />
          </span>
        </button>
      </div>
      <div className="pane-body">
        {totalCount === 0 && (
          <div className="px-3 py-3 text-fg-muted">
            {isCurrent && selectedDir ? "当前目录无问题" : "无问题"}
          </div>
        )}
        {Object.entries(visible).map(([song, errs]) => {
          if (errs.length === 0) return null;
          const isCollapsed = collapsed.has(song);
          return (
            <div key={song}>
              <div
                className="row cursor-pointer"
                onClick={() => toggle(song)}
              >
                {isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                <span className="font-medium truncate flex-1">{basename(song)}</span>
                <span className="text-xs text-danger">{errs.length}</span>
              </div>
              {!isCollapsed &&
                errs.map((e, i) => (
                  <div
                    key={i}
                    className="px-3 py-1 text-sm cursor-pointer hover:bg-bg-hover"
                    onClick={() => onJumpTo?.(e.path)}
                    style={{ paddingLeft: 36 }}
                  >
                    <div className="flex items-start gap-2">
                      <AlertCircle size={12} className="text-danger mt-0.5 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <code className="font-mono text-xs text-fg-subtle">{e.code}</code>
                          {e.machine_fixable && (
                            <span className="text-xs text-success">可自动修</span>
                          )}
                        </div>
                        <div>{e.message}</div>
                      </div>
                    </div>
                  </div>
                ))}
            </div>
          );
        })}
      </div>
    </div>
  );
}

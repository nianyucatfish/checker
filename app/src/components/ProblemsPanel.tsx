import { useState } from "react";
import { AlertCircle, ChevronDown, ChevronRight } from "lucide-react";
import { clsx } from "../utils";
import type { CheckErrorOut } from "../api";

interface Props {
  errorsBySong: Record<string, CheckErrorOut[]>;
  selectedSong: string | null;
  onJumpTo?: (path: string) => void;
}

type Mode = "all" | "current";

function basename(p: string) {
  const m = p.split(/[\/]/);
  return m[m.length - 1] || p;
}

export function ProblemsPanel({ errorsBySong, selectedSong, onJumpTo }: Props) {
  const [mode, setMode] = useState<Mode>("all");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const visible = mode === "current" && selectedSong
    ? { [selectedSong]: errorsBySong[selectedSong] || [] }
    : errorsBySong;

  const totalCount = Object.values(visible).reduce((acc, arr) => acc + arr.length, 0);

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
          onClick={() => setMode((m) => (m === "current" ? "all" : "current"))}
          disabled={!selectedSong}
          aria-pressed={mode === "current"}
          title={selectedSong ? "切换是否仅显示当前目录所属歌曲的问题" : "先在左侧选中一项"}
          className={clsx(
            "px-2.5 h-6 rounded-full border text-[11px] normal-case tracking-normal",
            "transition-colors disabled:opacity-40 disabled:cursor-not-allowed",
            mode === "current"
              ? "bg-accent text-accent-fg border-accent"
              : "border-border text-fg-muted hover:text-fg hover:bg-bg-hover",
          )}
        >
          仅查看当前目录问题
        </button>
      </div>
      <div className="pane-body">
        {totalCount === 0 && (
          <div className="px-3 py-3 text-fg-muted">无问题</div>
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

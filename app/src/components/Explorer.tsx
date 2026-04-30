import { ChevronRight, Folder, RefreshCw } from "lucide-react";
import { clsx } from "../utils";

export interface SongState {
  path: string;
  errorCount: number;
}

interface Props {
  root: string | null;
  songs: SongState[];
  selected: string | null;
  scanning: boolean;
  onPickWorkspace: () => void;
  onScan: () => void;
  onSelect: (path: string) => void;
}

function basename(p: string) {
  const m = p.split(/[\/]/);
  return m[m.length - 1] || p;
}

export function Explorer({
  root,
  songs,
  selected,
  scanning,
  onPickWorkspace,
  onScan,
  onSelect,
}: Props) {
  return (
    <div className="pane">
      <div className="pane-header justify-between flex">
        <span>{root ? basename(root) : "工作区"}</span>
        {root && (
          <button
            onClick={onScan}
            disabled={scanning}
            title="全量扫描"
            className="text-fg-muted hover:text-fg disabled:opacity-50"
          >
            <RefreshCw size={14} className={scanning ? "animate-spin" : ""} />
          </button>
        )}
      </div>
      <div className="pane-body">
        {!root && (
          <div className="px-3 py-4 flex flex-col gap-2 items-start text-fg-muted">
            <p>尚未打开工作区</p>
            <button onClick={onPickWorkspace} className="btn btn-primary">
              打开文件夹
            </button>
          </div>
        )}
        {root && songs.length === 0 && (
          <div className="px-3 py-2 text-fg-muted">该工作区下没有歌曲文件夹</div>
        )}
        {songs.map((song) => (
          <div
            key={song.path}
            className={clsx(
              "row",
              song.errorCount > 0 && "has-errors",
              selected === song.path && "selected"
            )}
            onClick={() => onSelect(song.path)}
          >
            <ChevronRight size={14} className="text-fg-muted shrink-0" />
            <Folder size={14} className="shrink-0" />
            <span className="truncate flex-1">{basename(song.path)}</span>
            {song.errorCount > 0 && (
              <span className="text-xs px-1 rounded bg-danger/20 text-danger shrink-0">
                {song.errorCount}
              </span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  ChevronRight,
  ChevronDown,
  Folder,
  FolderOpen,
  File,
  FileText,
  FileAudio,
  FileSpreadsheet,
  Music3,
  AlertCircle,
} from "lucide-react";
import { listDir } from "../api";
import type { DirEntryOut, CheckErrorOut } from "../api";
import { clsx } from "../utils";

interface Props {
  root: string | null;
  songs: string[];                  // 一级歌曲文件夹路径
  selected: string | null;
  allErrors: CheckErrorOut[];
  onPickWorkspace: () => void;
  onSelect: (path: string, isDir: boolean) => void;
}

function basename(p: string) {
  const m = p.split(/[\\/]/);
  return m[m.length - 1] || p;
}

function fileIcon(ext: string) {
  if (["wav", "mp3", "ogg", "flac", "m4a"].includes(ext)) return FileAudio;
  if (ext === "csv") return FileSpreadsheet;
  if (["mid", "midi"].includes(ext)) return Music3;
  if (["txt", "md", "json", "log"].includes(ext)) return FileText;
  return File;
}

interface NodeProps {
  path: string;
  name: string;
  isDir: boolean;
  ext: string;
  depth: number;
  expanded: Set<string>;
  childrenCache: Map<string, DirEntryOut[]>;
  loading: Set<string>;
  selected: string | null;
  errorCountFor: (path: string, isDir: boolean) => number;
  onToggle: (path: string) => void;
  onSelect: (path: string, isDir: boolean) => void;
}

function TreeNode(props: NodeProps) {
  const {
    path, name, isDir, ext, depth,
    expanded, childrenCache, loading, selected,
    errorCountFor, onToggle, onSelect,
  } = props;

  const isExpanded = expanded.has(path);
  const isLoading = loading.has(path);
  const isSelected = selected === path;
  const children = childrenCache.get(path);
  const errs = errorCountFor(path, isDir);
  const Icon = isDir ? (isExpanded ? FolderOpen : Folder) : fileIcon(ext);
  const padLeft = 8 + depth * 14;

  const handleClick = () => {
    onSelect(path, isDir);
    if (isDir) onToggle(path);
  };

  return (
    <>
      <div
        className={clsx(
          "row",
          errs > 0 && "has-errors",
          isSelected && "selected",
        )}
        style={{ paddingLeft: padLeft }}
        onClick={handleClick}
      >
        {isDir ? (
          isExpanded
            ? <ChevronDown size={12} className="shrink-0 text-fg-muted" />
            : <ChevronRight size={12} className="shrink-0 text-fg-muted" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <Icon size={14} className="shrink-0" />
        <span className="truncate flex-1">{name}</span>
        {errs > 0 && (
          <span className="text-xs px-1 rounded bg-danger/20 text-danger shrink-0 inline-flex items-center gap-0.5">
            <AlertCircle size={10} />
            {errs}
          </span>
        )}
      </div>
      {isDir && isExpanded && (
        isLoading ? (
          <div
            className="text-xs text-fg-muted py-0.5"
            style={{ paddingLeft: padLeft + 14 + 4 }}
          >
            加载中...
          </div>
        ) : children ? (
          children.length === 0 ? (
            <div
              className="text-xs text-fg-subtle italic py-0.5"
              style={{ paddingLeft: padLeft + 14 + 4 }}
            >
              (空)
            </div>
          ) : (
            children.map((c) => (
              <TreeNode
                key={c.path}
                path={c.path}
                name={c.name}
                isDir={c.is_dir}
                ext={c.ext}
                depth={depth + 1}
                expanded={expanded}
                childrenCache={childrenCache}
                loading={loading}
                selected={selected}
                errorCountFor={errorCountFor}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            ))
          )
        ) : null
      )}
    </>
  );
}

export function Explorer({ root, songs, selected, allErrors, onPickWorkspace, onSelect }: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [childrenCache, setChildrenCache] = useState<Map<string, DirEntryOut[]>>(new Map());
  const [loading, setLoading] = useState<Set<string>>(new Set());

  // 工作区切换：清空展开/缓存
  useEffect(() => {
    setExpanded(new Set());
    setChildrenCache(new Map());
    setLoading(new Set());
  }, [root]);

  const errorCountFor = useCallback(
    (p: string, isDir: boolean) => {
      if (isDir) {
        const sep1 = p + "\\";
        const sep2 = p + "/";
        let n = 0;
        for (const e of allErrors) {
          if (e.path === p || e.path.startsWith(sep1) || e.path.startsWith(sep2)) n++;
        }
        return n;
      }
      let n = 0;
      for (const e of allErrors) if (e.path === p) n++;
      return n;
    },
    [allErrors],
  );

  const loadChildren = useCallback(async (p: string) => {
    setChildrenCache((prevCache) => {
      if (prevCache.has(p)) return prevCache;
      // 异步加载
      setLoading((prev) => {
        if (prev.has(p)) return prev;
        const next = new Set(prev);
        next.add(p);
        listDir(p)
          .then((out) => {
            setChildrenCache((m) => {
              const n = new Map(m);
              n.set(p, out.entries);
              return n;
            });
          })
          .catch(() => {
            setChildrenCache((m) => {
              const n = new Map(m);
              n.set(p, []);
              return n;
            });
          })
          .finally(() => {
            setLoading((s) => {
              const n = new Set(s);
              n.delete(p);
              return n;
            });
          });
        return next;
      });
      return prevCache;
    });
  }, []);

  const onToggle = useCallback(
    (p: string) => {
      setExpanded((prev) => {
        const n = new Set(prev);
        if (n.has(p)) {
          n.delete(p);
        } else {
          n.add(p);
          loadChildren(p);
        }
        return n;
      });
    },
    [loadChildren],
  );

  // 选中变化时自动展开所有祖先目录（来自问题面板跳转或外部 reveal）
  useEffect(() => {
    if (!selected || !root) return;
    const ancestors: string[] = [];
    let cur = selected;
    // 一直向上找父目录直到 root（不含 root 自身）
    while (cur.length > root.length) {
      const idx = Math.max(cur.lastIndexOf("\\"), cur.lastIndexOf("/"));
      if (idx <= 0) break;
      cur = cur.slice(0, idx);
      if (cur.length <= root.length) break;
      ancestors.unshift(cur);
    }
    if (ancestors.length === 0) return;
    setExpanded((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const a of ancestors) {
        if (!next.has(a)) {
          next.add(a);
          changed = true;
          loadChildren(a);
        }
      }
      return changed ? next : prev;
    });
  }, [selected, root, loadChildren]);

  const songEntries = useMemo<DirEntryOut[]>(
    () => songs.map((p) => ({ path: p, name: basename(p), is_dir: true, size_bytes: 0, ext: "" })),
    [songs],
  );

  return (
    <div className="pane">
      <div className="pane-header">{root ? basename(root) : "工作区"}</div>
      <div className="pane-body">
        {!root && (
          <div className="px-3 py-3 flex flex-col gap-2 text-fg-muted">
            <p className="text-[13px] leading-snug">
              你尚未打开工作区文件夹。
            </p>
            <button
              onClick={onPickWorkspace}
              className="btn btn-primary w-full justify-center text-center"
            >
              打开文件夹
            </button>
            <p className="text-xs text-fg-subtle leading-snug mt-1">
              选择包含若干歌曲子文件夹的目录。
            </p>
          </div>
        )}
        {root && songs.length === 0 && (
          <div className="px-3 py-2 text-fg-muted">该工作区下没有歌曲文件夹</div>
        )}
        {root && songEntries.map((c) => (
          <TreeNode
            key={c.path}
            path={c.path}
            name={c.name}
            isDir={c.is_dir}
            ext={c.ext}
            depth={0}
            expanded={expanded}
            childrenCache={childrenCache}
            loading={loading}
            selected={selected}
            errorCountFor={errorCountFor}
            onToggle={onToggle}
            onSelect={onSelect}
          />
        ))}
      </div>
    </div>
  );
}

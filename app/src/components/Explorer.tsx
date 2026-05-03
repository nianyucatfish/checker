import { useEffect, useState, useCallback, useMemo, useRef, useLayoutEffect } from "react";
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
import {
  listDir,
  getAudioDurations,
  renamePath,
  deletePaths,
  copyPaths,
  movePaths,
  revealInFolder,
} from "../api";
import type { DirEntryOut, CheckErrorOut, AudioDurationItem } from "../api";
import { clsx } from "../utils";

interface Props {
  root: string | null;
  songs: string[];                  // 一级歌曲文件夹路径
  selected: string | null;
  selectedIsDir: boolean;
  allErrors: CheckErrorOut[];
  refreshKey: number;               // 父级 bump 时,重拉所有已缓存目录的时长 + 条目
  onPickWorkspace: () => void;
  onSelect: (path: string, isDir: boolean) => void;
  onAutofixSong: (songPath: string) => void;
  onPadSong: (songPath: string) => void;
  // 文件树内部任何写操作(rename / delete / paste)成功后回调
  // 父层用它触发 listWorkspace + 重扫,保证错误同步。
  onMutated: () => void;
}

const AUDIO_EXTS = new Set(["wav", "mp3", "flac", "ogg", "m4a"]);

function basename(p: string) {
  const m = p.split(/[\\/]/);
  return m[m.length - 1] || p;
}

function dirname(p: string) {
  const idx = Math.max(p.lastIndexOf("\\"), p.lastIndexOf("/"));
  return idx <= 0 ? "" : p.slice(0, idx);
}

function joinPath(parent: string, name: string) {
  // 简单拼接;沿用 parent 已有的分隔符风格
  const sep = parent.includes("\\") ? "\\" : "/";
  return parent.endsWith(sep) ? parent + name : parent + sep + name;
}

function fmtDuration(sec: number) {
  const total = Math.floor(sec);
  const mm = Math.floor(total / 60);
  const ss = total % 60;
  return `${String(mm).padStart(2, "0")}:${String(ss).padStart(2, "0")}`;
}

function fileIcon(ext: string) {
  if (AUDIO_EXTS.has(ext)) return FileAudio;
  if (ext === "csv") return FileSpreadsheet;
  if (["mid", "midi"].includes(ext)) return Music3;
  if (["txt", "md", "json", "log"].includes(ext)) return FileText;
  return File;
}

interface ContextMenuState {
  x: number;
  y: number;
  path: string;
  isDir: boolean;
}

interface ClipboardEntry {
  srcs: string[];
  mode: "copy" | "cut";
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
  durations: Map<string, AudioDurationItem | null>;
  inconsistent: Set<string>;
  selected: string | null;
  editing: string | null;
  errorCountFor: (path: string, isDir: boolean) => number;
  onToggle: (path: string) => void;
  onSelect: (path: string, isDir: boolean) => void;
  onContextMenu: (e: React.MouseEvent, path: string, isDir: boolean) => void;
  onRenameCommit: (path: string, isDir: boolean, newName: string) => void;
  onRenameCancel: () => void;
}

function TreeNode(props: NodeProps) {
  const {
    path, name, isDir, ext, depth,
    expanded, childrenCache, loading, durations, inconsistent, selected, editing,
    errorCountFor, onToggle, onSelect, onContextMenu, onRenameCommit, onRenameCancel,
  } = props;

  const isExpanded = expanded.has(path);
  const isLoading = loading.has(path);
  const isSelected = selected === path;
  const isEditing = editing === path;
  const children = childrenCache.get(path);
  const errs = errorCountFor(path, isDir);
  const Icon = isDir ? (isExpanded ? FolderOpen : Folder) : fileIcon(ext);
  const padLeft = 8 + depth * 14;
  const dur = !isDir && AUDIO_EXTS.has(ext) ? durations.get(path) : undefined;
  const isInconsistent = inconsistent.has(path);

  const handleClick = () => {
    if (isEditing) return;
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
        onContextMenu={(e) => onContextMenu(e, path, isDir)}
      >
        {isDir ? (
          isExpanded
            ? <ChevronDown size={12} className="shrink-0 text-fg-muted" />
            : <ChevronRight size={12} className="shrink-0 text-fg-muted" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <Icon size={14} className="shrink-0" />
        {isEditing ? (
          <RenameInput
            initial={name}
            onCommit={(v) => onRenameCommit(path, isDir, v)}
            onCancel={onRenameCancel}
          />
        ) : (
          <span className="truncate flex-1">{name}</span>
        )}
        {!isEditing && dur != null && (
          <span
            className={clsx(
              "text-xs shrink-0 font-mono",
              isInconsistent ? "text-warning font-semibold" : "text-fg-subtle",
            )}
            title={isInconsistent ? "同目录内时长不一致" : undefined}
          >
            {fmtDuration(dur.duration_seconds)}
          </span>
        )}
        {!isEditing && errs > 0 && (
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
                durations={durations}
                inconsistent={inconsistent}
                selected={selected}
                editing={editing}
                errorCountFor={errorCountFor}
                onToggle={onToggle}
                onSelect={onSelect}
                onContextMenu={onContextMenu}
                onRenameCommit={onRenameCommit}
                onRenameCancel={onRenameCancel}
              />
            ))
          )
        ) : null
      )}
    </>
  );
}

function RenameInput({
  initial,
  onCommit,
  onCancel,
}: {
  initial: string;
  onCommit: (v: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLInputElement | null>(null);
  const [val, setVal] = useState(initial);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    // 选中文件名主体(不含扩展名)
    const dot = initial.lastIndexOf(".");
    if (dot > 0) el.setSelectionRange(0, dot);
    else el.select();
  }, [initial]);
  return (
    <input
      ref={ref}
      value={val}
      onChange={(e) => setVal(e.target.value)}
      onClick={(e) => e.stopPropagation()}
      onKeyDown={(e) => {
        e.stopPropagation();
        if (e.key === "Enter") {
          e.preventDefault();
          onCommit(val.trim());
        } else if (e.key === "Escape") {
          e.preventDefault();
          onCancel();
        }
      }}
      onBlur={() => onCommit(val.trim())}
      className="flex-1 bg-bg border border-accent rounded-sm px-1 py-0 text-sm outline-none"
    />
  );
}

interface MenuItem {
  label: string;
  onClick: () => void;
  disabled?: boolean;
  danger?: boolean;
}

function ContextMenu({
  x,
  y,
  items,
  onClose,
}: {
  x: number;
  y: number;
  items: (MenuItem | "sep")[];
  onClose: () => void;
}) {
  // 自动调整位置避免出屏
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState({ x, y });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let nx = x;
    let ny = y;
    if (x + rect.width > window.innerWidth) nx = Math.max(0, window.innerWidth - rect.width - 4);
    if (y + rect.height > window.innerHeight) ny = Math.max(0, window.innerHeight - rect.height - 4);
    if (nx !== x || ny !== y) setPos({ x: nx, y: ny });
  }, [x, y]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  return (
    <div
      ref={ref}
      className="fixed z-50 min-w-[180px] bg-bg-sidebar border border-border rounded-sm shadow-lg py-1 text-sm"
      style={{ left: pos.x, top: pos.y }}
    >
      {items.map((it, i) => {
        if (it === "sep") {
          return <div key={i} className="my-1 border-t border-border-subtle" />;
        }
        return (
          <button
            key={i}
            disabled={it.disabled}
            onClick={() => {
              if (it.disabled) return;
              onClose();
              it.onClick();
            }}
            className={clsx(
              "w-full text-left px-3 py-1 hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed",
              it.danger && "text-danger",
            )}
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

export function Explorer({
  root,
  songs,
  selected,
  selectedIsDir,
  allErrors,
  refreshKey,
  onPickWorkspace,
  onSelect,
  onAutofixSong,
  onPadSong,
  onMutated,
}: Props) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [childrenCache, setChildrenCache] = useState<Map<string, DirEntryOut[]>>(new Map());
  const [loading, setLoading] = useState<Set<string>>(new Set());
  const [durations, setDurations] = useState<Map<string, AudioDurationItem | null>>(new Map());
  const [inconsistent, setInconsistent] = useState<Set<string>>(new Set());
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [clipboard, setClipboard] = useState<ClipboardEntry | null>(null);

  const songsSet = useMemo(() => new Set(songs), [songs]);

  // 工作区切换:清空所有缓存
  useEffect(() => {
    setExpanded(new Set());
    setChildrenCache(new Map());
    setLoading(new Set());
    setDurations(new Map());
    setInconsistent(new Set());
    setContextMenu(null);
    setEditing(null);
    setClipboard(null);
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

  // 拉指定目录的时长(只对其中的音频文件)+ 检测同目录时长不一致
  const fetchDurationsForEntries = useCallback((entries: DirEntryOut[]) => {
    const audioPaths = entries
      .filter((e) => !e.is_dir && AUDIO_EXTS.has(e.ext))
      .map((e) => e.path);
    if (audioPaths.length === 0) return;
    getAudioDurations(audioPaths)
      .then((out) => {
        setDurations((prev) => {
          const next = new Map(prev);
          Object.entries(out.durations).forEach(([k, v]) => next.set(k, v));
          return next;
        });
        // 同目录时长一致性检测:与错误扫描同精度,按采样率分组,组内整数帧比较
        // (项目规则下同 song folder 应该都是 96k,所以一般只有一个分组)
        const groups = new Map<number, { path: string; frames: number }[]>();
        for (const p of audioPaths) {
          const item = out.durations[p];
          if (!item) continue;
          const arr = groups.get(item.samplerate) ?? [];
          arr.push({ path: p, frames: item.frames });
          groups.set(item.samplerate, arr);
        }
        const newOutliers: string[] = [];
        for (const arr of groups.values()) {
          if (arr.length < 2) continue;
          const maxFrames = Math.max(...arr.map((a) => a.frames));
          for (const a of arr) {
            if (a.frames !== maxFrames) newOutliers.push(a.path);
          }
        }
        // 跨采样率(同目录混 sr)是另一种错误,这里也整体标黄
        if (groups.size >= 2) {
          for (const p of audioPaths) {
            if (out.durations[p]) newOutliers.push(p);
          }
        }
        setInconsistent((prev) => {
          // 把这一波的 audioPaths 全部清掉旧标记,然后只重新加 newOutliers
          const next = new Set(prev);
          for (const p of audioPaths) next.delete(p);
          for (const p of newOutliers) next.add(p);
          return next;
        });
      })
      .catch(() => {
        /* 静默 */
      });
  }, []);

  const loadChildren = useCallback(
    async (p: string, force = false) => {
      if (!force && childrenCache.has(p)) return;
      setLoading((prev) => {
        if (prev.has(p)) return prev;
        const next = new Set(prev);
        next.add(p);
        return next;
      });
      try {
        const out = await listDir(p);
        setChildrenCache((m) => {
          const n = new Map(m);
          n.set(p, out.entries);
          return n;
        });
        fetchDurationsForEntries(out.entries);
      } catch {
        setChildrenCache((m) => {
          const n = new Map(m);
          n.set(p, []);
          return n;
        });
      } finally {
        setLoading((s) => {
          const n = new Set(s);
          n.delete(p);
          return n;
        });
      }
    },
    [childrenCache, fetchDurationsForEntries],
  );

  // 目录改动后的热刷新(配合 rename / delete / paste)
  const refreshDir = useCallback(
    async (p: string) => {
      try {
        const out = await listDir(p);
        setChildrenCache((m) => {
          const n = new Map(m);
          n.set(p, out.entries);
          return n;
        });
        fetchDurationsForEntries(out.entries);
      } catch {
        /* ignore */
      }
    },
    [fetchDurationsForEntries],
  );

  // 父级请求强制刷新(autofix / pad 后调用):清掉所有时长 + 不一致缓存,
  // 然后对每个已展开目录重拉 listDir + 时长。
  const childrenCacheRef = useRef(childrenCache);
  useEffect(() => {
    childrenCacheRef.current = childrenCache;
  }, [childrenCache]);
  useEffect(() => {
    if (refreshKey === 0) return; // 初始挂载时跳过
    const dirs = Array.from(childrenCacheRef.current.keys());
    setDurations(new Map());
    setInconsistent(new Set());
    for (const d of dirs) refreshDir(d);
  }, [refreshKey, refreshDir]);

  // 订阅外部文件系统变化(chokidar via Electron main)
  // - 已缓存目录:增量重拉 listDir
  // - 任意变化:让父级触发 listWorkspace + 重扫(同步错误 + 顶层 song 增删)
  // 注意:本组件自己的写操作(rename/delete/paste)也会触发这个回调,造成 onMutated
  // 被调两次,可接受(check_workspace 是幂等的)。
  const onMutatedRef = useRef(onMutated);
  useEffect(() => {
    onMutatedRef.current = onMutated;
  }, [onMutated]);
  useEffect(() => {
    if (!root) return;
    const off = window.electronAPI.onFsChanged((dirs) => {
      for (const d of dirs) {
        if (childrenCacheRef.current.has(d)) refreshDir(d);
      }
      onMutatedRef.current();
    });
    return () => off();
  }, [root, refreshDir]);

  const onToggle = useCallback(
    (p: string) => {
      setExpanded((prev) => {
        const n = new Set(prev);
        if (n.has(p)) n.delete(p);
        else {
          n.add(p);
          loadChildren(p);
        }
        return n;
      });
    },
    [loadChildren],
  );

  // 选中跳转时自动展开祖先
  useEffect(() => {
    if (!selected || !root) return;
    const ancestors: string[] = [];
    let cur = selected;
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

  // ---- 操作:重命名 / 删除 / 复制 / 剪切 / 粘贴 / 资源管理器 ----

  const startRename = (path: string) => {
    setEditing(path);
  };

  const onRenameCommit = useCallback(
    async (path: string, isDir: boolean, newName: string) => {
      setEditing(null);
      const old = basename(path);
      if (!newName || newName === old) return;
      const parent = dirname(path);
      const dst = joinPath(parent, newName);
      try {
        await renamePath(path, dst);
        await refreshDir(parent);
        onSelect(dst, isDir);
        onMutated();
      } catch (e) {
        alert(`重命名失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [refreshDir, onSelect, onMutated],
  );

  const onRenameCancel = useCallback(() => setEditing(null), []);

  const doDelete = useCallback(
    async (paths: string[]) => {
      if (paths.length === 0) return;
      const msg =
        paths.length === 1
          ? `确定将 "${basename(paths[0])}" 移到回收站?`
          : `确定将这 ${paths.length} 个项目移到回收站?`;
      if (!window.confirm(msg)) return;
      try {
        const r = await deletePaths(paths);
        const parents = new Set(paths.map(dirname).filter(Boolean));
        for (const p of parents) await refreshDir(p);
        onMutated();
        if (r.errors.length > 0) {
          alert(`部分删除失败:\n${r.errors.join("\n")}`);
        }
      } catch (e) {
        alert(`删除失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [refreshDir, onMutated],
  );

  const doCopy = (paths: string[]) => setClipboard({ srcs: paths, mode: "copy" });
  const doCut = (paths: string[]) => setClipboard({ srcs: paths, mode: "cut" });

  const doPaste = useCallback(
    async (target: string, targetIsDir: boolean) => {
      if (!clipboard) return;
      const dstDir = targetIsDir ? target : dirname(target);
      if (!dstDir) return;
      try {
        if (clipboard.mode === "copy") {
          await copyPaths(clipboard.srcs, dstDir);
        } else {
          await movePaths(clipboard.srcs, dstDir);
          // 剪切后清空剪贴板
          setClipboard(null);
        }
        // 刷新目标目录 + 源目录(剪切的话源没了)
        const dirs = new Set<string>([dstDir]);
        if (clipboard.mode === "cut") {
          for (const s of clipboard.srcs) {
            const d = dirname(s);
            if (d) dirs.add(d);
          }
        }
        for (const d of dirs) await refreshDir(d);
        onMutated();
      } catch (e) {
        alert(`粘贴失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    },
    [clipboard, refreshDir, onMutated],
  );

  const doReveal = (path: string) => {
    revealInFolder(path).catch(() => {
      /* ignore */
    });
  };

  // ---- 右键菜单 ----

  const onContextMenu = useCallback(
    (e: React.MouseEvent, path: string, isDir: boolean) => {
      e.preventDefault();
      e.stopPropagation();
      onSelect(path, isDir);
      setContextMenu({ x: e.clientX, y: e.clientY, path, isDir });
    },
    [onSelect],
  );

  // ---- 键盘快捷键 ----
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!selected) return;
      // 编辑中或在 input/textarea 时不触发(避免和 rename 输入框 / Monaco / chat 冲突)
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (editing) return;
      if (e.key === "F2") {
        e.preventDefault();
        startRename(selected);
      } else if (e.key === "Delete") {
        e.preventDefault();
        doDelete([selected]);
      } else if (e.ctrlKey && (e.key === "c" || e.key === "C")) {
        e.preventDefault();
        doCopy([selected]);
      } else if (e.ctrlKey && (e.key === "x" || e.key === "X")) {
        e.preventDefault();
        doCut([selected]);
      } else if (e.ctrlKey && (e.key === "v" || e.key === "V")) {
        e.preventDefault();
        doPaste(selected, selectedIsDir);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, selectedIsDir, editing, doDelete, doPaste]);

  const songEntries = useMemo<DirEntryOut[]>(
    () => songs.map((p) => ({ path: p, name: basename(p), is_dir: true, size_bytes: 0, ext: "" })),
    [songs],
  );

  // 构造右键菜单 items
  const menuItems = useMemo<(MenuItem | "sep")[]>(() => {
    if (!contextMenu) return [];
    const { path, isDir } = contextMenu;
    const isSongFolder = songsSet.has(path);
    const ext = path.match(/\.([^.\\/]+)$/)?.[1].toLowerCase() ?? "";
    const isWav = !isDir && ext === "wav";

    const items: (MenuItem | "sep")[] = [
      { label: "重命名", onClick: () => startRename(path) },
      { label: "删除", onClick: () => doDelete([path]), danger: true },
      "sep",
      { label: "复制", onClick: () => doCopy([path]) },
      { label: "剪切", onClick: () => doCut([path]) },
      {
        label: "粘贴",
        onClick: () => doPaste(path, isDir),
        disabled: !clipboard,
      },
      "sep",
      { label: "在资源管理器中显示", onClick: () => doReveal(path) },
    ];

    // 项目级动作
    if (isSongFolder) {
      items.push("sep");
      items.push({ label: "自动修复本项目命名", onClick: () => onAutofixSong(path) });
      items.push({ label: "统一音频长度(尾部补空白)", onClick: () => onPadSong(path) });
    }

    if (isWav) {
      items.push("sep");
      items.push({
        label: "添加到混音台",
        onClick: () => alert("混音台尚未实现"),
        disabled: true,
      });
    } else if (isDir && !isSongFolder) {
      items.push("sep");
      items.push({
        label: "添加文件夹到混音台",
        onClick: () => alert("混音台尚未实现"),
        disabled: true,
      });
    }

    return items;
  }, [contextMenu, clipboard, songsSet, doDelete, doPaste, onAutofixSong, onPadSong]);

  return (
    <div className="pane">
      <div className="pane-header">{root ? basename(root) : "工作区"}</div>
      <div className="pane-body" tabIndex={0}>
        {!root && (
          <div className="px-3 py-3 flex flex-col gap-2 text-fg-muted">
            <p className="text-[13px] leading-snug">你尚未打开工作区文件夹。</p>
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
            durations={durations}
            inconsistent={inconsistent}
            selected={selected}
            editing={editing}
            errorCountFor={errorCountFor}
            onToggle={onToggle}
            onSelect={onSelect}
            onContextMenu={onContextMenu}
            onRenameCommit={onRenameCommit}
            onRenameCancel={onRenameCancel}
          />
        ))}
      </div>
      {contextMenu && (
        <ContextMenu
          x={contextMenu.x}
          y={contextMenu.y}
          items={menuItems}
          onClose={() => setContextMenu(null)}
        />
      )}
    </div>
  );
}

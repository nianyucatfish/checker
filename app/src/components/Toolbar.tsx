import { SheetModal } from "./SheetModal";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { clsx } from "../utils";
import { SettingsModal } from "./SettingsModal";

interface Props {
  hasWorkspace: boolean;
  scanning: boolean;
  applying: boolean;
  rootDir: string | null;
  mixConsoleOpen: boolean;
  onPickWorkspace: () => void;
  onScan: () => void;
  // 传按钮的 client rect,让主进程把混音窗动画从这个位置展开/收缩
  onToggleMixConsole: (rect: { x: number; y: number; w: number; h: number }) => void;
}

interface MenuItem {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

const FILE_LINKS = {
  workflow: "https://wcntr9kdkawk.feishu.cn/wiki/QGILwl3gPiGrhGkIka6cIi4Qncc",
  data_requirements: "https://ai.feishu.cn/docx/DbX8dJLcroIamLxRUi8cwarkn3c?from=from_copylink",
  work_registration: "https://docs.qq.com/sheet/DSUpxbWpOVFZrb3Rx?tab=BB08J2",
};

function DropdownMenu({
  anchorRect,
  items,
  onClose,
}: {
  anchorRect: DOMRect;
  items: (MenuItem | "sep")[];
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number }>({
    x: anchorRect.left,
    y: anchorRect.bottom + 2,
  });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    let nx = anchorRect.left;
    let ny = anchorRect.bottom + 2;
    if (nx + rect.width > window.innerWidth) {
      nx = Math.max(0, window.innerWidth - rect.width - 4);
    }
    if (ny + rect.height > window.innerHeight) {
      ny = Math.max(0, anchorRect.top - rect.height - 2);
    }
    setPos({ x: nx, y: ny });
  }, [anchorRect]);

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
      className="fixed z-50 min-w-[200px] bg-bg-sidebar border border-border rounded-sm shadow-lg py-1 text-sm"
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
            className="w-full text-left px-3 py-1 hover:bg-bg-hover disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

function ToolbarBtn({
  label,
  onClick,
  disabled,
  active,
  withSpinner,
}: {
  label: string;
  onClick?: (e: React.MouseEvent) => void;
  disabled?: boolean;
  active?: boolean;
  withSpinner?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      className={clsx(
        "h-7 px-2.5 inline-flex items-center gap-1.5 rounded-sm",
        "text-fg-muted hover:text-fg hover:bg-bg-hover",
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent",
        active && "text-fg bg-bg-hover",
      )}
    >
      {withSpinner && <Loader2 size={12} className="animate-spin" />}
      <span>{label}</span>
    </button>
  );
}

export function Toolbar({
  hasWorkspace,
  scanning,
  applying,
  rootDir,
  mixConsoleOpen,
  onPickWorkspace,
  onScan,
  onToggleMixConsole,
}: Props) {
  const [openMenu, setOpenMenu] = useState<"file" | "help" | "sheet" | "dev" | null>(null);
  const [showSheet, setShowSheet] = useState(false);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const fileBtnRef = useRef<HTMLButtonElement | null>(null);
  const helpBtnRef = useRef<HTMLButtonElement | null>(null);
  const devBtnRef = useRef<HTMLButtonElement | null>(null);

  // 分工表与开发者操作共用结果弹窗。
  type DevResult =
    | { kind: "text"; title: string; body: string; error: boolean }
    | { kind: "table"; title: string; columns: string[]; rows: string[][] };
  const [devResult, setDevResult] = useState<DevResult | null>(null);
  const [devBusy, setDevBusy] = useState(false);
  const [copyStatus, setCopyStatus] = useState("");
  useEffect(() => { setCopyStatus(""); }, [devResult]);
  const copyResult = async (text: string) => {
    try {
      await window.electronAPI.clipboardWriteText(text);
      setCopyStatus("已复制");
    } catch { setCopyStatus("复制失败，请重试"); }
  };
  const [personalEnabled, setPersonalEnabled] = useState(false);
  useEffect(() => { window.electronAPI.agentGetPersonalEnabled().then(setPersonalEnabled).catch(() => {}); }, []);
  const [taskDraft, setTaskDraft] = useState<string | null>(null);
  const [taskError, setTaskError] = useState("");
  const [taskSaving, setTaskSaving] = useState(false);
  const editTaskPrompt = async () => {
    try {
      const text = await window.electronAPI.agentGetTaskPrompt();
      setTaskError("");
      setTaskDraft(text);
    } catch (e) {
      setDevResult({ kind: "text", title: "读取当前任务失败", body: String(e), error: true });
    }
  };
  const saveTaskPrompt = async () => {
    if (taskDraft === null || taskSaving) return;
    setTaskSaving(true);
    try {
      await window.electronAPI.agentSetTaskPrompt(taskDraft);
      setTaskDraft(null);
    } catch (e) { setTaskError(String(e)); }
    finally { setTaskSaving(false); }
  };

  // 录制 LLM 上下文开关。mount 时从 main 拿当前值,优先用 localStorage 覆盖一次
  // (重启 Electron 后 main 端会回到环境变量默认,renderer 端持久化做"上次怎么开就怎么开")。
  const DUMP_LLM_LS_KEY = "audio_qc.dump_llm";
  const [dumpLlmOn, setDumpLlmOn] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const saved = localStorage.getItem(DUMP_LLM_LS_KEY);
      if (saved === "1") {
        await window.electronAPI.agentSetDumpLlm(true);
        if (!cancelled) setDumpLlmOn(true);
        return;
      }
      if (saved === "0") {
        await window.electronAPI.agentSetDumpLlm(false);
        if (!cancelled) setDumpLlmOn(false);
        return;
      }
      // localStorage 没值 → 用 main 当前值(可能来自环境变量)
      const cur = await window.electronAPI.agentGetDumpLlm();
      if (!cancelled) setDumpLlmOn(cur);
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  const toggleDumpLlm = async () => {
    const next = !dumpLlmOn;
    await window.electronAPI.agentSetDumpLlm(next);
    localStorage.setItem(DUMP_LLM_LS_KEY, next ? "1" : "0");
    setDumpLlmOn(next);
  };

  const runDev = async (title: string, fn: () => Promise<unknown>) => {
    if (devBusy) return;
    setDevBusy(true);
    try {
      const out = await fn();
      setDevResult({
        kind: "text",
        title,
        body: JSON.stringify(out, null, 2),
        error: false,
      });
    } catch (e) {
      setDevResult({
        kind: "text",
        title,
        body: e instanceof Error ? e.message : String(e),
        error: true,
      });
    } finally {
      setDevBusy(false);
    }
  };

  // F5 全量扫描快捷键
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea") return;
      if (e.key === "F5" && hasWorkspace && !scanning && !applying) {
        e.preventDefault();
        onScan();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [hasWorkspace, scanning, applying, onScan]);

  // ⚠️ 临时:dev 弹窗 Esc 关闭
  useEffect(() => {
    if (!devResult) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDevResult(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [devResult]);

  const showFileMenu = (e: React.MouseEvent) => {
    setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
    setOpenMenu("file");
  };
  const showHelpMenu = (e: React.MouseEvent) => {
    setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
    setOpenMenu("help");
  };
  const showDevMenu = (e: React.MouseEvent) => {
    setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
    setOpenMenu("dev");
  };
  const closeMenu = () => {
    setOpenMenu(null);
    setAnchor(null);
  };

  const fileMenuItems: (MenuItem | "sep")[] = [
    {
      label: "从文件夹打开工作区...",
      onClick: onPickWorkspace,
    },
    "sep",
    {
      label: "在资源管理器中显示根目录",
      onClick: () => {
        if (rootDir) window.electronAPI.openPath(rootDir);
      },
      disabled: !rootDir,
    },
  ];

  const helpMenuItems: (MenuItem | "sep")[] = [
    {
      label: "数据检查流程",
      onClick: () => window.electronAPI.openExternal(FILE_LINKS.workflow),
    },
    {
      label: "数据要求",
      onClick: () => window.electronAPI.openExternal(FILE_LINKS.data_requirements),
    },
    {
      label: "分工登记表",
      onClick: () => window.electronAPI.openExternal(FILE_LINKS.work_registration),
    },
  ];

  // ⚠️ 临时开发者菜单 —— 工具齐了删掉
  const devMenuItems: (MenuItem | "sep")[] = [
    { label: "拉取质检提示词", onClick: () => runDev("质检提示词版本", () => window.electronAPI.agentPullPrompts()) },
    "sep",
    ...(personalEnabled ? [{ label: "编辑当前任务提示词…", onClick: () => { void editTaskPrompt(); } }, "sep" as const] : []),
    {
      // 调试 GA 压缩 / trail 用:开启后每轮 LLM 调用前 dump 完整 messages + tools 到
      // tmp/agent_contexts/<chatId>_t<NNN>.txt。对比 t4 vs t5 可看出软压效果。
      label: dumpLlmOn
        ? "录制 LLM 上下文:已开启 → tmp/agent_contexts/"
        : "录制 LLM 上下文:未开启",
      onClick: () => {
        void toggleDumpLlm();
      },
    },
  ];

  return (
    <div className="h-9 border-b border-border bg-bg-sidebar flex items-center px-2 gap-1 shrink-0">
      <button
        ref={fileBtnRef}
        onClick={showFileMenu}
        className={clsx(
          "h-7 px-2.5 inline-flex items-center rounded-sm",
          "text-fg-muted hover:text-fg hover:bg-bg-hover",
          openMenu === "file" && "text-fg bg-bg-hover",
        )}
      >
        文件
      </button>
      <ToolbarBtn
        label="扫描"
        onClick={onScan}
        disabled={!hasWorkspace || scanning || applying}
        active={scanning}
        withSpinner={scanning}
      />
      <ToolbarBtn
        label="混音台"
        onClick={(e) => {
          const r = (e.currentTarget as HTMLButtonElement).getBoundingClientRect();
          onToggleMixConsole({ x: r.left, y: r.top, w: r.width, h: r.height });
        }}
        disabled={!hasWorkspace}
        active={mixConsoleOpen}
      />
      <ToolbarBtn label="分工表" active={showSheet} onClick={() => setShowSheet(true)} />
      <button
        ref={helpBtnRef}
        onClick={showHelpMenu}
        className={clsx(
          "h-7 px-2.5 inline-flex items-center rounded-sm",
          "text-fg-muted hover:text-fg hover:bg-bg-hover",
          openMenu === "help" && "text-fg bg-bg-hover",
        )}
      >
        帮助
      </button>
      <button
        onClick={() => setShowSettings(true)}
        title="设置(API / 模型 / 腾讯文档)"
        className={clsx(
          "h-7 px-2.5 inline-flex items-center rounded-sm",
          "text-fg-muted hover:text-fg hover:bg-bg-hover",
        )}
      >
        设置
      </button>
      {/* ⚠️ 临时开发者菜单 —— 工具齐了删除 */}
      <button
        ref={devBtnRef}
        onClick={showDevMenu}
        title="临时开发者测试菜单"
        className={clsx(
          "h-7 px-2.5 inline-flex items-center gap-1 rounded-sm",
          "text-yellow-600 dark:text-yellow-500 hover:bg-bg-hover",
          openMenu === "dev" && "bg-bg-hover",
        )}
      >
        {devBusy && <Loader2 size={12} className="animate-spin" />}
        <span>开发者</span>
      </button>

      {/* 占位 */}
      <div className="flex-1" />

      {/* 提示文字:F5 快捷键 */}
      <span className="text-xs text-fg-subtle hidden md:inline">
        {scanning ? "扫描中..." : applying ? "操作中..." : "F5 重新扫描"}
      </span>

      {openMenu === "file" && anchor && (
        <DropdownMenu anchorRect={anchor} items={fileMenuItems} onClose={closeMenu} />
      )}
      {openMenu === "help" && anchor && (
        <DropdownMenu anchorRect={anchor} items={helpMenuItems} onClose={closeMenu} />
      )}
      {openMenu === "dev" && anchor && (
        <DropdownMenu anchorRect={anchor} items={devMenuItems} onClose={closeMenu} />
      )}
      {/* ⚠️ 临时:开发者结果弹窗 (text=JSON / table=表格) */}
      {taskDraft !== null && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8" role="dialog" aria-modal="true" aria-labelledby="task-prompt-title">
          <div className="bg-bg-sidebar border border-border rounded-md flex flex-col max-w-3xl w-full p-4 gap-3">
            <h2 id="task-prompt-title" className="text-sm font-medium text-fg">当前任务提示词</h2>
            <p className="text-xs text-fg-muted">保存后应用于所有会话的下一次模型请求，放在系统提示词开头介绍之后，以“当前任务：”标识。留空并保存即可移除，重启后保留。</p>
            <textarea aria-label="当前任务提示词" autoFocus value={taskDraft} onChange={(e) => setTaskDraft(e.target.value)} disabled={taskSaving} maxLength={100000}
              className="h-[45vh] min-h-48 bg-bg border border-border rounded p-3 text-sm text-fg resize-y selectable" />
            {taskError && <p role="alert" className="text-xs text-red-500">{taskError}</p>}
            <div className="flex justify-end gap-3 text-sm">
              <button disabled={taskSaving} onClick={() => setTaskDraft(null)} className="text-fg-muted">取消</button>
              <button disabled={taskSaving} onClick={() => { void saveTaskPrompt(); }} className="rounded bg-accent px-3 py-1 text-white">{taskSaving ? "保存中…" : "保存"}</button>
            </div>
          </div>
        </div>
      )}
      {devResult && (
        <div
          className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8"
          onClick={() => setDevResult(null)}
        >
          <div
            className="bg-bg-sidebar border border-border rounded-md flex flex-col max-w-5xl w-full max-h-[85vh]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between px-4 py-2 border-b border-border">
              <div
                className={clsx(
                  "text-sm font-medium",
                  devResult.kind === "text" && devResult.error
                    ? "text-red-500"
                    : "text-fg",
                )}
              >
                {devResult.kind === "text" && devResult.error ? "✗ " : "✓ "}
                {devResult.title}
              </div>
              <div className="flex items-center gap-3">
                <span role="status" className="text-xs text-fg-muted">{copyStatus}</span>
                <button
                  onClick={() => void copyResult(devResult.kind === "text" ? devResult.body :
                    [devResult.columns, ...devResult.rows].map(row => row.map(cell => /[\t\r\n"]/.test(cell) ? `"${cell.replace(/"/g, '\"\"')}"` : cell).join("\t")).join("\n"))}
                  className="text-xs text-fg-muted hover:text-fg px-2 py-0.5 rounded hover:bg-bg-hover"
                >{devResult.kind === "table" ? "复制表格" : "复制内容"}</button>
              <button
                onClick={() => setDevResult(null)}
                className="text-xs text-fg-muted hover:text-fg px-2 py-0.5 rounded hover:bg-bg-hover"
              >
                关闭 (Esc)
              </button>
              </div>
            </div>
            {devResult.kind === "text" ? (
              <pre className="flex-1 overflow-auto p-3 text-xs font-mono text-fg whitespace-pre-wrap break-all selectable">
                {devResult.body}
              </pre>
            ) : devResult.rows.length === 0 ? (
              <div className="flex-1 flex items-center justify-center text-sm text-fg-muted p-8">
                没有数据
              </div>
            ) : (
              <div className="flex-1 overflow-auto selectable">
                <table className="w-full text-xs border-collapse">
                  <thead className="sticky top-0 bg-bg-sidebar border-b border-border">
                    <tr>
                      {devResult.columns.map((c, i) => (
                        <th
                          key={i}
                          className="text-left font-medium text-fg-muted px-3 py-2 whitespace-nowrap"
                        >
                          {c}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {devResult.rows.map((row, ri) => (
                      <tr
                        key={ri}
                        className="border-b border-border-subtle hover:bg-bg-hover"
                      >
                        {row.map((cell, ci) => (
                          <td
                            key={ci}
                            className={clsx(
                              "px-3 py-1.5 text-fg align-top",
                              ci === 0 && "font-mono text-fg-muted tabular-nums",
                            )}
                          >
                            <button
                              title="点击复制单元格"
                              onClick={() => void copyResult(cell)}
                              className="text-left whitespace-pre-wrap hover:underline"
                            >{cell || "—"}</button>
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      )}

      {showSheet && <SheetModal onClose={() => setShowSheet(false)} />}
      {showSettings && <SettingsModal onClose={() => setShowSettings(false)} />}
    </div>
  );
}

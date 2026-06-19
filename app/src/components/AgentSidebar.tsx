import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  Pencil,
  Plus,
  Send,
  Square,
  Trash2,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AgentEvent, HydratedTurn, SessionInfo } from "../api";

// 通过 IPC 接 Electron main 的 AgentRunner;事件 → Turn 流水。
// chatId 持久化在 localStorage,mount 时调 agentHydrate 把 db 里的历史 turns 拉回来。

interface UserTurn { kind: "user"; text: string }
interface AssistantTurn { kind: "assistant"; text: string }
interface ToolTurn { kind: "tool"; name: string; args: unknown; result?: unknown }
interface PhaseTurn { kind: "phase"; label: string }
interface ErrorTurn { kind: "error"; text: string }
interface HumanCheckDecision { question: string; options: string[] }
interface HumanCheckAnswer { choice: string; note: string }
interface HumanCheckTurn {
  kind: "human_check";
  state?: string;
  reason?: string;
  decisions: HumanCheckDecision[];
  resolved?: { answers: HumanCheckAnswer[]; cancelled: boolean };
}
type Turn = UserTurn | AssistantTurn | ToolTurn | PhaseTurn | ErrorTurn | HumanCheckTurn;

function AgentAvatar() {
  return (
    <div className="grid size-7 shrink-0 place-items-center rounded-full border border-border bg-bg shadow-sm">
      <Bot size={15} className="text-fg-muted" />
    </div>
  );
}

function stripSummary(text: string): string {
  return text.replace(/<summary>[\s\S]*?<\/summary>/g, "").trim();
}

function compact(value: unknown, max = 200): string {
  let s: string;
  try { s = typeof value === "string" ? value : JSON.stringify(value); }
  catch { s = String(value); }
  if (!s) return "";
  return s.length > max ? s.slice(0, max) + "…" : s;
}

// 工具参数压成一行 "k=v, k=v"。空对象 → 空字符串(让 "name()" 而不是 "name({})")
function fmtArgs(args: unknown, max = 100): string {
  if (args == null) return "";
  if (typeof args !== "object") return compact(args, max);
  const entries = Object.entries(args as Record<string, unknown>);
  if (entries.length === 0) return "";
  const parts = entries.map(([k, v]) => {
    const vs = typeof v === "string" ? `"${v}"` : compact(v, 40);
    return `${k}=${vs}`;
  });
  return compact(parts.join(", "), max);
}

// 工具结果摘要:常见结构(list/dict)给出形状信息,长 string 截断。
function fmtResult(result: unknown, max = 160): string {
  if (result == null) return "";
  if (typeof result === "string") return compact(result, max);
  if (typeof result !== "object") return String(result);
  const r = result as Record<string, unknown>;
  // 典型字段: ok / songs / errors / path / text / by_code
  const ok = "ok" in r ? (r.ok ? "ok" : "fail") : null;
  const hints: string[] = [];
  if (ok) hints.push(ok);
  for (const k of ["code", "message", "path", "song_name"]) {
    if (typeof r[k] === "string" && r[k]) hints.push(`${k}=${compact(r[k], 50)}`);
  }
  for (const k of ["songs", "errors", "entries", "items"]) {
    if (Array.isArray(r[k])) hints.push(`${k}: ${(r[k] as unknown[]).length} 项`);
  }
  if ("by_code" in r && typeof r.by_code === "object" && r.by_code) {
    const codes = Object.entries(r.by_code as Record<string, number>)
      .map(([k, v]) => `${k}=${v}`).join(", ");
    if (codes) hints.push(`by_code{${codes}}`);
  }
  if (hints.length > 0) return compact(hints.join(" · "), max);
  return compact(r, max);
}

function HumanCheckCard({
  turn,
  onResolve,
}: {
  turn: HumanCheckTurn;
  onResolve: (payload: { answers: HumanCheckAnswer[]; cancelled?: boolean }) => void;
}) {
  const total = turn.decisions.length;
  const [answers, setAnswers] = useState<HumanCheckAnswer[]>(() =>
    turn.decisions.map(() => ({ choice: "", note: "" })),
  );
  const [page, setPage] = useState(0);
  // useRef 必须在任何 early return 之前调,否则 resolved 后 hook 数变了 React 会崩。
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const isResolved = !!turn.resolved;
  if (isResolved) {
    const r = turn.resolved!;
    return (
      <div className="rounded-lg border border-border bg-bg-sidebar/60 p-3 text-xs text-fg-muted selectable space-y-1">
        <div className="flex items-center justify-between">
          <span>人工选择 · {turn.state ?? ""}</span>
          <span className={r.cancelled ? "text-fg-subtle" : "text-fg"}>
            {r.cancelled ? `⛔ cancel · ${r.answers.length}/${total}` : `✅ ${total}/${total}`}
          </span>
        </div>
        {r.answers.map((a, i) => (
          <div key={i} className="flex gap-2 break-all">
            <span className="text-fg-subtle shrink-0">Q{i + 1}</span>
            <div className="min-w-0">
              <div className="text-fg-muted">{turn.decisions[i]?.question}</div>
              <div className="text-fg">
                {a.choice && <span>{a.choice}</span>}
                {a.note && (
                  <span className={a.choice ? "text-fg-subtle"  : ""}>
                    {a.choice ? ` — ${a.note}` : a.note}
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>
    );
  }

  if (total === 0) {
    return (
      <div className="rounded-lg border-2 border-danger/40 bg-bg p-3 text-xs text-danger">
        human_check 调用缺 decisions
      </div>
    );
  }

  const cur = turn.decisions[page];
  const curAns = answers[page];
  // 含 "note" 字样的选项(如 prompt 模板里的"有小问题(详见 note)")要求用户
  // 先在 textarea 写明细再翻页,不立刻提交。其他选项保留秒翻。
  const needsNote = (opt: string) => opt.toLowerCase().includes("note");

  const updateCur = (patch: Partial<HumanCheckAnswer>) => {
    setAnswers((arr) => arr.map((a, i) => (i === page ? { ...a, ...patch } : a)));
  };

  const isLast = page === total - 1;
  const canSubmit = !!(curAns.choice || curAns.note.trim());

  const advanceOrSubmit = (next: HumanCheckAnswer) => {
    const nextAll = answers.map((a, i) => (i === page ? next : a));
    setAnswers(nextAll);
    if (isLast) onResolve({ answers: nextAll });
    else setPage(page + 1);
  };

  return (
    <div className="rounded-lg border-2 border-accent/60 bg-bg p-3 shadow-sm space-y-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-xs font-semibold text-fg shrink-0">人工确认</span>
          {turn.state && (
            <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-accent/15 text-accent shrink-0">
              {turn.state}
            </span>
          )}
        </div>
        <span className="text-[11px] font-mono text-fg-subtle shrink-0">
          {page + 1} / {total}
        </span>
      </div>
      {turn.reason && page === 0 && (
        <div className="text-xs text-fg-muted leading-5 whitespace-pre-wrap break-all selectable">
          {turn.reason}
        </div>
      )}
      <div className="text-xs text-fg leading-5 whitespace-pre-wrap break-all font-medium selectable">
        {cur.question}
      </div>
      {cur.options.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {cur.options.map((opt, k) => {
            const active = curAns.choice === opt;
            return (
              <button
                key={k}
                onClick={() => {
                  if (needsNote(opt)) {
                    // 点了"含 note"选项 → 先存 choice,把焦点送到 textarea 让用户写明细
                    updateCur({ choice: opt });
                    setTimeout(() => textareaRef.current?.focus(), 0);
                  } else {
                    advanceOrSubmit({ choice: opt, note: curAns.note });
                  }
                }}
                className={`text-left text-xs px-2 py-1.5 rounded border transition-colors break-all ${
                  active
                    ? "border-accent bg-accent/15 text-fg"
                    : "border-border bg-bg-sidebar/40 text-fg hover:bg-bg-hover"
                }`}
              >
                {opt}
              </button>
            );
          })}
        </div>
      )}
      <textarea
        ref={textareaRef}
        value={curAns.note}
        onChange={(e) => updateCur({ note: e.target.value })}
        placeholder={
          needsNote(curAns.choice)
            ? "请在此写明具体小问题,写完点右下「下一题 →」提交"
            : "或自填(可与选项并存,或单独提交)..."
        }
        rows={2}
        className={`w-full text-xs rounded border bg-bg px-2 py-1.5 outline-none focus:border-accent selectable ${
          needsNote(curAns.choice)
            ? "border-accent ring-1 ring-accent/30"
            : "border-border"
        }`}
      />
      <div className="flex gap-2 items-center pt-1">
        <button
          onClick={() => setPage(page - 1)}
          disabled={page === 0}
          className="text-xs px-2 py-1 rounded text-fg-muted hover:bg-bg-hover disabled:opacity-30"
        >
          ← 上一题
        </button>
        <button
          onClick={() => onResolve({ answers, cancelled: true })}
          className="text-xs px-2 py-1 rounded text-fg-subtle hover:bg-bg-hover"
        >
          ⛔ 全部取消
        </button>
        <div className="flex-1" />
        <button
          disabled={!canSubmit}
          onClick={() => advanceOrSubmit(curAns)}
          className="text-xs px-2 py-1 rounded bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-40"
        >
          {isLast ? "提交全部" : "下一题 →"}
        </button>
      </div>
    </div>
  );
}

function relTime(ms: number): string {
  const dt = Date.now() - ms;
  if (dt < 60_000) return "刚刚";
  if (dt < 3_600_000) return `${Math.floor(dt / 60_000)} 分钟前`;
  if (dt < 86_400_000) return `${Math.floor(dt / 3_600_000)} 小时前`;
  if (dt < 7 * 86_400_000) return `${Math.floor(dt / 86_400_000)} 天前`;
  return new Date(ms).toLocaleDateString();
}

export function AgentSidebar() {
  // chatId 持久化在 localStorage —— renderer reload / Electron 重启后能续上当前会话
  // (db 是真相源,chatId 只是指针)。改成 useState 以支持新建 / 切换会话。
  const [chatId, setChatId] = useState<string>(() => {
    const saved = localStorage.getItem("audio_qc.chat_id");
    if (saved) return saved;
    const fresh = `chat_${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    localStorage.setItem("audio_qc.chat_id", fresh);
    return fresh;
  });
  // ref 跟 chatId 同步:onAgentEvent 回调里需要拿最新 id 过滤事件,但 listener 闭包
  // 只在 mount 时订阅一次,用 ref 避免每次切换都重新订阅。
  const chatIdRef = useRef(chatId);
  useEffect(() => {
    chatIdRef.current = chatId;
    localStorage.setItem("audio_qc.chat_id", chatId);
  }, [chatId]);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const composerRef = useRef<HTMLTextAreaElement | null>(null);
  // cancel 已发出但 turn_done 还没回来的窗口:按钮 disable + 灰掉,避免双击
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [song, setSong] = useState<string | null>(null);
  const [expandedTools, setExpandedTools] = useState<Set<number>>(new Set());

  // 会话管理:list + dropdown 展开状态
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  // inline 重命名状态:点 ✎ 按钮时把对应 id 设进来,显示 input
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renamingDraft, setRenamingDraft] = useState("");
  const sessionTriggerRef = useRef<HTMLButtonElement | null>(null);
  const dropdownRef = useRef<HTMLDivElement | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const list = await window.electronAPI.agentListSessions();
      setSessions(list);
    } catch (e) {
      console.warn("[agent] list sessions failed:", e);
    }
  }, []);

  // dropdown 打开时刷一次列表
  useEffect(() => {
    if (showDropdown) void refreshSessions();
  }, [showDropdown, refreshSessions]);

  // 点 dropdown 外面关闭(挂在 document mousedown)
  useEffect(() => {
    if (!showDropdown) return;
    const onDocMouseDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        dropdownRef.current &&
        !dropdownRef.current.contains(target) &&
        !sessionTriggerRef.current?.contains(target)
      ) {
        setShowDropdown(false);
        setRenamingId(null);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [showDropdown]);

  const currentTitle = useMemo(
    () => sessions.find((s) => s.id === chatId)?.title ?? null,
    [sessions, chatId],
  );
  const toggleTool = (i: number) => {
    setExpandedTools((prev) => {
      const s = new Set(prev);
      if (s.has(i)) s.delete(i); else s.add(i);
      return s;
    });
  };

  // 切换 chatId(含首次 mount)→ 清 UI 瞬态状态 + 从 db hydrate 该会话
  useEffect(() => {
    setTurns([]);
    setSong(null);
    setError(null);
    setSending(false);
    setCancelling(false);
    setExpandedTools(new Set());
    let cancelled = false;
    void (async () => {
      try {
        const h = await window.electronAPI.agentHydrate(chatId);
        if (cancelled) return;
        if (h.song) setSong(h.song);
        // HydratedTurn shape 与本组件 Turn 部分对齐;tool / user / assistant / phase 直接复用
        const restored: Turn[] = (h.turns as HydratedTurn[]).map((t) => {
          if (t.kind === "tool") return { kind: "tool", name: t.name, args: t.args, result: t.result };
          if (t.kind === "phase") return { kind: "phase", label: t.label };
          if (t.kind === "user") return { kind: "user", text: t.text };
          return { kind: "assistant", text: t.text };
        });
        if (restored.length > 0) setTurns(restored);
      } catch (e) {
        console.warn("[agent] hydrate failed:", e);
      }
    })();
    return () => { cancelled = true; };
  }, [chatId]);

  // 当前 assistant 累积文本(同一轮可能多次 assistant_text,实际现在只发一次,
  // 但留 ref 方便以后切流式)
  const currentAssistantRef = useRef<string | null>(null);

  useEffect(() => {
    const off = window.electronAPI.onAgentEvent((ev: AgentEvent) => {
      if (ev.chatId !== chatIdRef.current) return;
      switch (ev.type) {
        case "assistant_text": {
          const text = stripSummary(((ev.data as { text: string }).text) ?? "");
          if (text) setTurns((t) => [...t, { kind: "assistant", text }]);
          currentAssistantRef.current = null;
          break;
        }
        case "tool_use": {
          const d = ev.data as { name: string; args: unknown };
          setTurns((t) => [...t, { kind: "tool", name: d.name, args: d.args }]);
          break;
        }
        case "tool_result": {
          const d = ev.data as { name: string; result: unknown };
          setTurns((t) => {
            // 把最后一条同名 tool turn 补上 result
            for (let i = t.length - 1; i >= 0; i--) {
              const cur = t[i];
              if (cur.kind === "tool" && cur.name === d.name && cur.result === undefined) {
                const next = t.slice();
                next[i] = { ...cur, result: d.result };
                return next;
              }
            }
            return [...t, { kind: "tool", name: d.name, args: null, result: d.result }];
          });
          break;
        }
        case "phase_change": {
          const d = ev.data as { song: string };
          setSong(d.song);
          setTurns((t) => [...t, { kind: "phase", label: `进入质检流程 · ${d.song}` }]);
          break;
        }
        case "turn_done":
          setSending(false);
          setCancelling(false);
          break;
        case "compacted": {
          const d = ev.data as { reason: string; dropped?: number; from?: string; to?: string };
          const label =
            d.reason === "song_switch"
              ? `切歌硬压缩 · ${d.from ?? "?"} → ${d.to ?? "?"}`
              : `软压缩 · 丢弃 ${d.dropped ?? 0} 条历史消息`;
          setTurns((t) => [...t, { kind: "phase", label }]);
          break;
        }
        case "awaiting_human": {
          setSending(false);
          const d = (ev.data ?? {}) as {
            state?: string;
            reason?: string;
            decisions?: unknown;
          };
          const rawDecisions = Array.isArray(d.decisions) ? d.decisions : [];
          const decisions: HumanCheckDecision[] = rawDecisions
            .map((x) => {
              const o = x as { question?: unknown; options?: unknown };
              const question = typeof o.question === "string" ? o.question : "";
              const options = Array.isArray(o.options)
                ? o.options.map(String).filter(Boolean)
                : [];
              return { question, options };
            })
            .filter((d) => d.question);
          setTurns((t) => [
            ...t,
            {
              kind: "human_check",
              state: typeof d.state === "string" ? d.state : undefined,
              reason: typeof d.reason === "string" ? d.reason : undefined,
              decisions,
            },
          ]);
          break;
        }
        case "error":
          setSending(false);
          setError(compact(ev.data, 500));
          break;
      }
    });
    return off;
  }, []);

  const visible = useMemo(() => turns, [turns]);
  const hasConversation = visible.length > 0;

  // ============ 滚动跟踪 ============
  // 行为:模型回复 / 工具事件来时自动滚到底;**用户手动往上翻就停止跟踪**,直到下次
  // 用户发消息(回到"最新"语义)。stick-to-bottom 经典模式。
  const scrollRef = useRef<HTMLDivElement | null>(null);
  // 用 ref 避 scroll 事件每次 setState 重渲染;只在送 turns 变更副作用时读它
  const pinnedRef = useRef(true);
  const scrollToBottom = (smooth = true) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: smooth ? "smooth" : "auto" });
  };
  const onScroll = () => {
    const el = scrollRef.current;
    if (!el) return;
    const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
    // 24px 阈值容差,避免 sub-pixel 抖动让 pinned 反复切
    pinnedRef.current = distFromBottom < 24;
  };
  // 任何 turns / sending 变更 → 若仍 pinned,跟到底
  useEffect(() => {
    if (pinnedRef.current) scrollToBottom(true);
  }, [turns, sending]);
  // hydrate 完毕时(turns 从空变非空)无动画跳一次底,避免 reload 看到滚动动画
  useEffect(() => {
    if (turns.length > 0 && pinnedRef.current) {
      // 等下一帧 DOM 已塞内容再跳
      requestAnimationFrame(() => scrollToBottom(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    const text = input.trim();
    if (!text || sending) return;
    // 首发自动标题:本会话还没 user 消息 + 标题是默认值 → 用前 24 字命名
    const hasUserMsg = turns.some((t) => t.kind === "user");
    if (!hasUserMsg) {
      const cur = sessions.find((s) => s.id === chatId);
      const isDefault =
        !cur || !cur.title || cur.title === "新会话" || cur.title === "chat";
      if (isDefault) {
        const auto = text.slice(0, 24).replace(/\s+/g, " ");
        void window.electronAPI
          .agentRenameSession(chatId, auto)
          .then(() => refreshSessions())
          .catch((err) => console.warn("[agent] auto rename failed:", err));
      }
    }
    setTurns((t) => [...t, { kind: "user", text }]);
    setInput("");
    setError(null);
    setSending(true);
    setCancelling(false);
    // 用户发消息 = 主动回到"最新",恢复跟踪;先 pin 再滚,等 turns 写入后 effect 也会再滚一次,等价 idempotent
    pinnedRef.current = true;
    scrollToBottom(true);
    try {
      await window.electronAPI.agentSend(chatId, text);
    } catch (err) {
      setSending(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  // 中断当前对话:设 cancelling flag,按钮立刻灰掉防双击;实际状态恢复等 turn_done。
  // 后端 AgentRunner.cancel 把 cancelled=true,下一次 runLoop 循环检查 + 唤醒挂起的 human_check。
  function onCancel() {
    if (!sending || cancelling) return;
    setCancelling(true);
    window.electronAPI.agentCancel(chatId).catch((err) => {
      console.warn("[agent] cancel failed:", err);
      setCancelling(false);
    });
  }

  // ============ 会话管理 handlers ============
  async function onNewSession() {
    try {
      const s = await window.electronAPI.agentNewSession();
      setShowDropdown(false);
      setChatId(s.id); // hydrate effect 会自动清状态 + 拉空 db 历史
      await refreshSessions();
    } catch (e) {
      setError(`新建会话失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function onSelectSession(id: string) {
    if (id === chatId) {
      setShowDropdown(false);
      return;
    }
    // 切走时不 cancel 旧会话 —— 后台继续跑,turn_done 会落 db,切回来能 hydrate 看到结果
    setShowDropdown(false);
    setChatId(id);
  }

  async function onRenameCommit(id: string) {
    const title = renamingDraft.trim();
    setRenamingId(null);
    setRenamingDraft("");
    if (!title) return;
    try {
      await window.electronAPI.agentRenameSession(id, title);
      await refreshSessions();
    } catch (e) {
      setError(`重命名失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function onDeleteSession(id: string) {
    if (!confirm("确定删除这个会话?所有消息记录会一并清除。")) return;
    try {
      await window.electronAPI.agentDeleteSession(id);
      const remaining = sessions.filter((s) => s.id !== id);
      // 如果删的是当前会话,要么切到其他会话,要么新建一个
      if (id === chatId) {
        // 当前 chat 已 dropChat,sending/cancelling 不再有意义 — 强制复位,
        // 不然旧 turn 的 turn_done 事件可能因为 chatId 不匹配而被过滤掉,
        // sending 会卡 true。
        setSending(false);
        setCancelling(false);
        if (remaining.length > 0) {
          setChatId(remaining[0].id);
        } else {
          const s = await window.electronAPI.agentNewSession();
          setChatId(s.id);
        }
      }
      await refreshSessions();
    } catch (e) {
      setError(`删除会话失败: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  const headerLabel = currentTitle && currentTitle !== "新会话" && currentTitle !== "chat"
    ? currentTitle
    : song
      ? song
      : "新会话";

  return (
    <div className="pane agent-shell">
      <div className="pane-header justify-between normal-case tracking-normal relative">
        <button
          ref={sessionTriggerRef}
          type="button"
          onClick={() => setShowDropdown((v) => !v)}
          className="agent-session-trigger"
          title="切换 / 管理会话"
        >
          <span className="truncate">聊天 · {headerLabel}</span>
          <ChevronDown size={12} className={`shrink-0 transition-transform ${showDropdown ? "rotate-180" : ""}`} />
        </button>
        <button
          type="button"
          onClick={onNewSession}
          className="agent-session-new"
          title="新建会话"
        >
          <Plus size={14} />
        </button>
        {showDropdown && (
          <div ref={dropdownRef} className="agent-session-dropdown">
            <div className="agent-session-dropdown-head">
              <span>会话</span>
              <button
                type="button"
                onClick={onNewSession}
                className="flex items-center gap-1 text-xs text-accent hover:underline"
              >
                <Plus size={12} /> 新建
              </button>
            </div>
            {sessions.length === 0 && (
              <div className="px-3 py-4 text-xs text-fg-subtle text-center">
                暂无历史会话
              </div>
            )}
            <div className="agent-session-list">
              {sessions.map((s) => {
                const isCurrent = s.id === chatId;
                const isRenaming = renamingId === s.id;
                return (
                  <div
                    key={s.id}
                    className={`agent-session-row ${isCurrent ? "agent-session-row--current" : ""}`}
                    onClick={() => !isRenaming && onSelectSession(s.id)}
                  >
                    <div className="min-w-0 flex-1">
                      {isRenaming ? (
                        <input
                          autoFocus
                          value={renamingDraft}
                          onChange={(e) => setRenamingDraft(e.target.value)}
                          onBlur={() => onRenameCommit(s.id)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") {
                              e.preventDefault();
                              void onRenameCommit(s.id);
                            } else if (e.key === "Escape") {
                              setRenamingId(null);
                              setRenamingDraft("");
                            }
                          }}
                          onClick={(e) => e.stopPropagation()}
                          className="w-full bg-bg border border-accent rounded px-1.5 py-0.5 text-xs outline-none"
                        />
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <span className={`truncate text-xs ${isCurrent ? "text-fg font-medium" : "text-fg-muted"}`}>
                            {s.title || "(未命名)"}
                          </span>
                          {isCurrent && <span className="text-[10px] text-accent shrink-0">当前</span>}
                        </div>
                      )}
                      {!isRenaming && (
                        <div className="text-[10px] text-fg-subtle truncate">
                          {s.song ? `${s.song} · ` : ""}
                          {relTime(s.updated_at)}
                        </div>
                      )}
                    </div>
                    {!isRenaming && (
                      <div className="agent-session-actions">
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            setRenamingId(s.id);
                            setRenamingDraft(s.title || "");
                          }}
                          className="p-1 rounded hover:bg-bg-hover text-fg-subtle hover:text-fg"
                          title="重命名"
                        >
                          <Pencil size={11} />
                        </button>
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            void onDeleteSession(s.id);
                          }}
                          className="p-1 rounded hover:bg-bg-hover text-fg-subtle hover:text-danger"
                          title="删除"
                        >
                          <Trash2 size={11} />
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
      <div className="pane-body agent-body">
        <div ref={scrollRef} onScroll={onScroll} className="agent-scroll scroll-stable">
          {!hasConversation && (
            <div className="agent-welcome">
              <div className="flex items-center gap-2">
                <AgentAvatar />
                <div>
                  <div className="font-semibold text-fg">Agent</div>
                  <div className="text-xs text-fg-subtle">Audio QC assistant</div>
                </div>
              </div>
              <p className="mt-3 text-sm leading-6 text-fg-muted">
                直接说"开始质检 &lt;歌曲名&gt;"即可进入 17 态工作流;或先聊一聊。
              </p>
            </div>
          )}

          <div className="flex flex-col gap-5 py-3">
            {visible.map((m, i) => {
              if (m.kind === "user") {
                return (
                  <div key={i} className="agent-user-row">
                    <div className="agent-user-bubble selectable">{m.text}</div>
                  </div>
                );
              }
              if (m.kind === "assistant") {
                return (
                  <div key={i} className="agent-assistant-row">
                    <AgentAvatar />
                    <div className="min-w-0 flex-1">
                      <div className="mb-1 font-semibold text-fg">Agent</div>
                      <div className="agent-assistant-text selectable agent-md">
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>{m.text}</ReactMarkdown>
                      </div>
                    </div>
                  </div>
                );
              }
              if (m.kind === "phase") {
                return (
                  <div key={i} className="text-xs text-fg-subtle">
                    — {m.label} —
                  </div>
                );
              }
              if (m.kind === "error") {
                return (
                  <div key={i} className="agent-error selectable">{m.text}</div>
                );
              }
              if (m.kind === "human_check") {
                return (
                  <HumanCheckCard
                    key={i}
                    turn={m}
                    onResolve={(payload) => {
                      // 标记卡片已处理(UI 显示)
                      setTurns((arr) => arr.map((t, j) =>
                        j === i
                          ? {
                              ...m,
                              resolved: {
                                answers: payload.answers,
                                cancelled: !!payload.cancelled,
                              },
                            }
                          : t,
                      ));
                      // 直接 resolve agent 端挂起的 promise(阻塞型);agent 收到 tool
                      // result 后会在同一轮继续推理,不再让出控制权
                      setError(null);
                      setSending(true);
                      window.electronAPI
                        .agentHumanCheckResolve(chatIdRef.current, payload)
                        .catch((err) => {
                          setSending(false);
                          setError(err instanceof Error ? err.message : String(err));
                        });
                      // 卡片处理完会变 disabled,焦点掉到 body → 光标消失;还回聊天输入框
                      requestAnimationFrame(() => composerRef.current?.focus());
                    }}
                  />
                );
              }
              // tool — 默认折叠成一行,点 chevron 展开看完整 args/result
              const expanded = expandedTools.has(i);
              return (
                <div key={i} className="text-xs text-fg-muted font-mono leading-5">
                  <button
                    onClick={() => toggleTool(i)}
                    className="flex items-center gap-1 w-full text-left hover:text-fg"
                  >
                    <ChevronRight
                      size={12}
                      className={`shrink-0 transition-transform ${expanded ? "rotate-90" : ""}`}
                    />
                    <span className="truncate">
                      🔧 {m.name}({fmtArgs(m.args, 80)})
                      {m.result !== undefined && (
                        <span className="text-fg-subtle"> · {fmtResult(m.result, 80)}</span>
                      )}
                    </span>
                  </button>
                  {expanded && (
                    <div className="pl-4 mt-1 space-y-1 selectable">
                      <pre className="whitespace-pre-wrap break-all bg-bg-sidebar/60 rounded p-2 text-[11px]">
                        {JSON.stringify(m.args, null, 2)}
                      </pre>
                      {m.result !== undefined && (
                        <pre className="whitespace-pre-wrap break-all bg-bg-sidebar/60 rounded p-2 text-[11px] text-fg-subtle">
                          {typeof m.result === "string"
                            ? m.result
                            : JSON.stringify(m.result, null, 2)}
                        </pre>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
            {sending && (
              <div className="agent-assistant-row">
                <AgentAvatar />
                <div className="min-w-0 flex-1">
                  <div className="mb-1 font-semibold text-fg">Agent</div>
                  <div className="agent-thinking">
                    <span /><span /><span />
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {error && <div className="agent-error selectable">{error}</div>}

        <form onSubmit={onSubmit} className="agent-composer">
          <textarea
            ref={composerRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="输入消息..."
            className="agent-input"
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void onSubmit(e);
              }
            }}
          />
          <div className="agent-composer-footer">
            <div className="text-xs text-fg-subtle">
              {song ? "Phase B" : "Phase A"}
            </div>
            {sending ? (
              <button
                type="button"
                onClick={onCancel}
                disabled={cancelling}
                className="agent-send agent-send--stop"
                title={cancelling ? "中断中..." : "中断当前对话"}
              >
                <Square size={10} fill="currentColor" />
              </button>
            ) : (
              <button className="agent-send" disabled={!input.trim()} title="发送">
                <Send size={14} />
              </button>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}

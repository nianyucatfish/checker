import { useEffect, useMemo, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import Papa from "papaparse";
import {
  Loader2,
  AlertCircle,
  Save,
  Plus,
  Trash2,
  Table2,
  FileText,
  Undo2,
  Redo2,
  ChevronUp,
  ChevronDown,
} from "lucide-react";
import { readCsv, writeCsv } from "../../api";
import { clsx } from "../../utils";

interface Props {
  path: string;
}

type Mode = "table" | "text";

type Cmd =
  | { type: "cell"; ri: number; ci: number; oldVal: string; newVal: string }
  | { type: "insertRow"; ri: number }
  | { type: "removeRow"; ri: number; data: string[] }
  | { type: "insertCol"; ci: number }
  | { type: "removeCol"; ci: number; data: string[] };

function rowsToText(rows: string[][]): string {
  return Papa.unparse(rows, { newline: "\n" });
}

function textToRows(text: string): string[][] {
  const result = Papa.parse<string[]>(text, { skipEmptyLines: false });
  return result.data.map((r) =>
    Array.isArray(r) ? r.map((c) => (c ?? "").toString()) : [],
  );
}

function colCountOf(rows: string[][]): number {
  return rows.reduce((m, r) => Math.max(m, r.length), 0);
}

function applyCmd(rows: string[][], cmd: Cmd, reverse: boolean): string[][] {
  switch (cmd.type) {
    case "cell": {
      const value = reverse ? cmd.oldVal : cmd.newVal;
      const next = rows.map((r) => [...r]);
      if (cmd.ri >= next.length) return next;
      while (next[cmd.ri].length <= cmd.ci) next[cmd.ri].push("");
      next[cmd.ri][cmd.ci] = value;
      return next;
    }
    case "insertRow": {
      if (reverse) return rows.filter((_, i) => i !== cmd.ri);
      const cols = Math.max(colCountOf(rows), 1);
      const blank = Array(cols).fill("");
      return [...rows.slice(0, cmd.ri), blank, ...rows.slice(cmd.ri)];
    }
    case "removeRow": {
      if (reverse) return [...rows.slice(0, cmd.ri), [...cmd.data], ...rows.slice(cmd.ri)];
      return rows.filter((_, i) => i !== cmd.ri);
    }
    case "insertCol": {
      if (reverse) return rows.map((r) => r.filter((_, i) => i !== cmd.ci));
      return rows.map((r) => {
        const next = [...r];
        while (next.length < cmd.ci) next.push("");
        next.splice(cmd.ci, 0, "");
        return next;
      });
    }
    case "removeCol": {
      if (reverse) {
        return rows.map((r, ri) => {
          const next = [...r];
          while (next.length < cmd.ci) next.push("");
          next.splice(cmd.ci, 0, cmd.data[ri] ?? "");
          return next;
        });
      }
      return rows.map((r) => r.filter((_, i) => i !== cmd.ci));
    }
  }
}

function useDarkTheme() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const obs = new MutationObserver(() => {
      setDark(document.documentElement.classList.contains("dark"));
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export function CsvViewer({ path }: Props) {
  const [rows, setRows] = useState<string[][] | null>(null);
  const [originalText, setOriginalText] = useState<string>("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [mode, setMode] = useState<Mode>("table");
  const [textValue, setTextValue] = useState<string>("");
  const [undoStack, setUndoStack] = useState<Cmd[]>([]);
  const [redoStack, setRedoStack] = useState<Cmd[]>([]);
  const [focused, setFocused] = useState<{ ri: number; ci: number } | null>(null);
  const dark = useDarkTheme();

  // 引用最新的 state 给键盘 handler 用，避免重新绑定
  const rowsRef = useRef<string[][] | null>(rows);
  const undoStackRef = useRef(undoStack);
  const redoStackRef = useRef(redoStack);
  const dirtyRef = useRef(dirty);
  const savingRef = useRef(saving);
  const modeRef = useRef(mode);
  const textValueRef = useRef(textValue);
  rowsRef.current = rows;
  undoStackRef.current = undoStack;
  redoStackRef.current = redoStack;
  dirtyRef.current = dirty;
  savingRef.current = saving;
  modeRef.current = mode;
  textValueRef.current = textValue;

  // 路径变化：重新拉取并清空所有状态
  useEffect(() => {
    let cancelled = false;
    setRows(null);
    setError(null);
    setDirty(false);
    setLoading(true);
    setMode("table");
    setUndoStack([]);
    setRedoStack([]);
    setFocused(null);
    readCsv(path)
      .then((out) => {
        if (cancelled) return;
        setRows(out.rows);
        const text = rowsToText(out.rows);
        setOriginalText(text);
        setTextValue(text);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const switchTo = (next: Mode) => {
    if (next === mode) return;
    if (next === "table") {
      setRows(textToRows(textValue));
    } else {
      setTextValue(rowsToText(rows ?? []));
    }
    setMode(next);
  };

  const colCount = useMemo(() => colCountOf(rows ?? []), [rows]);

  // 派发命令：应用到 rows，压入 undo 栈，清空 redo 栈
  const dispatch = (cmd: Cmd) => {
    if (!rows) return;
    setRows(applyCmd(rows, cmd, false));
    setUndoStack([...undoStack, cmd]);
    setRedoStack([]);
    setDirty(true);
  };

  const undo = () => {
    const stack = undoStackRef.current;
    const r = rowsRef.current;
    if (!r || stack.length === 0) return;
    const cmd = stack[stack.length - 1];
    setRows(applyCmd(r, cmd, true));
    setUndoStack(stack.slice(0, -1));
    setRedoStack([...redoStackRef.current, cmd]);
    setDirty(true);
  };

  const redo = () => {
    const stack = redoStackRef.current;
    const r = rowsRef.current;
    if (!r || stack.length === 0) return;
    const cmd = stack[stack.length - 1];
    setRows(applyCmd(r, cmd, false));
    setRedoStack(stack.slice(0, -1));
    setUndoStack([...undoStackRef.current, cmd]);
    setDirty(true);
  };

  const updateCell = (ri: number, ci: number, value: string) => {
    if (!rows) return;
    const oldVal = rows[ri]?.[ci] ?? "";
    if (oldVal === value) return;
    dispatch({ type: "cell", ri, ci, oldVal, newVal: value });
  };

  const insertRow = (ri: number) => {
    if (!rows) return;
    dispatch({ type: "insertRow", ri });
  };

  const removeRow = (ri: number) => {
    if (!rows) return;
    const data = rows[ri] ? [...rows[ri]] : [];
    dispatch({ type: "removeRow", ri, data });
  };

  const insertCol = (ci: number) => {
    if (!rows) return;
    dispatch({ type: "insertCol", ci });
  };

  const removeCol = (ci: number) => {
    if (!rows) return;
    const data = rows.map((r) => r[ci] ?? "");
    dispatch({ type: "removeCol", ci, data });
  };

  const handleTextChange = (v: string | undefined) => {
    setTextValue(v ?? "");
    setDirty((v ?? "") !== originalText);
  };

  const save = async () => {
    if (!dirtyRef.current || savingRef.current) return;
    setSaving(true);
    try {
      const finalRows = modeRef.current === "table"
        ? rowsRef.current ?? []
        : textToRows(textValueRef.current);
      await writeCsv(path, finalRows);
      setRows(finalRows);
      const text = rowsToText(finalRows);
      setOriginalText(text);
      setTextValue(text);
      setDirty(false);
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      alert(`保存失败：${msg}`);
    } finally {
      setSaving(false);
    }
  };

  // 全局快捷键：Ctrl+S 保存，Ctrl+Z 撤销，Ctrl+Y / Ctrl+Shift+Z 重做（仅表格模式）
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const ctrl = e.ctrlKey || e.metaKey;
      if (!ctrl) return;
      if (e.key === "s" || e.key === "S") {
        e.preventDefault();
        save();
      } else if (modeRef.current === "table" && (e.key === "z" || e.key === "Z")) {
        if (e.shiftKey) {
          e.preventDefault();
          redo();
        } else {
          e.preventDefault();
          undo();
        }
      } else if (modeRef.current === "table" && (e.key === "y" || e.key === "Y")) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path]);

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span>读取中…</span>
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-danger gap-2 px-6">
        <AlertCircle size={20} />
        <span className="text-sm">读取失败</span>
        <span className="text-xs text-fg-muted text-center break-all">{error}</span>
      </div>
    );
  }

  if (!rows && mode === "table") return null;

  const focusedRow = focused?.ri ?? -1;
  const focusedCol = focused?.ci ?? -1;
  const canDeleteRow = focusedRow >= 0 && rows && rows.length > 0;
  const canDeleteCol = focusedCol >= 0 && colCount > 0;

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* 子工具栏 */}
      <div className="h-9 border-b border-border-subtle bg-bg-sidebar px-2 flex items-center gap-1 shrink-0">
        <div className="inline-flex items-center rounded-sm border border-border overflow-hidden">
          <button
            onClick={() => switchTo("table")}
            className={clsx(
              "h-6 px-2 inline-flex items-center gap-1 text-xs",
              mode === "table"
                ? "bg-bg-selected text-fg"
                : "text-fg-muted hover:bg-bg-hover",
            )}
            title="表格模式"
          >
            <Table2 size={12} />
            表格
          </button>
          <button
            onClick={() => switchTo("text")}
            className={clsx(
              "h-6 px-2 inline-flex items-center gap-1 text-xs border-l border-border",
              mode === "text"
                ? "bg-bg-selected text-fg"
                : "text-fg-muted hover:bg-bg-hover",
            )}
            title="纯文本模式"
          >
            <FileText size={12} />
            纯文本
          </button>
        </div>
        <div className="flex-1" />
        {dirty && <span className="text-xs text-warning">● 未保存</span>}
        {mode === "table" && (
          <>
            <div className="w-px h-4 bg-border mx-1" />
            <button
              onClick={() => insertRow(rows?.length ?? 0)}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm"
              title="在末尾添加行"
            >
              <Plus size={12} />
              添加行
            </button>
            <button
              onClick={() => insertCol(colCount)}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm"
              title="在末尾添加列"
            >
              <Plus size={12} />
              添加列
            </button>
            <button
              onClick={() => canDeleteRow && removeRow(focusedRow)}
              disabled={!canDeleteRow}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
              title={canDeleteRow ? `删除当前行 (第 ${focusedRow} 行)` : "先点击一个单元格"}
            >
              <Trash2 size={12} />
              删除行
            </button>
            <button
              onClick={() => canDeleteCol && removeCol(focusedCol)}
              disabled={!canDeleteCol}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
              title={canDeleteCol ? `删除当前列 (第 ${focusedCol} 列)` : "先点击一个单元格"}
            >
              <Trash2 size={12} />
              删除列
            </button>
            <div className="w-px h-4 bg-border mx-1" />
            <button
              onClick={undo}
              disabled={undoStack.length === 0}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
              title="撤销 (Ctrl+Z)"
            >
              <Undo2 size={12} />
              撤销
            </button>
            <button
              onClick={redo}
              disabled={redoStack.length === 0}
              className="h-6 px-2 inline-flex items-center gap-1 text-xs text-fg-muted hover:text-fg hover:bg-bg-hover rounded-sm disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-transparent"
              title="重做 (Ctrl+Y)"
            >
              <Redo2 size={12} />
              重做
            </button>
          </>
        )}
        <div className="w-px h-4 bg-border mx-1" />
        <button
          onClick={save}
          disabled={!dirty || saving}
          className="h-6 px-2 inline-flex items-center gap-1 text-xs rounded-sm bg-accent text-accent-fg disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90"
          title="保存 (Ctrl+S)"
        >
          {saving ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
          保存
        </button>
      </div>

      {/* 主体 */}
      {mode === "table" ? (
        <div className="flex-1 overflow-auto scroll-stable">
          <table className="text-xs font-mono border-collapse">
            <tbody>
              {(rows ?? []).map((row, ri) => (
                <tr key={ri} className="group hover:bg-bg-hover">
                  <td
                    className={clsx(
                      "text-fg-subtle text-right px-1 py-0 border-r border-b border-border-subtle whitespace-nowrap w-14 select-none",
                      focusedRow === ri && "bg-bg-selected text-fg",
                    )}
                  >
                    <div className="flex items-center justify-end gap-1">
                      <button
                        onClick={() => insertRow(ri)}
                        className="opacity-0 group-hover:opacity-100 hover:text-fg transition"
                        title="在该行上方插入"
                      >
                        <ChevronUp size={10} />
                      </button>
                      <button
                        onClick={() => insertRow(ri + 1)}
                        className="opacity-0 group-hover:opacity-100 hover:text-fg transition"
                        title="在该行下方插入"
                      >
                        <ChevronDown size={10} />
                      </button>
                      <button
                        onClick={() => removeRow(ri)}
                        className="opacity-0 group-hover:opacity-100 hover:text-danger transition"
                        title="删除该行"
                      >
                        <Trash2 size={10} />
                      </button>
                      <span>{ri === 0 ? "H" : ri}</span>
                    </div>
                  </td>
                  {Array.from({ length: Math.max(colCount, 1) }).map((_, ci) => (
                    <td
                      key={ci}
                      className={clsx(
                        "border-r border-b border-border-subtle p-0 align-top",
                        ri === 0 && "bg-bg-sidebar",
                        focusedCol === ci && ri !== 0 && "bg-bg-hover",
                      )}
                    >
                      <input
                        value={row[ci] ?? ""}
                        onChange={(e) => updateCell(ri, ci, e.target.value)}
                        onFocus={() => setFocused({ ri, ci })}
                        className={clsx(
                          "w-full px-2 py-0.5 bg-transparent border-0 outline-none",
                          "focus:bg-bg-selected font-mono text-xs",
                          ri === 0 ? "text-fg font-semibold" : "text-fg",
                        )}
                      />
                    </td>
                  ))}
                </tr>
              ))}
              {(rows ?? []).length === 0 && (
                <tr>
                  <td className="text-fg-muted px-3 py-2">
                    空文件 — 点工具栏的 "+行" 开始编辑
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex-1 min-h-0">
          <Editor
            height="100%"
            path={path + ".__text_mode__"}
            value={textValue}
            language="plaintext"
            theme={dark ? "vs-dark" : "vs"}
            onChange={handleTextChange}
            options={{
              minimap: { enabled: false },
              fontSize: 12,
              lineNumbers: "on",
              scrollBeyondLastLine: false,
              wordWrap: "on",
              tabSize: 2,
            }}
          />
        </div>
      )}
    </div>
  );
}

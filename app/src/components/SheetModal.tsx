import { useEffect, useMemo, useState } from "react";
import { getSheetView, type SheetView } from "../api";

export function SheetModal({ onClose }: { onClose: () => void }) {
  const [data, setData] = useState<SheetView | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [mine, setMine] = useState(false);
  const [status, setStatus] = useState("all");
  const [copied, setCopied] = useState("");
  const [page, setPage] = useState(0);
  const load = async (force = false) => {
    setBusy(true); setError("");
    try { setData(await getSheetView(force)); setPage(0); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };
  useEffect(() => { void load(); }, []);
  useEffect(() => {
    const key = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [onClose]);
  const rows = useMemo(() => (data?.items || []).filter(row => (!mine || row.mine) &&
    (status === "all" || row.accepted === (status === "accepted"))), [data, mine, status]);
  const copy = async (text: string) => {
    try { await window.electronAPI.clipboardWriteText(text); setCopied("已复制"); }
    catch { setCopied("复制失败，请重试"); }
  };
  const copyTable = () => {
    if (!data) return;
    const values = [["行号", ...data.headers], ...rows.map(row => [String(row.row_index), ...row.cells])];
    void copy(values.map(row => row.map(cell => /[\t\r\n"]/.test(cell) ? `"${cell.replace(/"/g, '""')}"` : cell).join("\t")).join("\n"));
  };
  const pages = Math.max(1, Math.ceil(rows.length / 100));
  const button = "rounded px-3 py-1 text-sm hover:bg-bg-hover disabled:opacity-40";
  return <div className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-5" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
    <div className="bg-bg-sidebar border border-border rounded-md w-full h-[90vh] flex flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-4 px-4 py-3 border-b border-border">
        <h2 id="sheet-title" className="font-medium">分工表</h2>
        <button className={button} disabled={busy} onClick={() => void load(true)}>{busy ? "加载中…" : "刷新分工表"}</button>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={mine} disabled={!data?.reviewer_set} onChange={e => { setMine(e.target.checked); setPage(0); }} />仅查看我负责的歌曲</label>
        <label className="text-sm">验收状态 <select aria-label="验收状态" className="bg-bg border border-border rounded px-2 py-1" value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}>
          <option value="all">全部</option><option value="pending">未验收</option><option value="accepted">已验收</option>
        </select></label>
        <button className={button} disabled={!data || busy} onClick={copyTable}>复制表格</button>
        <span role="status" className="text-xs text-fg-muted">{copied}</span>
        <button className={`${button} ml-auto`} onClick={onClose}>关闭 (Esc)</button>
      </div>
      {error && <p role="alert" className="text-sm text-red-500 px-4 py-2">{error}{data ? "（仍显示上次加载的表格）" : ""}</p>}
      {data && !data.reviewer_set && <p className="text-xs text-fg-muted px-4 py-2">在设置中填写验收负责人姓名后，即可筛选自己负责的歌曲。</p>}
      <div className="flex-1 overflow-auto selectable">
        <table className="text-xs border-collapse w-full"><thead className="sticky top-0 bg-bg-sidebar"><tr>{["行号", ...(data?.headers || [])].map((v, i) => <th key={i} className="text-left whitespace-nowrap px-3 py-2 border-b border-border">{v}</th>)}</tr></thead>
          <tbody>{rows.slice(page * 100, (page + 1) * 100).map(row => <tr key={row.row_index} className="border-b border-border-subtle hover:bg-bg-hover">
            <td className="px-3 py-2 text-fg-muted">{row.row_index}</td>{row.cells.map((cell, i) => <td key={i} className="px-3 py-2 align-top"><button title="点击复制单元格" className="text-left whitespace-pre-wrap min-w-16 max-w-xs break-words hover:underline" onClick={() => void copy(cell)}>{cell || "—"}</button></td>)}
          </tr>)}</tbody></table>
        {!busy && data && !rows.length && <p className="p-8 text-center text-sm text-fg-muted">没有符合条件的歌曲</p>}
      </div>
      <div className="flex items-center gap-3 px-4 py-2 border-t border-border text-xs text-fg-muted">
        <span>共 {rows.length} 首 · 点击单元格复制；复制表格包含全部筛选结果</span><div className="flex-1" />
        <button className={button} disabled={page === 0} onClick={() => setPage(p => p - 1)}>上一页</button><span>{page + 1} / {pages}</span>
        <button className={button} disabled={page + 1 >= pages} onClick={() => setPage(p => p + 1)}>下一页</button>
      </div>
    </div>
  </div>;
}

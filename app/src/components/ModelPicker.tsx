import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Download, Loader2, Search } from "lucide-react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  models: string[];
  loading: boolean;
  canFetch: boolean;
  onFetch: () => void;
}

export function ModelPicker({ value, onChange, models, loading, canFetch, onFetch }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const [position, setPosition] = useState({ left: 0, top: 0, width: 0, height: 280 });
  const anchor = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const items = models.filter(id => id.toLowerCase().includes(query.trim().toLowerCase()));
  const close = () => { setOpen(false); toggle.current?.focus(); };
  const select = (id: string) => { onChange(id); close(); };
  useEffect(() => { setActive(0); }, [query, models]);
  useEffect(() => { if (!open) setQuery(""); }, [open]);
  useLayoutEffect(() => {
    if (!open) return;
    const update = () => {
      const rect = anchor.current?.getBoundingClientRect();
      if (!rect) return;
      const below = window.innerHeight - rect.bottom - 12;
      const above = rect.top - 12;
      const height = Math.min(300, Math.max(120, below >= 200 || below >= above ? below : above));
      const width = Math.min(Math.max(rect.width, 280), window.innerWidth - 16);
      setPosition({ left: Math.min(Math.max(8, rect.right - width), window.innerWidth - width - 8),
        top: below >= 200 || below >= above ? rect.bottom + 6 : Math.max(8, rect.top - height - 6), width, height });
    };
    update();
    window.addEventListener("resize", update);
    window.addEventListener("scroll", update, true);
    return () => { window.removeEventListener("resize", update); window.removeEventListener("scroll", update, true); };
  }, [open]);
  useEffect(() => {
    if (!open) return;
    const outside = (e: MouseEvent) => {
      if (!anchor.current?.contains(e.target as Node) && !panel.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", outside);
    return () => document.removeEventListener("mousedown", outside);
  }, [open]);
  useEffect(() => { panel.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" }); }, [active]);
  return <>
    <div ref={anchor} className="flex gap-1.5">
      <input aria-label="模型名称" role="combobox" aria-expanded={open} aria-controls={open ? "model-picker-list" : undefined}
        className="min-w-0 flex-1 bg-bg border border-border rounded px-2 py-1 text-xs text-fg focus:outline-none focus:border-accent"
        value={value} onChange={e => onChange(e.target.value)} placeholder="选择或输入模型名称"
        onKeyDown={e => { if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); } }} />
      <button type="button" title="获取模型列表" aria-label="获取模型列表" disabled={loading || !canFetch} onClick={onFetch}
        className="shrink-0 rounded border border-border p-2 text-fg-muted hover:bg-bg-hover hover:text-fg disabled:opacity-40 disabled:cursor-not-allowed">
        {loading ? <Loader2 size={14} className="animate-spin" /> : <Download size={14} />}
      </button>
      <button ref={toggle} type="button" title="选择模型" aria-label="选择模型" aria-haspopup="listbox" aria-expanded={open}
        onClick={() => setOpen(v => !v)} className="shrink-0 rounded border border-border p-2 text-fg-muted hover:bg-bg-hover hover:text-fg">
        <ChevronDown size={14} className={open ? "rotate-180" : ""} />
      </button>
    </div>
    {open && createPortal(<div ref={panel} style={{ position: "fixed", left: position.left, top: position.top, width: position.width, maxHeight: position.height }}
      className="z-[80] flex flex-col rounded-lg border border-border bg-bg-sidebar shadow-xl overflow-hidden"
      onKeyDown={e => {
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(); }
        if (e.key === "ArrowDown" || e.key === "ArrowUp") {
          e.preventDefault(); setActive(i => Math.max(0, Math.min(items.length - 1, i + (e.key === "ArrowDown" ? 1 : -1))));
        }
        if (e.key === "Enter" && items[active]) { e.preventDefault(); select(items[active]); }
        if (e.key === "Tab") setOpen(false);
      }}>
      <div className="flex items-center gap-2 px-3 py-2.5 border-b border-border text-fg-muted">
        <Search size={14} className="shrink-0" />
        <input autoFocus aria-label="搜索模型" className="min-w-0 flex-1 bg-transparent text-xs text-fg outline-none" placeholder="搜索模型…" value={query} onChange={e => setQuery(e.target.value)}
          aria-controls="model-picker-list" aria-activedescendant={items[active] ? `model-option-${active}` : undefined} />
      </div>
      <div className="px-3 pt-2 pb-1 text-[10px] text-fg-subtle">可用模型 · {items.length}</div>
      <div id="model-picker-list" role="listbox" aria-label="可用模型" className="overflow-y-auto min-h-0 px-1 pb-1">
        {items.map((id, index) => <button key={id} id={`model-option-${index}`} data-index={index} type="button" role="option" aria-selected={id === value}
          onMouseEnter={() => setActive(index)} onClick={() => select(id)}
          className={`w-full flex items-center justify-between gap-3 rounded px-2 py-2 text-left text-xs text-fg ${index === active || id === value ? "bg-bg-hover" : ""}`}>
          <span className="break-all">{id}</span>{id === value && <Check size={13} className="shrink-0 text-accent" />}
        </button>)}
        {!items.length && <p className="px-2 py-4 text-xs text-fg-muted">{loading ? "正在获取模型…" : models.length ? "没有匹配的模型，可直接手动输入。" : "点击获取按钮加载列表，或直接手动输入模型名称。"}</p>}
      </div>
    </div>, document.body)}
  </>;
}

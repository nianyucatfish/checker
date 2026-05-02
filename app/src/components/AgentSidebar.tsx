import { Bot, Sparkles, FolderSearch, Wrench, MessageSquare } from "lucide-react";

const chips = [
  { Icon: Sparkles, label: "质检一首歌" },
  { Icon: FolderSearch, label: "全量质检" },
  { Icon: Wrench, label: "自动修复命名" },
  { Icon: MessageSquare, label: "自由对话" },
];

export function AgentSidebar() {
  return (
    <div className="pane">
      <div className="pane-header">Agent</div>
      <div className="pane-body px-3 py-3 flex flex-col gap-3">
        <div className="flex items-center gap-2 text-fg-muted">
          <Bot size={14} className="text-accent" />
          <span>质检助手在这里</span>
        </div>
        <div className="flex flex-col gap-1">
          {chips.map(({ Icon, label }) => (
            <button
              key={label}
              disabled
              className="btn flex items-center gap-1.5 justify-start text-left opacity-50 cursor-not-allowed"
              title="Phase 4 接入"
            >
              <Icon size={13} />
              <span>{label}</span>
            </button>
          ))}
        </div>
        <div className="text-xs text-fg-subtle border-t border-border pt-3">
          Phase 4 接入：自然语言对话、流程编排、人工确认卡。
        </div>
      </div>
    </div>
  );
}

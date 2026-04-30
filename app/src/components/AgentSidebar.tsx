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
        <div className="border border-border rounded p-3 flex flex-col gap-3">
          <div className="flex items-center gap-2">
            <Bot size={16} className="text-accent" />
            <span className="font-medium">质检助手在这里</span>
          </div>
          <p className="text-fg-muted">
            我能跑质检流程、修可机修的部分、把改不动的调出面板让你确认。
          </p>
          <div className="grid grid-cols-2 gap-2">
            {chips.map(({ Icon, label }) => (
              <button
                key={label}
                disabled
                className="btn flex items-center gap-1.5 justify-start text-left opacity-60 cursor-not-allowed"
                title="Phase 4 接入"
              >
                <Icon size={13} />
                <span>{label}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="text-xs text-fg-subtle text-center">
          Agent 接入在 Phase 4 实现
        </div>
      </div>
    </div>
  );
}

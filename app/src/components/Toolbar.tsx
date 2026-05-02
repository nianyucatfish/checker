import { FolderOpen, RefreshCw, Wrench, AlignVerticalJustifyCenter } from "lucide-react";
import { clsx } from "../utils";

interface Props {
  hasWorkspace: boolean;
  scanning: boolean;
  onPickWorkspace: () => void;
  onScan: () => void;
}

function ToolbarBtn({
  Icon,
  label,
  onClick,
  disabled,
  active,
}: {
  Icon: typeof FolderOpen;
  label: string;
  onClick?: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={label}
      className={clsx(
        "h-7 px-2 inline-flex items-center gap-1.5 rounded-sm",
        "text-fg-muted hover:text-fg hover:bg-bg-hover",
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:bg-transparent",
        active && "text-fg bg-bg-hover"
      )}
    >
      <Icon size={14} className={Icon === RefreshCw && active ? "animate-spin" : ""} />
      <span>{label}</span>
    </button>
  );
}

export function Toolbar({ hasWorkspace, scanning, onPickWorkspace, onScan }: Props) {
  return (
    <div className="h-9 border-b border-border bg-bg-sidebar flex items-center px-2 gap-1 shrink-0">
      <ToolbarBtn Icon={FolderOpen} label="打开工作区" onClick={onPickWorkspace} />
      <div className="w-px h-4 bg-border mx-1" />
      <ToolbarBtn
        Icon={RefreshCw}
        label="全量扫描"
        onClick={onScan}
        disabled={!hasWorkspace || scanning}
        active={scanning}
      />
      <ToolbarBtn
        Icon={Wrench}
        label="自动修复命名"
        disabled={!hasWorkspace}
      />
      <ToolbarBtn
        Icon={AlignVerticalJustifyCenter}
        label="统一时长"
        disabled={!hasWorkspace}
      />
    </div>
  );
}

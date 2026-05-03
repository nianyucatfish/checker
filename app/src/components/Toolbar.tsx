import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Loader2 } from "lucide-react";
import { clsx } from "../utils";

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
  const [openMenu, setOpenMenu] = useState<"file" | "help" | null>(null);
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const fileBtnRef = useRef<HTMLButtonElement | null>(null);
  const helpBtnRef = useRef<HTMLButtonElement | null>(null);

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

  const showFileMenu = (e: React.MouseEvent) => {
    setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
    setOpenMenu("file");
  };
  const showHelpMenu = (e: React.MouseEvent) => {
    setAnchor((e.currentTarget as HTMLElement).getBoundingClientRect());
    setOpenMenu("help");
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
    </div>
  );
}

import { Files, MessageCircle, Settings } from "lucide-react";
import { clsx } from "../utils";

export type ActivityView = "explorer" | "agent" | "settings";

interface Props {
  current: ActivityView;
  onChange: (v: ActivityView) => void;
  sidecarReady: boolean | null;
}

const items: { id: ActivityView; label: string; Icon: typeof Files }[] = [
  { id: "explorer", label: "工作区", Icon: Files },
  { id: "agent", label: "Agent (Phase 4)", Icon: MessageCircle },
  { id: "settings", label: "设置", Icon: Settings },
];

export function ActivityBar({ current, onChange, sidecarReady }: Props) {
  return (
    <div className="w-12 bg-bg-activitybar flex flex-col items-center justify-between py-1 shrink-0">
      <div className="flex flex-col items-center">
        {items.map(({ id, label, Icon }) => {
          const active = current === id;
          return (
            <button
              key={id}
              title={label}
              onClick={() => onChange(id)}
              className={clsx(
                "w-12 h-12 flex items-center justify-center text-white/60 hover:text-white relative",
                active && "text-white"
              )}
            >
              {active && (
                <span className="absolute left-0 top-0 bottom-0 w-[2px] bg-white" />
              )}
              <Icon size={22} strokeWidth={1.4} />
            </button>
          );
        })}
      </div>
      <div
        title={`Sidecar ${sidecarReady === null ? "..." : sidecarReady ? "在线" : "离线"}`}
        className={clsx(
          "w-2 h-2 rounded-full mb-2",
          sidecarReady === null && "bg-yellow-500",
          sidecarReady === true && "bg-green-500",
          sidecarReady === false && "bg-red-500"
        )}
      />
    </div>
  );
}

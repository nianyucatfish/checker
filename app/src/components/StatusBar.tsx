import { Wifi, WifiOff } from "lucide-react";

interface Props {
  sidecarReady: boolean | null;
  songCount: number;
  errorCount: number;
}

export function StatusBar({ sidecarReady, songCount, errorCount }: Props) {
  return (
    <div className="h-[22px] bg-bg-statusbar text-fg-statusbar text-xs flex items-center px-3 gap-4 shrink-0">
      <div className="flex items-center gap-1">
        {sidecarReady ? <Wifi size={11} /> : <WifiOff size={11} />}
        <span>
          Sidecar {sidecarReady === null ? "…" : sidecarReady ? "Online" : "Offline"}
        </span>
      </div>
      {songCount > 0 && (
        <div>
          {songCount} 首歌 · <span className={errorCount > 0 ? "font-medium" : ""}>{errorCount} 处问题</span>
        </div>
      )}
      <div className="flex-1" />
      <div className="text-white/70">v0.1.0</div>
    </div>
  );
}

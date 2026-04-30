import { CheckCircle2, AlertCircle } from "lucide-react";
import type { CheckErrorOut } from "../api";

interface Props {
  selected: string | null;
  errors: CheckErrorOut[];
}

function basename(p: string) {
  const m = p.split(/[\/]/);
  return m[m.length - 1] || p;
}

function tail(p: string, depth = 2) {
  const parts = p.split(/[\/]/);
  return parts.slice(-depth).join("/");
}

export function Center({ selected, errors }: Props) {
  if (!selected) {
    return (
      <div className="pane bg-bg">
        <div className="flex-1 flex items-center justify-center text-fg-muted">
          在左侧选择一首歌
        </div>
      </div>
    );
  }
  return (
    <div className="pane bg-bg">
      <div className="pane-header">{basename(selected)}</div>
      <div className="pane-body">
        {errors.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-fg-muted gap-2 py-12">
            <CheckCircle2 size={32} className="text-success" />
            <p>该歌曲未发现问题（或还未扫描）</p>
          </div>
        ) : (
          <div className="px-3 flex flex-col gap-2">
            <div className="text-danger font-medium">{errors.length} 处问题</div>
            {errors.map((e, i) => (
              <div key={i} className="border border-border rounded px-3 py-2">
                <div className="flex items-start gap-2">
                  <AlertCircle size={16} className="text-danger mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <code className="font-mono text-xs text-fg-subtle bg-bg-hover px-1 rounded">
                        {e.code}
                      </code>
                      {e.machine_fixable && (
                        <span className="text-xs text-success">可自动修</span>
                      )}
                    </div>
                    <div className="mt-1">{e.message}</div>
                    <div className="font-mono text-xs text-fg-muted mt-1">{tail(e.path, 2)}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

import { FileQuestion, FolderOpen } from "lucide-react";
import { CsvViewer } from "./editors/CsvViewer";
import { MonacoTextViewer } from "./editors/MonacoTextViewer";
import { AudioViewer } from "./editors/AudioViewer";
import { MidiViewer } from "./editors/MidiViewer";
import { ErrorBoundary } from "./ErrorBoundary";

interface Props {
  selectedPath: string | null;
  selectedIsDir: boolean;
}

function basename(p: string) {
  const m = p.split(/[\\/]/);
  return m[m.length - 1] || p;
}

function extOf(p: string) {
  const m = p.match(/\.([^.\\/]+)$/);
  return m ? m[1].toLowerCase() : "";
}

const TEXT_EXTS = new Set(["txt", "md", "json", "log", "ini", "yml", "yaml"]);
const AUDIO_EXTS = new Set(["wav", "mp3", "ogg", "flac", "m4a"]);
const MIDI_EXTS = new Set(["mid", "midi"]);

export function Center({ selectedPath, selectedIsDir }: Props) {
  if (!selectedPath) {
    return (
      <div className="pane bg-bg">
        <div className="flex-1 flex flex-col items-center justify-center text-fg-muted gap-2">
          <FileQuestion size={32} />
          <p>从左侧选择一首歌或文件</p>
        </div>
      </div>
    );
  }

  if (selectedIsDir) {
    return (
      <div className="pane bg-bg">
        <div className="pane-header selectable">{basename(selectedPath)}</div>
        <div className="flex-1 flex flex-col items-center justify-center text-fg-muted gap-2">
          <FolderOpen size={32} />
          <p>选中目录：{basename(selectedPath)}</p>
          <p className="text-xs text-fg-subtle break-all px-8 text-center">{selectedPath}</p>
        </div>
      </div>
    );
  }

  const ext = extOf(selectedPath);
  let body: React.ReactNode;
  if (ext === "csv") {
    body = <CsvViewer key={selectedPath} path={selectedPath} />;
  } else if (TEXT_EXTS.has(ext)) {
    body = <MonacoTextViewer key={selectedPath} path={selectedPath} />;
  } else if (AUDIO_EXTS.has(ext)) {
    body = <AudioViewer key={selectedPath} path={selectedPath} />;
  } else if (MIDI_EXTS.has(ext)) {
    body = <MidiViewer key={selectedPath} path={selectedPath} />;
  } else {
    body = (
      <div className="flex-1 flex flex-col items-start gap-3 p-6 text-fg-muted overflow-auto scroll-stable">
        <div className="flex flex-col gap-1">
          <span className="text-xs uppercase tracking-wide text-fg-subtle">类型</span>
          <span className="text-fg">{ext ? `.${ext} 文件` : "文件"}</span>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs uppercase tracking-wide text-fg-subtle">路径</span>
          <span className="text-fg break-all font-mono text-xs">{selectedPath}</span>
        </div>
        <div className="text-xs text-fg-subtle italic mt-2">
          暂不支持预览此类型
        </div>
      </div>
    );
  }

  return (
    <div className="pane bg-bg">
      <div className="pane-header selectable">{basename(selectedPath)}</div>
      <ErrorBoundary label={`Center / ${ext || "无扩展"}`} key={selectedPath}>
        {body}
      </ErrorBoundary>
    </div>
  );
}

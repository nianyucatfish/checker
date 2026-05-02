import { useEffect, useRef, useState } from "react";
import Editor, { type OnMount } from "@monaco-editor/react";
import type { editor } from "monaco-editor";
import { Loader2, AlertCircle } from "lucide-react";
import { readText } from "../../api";

interface Props {
  path: string;
}

const EXT_TO_LANG: Record<string, string> = {
  json: "json",
  md: "markdown",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  py: "python",
  yml: "yaml",
  yaml: "yaml",
  html: "html",
  css: "css",
  ini: "ini",
  log: "plaintext",
  txt: "plaintext",
};

function langOf(path: string): string {
  const m = path.match(/\.([^.\\/]+)$/);
  const ext = m ? m[1].toLowerCase() : "";
  return EXT_TO_LANG[ext] ?? "plaintext";
}

function useDarkTheme() {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const obs = new MutationObserver(() => {
      setDark(document.documentElement.classList.contains("dark"));
    });
    obs.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class"],
    });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export function MonacoTextViewer({ path }: Props) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);
  const dark = useDarkTheme();

  useEffect(() => {
    let cancelled = false;
    setContent(null);
    setError(null);
    setTruncated(false);
    readText(path, 1_000_000)
      .then((out) => {
        if (cancelled) return;
        setContent(out.content);
        setTruncated(out.truncated);
      })
      .catch((e: Error) => {
        if (cancelled) return;
        setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const handleMount: OnMount = (e) => {
    editorRef.current = e;
  };

  if (error) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center text-danger gap-2 px-6">
        <AlertCircle size={20} />
        <span className="text-sm">读取失败</span>
        <span className="text-xs text-fg-muted text-center break-all">{error}</span>
      </div>
    );
  }

  if (content === null) {
    return (
      <div className="flex-1 flex items-center justify-center text-fg-muted gap-2">
        <Loader2 size={16} className="animate-spin" />
        <span>读取中…</span>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      <div className="flex-1 min-h-0">
        <Editor
          height="100%"
          path={path}
          value={content}
          language={langOf(path)}
          theme={dark ? "vs-dark" : "vs"}
          onMount={handleMount}
          options={{
            readOnly: true,
            minimap: { enabled: false },
            fontSize: 12,
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            wordWrap: "on",
            renderWhitespace: "selection",
            tabSize: 2,
          }}
        />
      </div>
      {truncated && (
        <div className="text-xs text-warning px-3 py-1 border-t border-border-subtle bg-bg-sidebar">
          已截断（仅显示前 1,000,000 字节）
        </div>
      )}
    </div>
  );
}

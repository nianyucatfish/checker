import { useEffect, useRef, useState } from "react";
import { Loader2, AlertCircle, Music3, Play } from "lucide-react";
import { rawFileUrl } from "../../api";

interface Props {
  path: string;
}

// Electron <webview> 的方法/事件 React 类型不带,本地补一个
interface WebviewElement extends HTMLElement {
  executeJavaScript(code: string): Promise<unknown>;
  reload(): void;
  openDevTools(): void;
  addEventListener(type: "dom-ready", listener: () => void): void;
  addEventListener(type: "did-start-loading", listener: () => void): void;
  addEventListener(type: "did-stop-loading", listener: () => void): void;
  addEventListener(type: "did-finish-load", listener: () => void): void;
  addEventListener(
    type: "console-message",
    listener: (e: { message: string; level: number; line: number; sourceId: string }) => void,
  ): void;
  addEventListener(
    type: "did-fail-load",
    listener: (e: { errorCode: number; errorDescription: string; validatedURL: string }) => void,
  ): void;
  addEventListener(type: "crashed", listener: () => void): void;
  removeEventListener(type: string, listener: (...args: unknown[]) => void): void;
}

const BRIDGE_MARKER = "__MIDI_BRIDGE__";

function basename(p: string) {
  const m = p.split(/[\\/]/);
  return m[m.length - 1] || p;
}

type Mode = "main" | "clean" | "magenta" | "idle";

function MidiWebview({ path, mode }: { path: string; mode: Mode }) {
  const wvRef = useRef<WebviewElement | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [stage, setStage] = useState<string>("挂载 webview");
  const [logs, setLogs] = useState<string[]>([]);
  // 每次 mount 用一个唯一 src,绕过 webview partition 缓存
  const srcRef = useRef<string>(
    `${
      mode === "clean"
        ? "/midi_test.html"
        : mode === "magenta"
        ? "/midi_test_magenta.html"
        : mode === "idle"
        ? "/midi_test_idle.html"
        : "/midi_player.html"
    }?t=${Date.now()}`,
  );

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setErrorMsg(null);
    setStage("挂载 webview");
    setLogs([]);

    const wv = wvRef.current;
    if (!wv) return;

    let bridgeInstalled = false;
    let injected = false;
    let pendingUrl: string | null = null;
    let lastStage = "挂载 webview";
    const setLastStage = (s: string) => {
      lastStage = s;
      if (!cancelled) setStage(s);
    };
    const pushLog = (s: string) => {
      if (cancelled) return;
      setLogs((prev) => (prev.length > 200 ? prev : [...prev, s]));
    };

    // 1) Webview → host:用 console-message 当回传通道
    const onConsole = (e: { message: string; level: number; line: number; sourceId: string }) => {
      if (e.message && e.message.startsWith(BRIDGE_MARKER)) {
        const json = e.message.slice(BRIDGE_MARKER.length);
        let data: { type?: string; ok?: boolean; error?: string } = {};
        try {
          data = JSON.parse(json);
        } catch {
          return;
        }
        if (cancelled) return;
        if (data.type === "midi_loaded") {
          setLastStage("MIDI 加载完成");
          if (data.ok) setStatus("ready");
          else { setStatus("error"); setErrorMsg("MIDI 解析失败"); }
        } else if (data.type === "midi_load_error") {
          setStatus("error");
          setErrorMsg(String(data.error || "未知错误"));
        } else if (data.type === "midi_iframe_error") {
          setStatus("error");
          setErrorMsg(String(data.error || "页面错误"));
        }
        return;
      }
      // 透传所有非 bridge 的 console 输出,便于诊断
      const levelTag = ["", "[warn]", "[err]"][e.level] || "";
      const src = e.sourceId ? e.sourceId.split(/[\\/]/).pop() : "";
      pushLog(`${levelTag} ${src}:${e.line} ${e.message}`.trim());
    };

    const tryInjectMidi = () => {
      if (cancelled || injected || !bridgeInstalled || !pendingUrl) return;
      injected = true;
      setLastStage("注入 load_midi_url");
      const code = `window.postMessage({type:'load_midi_url', url: ${JSON.stringify(
        pendingUrl,
      )}}, '*');`;
      wv.executeJavaScript(code).catch((err) => {
        if (cancelled) return;
        setStatus("error");
        setErrorMsg(`注入 url 失败: ${err}`);
      });
    };

    const onStartLoading = () => setLastStage("加载页面中");
    const onFinishLoad = () => setLastStage("页面加载完成 (window.load)");

    // 2) host → webview:dom-ready 后注入 bridge
    const onDomReady = () => {
      setLastStage("dom-ready,注入 bridge");
      // 自动开 webview DevTools,崩溃瞬间 Chrome 会显示具体错误码
      try { wv.openDevTools(); } catch {}
      const bridge = `(function(){
        if (window.__midi_bridge_installed__) return;
        window.__midi_bridge_installed__ = true;
        var marker = ${JSON.stringify(BRIDGE_MARKER)};
        var OUT = {midi_loaded:1, midi_load_error:1, midi_iframe_ready:1, midi_iframe_error:1};
        var send = function(data){
          try { console.log(marker + JSON.stringify(data)); } catch(e) {}
        };
        window.addEventListener('message', function(e){
          if (e.source !== window) return;
          if (!e.data || typeof e.data !== 'object') return;
          // 只转发出站类型,避免把巨大的 load_midi base64 回灌
          if (!OUT[e.data.type]) return;
          send(e.data);
        });
        window.addEventListener('error', function(e){
          send({type:'midi_iframe_error', error: e.message || String(e)});
        });
        window.addEventListener('unhandledrejection', function(e){
          send({type:'midi_iframe_error', error: (e.reason && e.reason.message) || String(e.reason)});
        });
      })();`;
      wv.executeJavaScript(bridge)
        .then(() => {
          bridgeInstalled = true;
          setLastStage("bridge 已就绪");
          tryInjectMidi();
        })
        .catch((err) => {
          if (cancelled) return;
          setStatus("error");
          setErrorMsg(`注入 bridge 失败: ${err}`);
        });
    };

    const onFailLoad = (e: { errorDescription: string; validatedURL: string }) => {
      if (cancelled) return;
      setStatus("error");
      setErrorMsg(`webview 加载失败: ${e.errorDescription} (${e.validatedURL})`);
    };

    const onCrashed = () => {
      if (cancelled) return;
      setStatus("error");
      setErrorMsg(`webview 进程崩溃,最后阶段:${lastStage}`);
    };

    wv.addEventListener("console-message", onConsole);
    wv.addEventListener("dom-ready", onDomReady);
    wv.addEventListener("did-start-loading", onStartLoading);
    wv.addEventListener("did-finish-load", onFinishLoad);
    wv.addEventListener("did-fail-load", onFailLoad);
    wv.addEventListener("crashed", onCrashed);

    (async () => {
      try {
        const url = await rawFileUrl(path);
        if (cancelled) return;
        pendingUrl = url;
        setLastStage(`MIDI URL 就绪`);
        tryInjectMidi();
      } catch (e) {
        if (cancelled) return;
        setStatus("error");
        setErrorMsg(`生成 URL 失败: ${e instanceof Error ? e.message : String(e)}`);
      }
    })();

    return () => {
      cancelled = true;
      wv.removeEventListener("console-message", onConsole as never);
      wv.removeEventListener("dom-ready", onDomReady as never);
      wv.removeEventListener("did-start-loading", onStartLoading as never);
      wv.removeEventListener("did-finish-load", onFinishLoad as never);
      wv.removeEventListener("did-fail-load", onFailLoad as never);
      wv.removeEventListener("crashed", onCrashed as never);
    };
  }, [path]);

  const openWebviewDevTools = () => {
    try {
      wvRef.current?.openDevTools();
    } catch (e) {
      console.error("openDevTools failed", e);
    }
  };

  return (
    <div className="flex-1 flex flex-col min-h-0 relative bg-bg">
      <webview
        ref={wvRef as unknown as React.Ref<HTMLElement>}
        src={srcRef.current}
        className="flex-1 w-full bg-white"
      />
      {status !== "ready" && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg/70 z-10 pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-fg-muted bg-bg-sidebar/95 border border-border rounded px-6 py-4 pointer-events-auto max-w-3xl w-[90%]">
            {status === "loading" ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span className="text-sm">加载 MIDI…</span>
                <span className="text-xs text-fg-subtle">阶段:{stage}</span>
              </>
            ) : (
              <>
                <AlertCircle size={20} className="text-danger" />
                <span className="text-sm text-danger">加载失败</span>
                {errorMsg && (
                  <span className="text-xs text-fg-muted text-center break-all max-w-xl">
                    {errorMsg}
                  </span>
                )}
                <button
                  onClick={openWebviewDevTools}
                  className="btn btn-secondary text-xs mt-1"
                >
                  打开 webview DevTools
                </button>
              </>
            )}
            {logs.length > 0 && (
              <details className="text-xs text-fg-subtle w-full mt-2">
                <summary className="cursor-pointer">webview 日志 ({logs.length})</summary>
                <pre className="bg-bg p-2 rounded max-h-48 overflow-auto whitespace-pre-wrap break-all mt-1">
                  {logs.join("\n")}
                </pre>
              </details>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function MidiViewer({ path }: Props) {
  const [enabled, setEnabled] = useState(false);
  const [mode, setMode] = useState<Mode>("main");

  useEffect(() => {
    setEnabled(false);
    setMode("main");
  }, [path]);

  if (!enabled) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-fg-muted p-6">
        <Music3 size={32} />
        <div className="flex flex-col items-center gap-1">
          <span className="text-sm">MIDI 文件</span>
          <span className="text-xs text-fg-subtle break-all max-w-md text-center">
            {basename(path)}
          </span>
        </div>
        <div className="flex gap-2 mt-2 flex-wrap justify-center">
          <button
            onClick={() => { setMode("main"); setEnabled(true); }}
            className="btn btn-primary inline-flex items-center gap-2"
          >
            <Play size={14} />
            加载多轨预览
          </button>
          <button
            onClick={() => { setMode("clean"); setEnabled(true); }}
            className="btn btn-secondary inline-flex items-center gap-2"
            title="不加载 magenta,只测 fetch / XHR"
          >
            诊断:无 magenta
          </button>
          <button
            onClick={() => { setMode("magenta"); setEnabled(true); }}
            className="btn btn-secondary inline-flex items-center gap-2"
            title="加载 magenta + SoundFontPlayer 后再 fetch"
          >
            诊断:magenta + fetch
          </button>
          <button
            onClick={() => { setMode("idle"); setEnabled(true); }}
            className="btn btn-secondary inline-flex items-center gap-2"
            title="加载 magenta + SoundFontPlayer 后空转 8 秒,看是否延迟自爆"
          >
            诊断:magenta 空转
          </button>
          <button
            onClick={() => window.electronAPI.openMidiPopup("/midi_test_idle.html")}
            className="btn btn-secondary inline-flex items-center gap-2"
            title="在独立 BrowserWindow(非 webview tag)里跑同样的 idle 页"
          >
            诊断:popup 窗口
          </button>
        </div>
        <span className="text-xs text-fg-subtle text-center max-w-md">
          预览运行在独立进程的 webview 里,即使内部出问题也不会影响主窗口。
        </span>
      </div>
    );
  }

  return <MidiWebview key={`${path}_${mode}`} path={path} mode={mode} />;
}

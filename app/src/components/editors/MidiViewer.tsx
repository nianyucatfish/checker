import { useEffect, useRef, useState } from "react";
import { Loader2, AlertCircle } from "lucide-react";
import { rawFileUrl, listDir } from "../../api";

interface Props {
  path: string;
}

// Electron <webview> 的方法/事件 React 类型不带,本地补一个
interface WebviewElement extends HTMLElement {
  executeJavaScript(code: string): Promise<unknown>;
  reload(): void;
  openDevTools(): void;
  addEventListener(type: "dom-ready", listener: () => void): void;
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

interface CompareWavPayload {
  files: string[];
  default: string | null;
  dir: string | null;
  urls: Record<string, string>;
}

// midi_player.html 期望的对比 WAV 数据(原 MidiExportBridge.getCompareWavList 的返回)。
// 老版从 <midi_parent>/../分轨wav 找 WAV 列表;默认选 *_vocal_a.wav,缺省取首项。
async function buildCompareWavPayload(midiPath: string): Promise<CompareWavPayload> {
  const empty: CompareWavPayload = { files: [], default: null, dir: null, urls: {} };
  if (!midiPath) return empty;
  const sep = midiPath.includes("\\") ? "\\" : "/";
  const parts = midiPath.split(/[\\/]/);
  if (parts.length < 3) return empty;
  // <song>/midi/X.mid → wav 目录在 <song>/分轨wav
  const wavDir = parts.slice(0, parts.length - 2).join(sep) + sep + "分轨wav";
  let entries;
  try {
    entries = await listDir(wavDir);
  } catch {
    return empty;
  }
  const files = entries.entries
    .filter((e) => !e.is_dir && e.ext.toLowerCase() === "wav")
    .map((e) => e.name)
    .sort();
  if (files.length === 0) return { ...empty, dir: wavDir };
  const vocal = files.find((n) => n.toLowerCase().endsWith("_vocal_a.wav"));
  const def = vocal || files[0];
  const urlEntries = await Promise.all(
    files.map(async (name) => [name, await rawFileUrl(wavDir + sep + name)] as const),
  );
  const urls: Record<string, string> = {};
  for (const [n, u] of urlEntries) urls[n] = u;
  return { files, default: def, dir: wavDir, urls };
}

function MidiWebview({ path }: { path: string }) {
  const wvRef = useRef<WebviewElement | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  // 每次 mount 用一个唯一 src,绕过 webview partition 缓存
  const srcRef = useRef<string>(`/midi_player.html?t=${Date.now()}`);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setErrorMsg(null);

    const wv = wvRef.current;
    if (!wv) return;

    let bridgeInstalled = false;
    let injected = false;
    let pendingUrl: string | null = null;
    let pendingComparePayload: CompareWavPayload | null = null;
    let comparePayloadInjected = false;

    // Webview → host:用 console-message 当回传通道
    const onConsole = (e: { message: string }) => {
      if (!e.message || !e.message.startsWith(BRIDGE_MARKER)) return;
      const json = e.message.slice(BRIDGE_MARKER.length);
      let data: { type?: string; ok?: boolean; error?: string } = {};
      try {
        data = JSON.parse(json);
      } catch {
        return;
      }
      if (cancelled) return;
      if (data.type === "midi_loaded") {
        if (data.ok) setStatus("ready");
        else { setStatus("error"); setErrorMsg("MIDI 解析失败"); }
      } else if (data.type === "midi_load_error") {
        setStatus("error");
        setErrorMsg(String(data.error || "未知错误"));
      } else if (data.type === "midi_iframe_error") {
        setStatus("error");
        setErrorMsg(String(data.error || "页面错误"));
      }
    };

    // 把对比 WAV 数据注成 window.exportBridge,让 midi_player.html 里
    // QWebChannel 那条路径走不通时也能拉到列表(老版 MidiExportBridge 的 JS 替身)。
    // saveMidi* 暂不支持,返回错误字串。
    const injectExportBridge = async (payload: CompareWavPayload) => {
      if (cancelled || comparePayloadInjected) return;
      comparePayloadInjected = true;
      const json = JSON.stringify(payload);
      const code = `(function(){
        var payload = ${json};
        var urls = payload.urls || {};
        window.exportBridge = {
          getCompareWavList: function(cb){
            try { cb(JSON.stringify(payload)); } catch(e) {}
          },
          getCompareWavUrl: function(name, cb){
            try {
              var u = urls[name];
              if (u) cb(JSON.stringify({ok:true, name:name, mime:'audio/wav', url:u}));
              else   cb(JSON.stringify({ok:false, error:'文件不在列表中'}));
            } catch(e) {}
          },
          saveMidiBase64: function(){ return 'ERROR: 保存功能在新版尚未接入'; },
          saveMidiToCurrentPath: function(){ return 'ERROR: 保存功能在新版尚未接入'; },
        };
      })();`;
      try {
        await wv.executeJavaScript(code);
      } catch (e) {
        console.warn("[midi] inject exportBridge failed:", e);
      }
    };

    const tryInjectMidi = async () => {
      if (cancelled || injected || !bridgeInstalled || !pendingUrl) return;
      injected = true;
      // 在 load_midi_url 前先注 exportBridge,确保页内 WAV 列表加载逻辑有数据
      if (pendingComparePayload) {
        await injectExportBridge(pendingComparePayload);
      }
      const code = `window.postMessage({type:'load_midi_url', url: ${JSON.stringify(
        pendingUrl,
      )}}, '*');`;
      wv.executeJavaScript(code).catch((err) => {
        if (cancelled) return;
        setStatus("error");
        setErrorMsg(`注入 url 失败: ${err}`);
      });
    };

    // host → webview:dom-ready 后注入 bridge
    const onDomReady = () => {
      // bridge:把 page 内出站 postMessage 转成 console.log 标记发给 host
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
      setErrorMsg("webview 进程崩溃");
    };

    wv.addEventListener("console-message", onConsole);
    wv.addEventListener("dom-ready", onDomReady);
    wv.addEventListener("did-fail-load", onFailLoad);
    wv.addEventListener("crashed", onCrashed);

    // 并发拉 MIDI URL + 对比 WAV 列表
    void (async () => {
      try {
        const [midiUrl, comparePayload] = await Promise.all([
          rawFileUrl(path),
          buildCompareWavPayload(path).catch(() => ({
            files: [],
            default: null,
            dir: null,
            urls: {},
          } as CompareWavPayload)),
        ]);
        if (cancelled) return;
        pendingUrl = midiUrl;
        pendingComparePayload = comparePayload;
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
      wv.removeEventListener("did-fail-load", onFailLoad as never);
      wv.removeEventListener("crashed", onCrashed as never);
    };
  }, [path]);

  return (
    <div className="flex-1 flex flex-col min-h-0 relative bg-bg">
      <webview
        ref={wvRef as unknown as React.Ref<HTMLElement>}
        src={srcRef.current}
        className="flex-1 w-full bg-white"
      />
      {status !== "ready" && (
        <div className="absolute inset-0 flex items-center justify-center bg-bg/70 z-10 pointer-events-none">
          <div className="flex flex-col items-center gap-2 text-fg-muted bg-bg-sidebar/95 border border-border rounded px-6 py-4 pointer-events-auto max-w-2xl">
            {status === "loading" ? (
              <>
                <Loader2 size={20} className="animate-spin" />
                <span className="text-sm">加载 MIDI…</span>
              </>
            ) : (
              <>
                <AlertCircle size={20} className="text-danger" />
                <span className="text-sm text-danger">加载失败</span>
                {errorMsg && (
                  <span className="text-xs text-fg-muted text-center break-all max-w-md">
                    {errorMsg}
                  </span>
                )}
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export function MidiViewer({ path }: Props) {
  return <MidiWebview key={path} path={path} />;
}

import { useEffect, useState } from "react";
import { Eye, EyeOff, Loader2, X } from "lucide-react";
import { getLlmConfig, saveLlmConfig, testLlmConfig } from "../api";
import { clsx } from "../utils";

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [protocol, setProtocol] = useState("openai");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [keyMasked, setKeyMasked] = useState("");
  const [keySet, setKeySet] = useState(false);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getLlmConfig()
      .then((c) => {
        setProtocol(c.protocol || "openai");
        setEndpoint(c.endpoint);
        setModel(c.model);
        setApiKey(c.api_key || "");
        setKeyMasked(c.key_masked);
        setKeySet(c.key_set);
      })
      .catch((e) => setMsg({ ok: false, text: `读取配置失败: ${e instanceof Error ? e.message : String(e)}` }))
      .finally(() => setLoading(false));
  }, []);

  const persist = () =>
    saveLlmConfig({ protocol, endpoint, model, api_key: apiKey });

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const c = await persist();
      setKeyMasked(c.key_masked);
      setKeySet(c.key_set);
      setApiKey(c.api_key || apiKey);
      setMsg({ ok: true, text: `已保存到 ${c.config_path}` });
    } catch (e) {
      setMsg({ ok: false, text: `保存失败: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setSaving(false);
    }
  };

  const test = async () => {
    setTesting(true);
    setMsg(null);
    try {
      await persist(); // 先存,确保测的是界面上的配置
      const r = await testLlmConfig();
      setMsg(r.ok
        ? { ok: true, text: `连接成功:${r.preview || "(空响应)"}` }
        : { ok: false, text: `连接失败:${r.error}` });
    } catch (e) {
      setMsg({ ok: false, text: `测试失败: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setTesting(false);
    }
  };

  const lbl = "text-xs text-fg-muted mb-1";
  const inp = "w-full text-sm rounded border border-border bg-bg px-2 py-1.5 outline-none focus:border-accent";
  const keyInput = "w-full text-sm rounded border border-border bg-bg py-1.5 pl-2 pr-9 outline-none focus:border-accent";

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8">
      <div
        className="bg-bg-sidebar border border-border rounded-md flex flex-col w-[480px] max-h-[85vh]"
      >
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <div className="text-sm font-medium text-fg">设置 · LLM API</div>
          <button onClick={onClose} className="text-fg-muted hover:text-fg p-1 rounded hover:bg-bg-hover">
            <X size={14} />
          </button>
        </div>

        {loading ? (
          <div className="p-6 flex items-center gap-2 text-sm text-fg-muted">
            <Loader2 size={14} className="animate-spin" /> 加载中…
          </div>
        ) : (
          <div className="p-4 flex flex-col gap-3 overflow-y-auto">
            <div>
              <div className={lbl}>协议</div>
              <select className={inp} value={protocol} onChange={(e) => setProtocol(e.target.value)}>
                <option value="openai">OpenAI 兼容</option>
                <option value="anthropic">Anthropic 原生</option>
              </select>
            </div>
            <div>
              <div className={lbl}>Endpoint(base 或完整 chat URL)</div>
              <input className={inp} value={endpoint} onChange={(e) => setEndpoint(e.target.value)} placeholder="https://api.deepseek.com" />
            </div>
            <div>
              <div className={lbl}>模型</div>
              <input className={inp} value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
            </div>
            <div>
              <div className={lbl}>
                API Key{keySet && !showApiKey && <span className="text-fg-subtle"> (当前 {keyMasked})</span>}
              </div>
              <div className="relative">
                <input
                  className={keyInput}
                  type={showApiKey ? "text" : "password"}
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="粘贴 API key"
                  autoComplete="off"
                />
                <button
                  type="button"
                  onClick={() => setShowApiKey((v) => !v)}
                  className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-fg-muted hover:bg-bg-hover hover:text-fg"
                  title={showApiKey ? "隐藏 API Key" : "显示 API Key"}
                >
                  {showApiKey ? <EyeOff size={14} /> : <Eye size={14} />}
                </button>
              </div>
            </div>

            {msg && (
              <div className={clsx("text-xs rounded px-2 py-1.5 break-all", msg.ok ? "text-green-600 bg-green-500/10" : "text-red-500 bg-red-500/10")}>
                {msg.text}
              </div>
            )}

            <div className="flex items-center gap-2 pt-1">
              <button onClick={save} disabled={saving || testing} className="text-sm px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50 inline-flex items-center gap-1">
                {saving && <Loader2 size={12} className="animate-spin" />} 保存
              </button>
              <button onClick={test} disabled={saving || testing} className="text-sm px-3 py-1.5 rounded border border-border text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-50 inline-flex items-center gap-1">
                {testing && <Loader2 size={12} className="animate-spin" />} 测试连接
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

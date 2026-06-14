import { useEffect, useState } from "react";
import { Loader2, X } from "lucide-react";
import { getLlmConfig, saveLlmConfig, testLlmConfig } from "../api";
import { clsx } from "../utils";

interface Preset {
  name: string;
  protocol: string;
  endpoint: string;
  model: string;
}

// 厂商预设:选了自动填 endpoint/protocol/model。base 不带 /v1,代理自动补 /v1/chat/completions。
const PRESETS: Preset[] = [
  { name: "DeepSeek", protocol: "openai", endpoint: "https://api.deepseek.com", model: "deepseek-chat" },
  { name: "OpenAI", protocol: "openai", endpoint: "https://api.openai.com", model: "gpt-4o" },
  { name: "OpenRouter", protocol: "openai", endpoint: "https://openrouter.ai/api", model: "" },
  { name: "Moonshot / Kimi", protocol: "openai", endpoint: "https://api.moonshot.cn", model: "kimi-k2-0905-preview" },
  { name: "智谱 GLM", protocol: "openai", endpoint: "https://open.bigmodel.cn/api/paas", model: "glm-4" },
  { name: "SiliconFlow", protocol: "openai", endpoint: "https://api.siliconflow.cn", model: "" },
  { name: "Anthropic(原生)", protocol: "anthropic", endpoint: "https://api.anthropic.com", model: "claude-sonnet-4-6" },
  { name: "Ollama(本地)", protocol: "openai", endpoint: "http://localhost:11434", model: "" },
];

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [loading, setLoading] = useState(true);
  const [protocol, setProtocol] = useState("openai");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");       // 新输入的 key,空 = 不改
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
        setKeyMasked(c.key_masked);
        setKeySet(c.key_set);
      })
      .catch((e) => setMsg({ ok: false, text: `读取配置失败: ${e instanceof Error ? e.message : String(e)}` }))
      .finally(() => setLoading(false));
  }, []);

  const applyPreset = (name: string) => {
    const p = PRESETS.find((x) => x.name === name);
    if (!p) return;
    setProtocol(p.protocol);
    setEndpoint(p.endpoint);
    if (p.model) setModel(p.model);
  };

  const persist = () =>
    saveLlmConfig({ protocol, endpoint, model, api_key: apiKey || undefined });

  const save = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const c = await persist();
      setKeyMasked(c.key_masked);
      setKeySet(c.key_set);
      setApiKey("");
      setMsg({ ok: true, text: "已保存(下条消息生效)" });
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
      setApiKey("");
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

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8" onClick={onClose}>
      <div
        className="bg-bg-sidebar border border-border rounded-md flex flex-col w-[480px] max-h-[85vh]"
        onClick={(e) => e.stopPropagation()}
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
              <div className={lbl}>厂商预设(选了自动填 endpoint / 协议 / 模型)</div>
              <select className={inp} defaultValue="" onChange={(e) => { applyPreset(e.target.value); e.currentTarget.value = ""; }}>
                <option value="" disabled>选一个预设…</option>
                {PRESETS.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
              </select>
            </div>
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
                API Key{keySet && <span className="text-fg-subtle">(现有 {keyMasked},留空不改)</span>}
              </div>
              <input
                className={inp}
                type="password"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={keySet ? "保留现有 key" : "粘贴 API key"}
              />
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

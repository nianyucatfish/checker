import { useEffect, useState } from "react";
import { Eye, EyeOff, Loader2, X } from "lucide-react";
import {
  getLlmConfig,
  getTencentDocsConfig,
  saveLlmConfig,
  saveTencentDocsConfig,
  testLlmConfig,
} from "../api";
import { clsx } from "../utils";

type Tab = "llm" | "tencent";

export function SettingsModal({ onClose }: { onClose: () => void }) {
  const [tab, setTab] = useState<Tab>("llm");

  const [llmLoading, setLlmLoading] = useState(true);
  const [protocol, setProtocol] = useState("openai");
  const [endpoint, setEndpoint] = useState("");
  const [model, setModel] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [showApiKey, setShowApiKey] = useState(false);
  const [llmKeyMasked, setLlmKeyMasked] = useState("");
  const [llmKeySet, setLlmKeySet] = useState(false);
  const [llmSaving, setLlmSaving] = useState(false);
  const [llmTesting, setLlmTesting] = useState(false);
  const [llmMsg, setLlmMsg] = useState<{ ok: boolean; text: string } | null>(null);

  const [tLoading, setTLoading] = useState(true);
  const [clientId, setClientId] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [openId, setOpenId] = useState("");
  const [spreadsheetId, setSpreadsheetId] = useState("");
  const [sheetId, setSheetId] = useState("");
  const [tokenExpiresAt, setTokenExpiresAt] = useState("");
  const [reviewerName, setReviewerName] = useState("");
  const [showToken, setShowToken] = useState(false);
  const [tokenMasked, setTokenMasked] = useState("");
  const [tokenSet, setTokenSet] = useState(false);
  const [tSaving, setTSaving] = useState(false);
  const [tMsg, setTMsg] = useState<{ ok: boolean; text: string } | null>(null);

  useEffect(() => {
    getLlmConfig()
      .then((c) => {
        setProtocol(c.protocol || "openai");
        setEndpoint(c.endpoint);
        setModel(c.model);
        setApiKey(c.api_key || "");
        setLlmKeyMasked(c.key_masked);
        setLlmKeySet(c.key_set);
      })
      .catch((e) => setLlmMsg({ ok: false, text: `读取失败: ${e instanceof Error ? e.message : String(e)}` }))
      .finally(() => setLlmLoading(false));

    getTencentDocsConfig()
      .then((c) => {
        setClientId(c.client_id);
        setAccessToken(c.access_token || "");
        setOpenId(c.open_id);
        setSpreadsheetId(c.spreadsheet_id);
        setSheetId(c.sheet_id);
        setTokenExpiresAt(c.access_token_expires_at);
        setReviewerName(c.reviewer_name);
        setTokenMasked(c.token_masked);
        setTokenSet(c.token_set);
      })
      .catch((e) => setTMsg({ ok: false, text: `读取失败: ${e instanceof Error ? e.message : String(e)}` }))
      .finally(() => setTLoading(false));
  }, []);

  const saveLlm = async () => {
    setLlmSaving(true);
    setLlmMsg(null);
    try {
      const c = await saveLlmConfig({ protocol, endpoint, model, api_key: apiKey });
      setLlmKeyMasked(c.key_masked);
      setLlmKeySet(c.key_set);
      setApiKey(c.api_key || apiKey);
      setLlmMsg({ ok: true, text: `已保存到 ${c.config_path}` });
    } catch (e) {
      setLlmMsg({ ok: false, text: `保存失败: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setLlmSaving(false);
    }
  };

  const testLlm = async () => {
    setLlmTesting(true);
    setLlmMsg(null);
    try {
      await saveLlmConfig({ protocol, endpoint, model, api_key: apiKey });
      const r = await testLlmConfig();
      setLlmMsg(r.ok
        ? { ok: true, text: `连接成功:${r.preview || "(空响应)"}` }
        : { ok: false, text: `连接失败:${r.error}` });
    } catch (e) {
      setLlmMsg({ ok: false, text: `测试失败: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setLlmTesting(false);
    }
  };

  const saveTencent = async () => {
    setTSaving(true);
    setTMsg(null);
    try {
      const c = await saveTencentDocsConfig({
        client_id: clientId,
        access_token: accessToken,
        open_id: openId,
        spreadsheet_id: spreadsheetId,
        sheet_id: sheetId,
        reviewer_name: reviewerName,
      });
      setTokenMasked(c.token_masked);
      setTokenSet(c.token_set);
      setAccessToken(c.access_token || accessToken);
      setTMsg({ ok: true, text: `已保存到 ${c.config_path}` });
    } catch (e) {
      setTMsg({ ok: false, text: `保存失败: ${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setTSaving(false);
    }
  };

  const lbl = "text-xs text-fg-muted mb-1";
  const inp = "w-full text-sm rounded border border-border bg-bg px-2 py-1.5 outline-none focus:border-accent";
  const keyInput = "w-full text-sm rounded border border-border bg-bg py-1.5 pl-2 pr-9 outline-none focus:border-accent";

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-8">
      <div className="bg-bg-sidebar border border-border rounded-md flex flex-col w-[480px] max-h-[85vh]">
        <div className="flex items-center justify-between px-4 py-2 border-b border-border">
          <div className="text-sm font-medium text-fg">设置</div>
          <button onClick={onClose} className="text-fg-muted hover:text-fg p-1 rounded hover:bg-bg-hover">
            <X size={14} />
          </button>
        </div>

        <div className="flex border-b border-border">
          <button
            onClick={() => setTab("llm")}
            className={clsx(
              "flex-1 text-sm py-2 text-center border-b-2 transition-colors",
              tab === "llm"
                ? "border-accent text-fg font-medium"
                : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            LLM API
          </button>
          <button
            onClick={() => setTab("tencent")}
            className={clsx(
              "flex-1 text-sm py-2 text-center border-b-2 transition-colors",
              tab === "tencent"
                ? "border-accent text-fg font-medium"
                : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            腾讯文档 · 用户
          </button>
        </div>

        {tab === "llm" && (
          llmLoading ? (
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
                  API Key{llmKeySet && !showApiKey && <span className="text-fg-subtle"> (当前 {llmKeyMasked})</span>}
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

              {llmMsg && (
                <div className={clsx("text-xs rounded px-2 py-1.5 break-all", llmMsg.ok ? "text-green-600 bg-green-500/10" : "text-red-500 bg-red-500/10")}>
                  {llmMsg.text}
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                <button onClick={saveLlm} disabled={llmSaving || llmTesting} className="text-sm px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50 inline-flex items-center gap-1">
                  {llmSaving && <Loader2 size={12} className="animate-spin" />} 保存
                </button>
                <button onClick={testLlm} disabled={llmSaving || llmTesting} className="text-sm px-3 py-1.5 rounded border border-border text-fg-muted hover:text-fg hover:bg-bg-hover disabled:opacity-50 inline-flex items-center gap-1">
                  {llmTesting && <Loader2 size={12} className="animate-spin" />} 测试连接
                </button>
              </div>
            </div>
          )
        )}

        {tab === "tencent" && (
          tLoading ? (
            <div className="p-6 flex items-center gap-2 text-sm text-fg-muted">
              <Loader2 size={14} className="animate-spin" /> 加载中…
            </div>
          ) : (
            <div className="p-4 flex flex-col gap-3 overflow-y-auto">
              <div>
                <div className={lbl}>Client ID</div>
                <input className={inp} value={clientId} onChange={(e) => setClientId(e.target.value)} placeholder="从 docs.qq.com/open 复制" />
              </div>
              <div>
                <div className={lbl}>
                  Access Token{tokenSet && !showToken && <span className="text-fg-subtle"> (当前 {tokenMasked})</span>}
                  {tokenExpiresAt && <span className="text-fg-subtle"> · 过期: {tokenExpiresAt}</span>}
                </div>
                <div className="relative">
                  <input
                    className={keyInput}
                    type={showToken ? "text" : "password"}
                    value={accessToken}
                    onChange={(e) => setAccessToken(e.target.value)}
                    placeholder="粘贴 access_token"
                    autoComplete="off"
                  />
                  <button
                    type="button"
                    onClick={() => setShowToken((v) => !v)}
                    className="absolute right-1.5 top-1/2 -translate-y-1/2 rounded p-1 text-fg-muted hover:bg-bg-hover hover:text-fg"
                    title={showToken ? "隐藏 Access Token" : "显示 Access Token"}
                  >
                    {showToken ? <EyeOff size={14} /> : <Eye size={14} />}
                  </button>
                </div>
              </div>
              <div>
                <div className={lbl}>Open ID</div>
                <input className={inp} value={openId} onChange={(e) => setOpenId(e.target.value)} placeholder="从 docs.qq.com/open 复制" />
              </div>
              <div>
                <div className={lbl}>分工表 ID (spreadsheet_id)</div>
                <input className={inp} value={spreadsheetId} onChange={(e) => setSpreadsheetId(e.target.value)} placeholder="从分工表 URL 里抠" />
              </div>
              <div>
                <div className={lbl}>子表 ID (sheet_id)</div>
                <input className={inp} value={sheetId} onChange={(e) => setSheetId(e.target.value)} placeholder="单 sheet 表格可留空" />
              </div>
              <div className="border-t border-border pt-3 mt-1">
                <div className={lbl}>验收负责人姓名</div>
                <input className={inp} value={reviewerName} onChange={(e) => setReviewerName(e.target.value)} placeholder="你在分工表里的名字" />
                <div className="text-xs text-fg-subtle mt-1">用于过滤"我的待办"，不会发给 AI</div>
              </div>

              {tMsg && (
                <div className={clsx("text-xs rounded px-2 py-1.5 break-all", tMsg.ok ? "text-green-600 bg-green-500/10" : "text-red-500 bg-red-500/10")}>
                  {tMsg.text}
                </div>
              )}

              <div className="flex items-center gap-2 pt-1">
                <button onClick={saveTencent} disabled={tSaving} className="text-sm px-3 py-1.5 rounded bg-accent text-white hover:opacity-90 disabled:opacity-50 inline-flex items-center gap-1">
                  {tSaving && <Loader2 size={12} className="animate-spin" />} 保存
                </button>
              </div>
            </div>
          )
        )}
      </div>
    </div>
  );
}

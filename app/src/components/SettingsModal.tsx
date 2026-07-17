import { useEffect, useState } from "react";
import { Eye, EyeOff, Loader2, PackageCheck, RefreshCw, X } from "lucide-react";
import {
  getLlmConfig,
  getTencentDocsConfig,
  saveLlmConfig,
  saveTencentDocsConfig,
  testLlmConfig,
} from "../api";
import { clsx } from "../utils";

type Tab = "llm" | "tencent" | "update";

type UpdateInfo = Awaited<ReturnType<typeof window.electronAPI.updateInfo>>;
type InspectedUpdate = Awaited<ReturnType<typeof window.electronAPI.updateInspect>>;

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

  const [updateInfo, setUpdateInfo] = useState<UpdateInfo | null>(null);
  const [updateBusy, setUpdateBusy] = useState(false);
  const [inspectedUpdate, setInspectedUpdate] = useState<InspectedUpdate | null>(null);
  const [updateMsg, setUpdateMsg] = useState<{ ok: boolean; text: string } | null>(null);

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

    window.electronAPI.updateInfo().then(setUpdateInfo).catch((e) => {
      setUpdateMsg({ ok: false, text: `读取版本信息失败:${e instanceof Error ? e.message : String(e)}` });
    });

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

  const inspectUpdatePath = async (zipPath: string) => {
    setUpdateBusy(true);
    setUpdateMsg(null);
    setInspectedUpdate(null);
    try {
      const inspected = await window.electronAPI.updateInspect(zipPath);
      setInspectedUpdate(inspected);
      setUpdateMsg({ ok: true, text: "产品名、平台、架构和文件哈希均匹配；该包未验证发布者身份。" });
    } catch (e) {
      setUpdateMsg({ ok: false, text: `更新包无效:${e instanceof Error ? e.message : String(e)}` });
    } finally {
      setUpdateBusy(false);
    }
  };

  const chooseUpdate = async () => {
    const zipPath = await window.electronAPI.updateSelectZip();
    if (zipPath) await inspectUpdatePath(zipPath);
  };

  const applyUpdate = async () => {
    if (!inspectedUpdate) return;
    const confirmed = await window.electronAPI.showConfirm(
      `将更新到 Audio QC ${inspectedUpdate.manifest.version}。应用会退出并重启，用户数据不会被覆盖。\n\n警告：当前更新包没有发布者数字签名，SHA-256 只能发现文件损坏，不能证明来源。请仅安装你从可信发布渠道取得并核对 SHA256SUMS.txt 的包。继续吗？`,
    );
    if (!confirmed) return;
    setUpdateBusy(true);
    setUpdateMsg(null);
    try {
      await window.electronAPI.updateApply();
    } catch (e) {
      setUpdateBusy(false);
      setUpdateMsg({ ok: false, text: `启动更新失败:${e instanceof Error ? e.message : String(e)}` });
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
      // AgentSidebar 的"表格·本地"徽章监听这个事件重查 sheet_mode
      window.dispatchEvent(new CustomEvent("sheet-config-changed"));
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
          <button
            onClick={() => setTab("update")}
            className={clsx(
              "flex-1 text-sm py-2 text-center border-b-2 transition-colors",
              tab === "update"
                ? "border-accent text-fg font-medium"
                : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            离线更新
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

        {tab === "update" && (
          <div className="p-4 flex flex-col gap-4 overflow-y-auto">
            <div className="rounded border border-border bg-bg/60 p-3">
              <div className="flex items-center gap-2 text-sm font-medium text-fg">
                <RefreshCw size={14} className="text-accent" /> 离线 Release 更新
              </div>
              <div className="mt-2 grid grid-cols-[88px_1fr] gap-y-1 text-xs">
                <span className="text-fg-subtle">当前版本</span>
                <span className="font-mono text-fg">{updateInfo?.currentVersion ?? "读取中…"}</span>
                <span className="text-fg-subtle">运行平台</span>
                <span className="font-mono text-fg">{updateInfo ? `${updateInfo.platform} / ${updateInfo.arch}` : "—"}</span>
                <span className="text-fg-subtle">构建类型</span>
                <span className="text-fg-muted">{updateInfo?.packaged ? "发布包" : "开发模式（仅可校验）"}</span>
              </div>
            </div>

            <div className="text-xs leading-relaxed text-fg-muted">
              选择同平台、同架构的 Audio QC Release ZIP。程序会验证产品名、版本、完整文件清单和 SHA-256，再允许重启更新；配置、聊天和质检进度不会被覆盖。当前机制不验证发布者身份，请只使用可信渠道取得并核对 SHA256SUMS.txt 的包。
            </div>

            <button
              type="button"
              onClick={chooseUpdate}
              onDragOver={(event) => {
                event.preventDefault();
                event.dataTransfer.dropEffect = "copy";
              }}
              onDrop={(event) => {
                event.preventDefault();
                const files = Array.from(event.dataTransfer.files);
                if (files.length !== 1) {
                  setUpdateMsg({ ok: false, text: "请只拖入一个 Release ZIP。" });
                  return;
                }
                const zipPath = window.electronAPI.getPathForFile(files[0]);
                if (!zipPath.toLowerCase().endsWith(".zip")) {
                  setUpdateMsg({ ok: false, text: "离线更新只接受 .zip 文件。" });
                  return;
                }
                void inspectUpdatePath(zipPath);
              }}
              disabled={updateBusy}
              className="inline-flex items-center justify-center gap-2 rounded border border-dashed border-border px-3 py-5 text-sm text-fg-muted hover:border-accent hover:bg-bg-hover hover:text-fg disabled:opacity-50"
            >
              {updateBusy ? <Loader2 size={15} className="animate-spin" /> : <PackageCheck size={15} />}
              拖入或选择 Release ZIP
            </button>

            {inspectedUpdate && (
              <div className="rounded border border-green-600/30 bg-green-500/10 p-3 text-xs">
                <div className="font-medium text-green-600">Audio QC {inspectedUpdate.manifest.version}</div>
                <div className="mt-1 text-fg-muted">
                  {inspectedUpdate.manifest.platform} / {inspectedUpdate.manifest.arch} · {inspectedUpdate.fileCount} 个版本文件
                </div>
                <div className="mt-1 break-all text-fg-subtle">{inspectedUpdate.zipPath}</div>
              </div>
            )}

            {updateMsg && (
              <div className={clsx("rounded px-2 py-1.5 text-xs break-all", updateMsg.ok ? "text-green-600 bg-green-500/10" : "text-red-500 bg-red-500/10")}>
                {updateMsg.text}
              </div>
            )}

            {inspectedUpdate && (
              <button
                type="button"
                onClick={applyUpdate}
                disabled={updateBusy || !updateInfo?.packaged}
                className="self-start rounded bg-accent px-3 py-1.5 text-sm text-white hover:opacity-90 disabled:opacity-40"
              >
                重启并更新
              </button>
            )}
            <div className="text-[11px] leading-relaxed text-fg-subtle">
              当前离线包是 unsigned draft：哈希只校验完整性，不证明来源。正式对外发布前必须增加更新清单签名；macOS 公证 / Windows 代码签名不能替代应用内清单验签。
            </div>
          </div>
        )}

        {tab === "tencent" && (
          tLoading ? (
            <div className="p-6 flex items-center gap-2 text-sm text-fg-muted">
              <Loader2 size={14} className="animate-spin" /> 加载中…
            </div>
          ) : (
            <div className="p-4 flex flex-col gap-3 overflow-y-auto">
              <div className="text-xs text-fg-subtle leading-relaxed">
                本区配置<b>可选</b>:没有腾讯开发者账号也能正常质检——分工表相关检查会降级为人工核对,本地检查照常进行。
              </div>
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

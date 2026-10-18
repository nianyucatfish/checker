const toggle = document.getElementById('toggle');
const hint = document.getElementById('hint');
function render(response) {
  if (!response?.ok) throw new Error('unavailable');
  const enabled = response.enabled !== false;
  const connected = response.data === 'connected';
  toggle.setAttribute('aria-checked', String(enabled));
  document.getElementById('state').textContent = !enabled ? '桥接已停用' : connected ? '已连接至本机' : response.data === 'connecting' ? '正在连接本机' : '等待本机服务';
  document.getElementById('dot').style.background = !enabled ? '#a5acb6' : connected ? '#32a34a' : '#d69b36';
  hint.textContent = enabled && !connected ? '启动 GA 或兼容的本机服务后，将自动连接。' : '';
}
async function refresh() {
  toggle.disabled = true;
  try { render(await chrome.runtime.sendMessage({ cmd: 'status' })); }
  catch { hint.textContent = '无法读取状态，请在扩展管理页面重新加载插件。'; }
  finally { toggle.disabled = false; }
}
toggle.onclick = async () => {
  toggle.disabled = true;
  try { render(await chrome.runtime.sendMessage({ cmd: 'set_enabled', enabled: toggle.getAttribute('aria-checked') !== 'true' })); }
  catch { hint.textContent = '设置失败，请重试。'; }
  finally { toggle.disabled = false; }
};
document.getElementById('copy').onclick = async () => {
  try { await navigator.clipboard.writeText('127.0.0.1:18765'); hint.textContent = '地址已复制'; }
  catch { hint.textContent = '复制失败，请重试。'; }
};
const more = document.getElementById('more');
const actions = document.getElementById('actions');
more.onclick = () => { actions.hidden = !actions.hidden; more.setAttribute('aria-expanded', String(!actions.hidden)); };
document.addEventListener('click', e => { if (!e.target.closest('.menu')) { actions.hidden = true; more.setAttribute('aria-expanded', 'false'); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape') { actions.hidden = true; more.setAttribute('aria-expanded', 'false'); } });
document.getElementById('refresh').onclick = () => { actions.hidden = true; more.setAttribute('aria-expanded', 'false'); void refresh(); };
chrome.runtime.onMessage.addListener(m => { if (m?.type === 'tmwd_status') void refresh(); });
void refresh();

// Checker adaptation: stay invisible while connected; show a quiet failure notice.
;(function () {
  if (window.self !== window.top || /streamlit/i.test(document.title)) return;
  const host = document.createElement('div');
  host.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:2147483647';
  const shadow = host.attachShadow({ mode: 'closed' });
  const style = document.createElement('style');
  style.textContent = 'button{font:12px/1.5 system-ui,sans-serif;color:#4b5563;background:#fff;border:1px solid #e5e7eb;border-radius:20px;padding:7px 12px;box-shadow:0 2px 10px #0001;cursor:pointer}span{display:inline-block;width:6px;height:6px;background:#d99a34;border-radius:50%;margin-right:7px}';
  const button = document.createElement('button');
  button.type = 'button';
  button.title = '点击隐藏；可从浏览器工具栏查看连接状态';
  let dismissed = false;
  button.onclick = () => { dismissed = true; host.hidden = true; };
  shadow.append(style, button);
  host.hidden = true;
  (document.body || document.documentElement).appendChild(host);
  function render(state) {
    if (state === 'connected' || state === 'disabled') { dismissed = false; host.hidden = true; return; }
    button.replaceChildren();
    button.append(document.createElement('span'), document.createTextNode(
      state === 'connecting' ? 'GA 浏览器正在连接' : 'GA 浏览器未连接'));
    host.hidden = dismissed;
  }
  chrome.runtime.onMessage.addListener(message => {
    if (message?.type === 'tmwd_status') render(message.data);
  });
  chrome.runtime.sendMessage({ cmd: 'status' }, response => {
    render(chrome.runtime.lastError || !response?.ok ? 'disconnected' : response.data);
  });
})();

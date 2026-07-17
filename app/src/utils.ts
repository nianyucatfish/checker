export function clsx(...args: (string | undefined | null | false)[]): string {
  return args.filter(Boolean).join(" ");
}

// window.alert / window.confirm 的替代。Electron 下原生对话框关闭后有 focus
// 后遗症:页面 input 拿得到 DOM 焦点却收不到键盘输入,切出窗口再切回才恢复
// (表现为"重命名框光标消失打不了字"),所以统一改走主进程 dialog.showMessageBox。
// electronAPI 不在(纯浏览器调试 / 旧 preload)时回退原生实现。
export async function appAlert(message: string): Promise<void> {
  if (window.electronAPI?.showAlert) return window.electronAPI.showAlert(message);
  window.alert(message);
}

export async function appConfirm(message: string): Promise<boolean> {
  if (window.electronAPI?.showConfirm) return window.electronAPI.showConfirm(message);
  return window.confirm(message);
}

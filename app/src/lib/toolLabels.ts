function filename(value: unknown): string {
  return typeof value === "string" ? value.replace(/[\\/]+$/, "").split(/[\\/]/).pop() ?? "" : "";
}

export function toolActionLabel(name: string, args: unknown): string {
  const a = args && typeof args === "object" ? args as Record<string, unknown> : {};
  const target = filename(a.path);
  const file = (verb: string) => target ? `${verb} ${target} 文件` : `${verb}文件`;
  switch (name) {
    case "web_scan": return a.tabs_only === true ? "扫描浏览器标签" : "查看浏览器页面";
    case "web_execute_js": return "执行浏览器操作";
    case "code_run": return "运行代码";
    case "file_read": case "read_text_file": return file("阅读");
    case "file_write": return file(a.mode === "append" ? "追加内容到" : "写入");
    case "file_patch": return file("修改");
    case "fs_list_dir": return target ? `查看 ${target} 目录` : "查看目录内容";
    case "start_qc": return typeof a.song === "string" && a.song ? `定位歌曲：${a.song}` : "定位歌曲";
    case "state_tree_read": return "读取歌曲检查进度";
    case "state_tree_update": return "更新歌曲检查进度";
    case "audit_list_errors": return "检查歌曲文件问题";
    case "sheet_get_song_meta": return "查询歌曲分工信息";
    case "sheet_list_my_pending": return "查询待验收歌曲";
    case "fix_execute_plan": return a.simulate === true ? "预览文件修复方案" : "执行文件修复";
    case "ui_open_file": return file("打开");
    case "mix_load_song": return "加载歌曲到混音台";
    case "playback_toggle_beat_render": return a.on === false ? "关闭节拍辅助" : "开启节拍辅助";
    case "playback_toggle_structure_render": return a.on === false ? "关闭段落辅助" : "开启段落辅助";
    case "human_check": return "请求人工确认";
    default: return `调用工具：${name}`;
  }
}

// App(IPC 桥接层)与 AudioViewer(执行层)之间 `playback:toggle:beat|structure`
// CustomEvent 的 detail 契约。事件是 cancelable 的:监听方在场就 e.preventDefault()
// 表示"接住了",并同步往 detail.result 塞执行结果的 Promise;没人 preventDefault
// = AudioViewer 未挂载,App 会重试直到超时,再回执 NO_WAV_OPEN 给 main/agent。
export interface PlaybackToggleResult {
  ok: boolean;
  code?: string;
  message?: string;
}

export interface PlaybackToggleDetail {
  on: boolean;
  result?: Promise<PlaybackToggleResult>;
}

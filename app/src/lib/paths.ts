// 路径前缀判断 —— path 是否严格位于 dir 之下(不含相等)。Windows `\` 与 POSIX `/` 都覆盖。
export function isPathUnderDir(path: string, dir: string): boolean {
  return path.startsWith(dir + "\\") || path.startsWith(dir + "/");
}

import { describe, it, expect } from "vitest";
import { isPathUnderDir } from "./paths";

describe("isPathUnderDir", () => {
  it("子项在目录下(两种分隔符)", () => {
    expect(isPathUnderDir("C:\\ws\\song\\a.wav", "C:\\ws\\song")).toBe(true);
    expect(isPathUnderDir("/ws/song/a.wav", "/ws/song")).toBe(true);
    expect(isPathUnderDir("C:\\ws\\song\\sub\\b.mid", "C:\\ws\\song")).toBe(true);
  });
  it("相等不算 under(严格)", () => {
    expect(isPathUnderDir("C:\\ws\\song", "C:\\ws\\song")).toBe(false);
  });
  it("仅前缀但非路径边界 → false", () => {
    // song2 不是 song 的子项,虽然字符串以 song 开头
    expect(isPathUnderDir("C:\\ws\\song2\\a.wav", "C:\\ws\\song")).toBe(false);
  });
  it("不相关路径 → false", () => {
    expect(isPathUnderDir("C:\\other\\a.wav", "C:\\ws\\song")).toBe(false);
  });
});

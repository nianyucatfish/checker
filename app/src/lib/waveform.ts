import { useEffect, useState } from "react";

// 跟随 <html class="dark"> 的暗色主题探测。波形/混音台两处画布都按它取色。
export function useDarkTheme(): boolean {
  const [dark, setDark] = useState(() =>
    document.documentElement.classList.contains("dark"),
  );
  useEffect(() => {
    const obs = new MutationObserver(() => {
      setDark(document.documentElement.classList.contains("dark"));
    });
    obs.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => obs.disconnect();
  }, []);
  return dark;
}

export interface WaveformCanvas {
  ctx: CanvasRenderingContext2D;
  width: number;
  height: number;
  dpr: number;
  centerY: number;
  ampHalf: number;
}

// 波形画布的公共前奏:按 dpr 调分辨率、清屏、铺背景、画中线、算振幅半高。
// 返回 null = 拿不到 2d context。索引映射 / 播放头由各调用方按自己的视图(全曲 / 窗口)处理。
export function setupWaveformCanvas(
  canvas: HTMLCanvasElement,
  dark: boolean,
  padPx: number,
): WaveformCanvas | null {
  const dpr = window.devicePixelRatio || 1;
  const width = Math.floor(canvas.clientWidth * dpr);
  const height = Math.floor(canvas.clientHeight * dpr);
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = dark ? "#1e1e1e" : "#fafafa";
  ctx.fillRect(0, 0, width, height);

  const centerY = height / 2;
  const ampHalf = Math.max(1, centerY - padPx * dpr);

  // 中线
  ctx.strokeStyle = dark ? "#3c3c3c" : "#e5e5e5";
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(0, centerY);
  ctx.lineTo(width, centerY);
  ctx.stroke();

  return { ctx, width, height, dpr, centerY, ampHalf };
}

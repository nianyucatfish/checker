import React from "react";
import ReactDOM from "react-dom/client";
import "./monaco-setup";
import App from "./App";
import { MixConsoleStandalone } from "./MixConsoleStandalone";
import "./styles.css";

// Electron 主进程为混音台独立窗口加载本页时带 ?view=mix-console;
// 否则正常加载主 App。
const params = new URLSearchParams(window.location.search);
const view = params.get("view");

const root = ReactDOM.createRoot(document.getElementById("root")!);
if (view === "mix-console") {
  root.render(
    <React.StrictMode>
      <MixConsoleStandalone />
    </React.StrictMode>,
  );
} else {
  root.render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

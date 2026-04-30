/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "var(--bg)",
          sidebar: "var(--bg-sidebar)",
          activitybar: "var(--bg-activitybar)",
          panel: "var(--bg-panel)",
          statusbar: "var(--bg-statusbar)",
          hover: "var(--bg-hover)",
          selected: "var(--bg-selected)",
        },
        fg: {
          DEFAULT: "var(--fg)",
          muted: "var(--fg-muted)",
          subtle: "var(--fg-subtle)",
          statusbar: "var(--fg-statusbar)",
        },
        border: {
          DEFAULT: "var(--border)",
          subtle: "var(--border-subtle)",
        },
        accent: {
          DEFAULT: "var(--accent)",
          fg: "var(--accent-fg)",
        },
        danger: "var(--danger)",
        warning: "var(--warning)",
        success: "var(--success)",
      },
      fontFamily: {
        sans: [
          "-apple-system", "BlinkMacSystemFont", "Segoe UI",
          "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", "sans-serif",
        ],
        mono: ["SF Mono", "Consolas", "Cascadia Code", "Menlo", "Monaco", "monospace"],
      },
      fontSize: {
        xs: ["11px", "16px"],
        sm: ["12px", "16px"],
        base: ["13px", "18px"],
        lg: ["15px", "20px"],
      },
    },
  },
  plugins: [],
};

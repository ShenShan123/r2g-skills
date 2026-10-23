/** Colors come from CSS variables (src/index.css) so light/dark is one switch. */
const v = (n) => `rgb(var(--${n}) / <alpha-value>)`;
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["class", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: v("bg"), side: v("side"), panel: v("panel"), panel2: v("panel2"), line: v("line"),
        fg: v("fg"), muted: v("muted"), faint: v("faint"),
        accent: v("accent"), accent2: v("accent2"),
        onaccent: v("on-accent"), ok: v("ok"), bad: v("bad"), warn: v("warn"), run: v("run"),
      },
      fontFamily: {
        sans: ['Inter', '"PingFang SC"', '"Microsoft YaHei"', '"Noto Sans CJK SC"', "system-ui", "sans-serif"],
        mono: ['"JetBrains Mono"', '"SFMono-Regular"', "Menlo", "Consolas", "monospace"],
      },
      boxShadow: { card: "0 1px 2px rgb(var(--shadow) / 0.05), 0 4px 16px -8px rgb(var(--shadow) / 0.12)" },
      keyframes: { pulse2: { "0%,100%": { opacity: 1 }, "50%": { opacity: 0.35 } } },
      animation: { pulse2: "pulse2 1.4s ease-in-out infinite" },
    },
  },
  plugins: [],
};

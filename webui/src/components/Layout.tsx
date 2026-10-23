import { NavLink, Outlet } from "react-router-dom";
import { Home, MessagesSquare, ListChecks, Settings, Moon, Sun, Cpu } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo } from "./ui";
import { api } from "../lib/api";

const NAV = [
  { to: "/", label: "首页", Icon: Home, end: true },
  { to: "/chat", label: "对话", Icon: MessagesSquare },
  { to: "/jobs", label: "任务", Icon: ListChecks },
  { to: "/settings", label: "设置", Icon: Settings },
];

export default function Layout() {
  const [theme, setTheme] = useState<string>(() => {
    try { return localStorage.getItem("r2g-theme-v2") || "light"; } catch { return "light"; }
  });
  const [model, setModel] = useState<any>(null);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem("r2g-theme-v2", theme); } catch { /* private mode */ }
  }, [theme]);
  useEffect(() => { api.config().then((c) => setModel(c.model_resolved)).catch(() => {}); }, []);

  return (
    <div className="flex h-full">
      <aside className="flex w-60 shrink-0 flex-col border-r border-line bg-side px-3 py-5">
        <div className="flex items-center gap-2.5 px-2">
          <Logo />
          <div>
            <div className="text-[17px] font-bold tracking-tight">r2g</div>
            <div className="text-[11px] text-muted">RTL → 签核版图 → 图数据集</div>
          </div>
        </div>
        <nav className="mt-8 flex flex-col gap-1">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition ${isActive
                  ? "bg-accent/15 text-accent font-medium" : "text-muted hover:bg-panel2 hover:text-fg"}`}>
              <Icon size={18} /> {label}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto space-y-3">
          <div className="rounded-xl border border-line bg-panel2/60 p-3 text-xs">
            <div className="flex items-center gap-1.5 text-muted"><Cpu size={13} /> 当前模型</div>
            <div className="mt-1 truncate font-medium">{model ? `${model.model || "未设置"}` : "…"}</div>
            <div className="mt-0.5 flex items-center gap-1.5 text-muted">
              <span className={`h-1.5 w-1.5 rounded-full ${model?.has_key ? "bg-ok" : "bg-warn"}`} />
              {model ? (model.has_key ? model.provider : "未配置密钥") : ""}
            </div>
          </div>
          <div className="flex items-center justify-between px-1 text-xs text-faint">
            <span>平台 nangate45 · v0.1</span>
            <button className="rounded-lg p-1.5 hover:bg-panel2 hover:text-fg" aria-label="切换主题"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun size={15} /> : <Moon size={15} />}
            </button>
          </div>
        </div>
      </aside>
      <main className="min-w-0 flex-1 overflow-y-auto"><Outlet /></main>
    </div>
  );
}

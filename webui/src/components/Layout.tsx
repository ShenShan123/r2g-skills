import { NavLink, Outlet } from "react-router-dom";
import { PlayCircle, MessagesSquare, ListChecks, Settings, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo } from "./ui";
import { api } from "../lib/api";

// three things a user does, in the order they do them; settings sits apart
const NAV = [
  { to: "/", label: "开始", Icon: PlayCircle, end: true },
  { to: "/jobs", label: "任务", Icon: ListChecks },
  { to: "/chat", label: "对话", Icon: MessagesSquare },
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
  const navCls = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-xl px-3 py-2.5 text-[15px] transition ${isActive
      ? "bg-accent/10 text-accent font-medium" : "text-muted hover:bg-panel2 hover:text-fg"}`;

  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* desktop sidebar */}
      <aside className="hidden w-56 shrink-0 flex-col border-r border-line bg-side px-3 py-5 md:flex">
        <div className="flex items-center gap-2.5 px-2">
          <Logo />
          <div>
            <div className="text-[17px] font-bold tracking-tight">r2g</div>
            <div className="text-[11px] text-muted">RTL → 版图 → 数据集</div>
          </div>
        </div>
        <nav className="mt-8 flex flex-col gap-1">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={navCls}><Icon size={19} /> {label}</NavLink>))}
        </nav>
        <div className="mt-auto space-y-1">
          <NavLink to="/settings" className={navCls}>
            <Settings size={19} />
            <span className="min-w-0 flex-1">
              设置
              <span className="flex items-center gap-1.5 text-xs font-normal text-faint">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${model?.has_key ? "bg-ok" : "bg-warn"}`} />
                <span className="truncate">{model ? (model.has_key ? model.model : "模型未配置") : "…"}</span>
              </span>
            </span>
          </NavLink>
          <button className="flex w-full items-center gap-3 rounded-xl px-3 py-2 text-sm text-faint hover:bg-panel2 hover:text-fg"
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />} {theme === "dark" ? "浅色" : "深色"}
          </button>
        </div>
      </aside>

      {/* phone header */}
      <header className="flex items-center justify-between border-b border-line bg-side px-4 py-2.5 md:hidden">
        <div className="flex items-center gap-2"><Logo size={26} /><span className="font-bold">r2g</span></div>
        <div className="flex items-center gap-1">
          <button className="rounded-lg p-2 text-muted" aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
          <NavLink to="/settings" className="rounded-lg p-2 text-muted" aria-label="设置"><Settings size={18} /></NavLink>
        </div>
      </header>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto pb-16 md:pb-0"><Outlet /></main>

      {/* phone tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-line bg-panel md:hidden">
        {NAV.map(({ to, label, Icon, end }) => (
          <NavLink key={to} to={to} end={end}
            className={({ isActive }) => `flex flex-col items-center gap-0.5 py-2 text-[11px] ${isActive ? "text-accent" : "text-muted"}`}>
            <Icon size={20} /> {label}
          </NavLink>))}
      </nav>
    </div>
  );
}

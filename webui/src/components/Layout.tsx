import { NavLink, Outlet } from "react-router-dom";
import { PlayCircle, MessagesSquare, ListChecks, Settings, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo } from "./ui";
import { api } from "../lib/api";
import { Lang, setLang, t, useLang } from "../lib/i18n";

// three things a user does, in the order they do them; settings sits apart
const NAV = [
  { to: "/", label: "开始", Icon: PlayCircle, end: true },
  { to: "/jobs", label: "任务", Icon: ListChecks },
  { to: "/chat", label: "对话", Icon: MessagesSquare },
];

/** zh / EN switch; the active language is filled in. */
function LangToggle() {
  const lang = useLang();
  return (
    <div className="inline-flex shrink-0 rounded-lg border border-line p-0.5 text-xs" role="group" aria-label="Language / 语言">
      {([["zh", "ZH"], ["en", "EN"]] as [Lang, string][]).map(([k, l]) => (
        <button key={k} type="button" aria-pressed={lang === k} onClick={() => setLang(k)}
          className={`rounded-md px-2 py-0.5 ${lang === k ? "bg-accent/15 font-medium text-accent" : "text-faint hover:text-fg"}`}>{l}</button>))}
    </div>);
}

export default function Layout() {
  const lang = useLang();
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
            <div className="text-[11px] text-muted">{t("RTL → 版图 → 数据集")}</div>
          </div>
        </div>
        <nav className="mt-8 flex flex-col gap-1">
          {NAV.map(({ to, label, Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={navCls}><Icon size={19} /> {t(label)}</NavLink>))}
        </nav>
        <div className="mt-auto space-y-1">
          <NavLink to="/settings" className={navCls}>
            <Settings size={19} />
            <span className="min-w-0 flex-1">
              {t("设置")}
              <span className="flex items-center gap-1.5 text-xs font-normal text-faint">
                <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${model?.has_key ? "bg-ok" : "bg-warn"}`} />
                <span className="truncate">{model ? (model.has_key ? model.model : t("模型未配置")) : "…"}</span>
              </span>
            </span>
          </NavLink>
          <div className="flex items-center gap-1 pr-2">
            <button className="flex min-w-0 flex-1 items-center gap-3 rounded-xl px-3 py-2 text-sm text-faint hover:bg-panel2 hover:text-fg"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />} {theme === "dark" ? t("浅色") : t("深色")}
            </button>
            <LangToggle />
          </div>
        </div>
      </aside>

      {/* phone header */}
      <header className="flex items-center justify-between border-b border-line bg-side px-4 py-2.5 md:hidden">
        <div className="flex items-center gap-2"><Logo size={26} /><span className="font-bold">r2g</span></div>
        <div className="flex items-center gap-1">
          <LangToggle />
          <button className="rounded-lg p-2 text-muted" aria-label={t("切换主题")} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}</button>
          <NavLink to="/settings" className="rounded-lg p-2 text-muted" aria-label={t("设置")}><Settings size={18} /></NavLink>
        </div>
      </header>

      <main className="min-h-0 min-w-0 flex-1 overflow-y-auto pb-16 md:pb-0"><Outlet key={lang} /></main>

      {/* phone tab bar */}
      <nav className="fixed inset-x-0 bottom-0 z-30 grid grid-cols-3 border-t border-line bg-panel md:hidden">
        {NAV.map(({ to, label, Icon, end }) => (
          <NavLink key={to} to={to} end={end}
            className={({ isActive }) => `flex flex-col items-center gap-0.5 py-2 text-[11px] ${isActive ? "text-accent" : "text-muted"}`}>
            <Icon size={20} /> {t(label)}
          </NavLink>))}
      </nav>
    </div>
  );
}

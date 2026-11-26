import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, ChevronRight, Search, Loader2 } from "lucide-react";
import { api, fmtTime, Job, JobFilter, jobStatus, OUTPUT_LABEL, PLATFORM_LABEL } from "../lib/api";
import { StatusBadge } from "../components/ui";
import DeleteResource from "../components/DeleteResource";
import { t, tb } from "../lib/i18n";

const PAGE = 50;
const FILTERS: [JobFilter, string][] = [["", "全部"], ["active", "进行中"], ["done", "完成"], ["failed", "失败"], ["cancelled", "已取消"]];

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<JobFilter>("");
  const [q, setQ] = useState("");
  const [shown, setShown] = useState(PAGE);
  const [error, setError] = useState("");
  const [more, setMore] = useState(false);
  const removed = useRef(new Set<string>());

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    // refresh what is on screen every few seconds; a new filter or search starts over
    const load = async () => {
      try {
        const page = await api.jobPage({ limit: shown, status, q: q.trim() });
        if (!alive) return;
        setJobs(page.jobs.filter((j) => !removed.current.has(j.id))); setTotal(page.total); setError("");
      } catch (e: any) { if (alive) setError(tb(e.message) || t("任务列表加载失败")); }
      finally { if (alive) { setMore(false); timer = setTimeout(load, 4000); } }
    };
    const debounce = setTimeout(load, q ? 250 : 0);
    return () => { alive = false; clearTimeout(timer); clearTimeout(debounce); };
  }, [status, q, shown]);

  const filtered = status !== "" || q.trim() !== "";
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("任务")}</h1>
        <Link to="/" className="btn-primary"><Plus size={16} /> {t("新建任务")}</Link>
      </div>
      <div className="mt-5 flex flex-wrap items-center gap-2">
        <div className="inline-flex flex-wrap rounded-xl bg-panel2 p-1 text-sm" role="group" aria-label={t("按状态筛选")}>
          {FILTERS.map(([k, l]) => (
            <button key={k || "all"} type="button" aria-pressed={status === k}
              onClick={() => { setStatus(k); setShown(PAGE); }}
              className={`rounded-lg px-3 py-1.5 ${status === k ? "bg-panel font-medium text-fg shadow-card" : "text-muted hover:text-fg"}`}>{t(l)}</button>))}
        </div>
        <label className="flex min-w-[12rem] flex-1 items-center gap-2 rounded-xl border border-line bg-panel px-3 py-1.5">
          <Search size={15} className="shrink-0 text-faint" />
          <input className="w-full bg-transparent py-0.5 text-sm outline-none" placeholder={t("按任务名或编号搜索")} value={q}
            aria-label={t("按任务名或编号搜索")} onChange={(e) => { setQ(e.target.value); setShown(PAGE); }} />
        </label>
      </div>
      {error && <p role="alert" className="mt-3 text-sm text-bad">{error}</p>}
      <div className="card mt-3 divide-y divide-line">
        {jobs === null && !error && <div className="px-5 py-10 text-center text-sm text-muted">{t("加载中…")}</div>}
        {jobs && jobs.length === 0 && (filtered
          ? <div className="px-5 py-10 text-center text-sm text-muted">{t("没有符合条件的任务。")}</div>
          : <div className="px-5 py-10 text-center text-sm text-muted">{t("还没有任务。")}<Link to="/" className="text-accent underline">{t("去新建一个")}</Link></div>)}
        {jobs?.map((j) => {
          const c = j.counts ?? {}; const total = Object.values(c).reduce((a, b) => a + b, 0);
          const deleting = j.deletion === "pending";
          return (
            <div key={j.id} className={`flex items-center gap-2 pr-3 hover:bg-panel2/60 ${deleting ? "opacity-60" : ""}`}>
            <Link to={`/jobs/${j.id}`} className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3.5 md:px-5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{tb(j.title)}</div>
                <div className="mt-0.5 text-xs text-muted">
                  {fmtTime(j.created)} · {PLATFORM_LABEL[j.spec?.platform] ?? j.spec?.platform} · {t(OUTPUT_LABEL[j.spec?.output ?? "graph"] ?? j.spec?.output)} ·{" "}
                  {j.dual_clean !== undefined && <span className="text-ok">{t("{n} 双清 · ", { n: j.dual_clean })}</span>}
                  <span className="text-ok">{t(j.spec?.output === "signoff" ? "{n} 通过" : "{n} 数据集通过", { n: c.passed ?? 0 })}</span>
                  {(c.failed ?? 0) > 0 && <span className="text-bad">{t(" · {n} 失败", { n: c.failed })}</span>}
                  {(c.running ?? 0) > 0 && <span>{t(" · {n} 运行", { n: c.running })}</span>}
                  {(c.pending ?? 0) > 0 && <span>{t(" · {n} 排队", { n: c.pending })}</span>}
                  {(c.cancelled ?? 0) > 0 && <span>{t(" · {n} 已取消", { n: c.cancelled })}</span>}
                  <span>{t(" · 共 {n}", { n: total })}</span>
                </div>
              </div>
              {deleting ? <span className="chip bg-bad/10 text-bad">{t("删除中")}</span> : <StatusBadge status={jobStatus(j)} />}
              <ChevronRight size={16} className="text-faint" />
            </Link>
            <DeleteResource kind="job" id={j.id} title={j.title} compact state={j.deletion}
              onDeleted={() => { removed.current.add(j.id); setJobs(js => js?.filter(x => x.id !== j.id) ?? []); setTotal((n) => Math.max(0, n - 1)); }} />
            </div>);
        })}
      </div>
      {jobs && jobs.length > 0 && (
        <div className="mt-3 flex items-center justify-between text-sm text-muted">
          <span>{t("显示 {n} / {total} 个任务", { n: jobs.length, total })}</span>
          {jobs.length < total && (
            <button type="button" className="btn-ghost" disabled={more} onClick={() => { setMore(true); setShown(shown + PAGE); }}>
              {more && <Loader2 size={14} className="animate-spin" />}{t("加载更多")}</button>)}
        </div>)}
    </div>
  );
}

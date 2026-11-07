import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, ChevronRight } from "lucide-react";
import { api, fmtTime, Job, jobStatus, OUTPUT_LABEL, PLATFORM_LABEL } from "../lib/api";
import { StatusBadge } from "../components/ui";
import DeleteResource from "../components/DeleteResource";
import { t, tb } from "../lib/i18n";

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  useEffect(() => {
    const load = () => api.jobs().then(setJobs).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, []);
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">{t("任务")}</h1>
        <Link to="/" className="btn-primary"><Plus size={16} /> {t("新建任务")}</Link>
      </div>
      <div className="card mt-5 divide-y divide-line">
        {jobs && jobs.length === 0 && (
          <div className="px-5 py-10 text-center text-sm text-muted">{t("还没有任务。")}<Link to="/" className="text-accent underline">{t("去新建一个")}</Link></div>)}
        {jobs?.map((j) => {
          const c = j.counts ?? {}; const total = Object.values(c).reduce((a, b) => a + b, 0);
          return (
            <div key={j.id} className="flex items-center gap-2 pr-3 hover:bg-panel2/60">
            <Link to={`/jobs/${j.id}`} className="flex min-w-0 flex-1 items-center gap-3 px-4 py-3.5 md:px-5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{tb(j.title)}</div>
                <div className="mt-0.5 text-xs text-muted">
                  {fmtTime(j.created)} · {PLATFORM_LABEL[j.spec?.platform] ?? j.spec?.platform} · {t(OUTPUT_LABEL[j.spec?.output ?? "graph"])} ·{" "}
                  {j.dual_clean !== undefined && <span className="text-ok">{t("{n} 双清 · ", { n: j.dual_clean })}</span>}
                  <span className="text-ok">{t("{n} 数据集通过", { n: c.passed ?? 0 })}</span>
                  {(c.failed ?? 0) > 0 && <span className="text-bad">{t(" · {n} 失败", { n: c.failed })}</span>}
                  {(c.running ?? 0) > 0 && <span>{t(" · {n} 运行", { n: c.running })}</span>}
                  {(c.pending ?? 0) > 0 && <span>{t(" · {n} 排队", { n: c.pending })}</span>}
                  {(c.cancelled ?? 0) > 0 && <span>{t(" · {n} 已取消", { n: c.cancelled })}</span>}
                  <span>{t(" · 共 {n}", { n: total })}</span>
                </div>
              </div>
              <StatusBadge status={jobStatus(j)} />
              <ChevronRight size={16} className="text-faint" />
            </Link>
            <DeleteResource kind="job" id={j.id} title={j.title} compact onDeleted={() => setJobs(js => js?.filter(x => x.id !== j.id) ?? [])} />
            </div>);
        })}
      </div>
    </div>
  );
}

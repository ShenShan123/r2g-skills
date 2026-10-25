import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, ChevronRight } from "lucide-react";
import { api, fmtTime, Job, OUTPUT_LABEL } from "../lib/api";
import { StatusBadge } from "../components/ui";

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  useEffect(() => {
    const load = () => api.jobs().then(setJobs).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, []);
  return (
    <div className="mx-auto max-w-3xl px-4 py-6 md:px-8 md:py-10">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-bold">任务</h1>
        <Link to="/" className="btn-primary"><Plus size={16} /> 新建任务</Link>
      </div>
      <div className="card mt-5 divide-y divide-line">
        {jobs && jobs.length === 0 && (
          <div className="px-5 py-10 text-center text-sm text-muted">还没有任务。<Link to="/" className="text-accent underline">去新建一个</Link></div>)}
        {jobs?.map((j) => {
          const c = j.counts ?? {}; const total = Object.values(c).reduce((a, b) => a + b, 0);
          return (
            <Link key={j.id} to={`/jobs/${j.id}`} className="flex items-center gap-3 px-4 py-3.5 hover:bg-panel2/60 md:px-5">
              <div className="min-w-0 flex-1">
                <div className="truncate font-medium">{j.title}</div>
                <div className="mt-0.5 text-xs text-muted">
                  {fmtTime(j.created)} · {OUTPUT_LABEL[j.spec?.output ?? "graph"]} ·{" "}
                  <span>{c.passed ?? 0} 流程完成</span>
                  {(c.failed ?? 0) > 0 && <span className="text-bad"> · {c.failed} 失败</span>}
                  <span> · 共 {total}</span>
                </div>
              </div>
              <StatusBadge status={j.status} />
              <ChevronRight size={16} className="text-faint" />
            </Link>);
        })}
      </div>
    </div>
  );
}

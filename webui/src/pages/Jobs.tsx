import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus } from "lucide-react";
import { api, fmtTime, Job, OUTPUT_LABEL } from "../lib/api";
import { StatusBadge } from "../components/ui";
import NewJobDialog from "../components/NewJobDialog";

export default function Jobs() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [dlg, setDlg] = useState(false);
  useEffect(() => {
    const load = () => api.jobs().then(setJobs).catch(() => {});
    load(); const t = setInterval(load, 4000); return () => clearInterval(t);
  }, []);
  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-2xl font-bold">任务</h1>
          <p className="mt-1 text-sm text-muted">每个任务是一批设计；只有签核通过且数据集校验通过的设计会进入数据集。</p>
        </div>
        <button className="btn-primary" onClick={() => setDlg(true)}><Plus size={16} /> 新建任务</button>
      </div>
      <div className="card mt-6 overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-panel2/70 text-left text-xs uppercase tracking-wider text-muted">
            <tr><th className="px-5 py-3">任务</th><th className="px-5 py-3">创建</th><th className="px-5 py-3">设计</th><th className="px-5 py-3">状态</th></tr>
          </thead>
          <tbody className="divide-y divide-line">
            {jobs.length === 0 && <tr><td colSpan={4} className="px-5 py-8 text-center text-muted">暂无任务</td></tr>}
            {jobs.map((j) => {
              const c = j.counts ?? {}; const total = Object.values(c).reduce((a, b) => a + b, 0);
              return (
                <tr key={j.id} className="hover:bg-panel2/50">
                  <td className="px-5 py-3.5"><Link to={`/jobs/${j.id}`} className="font-medium hover:text-accent">{j.title}</Link>
                    <div className="font-mono text-xs text-faint">{j.id} · {OUTPUT_LABEL[j.spec?.output ?? "graph"]}</div></td>
                  <td className="px-5 py-3.5 text-muted">{fmtTime(j.created)}</td>
                  <td className="px-5 py-3.5">
                    <span className="text-ok">{c.passed ?? 0}</span><span className="text-faint"> 通过 · </span>
                    <span className="text-bad">{c.failed ?? 0}</span><span className="text-faint"> 失败 · 共 {total}</span>
                  </td>
                  <td className="px-5 py-3.5"><StatusBadge status={j.status} /></td>
                </tr>);
            })}
          </tbody>
        </table>
      </div>
      {dlg && <NewJobDialog onClose={() => setDlg(false)} />}
    </div>
  );
}

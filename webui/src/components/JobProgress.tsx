import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, Download, ExternalLink, Loader2, RefreshCw } from "lucide-react";
import { api, Job } from "../lib/api";
import { DesignProgressCard } from "./DesignProgressCard";
import { isActive, StatusBadge } from "./ui";

const SUMMARY: Record<string, string> = {
  pending: "任务已提交，等待空闲算力。", running: "任务正在执行，进度自动更新。",
  finalizing: "设计检查已结束，正在打包数据集。",
  cancelling: "取消请求已保存，正在确认执行进程停止。",
  cancelled: "任务已取消，已有日志与结果仍可查看。",
  recovery_required: "需要核对旧执行状态，尚未自动重跑。",
  failed: "任务失败，请查看设计状态与报告。",
};
const PRIORITY: Record<string, number> = { running: 0, recovery_required: 1, failed: 2, pending: 3 };

/** Poll durable task state independently of the model's text or tool snapshot. */
export function JobProgress({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let alive = true, inFlight = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      if (!alive || inFlight) return;
      clearTimeout(timer);
      inFlight = true;
      try {
        const current = await api.job(jobId);
        if (!alive) return;
        setJob(current); setError(false);
        if (isActive(current.status)) timer = setTimeout(tick, current.status === "recovery_required" || (current.designs?.length ?? 0) > 12 ? 5000 : 2000);
      } catch {
        if (!alive) return;
        setError(true);
        timer = setTimeout(tick, 5000);
      } finally { inFlight = false; }
    };
    const visible = () => { if (document.visibilityState === "visible") void tick(); };
    void tick();
    window.addEventListener("focus", visible);
    window.addEventListener("online", visible);
    document.addEventListener("visibilitychange", visible);
    return () => {
      alive = false; clearTimeout(timer);
      window.removeEventListener("focus", visible);
      window.removeEventListener("online", visible);
      document.removeEventListener("visibilitychange", visible);
    };
  }, [jobId, refresh]);

  const ds = job?.designs ?? [];
  const passed = ds.filter((d) => d.status === "passed").length;
  const shown = ds.length > 6 ? [...ds].sort((a, b) => (PRIORITY[a.status] ?? 4) - (PRIORITY[b.status] ?? 4)).slice(0, 6) : ds;
  const detailsUrl = `/jobs/${encodeURIComponent(jobId)}`;
  return (
    <section aria-label="任务进度" data-job-id={jobId} className="my-3 min-w-0 space-y-3 rounded-2xl bg-panel2/40 p-2 sm:p-3">
      <div className="flex flex-wrap items-center gap-2 px-1">
        <h3 className="min-w-0 flex-1 truncate font-medium" title={job?.title}>{job?.title || "任务进度"}</h3>
        {job && <StatusBadge status={job.status} />}
        <Link to={detailsUrl} className="inline-flex items-center gap-1 text-xs text-accent hover:underline">查看任务 <ExternalLink size={12} /></Link>
      </div>
      {!job && !error && <p className="flex items-center gap-2 px-1 text-xs text-muted"><Loader2 size={14} className="animate-spin" /> 正在读取任务进度…</p>}
      {error && <div role="status" className="flex flex-wrap items-center gap-2 px-1 text-xs text-warn">
        <AlertCircle size={14} /><span>{job ? "暂时无法更新，以下为上次读取的状态。" : "暂时无法读取任务状态，正在重连。"}</span>
        <button className="inline-flex items-center gap-1 underline" onClick={() => setRefresh((n) => n + 1)}><RefreshCw size={12} /> 重试</button>
      </div>}
      {job && <>
        <p className="px-1 text-xs text-muted">{job.status === "done" ? `已完成 · ${passed}/${ds.length} 个设计通过` : SUMMARY[job.status] ?? "查看设计状态与报告。"}</p>
        {shown.map((d) => <DesignProgressCard key={d.name} jobId={jobId} d={d} jobStatus={job.status} offerAnalysis={false} />)}
        {ds.length > shown.length && <Link to={detailsUrl} className="block px-1 text-xs text-accent hover:underline">共 {ds.length} 个设计，{passed} 个通过 · 查看全部</Link>}
        {job.result?.package && <a className="btn-primary" href={`/api/jobs/${encodeURIComponent(jobId)}/download`}><Download size={15} /> 下载数据集</a>}
      </>}
    </section>
  );
}

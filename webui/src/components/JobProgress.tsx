import { Link } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Download, ExternalLink, XCircle } from "lucide-react";
import { askAgent, Design, Job, JobEvent } from "../lib/api";
import { DesignProgressCard, STAGE_ZH } from "./DesignProgressCard";
import { StatusBadge } from "./ui";

export const jobFinished = (job: Job) => ["done", "failed", "cancelled", "partial"].includes(job.status);

/** This is a view of recorded job state, independent of the model's narrative. */
export function JobProgress({ job }: { job: Job }) {
  const designs = job.designs ?? [];
  const shown = designs.length > 6 ? [...designs].sort((a, b) =>
    Number(["passed", "cancelled"].includes(a.status)) - Number(["passed", "cancelled"].includes(b.status))).slice(0, 6) : designs;
  return (
    <section aria-label="任务进度" data-job-id={job.id} className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
        <span className="min-w-0 flex-1 truncate">{job.title}</span>
        <StatusBadge status={job.status} />
        <Link to={`/jobs/${encodeURIComponent(job.id)}`} className="inline-flex items-center gap-1 text-accent">查看任务 <ExternalLink size={12} /></Link>
      </div>
      {shown.map((d) => <DesignProgressCard key={d.name} jobId={job.id} d={d} jobStatus={job.status} offerAnalysis={false} />)}
      {designs.length > shown.length && <Link to={`/jobs/${encodeURIComponent(job.id)}`} className="text-xs text-accent">查看全部 {designs.length} 个设计</Link>}
    </section>
  );
}

function failureReason(d: Design, events: JobEvent[]): string {
  const failure = [...events].reverse().find((e) => e.design === d.name && e.kind === "fail");
  const log = String(failure?.data?.log_tail ?? "");
  if (/cannot create directory[^\n]*Permission denied/i.test(log)) {
    return "工具链工作目录不可写，流程在综合前停止。请检查工具链路径和写入权限。";
  }
  return d.detail?.reason || failure?.message || "请查看该阶段的日志与报告。";
}

export function JobResult({ job, events }: { job: Job; events: JobEvent[] }) {
  if (!jobFinished(job)) return null;
  const designs = job.designs ?? [];
  const passed = designs.filter((d) => d.status === "passed");
  const failed = designs.filter((d) => d.status === "failed");
  const cancelled = job.status === "cancelled";
  const success = job.status === "done" && passed.length === designs.length && designs.length > 0;
  const title = cancelled ? "任务已取消" : success ? "任务已完成" : job.status === "failed" ? "任务失败" : passed.length ? "任务部分完成" : "任务已结束";
  const Icon = success ? CheckCircle2 : cancelled ? AlertTriangle : XCircle;
  return (
    <section aria-label="任务结果" data-job-id={job.id} className="space-y-3 py-1">
      <div className={`flex items-center gap-2 font-semibold ${success ? "text-ok" : cancelled ? "text-warn" : "text-bad"}`}><Icon size={18} /><h3>{title}</h3></div>
      {passed.length > 0 && <>
        <p className="text-sm">{passed.length}/{designs.length} 个设计通过签核和数据集校验。</p>
        <ul className="space-y-1 text-sm text-muted">
          {passed.slice(0, 5).map((d) => <li key={d.name}><span className="font-mono text-fg">{d.name}</span>
            {d.detail?.verify && <> · 数据集校验 {d.detail.verify.passed}/{d.detail.verify.total} 项通过</>}</li>)}
        </ul>
      </>}
      {failed.length > 0 && <ul className="space-y-3 text-sm">
        {failed.slice(0, 5).map((d) => <li key={d.name} className="space-y-1">
          <p><span className="font-mono font-medium">{d.name}</span> · {STAGE_ZH[d.detail?.failed_stage ?? d.stage] ?? d.stage}阶段失败</p>
          <p className="break-words text-muted">{failureReason(d, events)}</p>
        </li>)}
      </ul>}
      {!passed.length && !cancelled && <p className="text-sm text-muted">本次未生成可交付的数据集。</p>}
      {cancelled && <p className="text-sm text-muted">已确认取消，已有执行记录可在过程中查看。</p>}
      <div className="flex flex-wrap items-center gap-3">
        {job.result?.package && <a className="btn-primary" href={`/api/jobs/${encodeURIComponent(job.id)}/download`}><Download size={15} /> 下载数据集</a>}
        <Link to={`/jobs/${encodeURIComponent(job.id)}`} className="inline-flex items-center gap-1 text-sm text-accent hover:underline">查看报告 <ExternalLink size={13} /></Link>
        {failed.length > 0 && <Link to={askAgent(`任务 ${job.id} 执行失败，请读取失败日志，说明实际原因和下一步处理方法。`)} className="text-sm text-accent hover:underline">分析失败原因</Link>}
      </div>
    </section>
  );
}

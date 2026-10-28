import { useEffect, useId, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Loader2, RefreshCw, XCircle } from "lucide-react";
import { JobEvent, ToolOutputRef, fmtClock } from "../lib/api";
import { useJobProgress } from "../hooks/useJobProgress";
import { designSummary } from "./DesignProgressCard";
import { JobProgress, JobResult, jobFinished } from "./JobProgress";
import { ToolOutputView } from "./ToolOutputView";
import { RepairProposal } from "./RepairProposal";
import { Md } from "./ui";

export type ConversationPart =
  | { kind: "text"; text: string }
  | { kind: "tool"; id: string; name: string; args: any; result?: any; output_ref?: ToolOutputRef };
export type ConversationTurnKind = "user" | "job_review" | "repair_review" | "debug_review";

export function toolJobId(p: Extract<ConversationPart, { kind: "tool" }>): string | null {
  if (!p.result || typeof p.result !== "object" || p.result.error) return null;
  const id = p.name === "execute_dependency_debug" ? p.result.child_job_id ?? p.result.job_id : p.result.job_id;
  return typeof id === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(id) ? id : null;
}

const ACTIONS: Record<string, string> = {
  scan_batch: "识别上传的 RTL、顶层模块和依赖", start_batch: "提交批量 EDA 任务",
  start_job: "提交 EDA 任务", job_status: "核对任务的实际进度", list_jobs: "查看已有任务",
  list_examples: "查看内置设计", design_detail: "读取设计报告", read_log: "查看执行日志并定位报错",
  diagnose: "根据报告诊断失败原因", knowledge_lookup: "检索相关故障经验", cancel_job: "请求停止任务",
  dataset_info: "核对数据集产物", check_environment: "检查工具链环境", read_rtl: "阅读 RTL 源码",
  read_tool_output: "查看已保存的工具结果", read_session_history: "查阅本次对话的历史要求与记录",
  flow_params: "确认流程参数",
  propose_rtl_repair: "提出修复差异，等待审核", execute_rtl_repair: "执行已授权的有限修复",
  eda_capabilities: "核对工具与工艺资产", task_skill: "读取任务检查规则",
  plan_next_step: "依据当前证据选择下一项检查", inspect_design: "核对候选设计的接口与依赖",
  check_design: "执行编译或结构检查",
  execute_dependency_debug: "补齐已授权输入依赖并验证隔离副本", task_status: "核对完成条件与未决证据",
  search_rtl: "搜索 RTL 设计", fetch_rtl: "读取仓库中的设计", disk_usage: "检查可用磁盘空间",
};
const PUBLIC_EVENTS = new Set(["created", "start", "ok", "warn", "fail", "done", "cancelled", "recovery_required"]);

export function ConversationRun({ parts, sid, busy, error, jobIds, turnKind = "user", queued = false }: {
  parts: ConversationPart[]; sid: string | null; busy: boolean; error?: string; jobIds: string[];
  turnKind?: ConversationTurnKind; queued?: boolean;
}) {
  const { states, retry } = useJobProgress(jobIds);
  const [expanded, setExpanded] = useState(true);
  const panelId = useId();
  const hasJobReference = parts.some((p) => p.kind === "tool" && toolJobId(p));
  const hasTools = parts.some((p) => p.kind === "tool");
  const proposalIds = [...new Set(parts.flatMap((p) => {
    if (p.kind !== "tool" || p.name !== "propose_rtl_repair" || p.result?.error) return [];
    const id = p.result?.repair_proposal;
    return typeof id === "string" && /^[a-zA-Z0-9_-]{1,100}$/.test(id) ? [id] : [];
  }))];
  const reviewing = turnKind !== "user";
  const settled = reviewing && busy ? false : jobIds.length > 0
    ? jobIds.every((id) => states[id]?.job && jobFinished(states[id].job!)) : !busy;
  useEffect(() => { setExpanded(!settled); }, [settled]);

  if (!hasTools && !busy) return <>
    {parts.map((p, i) => p.kind === "text" && <Md key={i}>{p.text}</Md>)}
    {error && <TurnError message={error} />}
  </>;

  const jobs = jobIds.flatMap((id) => states[id]?.job ? [states[id].job!] : []);
  const running = jobs.flatMap((j) => j.designs ?? []).find((d) => d.status === "running");
  const needsReview = jobs.some((j) => j.status === "recovery_required");
  const failed = jobs.some((j) => ["failed", "partial"].includes(j.status));
  const disconnected = jobIds.some((id) => states[id]?.error);
  const headline = queued ? turnKind === "debug_review" ? "自动诊断正在排队…" : turnKind === "repair_review" ? "已授权修复正在排队…" : turnKind === "job_review" ? "任务审查正在排队…" : "请求正在排队…"
    : reviewing && busy
    ? turnKind === "debug_review" ? "正在诊断报错并尝试合法恢复…" : turnKind === "repair_review" ? "正在执行已授权的有限修复…" : "正在审查任务结果…"
    : disconnected ? "正在重新连接任务" : needsReview ? "任务需要核对" : settled
    ? jobs.some((j) => j.status === "cancelled") ? "任务已取消" : failed ? "任务已结束，未全部通过" : "处理已结束"
    : running ? designSummary(running) : jobs.some((j) => j.status === "cancelling") ? "正在确认任务停止"
    : busy ? "正在分析并执行请求…" : "正在等待任务完成…";
  const lastTool = parts.reduce((last, p, i) => p.kind === "tool" ? i : last, -1);
  const keepAnswerInHistory = hasJobReference && !reviewing;
  const activity = parts.filter((p, i) => p.kind === "tool" || keepAnswerInHistory || i <= lastTool);
  const answer = keepAnswerInHistory ? [] : parts.filter((p, i) => p.kind === "text" && i > lastTool);
  const events = Object.values(states).flatMap((s) => s.events).filter((e) => PUBLIC_EVENTS.has(e.kind))
    .sort((a, b) => a.ts - b.ts).slice(-30);
  return (
    <div className="space-y-4">
      <section aria-label="思考与执行过程" className="min-w-0 rounded-xl border border-line/70 bg-panel2/30">
        <button type="button" aria-expanded={expanded} aria-controls={panelId}
          onClick={() => setExpanded((v) => !v)} className="flex w-full items-center gap-2 px-3 py-3 text-left text-sm">
          {settled ? failed ? <XCircle size={15} className="shrink-0 text-bad" /> : <CheckCircle2 size={15} className="shrink-0 text-muted" />
            : needsReview ? <AlertCircle size={15} className="shrink-0 text-warn" /> : <Loader2 size={15} className="shrink-0 animate-spin text-run" />}
          <span role="status" aria-live="polite" className="min-w-0 flex-1 break-words text-muted">{headline}</span>
          <span className="shrink-0 text-xs text-faint">{expanded ? "收起过程" : "展开过程"}</span>
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </button>
        {expanded && <div id={panelId} className="space-y-4 border-t border-line/70 p-3 sm:p-4">
          <div className="text-xs text-faint">操作说明与工具记录</div>
          <div className="space-y-3">
            {activity.map((p, i) => p.kind === "text"
              ? <div key={i} className="text-sm text-muted"><Md>{p.text}</Md></div>
              : <ToolStep key={i} part={p} sid={sid} />)}
          </div>
          {jobIds.map((id) => <div key={id}>
            {states[id]?.error && <div role="status" className="mb-3 flex flex-wrap items-center gap-2 text-xs text-warn">
              <AlertCircle size={14} /><span>{states[id].error}{states[id].job && " 以下保留上次读取的状态。"}</span>
              <button className="inline-flex items-center gap-1 underline" onClick={retry}><RefreshCw size={12} /> 重试</button>
            </div>}
            {states[id]?.job ? <JobProgress job={states[id].job!} /> : <p className="text-xs text-muted">正在读取任务进度…</p>}
            {states[id]?.eventsUnavailable && <p className="mt-2 text-xs text-warn">暂时无法读取执行记录，正在重连。</p>}
          </div>)}
          {events.length > 0 && <div className="space-y-3">
            <p className="text-xs text-faint">阶段记录（最近 {events.length} 条）</p>
            <ol aria-label="执行记录" className="space-y-3 border-l border-line pl-3">
              {events.map((e) => <EventLine key={e.job_id + ":" + e.id} event={e} />)}
            </ol>
          </div>}
        </div>}
      </section>
      {sid && proposalIds.map((id) => <RepairProposal key={id} sid={sid} proposalId={id} />)}
      {answer.map((p, i) => p.kind === "text" && <Md key={i}>{p.text}</Md>)}
      {error && <TurnError message={error} />}
      {jobIds.map((id) => states[id]?.job && <JobResult key={id} job={states[id].job!} events={states[id].events} />)}
    </div>
  );
}

function EventLine({ event }: { event: JobEvent }) {
  return <li className="flex items-start gap-3 text-xs">
    <time className="shrink-0 tabular-nums text-faint">{fmtClock(event.ts)}</time>
    <p className={`min-w-0 break-words ${event.kind === "fail" ? "text-bad" : event.kind === "warn" ? "text-warn" : "text-muted"}`}>
      {event.design && <span className="mr-1.5 font-mono">{event.design}</span>}{event.message}
    </p>
  </li>;
}

function TurnError({ message }: { message: string }) {
  return <div role="alert" className="flex items-start gap-2 rounded-xl bg-bad/10 px-3 py-2 text-sm text-bad">
    <AlertCircle size={16} className="mt-0.5 shrink-0" />
    <div>{message}<div className="mt-1 text-xs text-muted">请核对任务状态或 <Link to="/settings" className="underline">设置</Link>，再决定是否重试。</div></div>
  </div>;
}

function ToolStep({ part, sid }: { part: Extract<ConversationPart, { kind: "tool" }>; sid: string | null }) {
  const [open, setOpen] = useState(false);
  const done = part.result !== undefined;
  const failed = done && part.result && typeof part.result === "object" && "error" in part.result;
  return <div className="text-sm">
    <button type="button" aria-expanded={open} onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 text-left text-muted">
      {!done ? <Loader2 size={14} className="shrink-0 animate-spin text-run" /> : failed ? <XCircle size={14} className="shrink-0 text-bad" /> : <CheckCircle2 size={14} className="shrink-0 text-ok" />}
      <span className="min-w-0 flex-1 break-words">{ACTIONS[part.name] ?? part.name}{failed && "（失败）"}</span>
      {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
    </button>
    {open && <div className="mt-2 space-y-2">
      <pre className="max-h-60 overflow-auto rounded-lg bg-panel2 p-3 text-[11px] text-muted">{JSON.stringify({ arguments: part.args, ...(done ? { result: part.result } : {}) }, null, 2)}</pre>
      {sid && part.output_ref && <ToolOutputView sid={sid} output={part.output_ref} />}
    </div>}
  </div>;
}

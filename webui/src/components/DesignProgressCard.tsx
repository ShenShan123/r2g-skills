import { Link } from "react-router-dom";
import { MessagesSquare, Wand2 } from "lucide-react";
import { askAgent, Design } from "../lib/api";
import { StepTrack } from "./ui";
import { DesignStatusBadge, legacyValidation, ValidationSummary, validationLabel, validationVerdict } from "./ValidationEvidence";

export const STAGE_ZH: Record<string, string> = {
  queued: "排队", prepare: "准备", synth: "综合", size: "定尺寸", flow: "布局布线", signoff: "签核", repair: "自动修复",
  graph: "生成数据集", verify: "数据集校验", preview: "版图预览", ingest: "写入知识库", done: "完成",
};

export function designSummary(d: Design, status = d.status): string {
  const v = d.detail?.verify;
  if (status === "pending") return "排队中，等待空闲的算力";
  if (status === "passed") return `流程完成 · ${validationLabel(d.detail?.validation)}`
    + (v && typeof v.passed === "number" && typeof v.total === "number" ? ` · 数据集校验原记录 ${v.passed}/${v.total} 项` : "");
  if (status === "failed") return d.detail?.reason ?? "失败";
  if (status === "cancelled") return "已取消";
  if (status === "cancelling") return "取消请求已保存，正在等待执行进程停止";
  if (status === "recovery_required") return d.detail?.reason ?? "需要核对旧执行状态，未自动重跑";
  return `正在${STAGE_ZH[d.stage] ?? d.stage}…`;
}

function verdictLabel(value: string | undefined): string {
  return ({ failed: "失败", incomplete: "未完成", missing: "缺少报告", skipped: "未执行",
    timeout: "超时", violations: "存在违例", mismatch: "不一致" } as Record<string, string>)[value ?? ""] ?? value ?? "未确认";
}

/** Both the task page and conversation project the same persisted design state. */
export function DesignProgressCard({ jobId, d, onOpen, jobStatus, offerAnalysis = true }: {
  jobId: string; d: Design; onOpen?: () => void; jobStatus?: string; offerAnalysis?: boolean;
}) {
  const s = d.detail?.sizing; const so = d.detail?.signoff;
  const validation = d.detail?.validation;
  const fixes: any[] = [...(d.detail?.frontend_fixes ?? []), ...(d.detail?.flow_fixes ?? [])];
  const repaired = Array.isArray(d.detail?.repair) && d.detail.repair.length > 0;
  const status = jobStatus === "cancelling" && ["running", "pending"].includes(d.status) ? "cancelling" : d.status;
  const statusColor = status === "failed" ? "text-bad" : ["cancelling", "recovery_required"].includes(status) ? "text-warn" : "text-muted";
  const detailsUrl = `/jobs/${encodeURIComponent(jobId)}`;
  return (
    <div className="card min-w-0 p-3 sm:p-4 md:p-5">
      <div className="flex flex-wrap items-center gap-2 sm:gap-3">
        <div className="min-w-0 flex-1 truncate font-mono font-semibold" title={d.name}>{d.name}</div>
        <DesignStatusBadge status={status} validation={validation} />
        {onOpen ? <button className="btn-ghost px-3 py-1.5" onClick={onOpen}>详情</button>
          : <Link className="btn-ghost px-3 py-1.5" to={detailsUrl}>详情</Link>}
      </div>
      <div className="mt-4"><StepTrack stage={d.stage} status={status} repaired={repaired}
        validation={validationVerdict(validation)} /></div>
      <p className={`mt-3 break-words text-sm ${statusColor}`}>{designSummary(d, status)}</p>
      <div className="mt-2"><ValidationSummary value={validation} /></div>
      {(s || so) && (
        <div className="mt-3 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
          {s && <span>{s.cells ?? "—"} 个单元 · die {s.die_side_um ?? "—"} µm{s.binding === "fixed" ? "（自定义）" : s.binding === "pins_ppl" ? "（按引脚数修正）" : ""} · 时钟 {s.period_ns ?? "—"} ns{s.period_source === "fixed" ? "（自定义）" : ""}</span>}
          {so && legacyValidation(validation) && <span>原报告声称：DRC {so.drc === "clean" ? "干净" : so.drc_violations > 0 ? `${so.drc_violations} 处违例` : verdictLabel(so.drc)}
            {" · "}LVS {so.lvs === "clean" ? "一致" : verdictLabel(so.lvs)}（待复核）</span>}
        </div>)}
      {(fixes.length > 0 || repaired) && (
        <div className="mt-3 rounded-xl bg-accent2/10 px-3 py-2 text-xs">
          <div className="flex items-center gap-1.5 font-medium text-accent2"><Wand2 size={13} /> 自动修复</div>
          <ul className="mt-1 space-y-0.5 break-words text-muted">
            {fixes.map((f, i) => <li key={i}>{f.detail}</li>)}
            {repaired && <li>签核未通过后自动诊断并修复（{d.detail.repair.length} 步，见详情）</li>}
          </ul>
        </div>)}
      {offerAnalysis && d.status === "failed" && (
        <Link className="btn-ghost mt-3" to={askAgent(`任务 ${jobId} 里的设计 ${d.name} 失败了，帮我分析原因并告诉我怎么改`)}>
          <MessagesSquare size={15} /> 让 agent 分析原因</Link>)}
    </div>
  );
}

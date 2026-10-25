import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertCircle, CheckCircle2, ChevronDown, ChevronRight, Loader2, ShieldCheck } from "lucide-react";

type Proposal = {
  repair_proposal: string; status: string; diff: string; source_snapshot_id: string;
  design?: string; parent_job_id?: string; child_job_id?: string; action: string;
};
const TERMINAL = new Set(["validated", "stopped", "check_failed", "budget_exhausted", "failed", "rejected", "revoked", "cancelled", "expired"]);
const STATUS: Record<string, string> = {
  approved: "已授权，等待执行", executing: "正在检查候选修复", running: "候选修复正在运行验证流程",
  recovery_required: "执行状态需要核对，已停止自动重跑",
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 15000);
  try {
    const response = await fetch(url, { ...init, signal: controller.signal });
    let result: any;
    try { result = await response.json(); } catch { throw new Error("服务返回了无法读取的响应。"); }
    if (!response.ok) throw new Error(typeof result?.detail === "string" ? result.detail : `请求失败（${response.status}）`);
    return result;
  } finally { clearTimeout(timer); }
}

/** Approval is an explicit user action against a freshly loaded, concrete diff. */
export function RepairProposal({ sid, proposalId }: { sid: string; proposalId: string }) {
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [actionError, setActionError] = useState("");
  const [approving, setApproving] = useState(false);
  const [expanded, setExpanded] = useState(true);
  const [refresh, setRefresh] = useState(0);
  const lastStatus = useRef<string>();
  const url = `/api/sessions/${encodeURIComponent(sid)}/repair-proposals/${encodeURIComponent(proposalId)}`;

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const current = await request<Proposal>(url);
        if (!alive) return;
        if (current.repair_proposal !== proposalId || typeof current.status !== "string" || typeof current.diff !== "string"
            || typeof current.source_snapshot_id !== "string") throw new Error("提案状态不完整，暂不可授权。");
        setProposal(current); setLoadError("");
        if (current.status !== "awaiting_approval") {
          if (lastStatus.current !== current.status) setExpanded(false);
          setActionError("");
        }
        lastStatus.current = current.status;
        if (!TERMINAL.has(current.status)) timer = setTimeout(load, 3000);
      } catch (e: any) {
        if (!alive) return;
        setLoadError(e.name === "AbortError" ? "读取提案状态超时，暂不可授权。" : e.message || "无法读取提案状态，暂不可授权。");
        timer = setTimeout(load, 5000);
      } finally { if (alive) setLoading(false); }
    };
    void load();
    return () => { alive = false; clearTimeout(timer); };
  }, [url, proposalId, refresh]);

  async function approve() {
    if (!proposal || proposal.status !== "awaiting_approval" || !supported || loadError || loading || approving) return;
    setApproving(true); setActionError("");
    try {
      await request(url + "/approve", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_snapshot_id: proposal.source_snapshot_id, max_rounds: 2, max_model_calls: 6, wall_seconds: 900 }) });
    } catch (e: any) {
      setActionError(e.name === "AbortError"
        ? "授权请求超时，结果尚未确认。请核对提案状态；系统不会自动重复提交。"
        : `授权请求未确认：${e.message || "连接中断"}。请核对状态后再决定是否重试。`);
    } finally {
      setApproving(false); setLoading(true); setRefresh((n) => n + 1);
    }
  }

  // Completed proposals stay in the inspectable tool record, not the result area.
  if (proposal && TERMINAL.has(proposal.status) && !loadError) return null;
  const awaiting = proposal?.status === "awaiting_approval";
  const supported = proposal?.action === "rtl_insert_semicolon" && /^[0-9a-f]{64}$/i.test(proposal.source_snapshot_id) && !!proposal.diff.trim();
  return <section aria-label="修复提案" data-proposal-id={proposalId} className="space-y-3 rounded-xl border border-warn/40 bg-warn/5 p-3 sm:p-4">
    <div className="flex items-center gap-2 text-sm font-semibold">
      {loadError ? <AlertCircle size={17} className="text-warn" /> : awaiting ? <ShieldCheck size={17} className="text-warn" /> : loading ? <Loader2 size={16} className="animate-spin text-run" />
        : proposal?.status === "recovery_required" ? <AlertCircle size={17} className="text-warn" /> : <CheckCircle2 size={17} className="text-muted" />}
      <h3>{loadError ? "修复提案状态尚未确认" : awaiting ? "请审核修复差异" : proposal ? STATUS[proposal.status] ?? "核对修复提案" : "正在读取修复提案…"}</h3>
      {proposal?.design && <span className="ml-auto min-w-0 truncate font-mono text-xs text-muted">{proposal.design}</span>}
    </div>
    {loadError && <div role="alert" className="space-y-1 text-xs text-warn">
      <p>{loadError}{proposal && " 以下为上次读取的提案，当前状态尚未确认。"}</p>
      <button className="underline" onClick={() => { setLoading(true); setRefresh((n) => n + 1); }}>重新读取状态</button>
    </div>}
    {proposal && <>
      {awaiting && supported && <>
        <p className="text-sm text-muted">仅在 RTL 副本的一处补充分号。原始 RTL、时序约束、模块接口和功能测试标准均受保护。</p>
        <p className="text-xs text-muted">本次授权上限：2 轮修复、6 次模型调用、15 分钟。验证通过后才交付结果。</p>
      </>}
      <div>
        <button type="button" aria-expanded={expanded} onClick={() => setExpanded((v) => !v)} className="flex items-center gap-1.5 text-xs text-accent">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {expanded ? "收起修复差异" : "查看修复差异"}
        </button>
        {expanded && <pre aria-label="修复差异" className="mt-2 max-h-80 overflow-auto whitespace-pre rounded-lg border border-line bg-panel p-3 font-mono text-xs leading-5">{proposal.diff}</pre>}
      </div>
      <p className="text-xs text-faint" title={proposal.source_snapshot_id}>源码快照：{proposal.source_snapshot_id.slice(0, 12)}</p>
      {awaiting && !supported && <p role="alert" className="text-xs text-warn">该提案不满足当前可授权的修复范围，请重新读取并核对。</p>}
      {actionError && <p role="alert" className="text-xs text-bad">{actionError}</p>}
      <div className="flex flex-wrap items-center gap-3">
        {awaiting && <button type="button" className="btn-primary" disabled={!supported || loading || !!loadError || approving} onClick={approve}>
          {approving ? <Loader2 size={15} className="animate-spin" /> : <ShieldCheck size={15} />} {approving ? "正在提交授权…" : "同意差异，授权有限修复"}
        </button>}
        {proposal.parent_job_id && <Link to={`/jobs/${encodeURIComponent(proposal.parent_job_id)}`} className="text-xs text-accent hover:underline">查看原始失败报告</Link>}
      </div>
    </>}
  </section>;
}

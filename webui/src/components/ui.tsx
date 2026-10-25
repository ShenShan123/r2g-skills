import { CheckCircle2, CircleDashed, Loader2, XCircle, Ban, Minus, AlertTriangle } from "lucide-react";
import { useId } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const STATUS: Record<string, { label: string; cls: string; Icon: any }> = {
  passed: { label: "通过", cls: "bg-ok/15 text-ok", Icon: CheckCircle2 },
  done: { label: "完成", cls: "bg-ok/15 text-ok", Icon: CheckCircle2 },
  failed: { label: "失败", cls: "bg-bad/15 text-bad", Icon: XCircle },
  running: { label: "进行中", cls: "bg-run/15 text-run", Icon: Loader2 },
  finalizing: { label: "打包中", cls: "bg-run/15 text-run", Icon: Loader2 },
  pending: { label: "排队中", cls: "bg-faint/15 text-muted", Icon: CircleDashed },
  cancelled: { label: "已取消", cls: "bg-warn/15 text-warn", Icon: Ban },
  cancelling: { label: "正在取消", cls: "bg-warn/15 text-warn", Icon: Loader2 },
  recovery_required: { label: "需要恢复核对", cls: "bg-warn/15 text-warn", Icon: AlertTriangle },
};
export const isActive = (s: string) => ["running", "pending", "finalizing", "cancelling", "recovery_required"].includes(s);

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.pending;
  return (
    <span className={`chip whitespace-nowrap ${s.cls}`}>
      <s.Icon size={13} className={status === "running" || status === "finalizing" ? "animate-spin" : ""} />
      {s.label}
    </span>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  // a small graph: the product turns layouts into graphs. The gradient id is per
  // instance: with a shared id, a copy inside a hidden sidebar blanked the visible one
  const gid = "r2g-lg" + useId().replace(/:/g, "");
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="rgb(var(--accent))" />
          <stop offset="1" stopColor="rgb(var(--accent2))" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill={`url(#${gid})`} />
      <g stroke="rgb(var(--on-accent))" strokeWidth="2" strokeLinecap="round" opacity="0.9">
        <line x1="10" y1="10" x2="22" y2="13" /><line x1="10" y1="10" x2="13" y2="22" /><line x1="22" y1="13" x2="13" y2="22" />
        <line x1="22" y1="13" x2="23" y2="23" />
      </g>
      <g fill="rgb(var(--on-accent))">
        <circle cx="10" cy="10" r="3" /><circle cx="22" cy="13" r="2.6" /><circle cx="13" cy="22" r="2.6" /><circle cx="23" cy="23" r="2" />
      </g>
    </svg>
  );
}

/** The six steps a user recognises, each covering one or more pipeline stages. */
export const STEPS = [
  { label: "综合", stages: ["prepare", "synth", "size"] },
  { label: "布局布线", stages: ["flow"] },
  { label: "签核", stages: ["signoff"] },
  { label: "修复", stages: ["repair"] },
  { label: "数据集", stages: ["graph"] },
  { label: "校验", stages: ["verify", "preview"] },
];

type StepState = "done" | "now" | "fail" | "todo" | "skip" | "stopped" | "review";

export function stepStates(stage: string, status: string, repaired: boolean): StepState[] {
  if (status === "pending") return STEPS.map(() => "todo");
  const cur = stage === "done" ? STEPS.length : Math.max(0, STEPS.findIndex((s) => s.stages.includes(stage)));
  return STEPS.map((s, i) => {
    if (i < cur) return s.label === "修复" && !repaired ? "skip" : "done";
    if (i > cur) return "todo";
    return status === "failed" ? "fail" : status === "passed" ? "done" : status === "recovery_required" ? "review"
      : status === "cancelled" || status === "cancelling" ? "stopped" : "now";
  });
}

/** A labelled step track: what has happened, what is happening, what is next. */
export function StepTrack({ stage, status, repaired, validation }: {
  stage: string; status: string; repaired: boolean; validation?: "PASS" | "FAIL" | "UNKNOWN" | "STALE";
}) {
  const st = stepStates(stage, status, repaired);
  // Completed execution is distinct from a supported signoff verdict.
  if (st[2] === "done" && validation && validation !== "PASS") st[2] = validation === "FAIL" ? "fail" : "review";
  const dot: Record<StepState, string> = {
    done: "bg-ok text-onaccent", now: "bg-run text-onaccent animate-pulse2", fail: "bg-bad text-onaccent",
    todo: "bg-panel2 text-faint border border-line", skip: "bg-panel2 text-faint border border-line",
    stopped: "bg-warn/15 text-warn border border-warn/30", review: "bg-warn/15 text-warn border border-warn/30",
  };
  return (
    <ol className="flex items-start">
      {STEPS.map((s, i) => (
        <li key={s.label} className="flex min-w-0 flex-1 flex-col items-center">
          <div className="flex w-full items-center">
            <div className={`h-0.5 flex-1 ${i === 0 ? "invisible" : st[i] === "todo" ? "bg-line" : "bg-ok/60"}`} />
            <span className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${dot[st[i]]}`}>
              {st[i] === "done" ? <CheckCircle2 size={14} /> : st[i] === "fail" ? <XCircle size={14} /> :
               st[i] === "skip" ? <Minus size={12} /> : st[i] === "review" ? <AlertTriangle size={13} /> : st[i] === "stopped" ? <Ban size={13} /> : i + 1}
            </span>
            <div className={`h-0.5 flex-1 ${i === STEPS.length - 1 ? "invisible" : st[i + 1] === "todo" ? "bg-line" : "bg-ok/60"}`} />
          </div>
          <span className={`mt-1.5 text-center text-[10px] leading-4 sm:text-[11px] ${st[i] === "now" ? "font-medium text-run" : st[i] === "fail" ? "font-medium text-bad" : st[i] === "stopped" || st[i] === "review" ? "text-warn" : "text-muted"}`}>
            {s.label}{st[i] === "skip" && <span className="block sm:inline">（无需）</span>}
          </span>
        </li>))}
    </ol>
  );
}

export function Toggle({ on, onChange, label }: { on: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className={`relative h-6 w-11 shrink-0 rounded-full transition ${on ? "bg-accent" : "bg-line"}`}>
      <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-[22px]" : "left-0.5"}`} />
    </button>
  );
}

export function Md({ children }: { children: string }) {
  return <div className="prose-r2g text-[15px]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown></div>;
}

export function Stat({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return (
    <div className="rounded-xl bg-panel2 px-4 py-3">
      <div className="text-xs text-muted">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value ?? "—"}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

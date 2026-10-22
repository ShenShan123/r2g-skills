import { CheckCircle2, CircleDashed, Loader2, XCircle, Ban } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

export const STATUS: Record<string, { label: string; cls: string; Icon: any }> = {
  passed: { label: "通过", cls: "bg-ok/15 text-ok", Icon: CheckCircle2 },
  done: { label: "完成", cls: "bg-ok/15 text-ok", Icon: CheckCircle2 },
  failed: { label: "失败", cls: "bg-bad/15 text-bad", Icon: XCircle },
  running: { label: "进行中", cls: "bg-run/15 text-run", Icon: Loader2 },
  pending: { label: "排队", cls: "bg-faint/15 text-muted", Icon: CircleDashed },
  cancelled: { label: "已取消", cls: "bg-warn/15 text-warn", Icon: Ban },
};

export function StatusBadge({ status }: { status: string }) {
  const s = STATUS[status] ?? STATUS.pending;
  return (
    <span className={`chip whitespace-nowrap ${s.cls}`}>
      <s.Icon size={13} className={status === "running" ? "animate-spin" : ""} />
      {s.label}
    </span>
  );
}

export function Logo({ size = 30 }: { size?: number }) {
  // a small graph: the product turns layouts into graphs
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <defs>
        <linearGradient id="r2g-lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="rgb(var(--accent))" />
          <stop offset="1" stopColor="rgb(var(--accent2))" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="8" fill="url(#r2g-lg)" />
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

/** One segment per pipeline stage; filled up to the current one. */
export function StagePipe({ stages, current, status }: { stages: { id: string; label: string }[]; current: string; status: string }) {
  const idx = current === "done" ? stages.length : Math.max(0, stages.findIndex((s) => s.id === current));
  return (
    <div className="flex gap-1" title={stages[idx]?.label ?? "完成"}>
      {stages.map((s, i) => {
        let c = "bg-line";
        if (i < idx) c = "bg-ok";
        else if (i === idx) c = status === "failed" ? "bg-bad" : status === "running" ? "bg-run animate-pulse2" : status === "passed" ? "bg-ok" : "bg-line";
        return <div key={s.id} className={`h-1.5 flex-1 rounded-full ${c}`} title={s.label} />;
      })}
    </div>
  );
}

export function Md({ children }: { children: string }) {
  return <div className="prose-r2g text-[15px]"><ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown></div>;
}

export function Stat({ label, value, sub }: { label: string; value: any; sub?: string }) {
  return (
    <div className="rounded-xl bg-panel2 px-4 py-3">
      <div className="label">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value ?? "—"}</div>
      {sub && <div className="text-xs text-muted">{sub}</div>}
    </div>
  );
}

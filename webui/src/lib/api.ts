// Thin client for the r2g agent API. All verdicts shown in the UI come from here.
export type Stage = { id: string; label: string };
export type Platform = "nangate45" | "sky130hd" | "gf180";
export const PLATFORM_LABEL: Record<string, string> = { nangate45: "Nangate45 · 45 nm", sky130hd: "SKY130 HD · 130 nm", gf180: "GF180 MCU · 180 nm" };
export type Output = "graph" | "image";
export const OUTPUT_LABEL: Record<string, string> = { graph: "图数据集", image: "图像数据集" };
export type Design = {
  job_id: string; name: string; project: string; stage: string; status: string;
  detail: Record<string, any>; updated: number;
};
export type Job = {
  id: string; created: number; updated: number; status: string; title: string;
  spec: Record<string, any>; result: Record<string, any> | null;
  designs?: Design[]; counts?: Record<string, number>; dual_clean?: number;
};
export function jobStatus(job: Job): string {
  const c = job.counts ?? Object.fromEntries(["pending", "running"].map(
    s => [s, job.designs?.filter(d => d.status === s).length ?? 0]));
  return job.status === "running" && !c.running && c.pending ? "pending" : job.status;
}
export type ScanDesign = { name: string; path: string; files: number; top: string | null; top_source: string | null; problem: string | null };
export type Scan = { root: string; total: number; ok: number; skipped: number; designs: ScanDesign[] };
export type JobEvent = {
  id: number; job_id: string; design: string | null; ts: number; stage: string;
  kind: string; message: string; data: Record<string, any>;
};

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

export const api = {
  deleteResource: (kind: "job" | "session", id: string) =>
    fetch(`/api/${kind === "job" ? "jobs" : "sessions"}/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }),
    }).then(j<{ status: string; error?: string }>),
  deletion: (kind: "job" | "session", id: string) =>
    fetch(`/api/deletions/${kind}/${encodeURIComponent(id)}`).then(j<{ status: string; error?: string }>),
  health: () => fetch("/api/health").then(j<any>),
  config: () => fetch("/api/config").then(j<any>),
  saveConfig: (body: any) =>
    fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j<any>),
  testModel: () => fetch("/api/config/test", { method: "POST" }).then(j<any>),
  examples: () => fetch("/api/examples").then(j<{ examples: { name: string; top: string; description: string }[] }>),
  jobs: async () => {
    const jobs = await fetch("/api/jobs").then(j<Job[]>);
    // Old workers can finish their experiments before the API is restarted.
    // During that transition only active rows need the detail fallback.
    return Promise.all(jobs.map(async job => {
      if (job.dual_clean !== undefined || !["running", "pending", "cancelling"].includes(job.status)) return job;
      try {
        const full = await fetch(`/api/jobs/${job.id}`).then(j<Job>);
        return { ...job, dual_clean: full.designs?.filter(d => d.detail?.signoff?.dual_clean === true).length };
      } catch { return job; }
    }));
  },
  job: (id: string) => fetch(`/api/jobs/${id}`).then(j<Job>),
  events: (id: string, after = 0, tail = 0) => fetch(`/api/jobs/${id}/events?after=${after}&tail=${tail}`).then(j<JobEvent[]>),
  scan: (path: string) =>
    fetch("/api/batch/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) })
      .then(j<Scan>),
  packageNow: (id: string) => fetch(`/api/jobs/${id}/package`, { method: "POST" }).then(j<{ url: string; designs: number }>),
  createJob: (designs: any[], title = "", output: Output = "graph", params?: Record<string, any>, platform: Platform = "nangate45") =>
    fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ designs, platform, output, title, params }) }).then(j<{ job_id: string }>),
  images: (id: string, d: string) => fetch(`/api/jobs/${id}/designs/${d}/images`).then(j<any>),
  stageViews: (id: string, d: string, version?: "final") => fetch(`/api/jobs/${encodeURIComponent(id)}/designs/${encodeURIComponent(d)}/stage-views${version ? `?version=${version}` : ""}`).then(j<any>),
  cancel: (id: string) => fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).then(j<any>),
  reports: (id: string, d: string) => fetch(`/api/jobs/${id}/designs/${d}/reports`).then(j<any>),
  log: (id: string, d: string, stage: string) => fetch(`/api/jobs/${id}/designs/${d}/log/${stage}?lines=300`).then(j<any>),
  sessions: () => fetch("/api/sessions").then(j<any[]>),
  session: (id: string) => fetch(`/api/sessions/${id}`).then(j<any>),
  upload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f, (f as any).webkitRelativePath || f.name));
    return fetch("/api/upload", { method: "POST", body: fd }).then(j<{ path: string; files: string[]; skipped: string[] }>);
  },
};

export type ChatEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; delta: string }
  | { type: "thinking"; delta: string }
  | { type: "tool_call"; id: string; name: string; arguments: any }
  | { type: "tool_result"; id: string; name: string; result: any }
  | { type: "error"; message: string }
  | { type: "done" };

/** POST a chat turn and read the SSE stream event by event. */
export async function chatStream(message: string, sessionId: string | null, on: (e: ChatEvent) => void) {
  const r = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });
  if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
  const reader = r.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data:"));
      if (line) { try { on(JSON.parse(line.slice(5))); } catch { /* partial */ } }
    }
  }
}

export const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
/** Chat link that pre-fills a question for the agent (the user still presses send). */
export const askAgent = (q: string) => `/chat?q=${encodeURIComponent(q)}`;
export const fmtClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });

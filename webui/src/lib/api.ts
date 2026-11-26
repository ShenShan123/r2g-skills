// Thin client for the r2g agent API. All verdicts shown in the UI come from here.
import { getLang } from "./i18n";
export type Stage = { id: string; label: string };
export type Platform = "nangate45" | "sky130hd" | "gf180";
export const PLATFORM_LABEL: Record<string, string> = { nangate45: "Nangate45 · 45 nm", sky130hd: "SKY130 HD · 130 nm", gf180: "GF180 MCU · 180 nm" };
export type Output = "graph" | "image";
export const OUTPUT_LABEL: Record<string, string> = { graph: "图数据集", image: "图像数据集", signoff: "仅签核" };
export type Design = {
  job_id: string; name: string; project: string; stage: string; status: string;
  detail: Record<string, any>; updated: number;
};
export type Job = {
  id: string; created: number; updated: number; status: string; title: string;
  spec: Record<string, any>; result: Record<string, any> | null;
  designs?: Design[]; counts?: Record<string, number>; dual_clean?: number;
  deletion?: string | null;
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

/** An API error: the server's message (FastAPI `detail`, a string or a validation list) plus the status. */
export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}

async function errorOf(r: Response): Promise<ApiError> {
  let msg = r.statusText || `HTTP ${r.status}`;
  try {
    const d = (await r.json()).detail;
    if (typeof d === "string") msg = d;
    else if (Array.isArray(d)) msg = d.map((x: any) => x?.msg ?? JSON.stringify(x)).join("; ");
  } catch { /* not json */ }
  if (r.status === 413 && !msg) msg = "上传太大";
  return new ApiError(msg, r.status);
}

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) throw await errorOf(r);
  return r.json();
}

const post = (url: string, body?: any) => fetch(url, body === undefined ? { method: "POST" } : {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export type Upload = { path: string; id: string; files: string[]; skipped: string[];
  rejected: { file: string; reason: string }[]; bytes: number };
export type Inspect = ScanDesign & { candidates: string[]; modules: number; file_names: string[] };
export type JobFilter = "" | "active" | "done" | "failed" | "cancelled";

// a large folder goes up in several requests into one upload (the server takes
// at most 2000 files per request, and one huge request is all-or-nothing)
const UPLOAD_BATCH_FILES = 400;
const UPLOAD_BATCH_BYTES = 48 << 20;

export const api = {
  deleteResource: (kind: "job" | "session", id: string) =>
    fetch(`/api/${kind === "job" ? "jobs" : "sessions"}/${encodeURIComponent(id)}`, {
      method: "DELETE", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ confirm: true }),
    }).then(j<{ status: string; error?: string }>),
  deletion: (kind: "job" | "session", id: string) =>
    fetch(`/api/deletions/${kind}/${encodeURIComponent(id)}`).then(j<{ status: string; error?: string }>),
  health: () => fetch("/api/health").then(j<any>),
  config: () => fetch("/api/config").then(j<any>),
  saveConfig: (body: any) => post("/api/config", body).then(j<any>),
  testModel: () => post("/api/config/test").then(j<any>),
  platforms: () => fetch("/api/platforms").then(j<{ platforms: { id: string; label: string }[] }>),
  examples: () => fetch("/api/examples").then(j<{ examples: { name: string; top: string; description: string }[] }>),
  /** One page of jobs, newest first, with the number that match the filter. */
  jobPage: async (opts: { limit?: number; offset?: number; status?: JobFilter; q?: string } = {}) => {
    const qs = new URLSearchParams({ limit: String(opts.limit ?? 50), offset: String(opts.offset ?? 0),
      status: opts.status ?? "", q: opts.q ?? "" });
    const r = await fetch(`/api/jobs?${qs}`);
    const jobs = await j<Job[]>(r);
    return { jobs, total: Number(r.headers.get("X-Total-Count") ?? jobs.length) };
  },
  jobs: (limit = 50) => api.jobPage({ limit }).then((p) => p.jobs),
  job: (id: string) => fetch(`/api/jobs/${encodeURIComponent(id)}`).then(j<Job>),
  events: (id: string, after = 0, tail = 0) => fetch(`/api/jobs/${encodeURIComponent(id)}/events?after=${after}&tail=${tail}`).then(j<JobEvent[]>),
  scan: (path: string) => post("/api/batch/scan", { path }).then(j<Scan>),
  inspect: (path: string) => post("/api/inspect", { path }).then(j<Inspect>),
  packageNow: (id: string) => post(`/api/jobs/${encodeURIComponent(id)}/package`).then(j<{ url: string; designs: number }>),
  createJob: (designs: any[], title = "", output: Output = "graph", params?: Record<string, any>, platform: Platform = "nangate45") =>
    post("/api/jobs", { designs, platform, output, title, params }).then(j<{ job_id: string }>),
  retry: (id: string, designs?: string[]) =>
    post(`/api/jobs/${encodeURIComponent(id)}/retry`, designs ? { designs } : {}).then(j<{ job_id: string; designs: string[] }>),
  images: (id: string, d: string) => fetch(`/api/jobs/${encodeURIComponent(id)}/designs/${encodeURIComponent(d)}/images`).then(j<any>),
  stageViews: (id: string, d: string, version?: "final") => fetch(`/api/jobs/${encodeURIComponent(id)}/designs/${encodeURIComponent(d)}/stage-views${version ? `?version=${version}` : ""}`).then(j<any>),
  cancel: (id: string) => post(`/api/jobs/${encodeURIComponent(id)}/cancel`).then(j<any>),
  reports: (id: string, d: string) => fetch(`/api/jobs/${encodeURIComponent(id)}/designs/${encodeURIComponent(d)}/reports`).then(j<any>),
  log: (id: string, d: string, stage: string) => fetch(`/api/jobs/${encodeURIComponent(id)}/designs/${encodeURIComponent(d)}/log/${stage}?lines=300`).then(j<any>),
  sessions: () => fetch("/api/sessions").then(j<any[]>),
  session: (id: string) => fetch(`/api/sessions/${encodeURIComponent(id)}`).then(j<any>),
  stopChat: (id: string) => post(`/api/sessions/${encodeURIComponent(id)}/stop`).then(j<{ stopping: boolean }>),
  /** keepalive: still sent while the tab is closing */
  deleteUpload: (id: string, keepalive = false) => fetch(`/api/uploads/${encodeURIComponent(id)}`, { method: "DELETE", keepalive }).then(j<any>),
  /** Upload files (HDL first) in batches into ONE upload; `onProgress(sentBytes, totalBytes)`. */
  upload: async (files: File[], onProgress?: (sent: number, total: number) => void): Promise<Upload> => {
    const hdlFirst = [...files].sort((a, b) => Number(!/\.s?vh?$/i.test(a.name)) - Number(!/\.s?vh?$/i.test(b.name)));
    const total = hdlFirst.reduce((n, f) => n + f.size, 0);
    let sent = 0, result: Upload | null = null;
    for (let i = 0; i < hdlFirst.length;) {
      const fd = new FormData();
      let n = 0, bytes = 0;
      while (i < hdlFirst.length && n < UPLOAD_BATCH_FILES && (n === 0 || bytes + hdlFirst[i].size <= UPLOAD_BATCH_BYTES)) {
        const f = hdlFirst[i++];
        fd.append("files", f, (f as any).webkitRelativePath || f.name);
        n += 1; bytes += f.size;
      }
      let part: Upload;
      try {
        part = await fetch(`/api/upload${result ? `?into=${result.id}` : ""}`, { method: "POST", body: fd }).then(j<Upload>);
      } catch (e) {
        if (result) api.deleteUpload(result.id).catch(() => {});   // no half upload left behind
        throw e;
      }
      result = result ? { ...part, files: [...result.files, ...part.files], skipped: [...result.skipped, ...part.skipped],
        rejected: [...result.rejected, ...part.rejected], bytes: part.bytes } : part;
      sent += bytes; onProgress?.(sent, total);
    }
    if (!result) throw new ApiError("没有要上传的文件", 400);
    return result;
  },
};

export type ChatEvent =
  | { type: "session"; session_id: string }
  | { type: "text"; delta: string }
  | { type: "thinking"; delta: string }
  | { type: "tool_call"; id: string; name: string; arguments: any }
  | { type: "tool_result"; id: string; name: string; result: any }
  | { type: "error"; message: string }
  | { type: "stopped" }
  | { type: "done" };

/** POST a chat turn and read the SSE stream event by event. Aborting `signal` stops
 *  reading only: the server finishes (or, after api.stopChat, stops) the turn itself. */
export async function chatStream(message: string, sessionId: string | null, on: (e: ChatEvent) => void, signal?: AbortSignal) {
  const r = await fetch("/api/chat", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }), signal,
  });
  if (!r.ok || !r.body) throw await errorOf(r);
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
  new Date(ts * 1000).toLocaleString(getLang() === "en" ? "en-US" : "zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
/** Chat link that pre-fills a question for the agent (the user still presses send). */
export const askAgent = (q: string) => `/chat?q=${encodeURIComponent(q)}`;
/** "12.3 MB" */
export const fmtBytes = (n: number) => n >= 1 << 30 ? `${(n / (1 << 30)).toFixed(1)} GB` : n >= 1 << 20 ? `${(n / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;
export const fmtClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString(getLang() === "en" ? "en-US" : "zh-CN", { hour12: false });

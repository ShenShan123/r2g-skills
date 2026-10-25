// Thin client for the r2g agent API. All verdicts shown in the UI come from here.
export type Stage = { id: string; label: string };
export type ToolOutputRef = {
  schema_version: number; artifact_id: string; sha256: string;
  original_bytes: number; stored_bytes: number; redacted: boolean;
};
export type ToolOutputPage = {
  artifact_id: string; sha256: string; text: string; offset: number;
  next_offset: number | null; total_chars: number; stored_bytes: number;
  redacted: boolean; match: number | null;
};
export type Output = "graph" | "image";
export const OUTPUT_LABEL: Record<string, string> = { graph: "图数据集", image: "图像数据集" };
export type Design = {
  job_id: string; name: string; project: string; stage: string; status: string;
  detail: Record<string, any>; updated: number;
};
export type Job = {
  id: string; created: number; updated: number; status: string; title: string;
  spec: Record<string, any>; result: Record<string, any> | null;
  designs?: Design[]; counts?: Record<string, number>;
  cancel_requested_at?: number | null; cancelled_at?: number | null;
};
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
  health: () => fetch("/api/health").then(j<any>),
  config: () => fetch("/api/config").then(j<any>),
  saveConfig: (body: any) =>
    fetch("/api/config", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }).then(j<any>),
  testModel: () => fetch("/api/config/test", { method: "POST" }).then(j<any>),
  examples: () => fetch("/api/examples").then(j<{ examples: { name: string; top: string; description: string }[] }>),
  jobs: () => fetch("/api/jobs").then(j<Job[]>),
  job: (id: string, signal?: AbortSignal) => fetch(`/api/jobs/${encodeURIComponent(id)}`, { signal }).then(j<Job>),
  events: (id: string, after = 0, tail = 0, signal?: AbortSignal) =>
    fetch(`/api/jobs/${encodeURIComponent(id)}/events?after=${after}&tail=${tail}`, { signal }).then(j<JobEvent[]>),
  scan: (path: string) =>
    fetch("/api/batch/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) })
      .then(j<Scan>),
  packageNow: (id: string) => fetch(`/api/jobs/${id}/package`, { method: "POST" }).then(j<{ url: string; designs: number }>),
  createJob: (designs: any[], title = "", output: Output = "graph", params?: Record<string, any>) =>
    fetch("/api/jobs", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ designs, platform: "nangate45", output, title, params }) }).then(j<{ job_id: string }>),
  images: (id: string, d: string) => fetch(`/api/jobs/${id}/designs/${d}/images`).then(j<any>),
  cancel: (id: string) => fetch(`/api/jobs/${id}/cancel`, { method: "POST" }).then(j<any>),
  reports: (id: string, d: string) => fetch(`/api/jobs/${id}/designs/${d}/reports`).then(j<any>),
  log: (id: string, d: string, stage: string) => fetch(`/api/jobs/${id}/designs/${d}/log/${stage}?lines=300`).then(j<any>),
  sessions: () => fetch("/api/sessions").then(j<any[]>),
  session: (id: string) => fetch(`/api/sessions/${id}`).then(j<any>),
  turnEvents: (id: string, after = 0) => fetch(`/api/turns/${encodeURIComponent(id)}/events?after=${after}`).then(j<any>),
  toolOutput: (sid: string, id: string, offset = 0, query = "") =>
    fetch(`/api/sessions/${encodeURIComponent(sid)}/tool-outputs/${encodeURIComponent(id)}?` +
      new URLSearchParams({ offset: String(offset), limit: "4000", query })).then(j<ToolOutputPage>),
  upload: (files: File[]) => {
    const fd = new FormData();
    files.forEach((f) => fd.append("files", f, (f as any).webkitRelativePath || f.name));
    return fetch("/api/upload", { method: "POST", body: fd }).then(j<{ path: string; files: string[]; skipped: string[] }>);
  },
};

export type ChatEvent = ({ session_id?: string; turn_id?: string; event_id?: number } & (
  | { type: "session"; session_id: string; turn_id: string }
  | { type: "text"; delta: string }
  | { type: "model_response"; id: string; metadata: Record<string, any> }
  | { type: "tool_call"; id: string; name: string; arguments: any }
  | { type: "tool_result"; id: string; name: string; result: any; output_ref?: ToolOutputRef }
  | { type: "error"; message: string }
  | { type: "done" }));

/** POST a chat turn and read the SSE stream event by event. */
export async function chatStream(message: string, sessionId: string | null, on: (e: ChatEvent) => void,
  options: { requestId?: string; signal?: AbortSignal } = {}) {
  const requestId = options.requestId ?? crypto.randomUUID();
  let turnId: string | null = null, cursor = 0, terminal = false;
  const consume = async (r: Response) => {
  if (!r.ok) { await j(r); return; }
  if (!r.body) throw new Error("响应没有事件流");
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
      if (line) {
        const event = JSON.parse(line.slice(5)) as ChatEvent;
        if (event.type === "session") {
          if (turnId && turnId !== event.turn_id) throw new Error("响应轮次不匹配");
          turnId = event.turn_id;
        } else if (event.turn_id !== turnId) throw new Error("事件不属于当前轮次");
        if (event.event_id && event.event_id <= cursor) continue;
        if (event.event_id) cursor = event.event_id;
        on(event);
        if (event.type === "done" || event.type === "error") terminal = true;
      }
    }
  }
  };
  for (let attempt = 0; attempt < 3; attempt++) {
    let permanentFailure = false;
    try {
      const response = turnId
        ? await fetch(`/api/turns/${encodeURIComponent(turnId)}/stream?after=${cursor}`, { signal: options.signal })
        : await fetch("/api/chat", { method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ message, session_id: sessionId, request_id: requestId }), signal: options.signal });
      // Submission conflicts are not transport retries.
      if (!response.ok) { permanentFailure = true; await j(response); return; }
      await consume(response);
      if (terminal) return;
      if (turnId) {
        const state = await api.turnEvents(turnId, cursor);
        if (["complete", "error", "recovery_required"].includes(state.turn.status)) return;
      }
    } catch (error) {
      if (permanentFailure || options.signal?.aborted || attempt === 2) throw error;
    }
  }
  throw new Error("连接已中断；已接受的任务会继续，重新打开本会话可读取已保存的进度。");
}

export const fmtTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
/** Chat link that pre-fills a question for the agent (the user still presses send). */
export const askAgent = (q: string) => `/chat?q=${encodeURIComponent(q)}`;
export const fmtClock = (ts: number) =>
  new Date(ts * 1000).toLocaleTimeString("zh-CN", { hour12: false });

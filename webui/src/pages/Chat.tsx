import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Send, Paperclip, X, ChevronDown, ChevronRight, Plus, MessageSquare, ExternalLink, AlertCircle,
         Loader2, CheckCircle2, XCircle, Brain, History } from "lucide-react";
import { api, chatStream, ToolOutputRef } from "../lib/api";
import { Md, Logo } from "../components/ui";
import { ToolOutputView } from "../components/ToolOutputView";

type Part =
  | { kind: "text"; text: string }
  | { kind: "thinking"; text: string }
  | { kind: "tool"; id: string; name: string; args: any; result?: any; output_ref?: ToolOutputRef };
type Msg = { role: "user" | "assistant"; parts: Part[]; error?: string };

const TOOL_LABEL: Record<string, string> = {
  read_tool_output: "读取已保存的工具结果",
  list_examples: "查看内置示例", start_job: "提交任务", job_status: "查询任务进度", list_jobs: "列出任务",
  design_detail: "查看设计详情", read_log: "读取日志", diagnose: "请修复引擎诊断", knowledge_lookup: "查询故障知识库",
  cancel_job: "取消任务", dataset_info: "查询数据集", check_environment: "检查工具链", read_rtl: "读取源码",
  search_rtl: "在 GitHub 上搜索 RTL", fetch_rtl: "下载仓库并列出设计", disk_usage: "查看磁盘占用",
};
const SUGGEST = [
  "帮我在 GitHub 上找一个小的 UART 设计，跑一遍生成图数据集",
  "最近的任务进展怎么样？",
  "跑一下三个内置示例，生成图数据集",
  "PDN-0185 是什么问题，怎么解决？",
];
// HDL plus the data files $readmem reads; nothing else in the folder is sent
const SEND = /\.(s?v|s?vh|hex|mem|bin|txt|dat|coe|mif|init|rom|data)$/i;

/** Rebuild the display from a stored session (internal message format). */
function fromStored(ms: any[]): Msg[] {
  const out: Msg[] = [];
  for (const m of ms) {
    if (m.role === "user") out.push({ role: "user", parts: [{ kind: "text", text: m.content }] });
    else if (m.role === "assistant") {
      const last = out[out.length - 1];
      const target = last && last.role === "assistant" ? last : (out.push({ role: "assistant", parts: [] }), out[out.length - 1]);
      if (m.thinking) target.parts.push({ kind: "thinking", text: m.thinking });
      if (m.content) target.parts.push({ kind: "text", text: m.content });
      for (const t of m.tool_calls ?? []) target.parts.push({ kind: "tool", id: t.id, name: t.name, args: t.arguments });
    } else if (m.role === "tool") {
      const last = out[out.length - 1];
      const p = last?.parts.find((x) => x.kind === "tool" && x.id === m.tool_call_id) as any;
      if (p) { try { p.result = JSON.parse(m.content); } catch { p.result = m.content; } p.output_ref = m.output_ref; }
    }
  }
  return out;
}

export default function Chat() {
  const [params, setParams] = useSearchParams();
  const [sessions, setSessions] = useState<any[]>([]);
  const [sid, setSid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [att, setAtt] = useState<{ path: string; name: string; files: number } | null>(null);
  const [attBusy, setAttBusy] = useState(false);
  const [attErr, setAttErr] = useState("");
  const [hist, setHist] = useState(false);
  const end = useRef<HTMLDivElement>(null);
  const dirInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);

  const loadSessions = () => api.sessions().then(setSessions).catch(() => {});
  useEffect(() => { loadSessions(); }, []);
  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  // a question handed over from another page (e.g. "why did this design fail") is
  // put in the box, not sent: the user sees it and presses send
  useEffect(() => {
    const q = params.get("q");
    if (q) { setInput(q); setSid(null); setMsgs([]); setParams({}, { replace: true }); setTimeout(() => box.current?.focus(), 0); }
  }, [params, setParams]);

  async function open(id: string) {
    setHist(false); setSid(id); const s = await api.session(id); setMsgs(fromStored(s.messages));
  }

  async function attach(list: File[]) {
    setAttErr("");
    const keep = list.filter((f) => SEND.test(f.name));
    if (!keep.some((f) => /\.(s?v|s?vh)$/i.test(f.name))) { setAttErr("这个文件夹里没有 .v / .sv 文件"); return; }
    setAttBusy(true);
    try {
      const up = await api.upload(keep);
      const rel = ((keep[0] as any).webkitRelativePath || keep[0].name) as string;
      setAtt({ path: up.path, name: rel.split("/")[0], files: up.files.length });
    } catch (e: any) { setAttErr(e.message); } finally { setAttBusy(false); }
  }

  async function send(typed: string) {
    if ((!typed.trim() && !att) || busy) return;
    // the model reads the attachment as a path it can pass to start_job
    const text = att ? `[附件] 已上传 RTL 文件夹 ${att.name}（${att.files} 个文件），路径：${att.path}\n${typed}` : typed;
    setInput(""); setAtt(null); setBusy(true);
    setMsgs((m) => [...m, { role: "user", parts: [{ kind: "text", text }] }, { role: "assistant", parts: [] }]);
    const upd = (f: (a: Msg) => void) => setMsgs((m) => {
      const c = m.slice(); const a = { ...c[c.length - 1], parts: c[c.length - 1].parts.slice() }; f(a); c[c.length - 1] = a; return c;
    });
    const append = (kind: "text" | "thinking", delta: string) => upd((a) => {
      const p = a.parts[a.parts.length - 1];
      if (p && p.kind === kind) a.parts[a.parts.length - 1] = { kind, text: (p as any).text + delta };
      else a.parts.push({ kind, text: delta });
    });
    try {
      await chatStream(text, sid, (e) => {
        if (e.type === "session") setSid(e.session_id);
        else if (e.type === "text") append("text", e.delta);
        else if (e.type === "thinking") append("thinking", e.delta);
        else if (e.type === "tool_call") upd((a) => { a.parts.push({ kind: "tool", id: e.id, name: e.name, args: e.arguments }); });
        else if (e.type === "tool_result") upd((a) => {
          a.parts = a.parts.map((p) => (p.kind === "tool" && p.id === e.id ? { ...p, result: e.result, output_ref: e.output_ref } : p));
        });
        else if (e.type === "error") upd((a) => { a.error = e.message; });
      });
    } catch (err: any) { upd((a) => { a.error = err.message; }); }
    setBusy(false); loadSessions();
  }

  const history = (
    <>
      <div className="p-3"><button className="btn-ghost w-full justify-center" onClick={() => { setSid(null); setMsgs([]); setHist(false); }}>
        <Plus size={15} /> 新对话</button></div>
      <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-3">
        {sessions.filter((s) => s.title).map((s) => (
          <button key={s.id} onClick={() => open(s.id)}
            className={`flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm ${sid === s.id ? "bg-panel2 text-fg" : "text-muted hover:bg-panel2/60"}`}>
            <MessageSquare size={14} className="shrink-0" /><span className="truncate">{s.title.replace(/^\[附件\] 已上传 RTL 文件夹 ([^（\s]+).*$/s, "📎 $1")}</span></button>))}
      </div>
    </>);

  return (
    <div className="flex h-full">
      <div className="hidden w-60 shrink-0 flex-col border-r border-line bg-panel/40 lg:flex">{history}</div>
      {hist && (
        <div className="fixed inset-0 z-40 flex bg-black/40 lg:hidden" onClick={() => setHist(false)}>
          <div className="flex w-72 flex-col bg-panel" onClick={(e) => e.stopPropagation()}>{history}</div>
        </div>)}

      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2 lg:hidden">
          <button className="btn-ghost px-3 py-1.5" onClick={() => setHist(true)}><History size={15} /> 历史</button>
          <button className="btn-ghost px-3 py-1.5" onClick={() => { setSid(null); setMsgs([]); }}><Plus size={15} /> 新对话</button>
        </div>
        <div className="flex-1 overflow-y-auto">
          <div className="mx-auto max-w-3xl px-4 py-6 md:px-6 md:py-8">
            {msgs.length === 0 && (
              <div className="mt-8 text-center md:mt-14">
                <div className="flex justify-center"><Logo size={44} /></div>
                <h1 className="mt-4 text-xl font-bold md:text-2xl">有什么可以帮你？</h1>
                <p className="mt-2 text-sm text-muted">我可以帮你找设计、提交任务、看进度、解释失败原因。<br className="hidden md:block" />
                  通过与否由签核工具判定，不是我说了算。</p>
                <div className="mx-auto mt-6 grid max-w-xl grid-cols-1 gap-2 sm:grid-cols-2">
                  {SUGGEST.map((s) => (
                    <button key={s} onClick={() => send(s)} className="card px-4 py-3 text-left text-sm hover:border-accent/60">{s}</button>))}
                </div>
              </div>)}
            <div className="space-y-6">
              {msgs.map((m, i) => m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <UserBubble text={(m.parts[0] as any).text} />
                </div>
              ) : (
                <div key={i} className="flex gap-3">
                  <div className="mt-1 hidden shrink-0 sm:block"><Logo size={26} /></div>
                  <div className="min-w-0 flex-1 space-y-2">
                    {m.parts.map((p, k) => p.kind === "text" ? <Md key={k}>{p.text}</Md>
                      : p.kind === "thinking" ? <Thinking key={k} text={p.text} live={busy && i === msgs.length - 1 && k === m.parts.length - 1} />
                      : <ToolStep key={k} p={p} sid={sid} />)}
                    {busy && i === msgs.length - 1 && !m.error && m.parts.length === 0 && (
                      <div className="flex items-center gap-2 text-sm text-muted"><Loader2 size={14} className="animate-spin" /> 正在思考…</div>)}
                    {m.error && (
                      <div className="flex items-start gap-2 rounded-xl bg-bad/10 px-3 py-2 text-sm text-bad">
                        <AlertCircle size={16} className="mt-0.5 shrink-0" />
                        <div>{m.error}<div className="mt-1 text-xs text-muted">请核对任务状态或 <Link to="/settings" className="underline">设置</Link>，再决定是否重试。</div></div>
                      </div>)}
                  </div>
                </div>))}
            </div>
            <div ref={end} />
          </div>
        </div>
        <div className="border-t border-line bg-panel/60 px-3 py-3 md:px-6 md:py-4">
          {(att || attErr || attBusy) && (
            <div className="mx-auto mb-2 flex max-w-3xl items-center gap-2 text-sm">
              {attBusy && <span className="flex items-center gap-1.5 text-muted"><Loader2 size={13} className="animate-spin" /> 上传中…</span>}
              {att && <span className="chip bg-accent/10 text-accent"><Paperclip size={12} />{att.name} · {att.files} 个文件
                <button type="button" className="ml-1" aria-label="移除附件" onClick={() => setAtt(null)}><X size={12} /></button></span>}
              {attErr && <span className="text-bad">{attErr}</span>}
            </div>)}
          <form className="mx-auto flex max-w-3xl items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(input); }}>
            <label className="btn-ghost h-[44px] shrink-0 cursor-pointer px-3" title="上传一个 RTL 文件夹，agent 可以直接拿它提交任务">
              <Paperclip size={17} /><span className="hidden sm:inline">上传 RTL</span>
              <input ref={dirInput} type="file" multiple className="hidden"
                onChange={(e) => { attach(Array.from(e.target.files || [])); e.target.value = ""; }} />
            </label>
            <textarea ref={box} value={input} onChange={(e) => setInput(e.target.value)} rows={input.split("\n").length > 1 ? 3 : 1}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(input); } }}
              placeholder={att ? "说说要对这个设计做什么，例如：跑一遍生成图数据集" : "输入问题，Enter 发送"}
              className="input min-h-[44px] resize-none py-2.5 text-[15px]" />
            <button className="btn-primary h-[44px] shrink-0 px-4" aria-label="发送" disabled={busy || (!input.trim() && !att)}><Send size={17} /></button>
          </form>
        </div>
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  const m = text.match(/^\[附件\] 已上传 RTL 文件夹 (.+?)（(\d+) 个文件），路径：\S+\n?([\s\S]*)$/);
  return (
    <div className="max-w-[85%] space-y-1.5 rounded-2xl rounded-br-md bg-accent/15 px-4 py-2.5 text-[15px]">
      {m && <div className="chip bg-panel/70 text-muted"><Paperclip size={12} />{m[1]} · {m[2]} 个文件</div>}
      <div className="whitespace-pre-wrap">{m ? m[3] : text}</div>
    </div>);
}

function Thinking({ text, live }: { text: string; live: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-xl border border-line/70 bg-panel2/40">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-muted">
        {live ? <Loader2 size={13} className="animate-spin" /> : <Brain size={13} />}
        {live ? "正在思考…" : `思考过程（${text.length} 字）`}
        <span className="ml-auto">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </button>
      {open && <div className="whitespace-pre-wrap border-t border-line/70 px-3 py-2 text-xs leading-5 text-muted">{text}</div>}
    </div>);
}

function ToolStep({ p, sid }: { p: Extract<Part, { kind: "tool" }>; sid: string | null }) {
  const [open, setOpen] = useState(false);
  const done = p.result !== undefined;
  const err = done && p.result && typeof p.result === "object" && "error" in p.result;
  const jobId = p.result?.job_id;
  return (
    <div className="text-sm">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left text-muted hover:text-fg">
        {!done ? <Loader2 size={14} className="animate-spin text-run" /> : err ? <XCircle size={14} className="text-bad" /> : <CheckCircle2 size={14} className="text-ok" />}
        <span>{TOOL_LABEL[p.name] ?? p.name}{err ? "（出错）" : ""}</span>
        {jobId && <Link to={`/jobs/${jobId}`} onClick={(e) => e.stopPropagation()} className="chip bg-accent/15 text-accent">打开任务 <ExternalLink size={11} /></Link>}
        <span className="ml-auto text-faint">{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
      </button>
      {open && (
        <>
        <pre className="mt-1 max-h-72 overflow-auto rounded-xl bg-panel2 px-3 py-2 font-mono text-[11.5px] leading-5 text-muted">
          {"参数 " + JSON.stringify(p.args, null, 2) + (done ? "\n\n结果 " + JSON.stringify(p.result, null, 2) : "")}</pre>
        {sid && p.output_ref && <ToolOutputView key={sid + p.output_ref.artifact_id} sid={sid} output={p.output_ref} />}
        </>)}
    </div>
  );
}

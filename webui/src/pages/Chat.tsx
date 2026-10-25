import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Send, Paperclip, X, Plus, MessageSquare, Loader2, History, ArrowDown } from "lucide-react";
import { api, chatStream, ChatEvent } from "../lib/api";
import { Logo } from "../components/ui";
import { ConversationRun, ConversationPart, ConversationTurnKind, toolJobId } from "../components/ConversationRun";
import { useChatScroll } from "../hooks/useChatScroll";

type Part = ConversationPart;
type Msg = {
  role: "user" | "assistant"; parts: Part[]; error?: string; turnId?: string;
  turnKind?: ConversationTurnKind; turnStatus?: string;
};

function turnKind(value: unknown): ConversationTurnKind {
  return value === "job_review" || value === "repair_review" ? value : "user";
}

const SUGGEST = [
  "帮我在 GitHub 上找一个小的 UART 设计，跑一遍生成图数据集",
  "最近的任务进展怎么样？",
  "跑一下三个内置示例，生成图数据集",
  "PDN-0185 是什么问题，怎么解决？",
];
// HDL plus the data files $readmem reads; nothing else in the folder is sent
const SEND = /\.(s?v|s?vh|hex|mem|bin|txt|dat|coe|mif|init|rom|data)$/i;

/** Attach a reconstructed message to its turn without moving legacy history. */
function insertTurnMessage(messages: Msg[], message: Msg, turns: any[]) {
  const own = messages.reduce((last, item, index) => item.turnId === message.turnId ? index : last, -1);
  const order = new Map(turns.map((turn, index) => [turn.id, index]));
  const position = order.get(message.turnId);
  const next = position === undefined ? -1 : messages.findIndex((item) => {
    const other = order.get(item.turnId);
    return other !== undefined && other > position;
  });
  messages.splice(own >= 0 ? own + 1 : next >= 0 ? next : messages.length, 0, message);
}

/** Rebuild the display from a stored session (internal message format). */
function fromStored(ms: any[], turns: any[] = []): Msg[] {
  const out: Msg[] = [];
  const kinds = new Map(turns.map((turn) => [turn.id, turnKind(turn.kind)]));
  let currentKind: ConversationTurnKind = "user";
  for (const m of ms) {
    if (m.role === "user") {
      currentKind = kinds.get(m.turn_id) ?? turnKind(m.turn_kind);
      out.push({ role: "user", parts: [{ kind: "text", text: m.content }], turnId: m.turn_id, turnKind: currentKind });
    }
    else if (m.role === "assistant") {
      const last = out[out.length - 1];
      const target = last && last.role === "assistant" && last.turnId === m.turn_id ? last
        : (out.push({ role: "assistant", parts: [], turnId: m.turn_id,
          turnKind: kinds.get(m.turn_id) ?? currentKind }), out[out.length - 1]);
      if (m.content) target.parts.push({ kind: "text", text: m.content });
      for (const t of m.tool_calls ?? []) target.parts.push({ kind: "tool", id: t.id, name: t.name, args: t.arguments });
    } else if (m.role === "tool") {
      const last = out[out.length - 1];
      const p = last?.parts.find((x) => x.kind === "tool" && x.id === m.tool_call_id) as any;
      if (p) { try { p.result = JSON.parse(m.content); } catch { p.result = m.content; } p.output_ref = m.output_ref; }
    }
  }
  for (const turn of turns) {
    if (!["error", "recovery_required"].includes(turn.status)) continue;
    const target = [...out].reverse().find((m) => m.role === "assistant" && m.turnId === turn.id);
    const error = turn.error ?? "本轮需要核对执行记录。";
    if (target) target.error = error;
    else insertTurnMessage(out, { role: "assistant", turnId: turn.id, turnKind: turnKind(turn.kind), parts: [], error }, turns);
  }
  return out;
}

function addEvent(message: Msg, event: ChatEvent) {
  if (event.type === "text") {
    const last = message.parts[message.parts.length - 1];
    if (last?.kind === "text") message.parts[message.parts.length - 1] = { kind: "text", text: last.text + event.delta };
    else message.parts.push({ kind: "text", text: event.delta });
  } else if (event.type === "tool_call") {
    message.parts.push({ kind: "tool", id: event.id, name: event.name, args: event.arguments });
  } else if (event.type === "tool_result") {
    message.parts = message.parts.map((p) => p.kind === "tool" && p.id === event.id
      ? { ...p, result: event.result, output_ref: event.output_ref } : p);
  } else if (event.type === "error") message.error = event.message;
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
  const { scrollRef, contentRef, showScrollButton, scrollToBottom, reset: resetScroll, scrollEvents } = useChatScroll(msgs);
  const dirInput = useRef<HTMLInputElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  const viewEpoch = useRef(0);
  const activeStream = useRef<AbortController | null>(null);
  const streaming = useRef(false);
  // Keep one live card per task at its most recent mention in this conversation.
  const latestJobs = new Map<string, number>();
  msgs.forEach((m, i) => {
    m.parts.forEach((p) => {
      if (p.kind !== "tool") return;
      const id = toolJobId(p);
      if (id) latestJobs.set(id, i);
    });
  });

  const loadSessions = () => api.sessions().then(setSessions).catch(() => {});
  useEffect(() => { loadSessions(); }, []);
  useEffect(() => { dirInput.current?.setAttribute("webkitdirectory", ""); }, []);
  useEffect(() => () => activeStream.current?.abort(), []);
  useEffect(() => {
    if (!sid) return;
    const epoch = viewEpoch.current;
    const timer = window.setInterval(() => {
      if (!streaming.current) refreshSession(sid, epoch).catch(() => {});
    }, 2000);
    return () => window.clearInterval(timer);
  }, [sid]);
  // a question handed over from another page (e.g. "why did this design fail") is
  // put in the box, not sent: the user sees it and presses send
  useEffect(() => {
    const q = params.get("q");
    if (q) { newChat(); setInput(q); setParams({}, { replace: true }); setTimeout(() => box.current?.focus(), 0); }
  }, [params, setParams]);

  async function open(id: string) {
    const epoch = leaveView();
    setHist(false); setSid(id); setMsgs([]);
    await refreshSession(id, epoch);
  }

  function leaveView() {
    resetScroll();
    activeStream.current?.abort(); activeStream.current = null;
    streaming.current = false; setBusy(false);
    return ++viewEpoch.current;
  }

  function newChat() {
    leaveView(); setSid(null); setMsgs([]); setHist(false);
  }

  async function refreshSession(id: string, epoch: number) {
    const state = await api.session(id);
    if (epoch !== viewEpoch.current || streaming.current) return;
    let messages = fromStored(state.messages, state.turns);
    const active = (state.turns ?? []).filter((turn: any) => ["queued", "running"].includes(turn.status));
    for (const turn of active) {
      messages = messages.filter((m) => !(m.role === "assistant" && m.turnId === turn.id));
      if (!messages.some((m) => m.turnId === turn.id)) insertTurnMessage(messages, { role: "user", turnId: turn.id,
        turnKind: turnKind(turn.kind), parts: [{ kind: "text", text: turn.payload.message }] }, state.turns ?? []);
      const message: Msg = { role: "assistant", turnId: turn.id, turnKind: turnKind(turn.kind), turnStatus: turn.status, parts: [] };
      let cursor = 0;
      for (;;) {
        const page = await api.turnEvents(turn.id, cursor);
        for (const event of page.events) { addEvent(message, event); cursor = event.event_id; }
        if (page.events.length < 500) break;
      }
      insertTurnMessage(messages, message, state.turns ?? []);
    }
    if (epoch === viewEpoch.current && !streaming.current) { setMsgs(messages); setBusy(active.length > 0); }
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
    scrollToBottom();
    // the model reads the attachment as a path it can pass to start_job
    const text = att ? `[附件] 已上传 RTL 文件夹 ${att.name}（${att.files} 个文件），路径：${att.path}\n${typed}` : typed;
    const epoch = viewEpoch.current, requestId = crypto.randomUUID();
    const abort = new AbortController(); activeStream.current = abort; streaming.current = true;
    let turnId: string = requestId, sessionId = sid;
    setInput(""); setAtt(null); setBusy(true);
    setMsgs((m) => [...m, { role: "user", turnId: requestId, turnKind: "user", parts: [{ kind: "text", text }] },
      { role: "assistant", turnId: requestId, turnKind: "user", parts: [] }]);
    const upd = (f: (a: Msg) => void) => setMsgs((m) => {
      if (epoch !== viewEpoch.current) return m;
      return m.map((message) => {
        if (message.role !== "assistant" || message.turnId !== turnId) return message;
        const copy = { ...message, parts: message.parts.slice() }; f(copy); return copy;
      });
    });
    try {
      await chatStream(text, sid, (e) => {
        if (epoch !== viewEpoch.current) return;
        if (e.type === "session") {
          turnId = e.turn_id; sessionId = e.session_id; setSid(e.session_id);
          setMsgs((m) => m.map((message) => message.turnId === requestId ? { ...message, turnId } : message));
        } else if (e.turn_id === turnId) upd((message) => addEvent(message, e));
      }, { requestId, signal: abort.signal });
    } catch (err: any) { upd((a) => { a.error = err.message; }); }
    if (epoch !== viewEpoch.current) return;
    streaming.current = false; activeStream.current = null; setBusy(false); loadSessions();
    if (sessionId) refreshSession(sessionId, epoch).catch(() => {});
  }

  const history = (
    <>
      <div className="p-3"><button className="btn-ghost w-full justify-center" onClick={newChat}>
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

      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-3 py-2 lg:hidden">
          <button className="btn-ghost px-3 py-1.5" onClick={() => setHist(true)}><History size={15} /> 历史</button>
          <button className="btn-ghost px-3 py-1.5" onClick={newChat}><Plus size={15} /> 新对话</button>
        </div>
        <div className="relative min-h-0 flex-1">
          <div ref={scrollRef} {...scrollEvents} tabIndex={0} role="region" aria-label="对话消息"
            className="h-full overflow-y-auto overscroll-contain focus-visible:outline-none">
            <div ref={contentRef} className="mx-auto max-w-3xl px-4 py-6 md:px-6 md:py-8">
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
                  <div key={i} className={m.turnKind && m.turnKind !== "user" ? "text-xs text-muted" : "flex justify-end"}>
                    {m.turnKind === "repair_review" ? "已授权的修复与验证" : m.turnKind === "job_review"
                      ? "任务结束后的自动审查" : <UserBubble text={(m.parts[0] as any).text} />}
                  </div>
                ) : (
                  <div key={i} className="flex gap-3">
                    <div className="mt-1 hidden shrink-0 sm:block"><Logo size={26} /></div>
                    <div className="min-w-0 flex-1 space-y-2">
                      <ConversationRun parts={m.parts} sid={sid} busy={m.turnStatus ? ["queued", "running"].includes(m.turnStatus) : busy && i === msgs.length - 1}
                        queued={m.turnStatus === "queued"} turnKind={m.turnKind}
                        error={m.error} jobIds={[...latestJobs].filter(([, owner]) => owner === i).map(([id]) => id)} />
                    </div>
                  </div>))}
              </div>
            </div>
          </div>
          {showScrollButton && <button type="button" aria-label="回到最新消息" title="回到最新消息"
            onClick={scrollToBottom}
            className="absolute bottom-4 left-1/2 z-10 flex h-10 w-10 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-panel/80 text-muted shadow-card backdrop-blur-sm transition-colors hover:bg-panel hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent">
            <ArrowDown size={19} aria-hidden="true" />
          </button>}
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

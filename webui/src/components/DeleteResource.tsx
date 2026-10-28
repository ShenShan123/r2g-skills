import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Loader2, Trash2 } from "lucide-react";
import { api } from "../lib/api";

export default function DeleteResource({ kind, id, title, onDeleted, compact = false }: {
  kind: "job" | "session"; id: string; title: string; onDeleted: () => void; compact?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [confirming, setConfirming] = useState(false);
  const dialog = useRef<HTMLDivElement>(null);
  const cancel = useRef<HTMLButtonElement>(null);
  const dialogId = useId();
  const deleted = useRef(onDeleted);
  deleted.current = onDeleted;
  const completed = useRef(false);
  const label = kind === "job" ? "任务" : "对话及其创建的任务";
  useEffect(() => {
    if (!confirming) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = document.getElementById("root");
    const wasInert = root?.inert ?? false;
    const previousOverflow = document.body.style.overflow;
    if (root) root.inert = true;
    document.body.style.overflow = "hidden";
    cancel.current?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        setConfirming(false);
      } else if (event.key === "Tab") {
        const buttons = dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
        if (!buttons?.length) return;
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault(); first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("keydown", handleKey);
      if (root) root.inert = wasInert;
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [confirming]);
  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.deletion(kind, id);
        if (!alive) return;
        setError("");
        if (result.status === "done") {
          setBusy(false);
          if (!completed.current) { completed.current = true; deleted.current(); }
          return;
        }
        if (result.status === "error") { setBusy(false); setError(result.error || "删除失败，请重试"); return; }
        setBusy(true);
        timer = setTimeout(poll, 2000);
      } catch (e) {
        if (alive && busy) {
          setError(e instanceof Error ? e.message : "状态查询失败，正在重试");
          timer = setTimeout(poll, 3000);
        }
      }
    };
    poll();
    return () => { alive = false; clearTimeout(timer); };
  }, [kind, id, busy]);

  async function remove() {
    if (busy) return;
    setConfirming(false);
    setError("");
    setBusy(true);
    try {
      const result = await api.deleteResource(kind, id);
      if (result.status === "done" && !completed.current) { completed.current = true; deleted.current(); }
    } catch (e) {
      setBusy(false); setError(e instanceof Error ? e.message : "删除失败，请重试");
    }
  }
  return <div className="shrink-0">
    <button type="button" className={`inline-flex items-center justify-center rounded-md text-faint transition hover:bg-bad/10 hover:text-bad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-wait disabled:opacity-50 ${compact ? "h-7 w-7" : "h-8 w-8"}`} disabled={busy}
      aria-label={`删除${kind === "job" ? "任务" : "对话"} ${title}`} title={busy ? "正在停止计算并删除生成数据" : `删除${label}及生成数据`}
      aria-haspopup="dialog" aria-expanded={confirming} onClick={() => setConfirming(true)}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
    </button>
    {error && <p role="alert" className="max-w-xs px-2 text-xs text-bad">{error}</p>}
    {confirming && createPortal(
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/40 p-4 backdrop-blur-sm"
        onClick={(event) => { event.stopPropagation(); if (event.target === event.currentTarget) setConfirming(false); }}>
        <div ref={dialog} role="alertdialog" aria-modal="true" aria-labelledby={`${dialogId}-title`}
          aria-describedby={`${dialogId}-description`} className="w-full max-w-md max-h-[calc(100dvh-2rem)] overflow-y-auto rounded-2xl border border-line bg-panel p-5 shadow-xl md:p-6">
          <h2 id={`${dialogId}-title`} className="text-lg font-semibold">确认删除{kind === "job" ? "任务" : "对话"}？</h2>
          <div id={`${dialogId}-description`} className="mt-3 space-y-3 text-sm leading-6 text-muted">
            <p className="break-words font-medium text-fg">{title || "未命名"}</p>
            <p>{kind === "session" ? "该对话及其创建的任务将被删除，任务生成的所有数据集、版图、报告和中间文件也会一并永久删除。" : "该任务生成的所有数据集、版图、报告和中间文件都会一并永久删除。"}</p>
            <p>运行中的计算会先停止。原始输入和其他任务的数据会保留。删除后无法恢复。</p>
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <button ref={cancel} type="button" className="btn-ghost" onClick={() => setConfirming(false)}>取消</button>
            <button type="button" className="btn border border-bad/25 bg-bad/10 text-bad hover:bg-bad/20 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-bad/50" onClick={remove}>确认删除</button>
          </div>
        </div>
      </div>, document.body)}
  </div>;
}

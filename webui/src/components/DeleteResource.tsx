import { useEffect, useRef, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import { api } from "../lib/api";
import { t, tb } from "../lib/i18n";
import ConfirmDialog from "./ConfirmDialog";

/** Trash button + confirmation + progress for deleting a job or a conversation with
 *  everything it generated. `state` is the deletion status the list already knows
 *  (null = never requested): only a deletion in progress is polled, so a list of
 *  50 rows no longer fires 50 status requests (each a 404) on every load. */
export default function DeleteResource({ kind, id, title, onDeleted, onRequested, compact = false, state = null }: {
  kind: "job" | "session"; id: string; title: string; onDeleted: () => void; compact?: boolean;
  state?: string | null; onRequested?: () => void;
}) {
  const [busy, setBusy] = useState(state === "pending");
  const [error, setError] = useState(state === "error" ? t("上次删除没有完成，请重试") : "");
  const [confirming, setConfirming] = useState(false);
  const [note, setNote] = useState("");
  const deleted = useRef(onDeleted);
  deleted.current = onDeleted;
  const completed = useRef(false);
  const finish = () => { if (!completed.current) { completed.current = true; deleted.current(); } };

  useEffect(() => { if (state === "pending") setBusy(true); if (state === "done") finish(); }, [state]);
  useEffect(() => {
    if (!busy) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const result = await api.deletion(kind, id);
        if (!alive) return;
        setError("");
        if (result.status === "done") { setBusy(false); finish(); return; }
        if (result.status === "error") { setBusy(false); setError(tb(result.error) || t("删除失败，请重试")); return; }
        setNote(result.error ? tb(result.error) : "");
        timer = setTimeout(poll, 1500);
      } catch (e) {
        if (!alive) return;
        setError(e instanceof Error ? tb(e.message) : t("状态查询失败，正在重试"));
        timer = setTimeout(poll, 3000);
      }
    };
    timer = setTimeout(poll, 800);
    return () => { alive = false; clearTimeout(timer); };
  }, [kind, id, busy]);

  async function remove() {
    setConfirming(false);
    if (busy) return;
    setError(""); setBusy(true); onRequested?.();
    try {
      const result = await api.deleteResource(kind, id);
      if (result.status === "done") { setBusy(false); finish(); }
    } catch (e) {
      setBusy(false); setError(e instanceof Error ? tb(e.message) : t("删除失败，请重试"));
    }
  }
  return <div className="flex shrink-0 items-center gap-1">
    {busy && !compact && <span className="text-xs text-muted">{t("正在删除…")}</span>}
    <button type="button" className={`inline-flex items-center justify-center rounded-md text-faint transition hover:bg-bad/10 hover:text-bad focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/50 disabled:cursor-wait disabled:opacity-50 ${compact ? "h-7 w-7" : "h-8 w-8"}`} disabled={busy}
      aria-label={t(kind === "job" ? "删除任务 {title}" : "删除对话 {title}", { title: tb(title) })}
      title={busy ? t("正在停止计算并删除生成数据") : t(kind === "job" ? "删除任务及生成数据" : "删除对话及其创建的任务及生成数据")}
      aria-haspopup="dialog" aria-expanded={confirming} onClick={() => setConfirming(true)}>
      {busy ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
    </button>
    {error && <p role="alert" className="max-w-xs px-2 text-xs text-bad">{error}</p>}
    {busy && note && !compact && <p role="status" className="max-w-xs px-2 text-xs text-muted">{note}</p>}
    {confirming && (
      <ConfirmDialog title={t(kind === "job" ? "确认删除任务？" : "确认删除对话？")} confirmLabel={t("确认删除")}
        onConfirm={remove} onCancel={() => setConfirming(false)}>
        <p className="break-words font-medium text-fg">{tb(title) || t("未命名")}</p>
        <p>{t(kind === "session" ? "该对话及其创建的任务将被删除，任务生成的所有数据集、版图、报告和中间文件也会一并永久删除。" : "该任务生成的所有数据集、版图、报告和中间文件都会一并永久删除。")}</p>
        <p>{t("运行中的计算会先停止。通过网页上传的 RTL 副本若没有其他任务在用也会删除；服务器上原有目录里的文件和其他任务的数据会保留。删除后无法恢复。")}</p>
      </ConfirmDialog>)}
  </div>;
}

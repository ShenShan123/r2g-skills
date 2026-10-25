import { useState } from "react";
import { api, ToolOutputPage, ToolOutputRef } from "../lib/api";

/** Session-scoped observations, rendered as text and loaded one bounded page at a time. */
export function ToolOutputView({ sid, output }: { sid: string; output: ToolOutputRef }) {
  const [page, setPage] = useState<ToolOutputPage | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function read(offset = 0, find = "") {
    setBusy(true); setError("");
    try { setPage(await api.toolOutput(sid, output.artifact_id, offset, find)); }
    catch (e: any) { setPage(null); setError(e.message); }
    finally { setBusy(false); }
  }
  return <div className="mt-2 space-y-2 rounded-xl border border-line p-3 text-xs">
    <div className="flex flex-wrap items-center gap-2 text-muted">
      <span>已保存工具结果 · {output.stored_bytes.toLocaleString()} 字节{output.redacted ? " · 已脱敏" : ""}</span>
      <button className="btn-ghost" disabled={busy} onClick={() => read()}>查看完整结果</button>
    </div>
    {page && <>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); read(0, query); }}>
        <input className="input min-w-0" aria-label="在工具结果中查找" placeholder="查找错误码或文字"
          maxLength={256} value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="btn-ghost shrink-0 whitespace-nowrap" disabled={busy || !query}>查找</button>
      </form>
      <div className="text-muted">{page.match === -1 ? "没有找到匹配内容" :
        `字符 ${page.offset + 1}–${page.offset + page.text.length} / ${page.total_chars}`}</div>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-panel2 p-2">{page.text}</pre>
      {page.next_offset !== null && <button className="btn-ghost" disabled={busy}
        onClick={() => read(page.next_offset!)}>下一段</button>}
    </>}
    {busy && <div role="status">正在读取…</div>}
    {error && <div role="alert" className="text-bad">{error}</div>}
  </div>;
}

import { useEffect, useState } from "react";
import { api, Job, JobEvent } from "../lib/api";
import { isActive } from "../components/ui";

export type JobProgressState = { job?: Job; events: JobEvent[]; error?: string; eventsUnavailable?: boolean };

/** A conversation follows background jobs after the model stream has ended. */
export function useJobProgress(ids: string[]) {
  const key = [...new Set(ids)].sort().join(",");
  const [states, setStates] = useState<Record<string, JobProgressState>>({});
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const jobIds = key ? key.split(",") : [];
    const current: Record<string, JobProgressState> = {};
    const controller = new AbortController();
    let alive = true, polling = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setStates({});
    const tick = async () => {
      if (!alive || polling || !jobIds.length) return;
      polling = true;
      clearTimeout(timer);
      await Promise.all(jobIds.map(async (id) => {
        const previous = current[id] ?? { events: [] };
        const after = previous.events[previous.events.length - 1]?.id ?? 0;
        const request = new AbortController();
        const timeout = setTimeout(() => request.abort(), 15000);
        const abort = () => request.abort();
        controller.signal.addEventListener("abort", abort, { once: true });
        try {
          const [job, events] = await Promise.allSettled([
            api.job(id, request.signal), api.events(id, after, after ? 0 : 100, request.signal),
          ]);
          if (!alive) return;
          const combined = events.status === "fulfilled"
            ? [...new Map([...previous.events, ...events.value].map((e) => [e.id, e])).values()].sort((a, b) => a.id - b.id).slice(-200)
            : previous.events;
          current[id] = {
            job: job.status === "fulfilled" ? job.value : previous.job,
            events: combined, eventsUnavailable: events.status === "rejected",
            error: job.status === "rejected" ? "暂时无法更新任务状态，正在重连。" : undefined,
          };
        } finally {
          clearTimeout(timeout);
          controller.signal.removeEventListener("abort", abort);
        }
      }));
      polling = false;
      if (!alive) return;
      setStates({ ...current });
      if (jobIds.some((id) => !current[id]?.job || current[id].error || current[id].eventsUnavailable || isActive(current[id].job!.status))) {
        timer = setTimeout(tick, Object.values(current).some((s) => s.error) ? 5000 : 2000);
      }
    };
    const resume = () => { if (document.visibilityState === "visible") void tick(); };
    void tick();
    window.addEventListener("online", resume);
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      alive = false; clearTimeout(timer); controller.abort();
      window.removeEventListener("online", resume);
      window.removeEventListener("focus", resume);
      document.removeEventListener("visibilitychange", resume);
    };
  }, [key, revision]);
  return { states, retry: () => setRevision((n) => n + 1) };
}

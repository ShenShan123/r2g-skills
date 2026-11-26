# r2g web UI — end-to-end test and hardening report (2026-11-25)

## How it was tested

- **Code** edited on 208 (`agent/`, `webui/`); nothing under `r2g-skills/` or `experiments/` was changed.
- **Server and real jobs on 203**, isolated instance under `/data/yangao/r2g_ui_test_20261125/`
  (`release/` = committed `r2g-skills/` + `tools/` and the working-tree `agent/`; `state/` = its own
  `R2G_HOME`), port **8790 on 127.0.0.1**, the whole process tree pinned with `taskset -c 176-183`
  (8 cores), compute limit 2 designs × 4 cores. Toolchain: the same tool file the 11-15 deployment uses
  (`tools203_gf180.env`: ORFS `/data/yangao/orfs203`, pinned openroad/yosys, magic/netgen, PDKs).
- **Model**: the 203 deployment's model goes through a relay on 127.0.0.1:18774, which the brief forbids
  touching, so chat was tested against a **scripted OpenAI-compatible endpoint** (`mock_llm.py`, port 8791)
  that streams thinking, text deltas, tool calls and usage like the real APIs, can stream slowly, and can
  return HTTP 500. This exercises the real adapter, SSE path, tool loop and UI; it does not measure a real
  model's answers (see "Remaining issues").
- **Browser**: Playwright (headless Chromium) on 208 through an SSH tunnel (208:18790 → 203:8790), plus
  direct HTTP (curl/httpx). Effects were checked on 203 itself: the process table (every process carrying
  the instance's environment), the workspace, the ORFS `flow/{results,logs,reports,objects}` variant
  directories, the upload directories, and fcntl slot locks.
- Designs: the bundled `counter8`, `alu8`, `uart_tx`, plus small fixtures (ambiguous top, empty file,
  syntax error, missing module, a 3-design batch, a 14-design failing batch, 1200 one-file designs).

## Controls and endpoints

Result: **works** = behaved correctly as found; **fixed** = defect found and fixed (see below), re-tested
green; **new** = control added because the goal needed it (retry, stop answer, filters).

### Start page
| Control | Result | Evidence |
|---|---|---|
| Upload one RTL folder | fixed (D1–D5, D7) | uploads at once with progress; top module shown; empty/no-module folder refused with reason; job gets the chosen top |
| Several top candidates → choose one | new (D7) | warning + select; Start disabled until chosen; job records `top=blink` |
| Clear (×) / re-pick / leave page | fixed (D8) | discarded upload deleted on 203 (in-app navigation and tab close/reload) |
| Example chips (alu8/counter8/uart_tx) | works | mixed upload + example job ran to "passed" |
| One design / batch toggle | works | |
| Batch: upload a folder of design folders | fixed (D1, D2) | 3-design folder and 1200-design folder (3 requests) scanned; one upload dir |
| Batch: server path + Scan | fixed (D9) | empty / relative / missing / `/etc` each give a clear message; a real path scans 3 (2 run, 1 skipped) |
| Skipped designs and reasons | works | |
| Platform cards nangate45 / sky130hd / gf180 | works | each platform's default clock tiers load; counter8 **passed** on all three |
| Dataset type graph / image | works | image job passed; all channel previews load in the panel |
| Flow parameters: defaults, custom, fixed clock, out of range | works; fixed (D23) | `utilization 5` refused naming the field; custom values recorded (now as numbers) |
| Start button states | fixed (D7) | disabled while uploading / until a top is chosen, says why |
| Recent jobs, "Ask the agent" card | works | |

### Jobs page
| Control | Result | Evidence |
|---|---|---|
| Job list | fixed (D10) | was capped at the newest 50 with no way to see more; now "load more" with total count |
| Status filter (all/active/done/failed/cancelled), search by name or id | new | filters verified; empty-result message |
| Delete from list | works; fixed (D11, D12) | job, workspace, ORFS dirs and its own upload removed; no per-row 404 polling |
| "Deleting" state | new | rows show "删除中" while a deletion is pending |

### Job page
| Control | Result | Evidence |
|---|---|---|
| Stop whole job | fixed (D13) | now asks first (cancel keeps it running); confirmed stop → "cancelled" in the UI within ~3 s; **all ORFS/OpenROAD/Yosys processes of the job gone** (process table on 203) |
| Re-run unfinished designs (job / single design) | new (D14) | new job with same platform/output/params, `retry_of` link back; original unchanged |
| Delete job (running) | works; fixed (D15) | stops processes, removes workspace + ORFS variant dirs + its upload; page returns to the list (used to land on a dead page) |
| Open a deleted/unknown job | fixed (D15) | "不存在或已被删除" instead of "加载中…" forever |
| Download dataset / per-design / finished part while running | works; fixed (D16) | gzip archives; partial archive has index.json + the passed design; packaging errors now shown |
| Design card: step track, stage layouts, "ask the agent" | works | |
| Design panel tabs overview / layout / log / images | fixed (D17, D18) | layout tab shows the 6 stage pictures at once (was a second collapsed toggle); log follows a running stage, refresh button, ingest log; Esc closes; header no longer wraps "下载" |
| Failure reason | fixed (D19) | syntax error now reads `bad_syntax.v:2:32：expected ';'` (was "Slang 转换失败（rc=1）") |
| Batch view (>12 designs): counts, failure groups, search | works | 14-design job: grouped causes, filter by cause, search |
| Lost connection while viewing | fixed (D20) | banner "暂时连不上 r2g 服务…" instead of silently stale data |
| Detailed events | works | |

### Chat
| Control | Result | Evidence |
|---|---|---|
| Send, suggestions, thinking, tool steps, "open job" chip | works | tool step + streamed answer rendered |
| Stop answer | new (D21) | partial text kept with "（已停止）"; unanswered tool calls get a result so the next turn is valid |
| Close tab / reload / switch conversation mid-answer | fixed (D21) | used to lock the conversation forever ("该对话正在处理上一条消息" until a server restart); now the turn finishes and is saved, no deltas leak into the new chat, the reopened chat shows "仍在回答中" and then the answer |
| Second message while answering | fixed (D21) | clean 409 message |
| Attach RTL folder | fixed (D22) | path points at the folder (design named after it, not the upload id); folder without modules refused; unsent/removed attachment deleted |
| Model error | works | HTTP 500 shown with a pointer to Settings |
| History, new chat, delete conversation | works; fixed (D22) | deletes the jobs it created and folders attached only in it; deleting while it answers stops the answer |

### Settings
| Control | Result | Evidence |
|---|---|---|
| Concurrent designs / cores per design / free-space floor / keep intermediate | fixed (D24) | 0, negative, non-integer, "abc", bad JSON were saved or crashed (500); now refused, all-or-nothing, inline message; oversubscription warning vs CPUs this server may use (D25) |
| Provider, model, API key, endpoint, key env var, thinking | fixed (D24, D26) | key stored 0600, never returned; malformed endpoint refused; local endpoint no longer "模型未配置"; sidebar refreshes after save |
| Save and test | works | unreachable endpoint reported (not hung); mock endpoint "连接正常，支持工具调用" |
| Theme, language (ZH/EN) | works | EN pages show no untranslated UI text (only user-entered chat titles); all new strings translated |

### Service behaviour
| Check | Result | Evidence |
|---|---|---|
| Compute limit across concurrent jobs | works | two 3-design jobs with limit 2: never more than 2 slots held; "排队中" event; raising to 3 at runtime let a third start |
| Disk floor | works | floor above free space → design pauses with message; lowering it resumes |
| `kill -9` the server mid-run | fixed (D31) | restart stops the orphaned stages and re-queues the designs; with the old orphan stop one restarted design failed at once ("another run holds the ORFS workspace"); after the fix two more kill -9 rounds: 4/4 designs passed |
| Graceful stop (TERM) mid-run | fixed (D27) | no leftover processes; the restart no longer counts as a crash (3 deliberate restarts used to fail a design) and keeps the user's top choice |
| Mobile width (390 px) | works | no horizontal scroll on any page |

### HTTP API
| Endpoint | Result |
|---|---|
| `GET /api/health` | works; fixed `machine.threads` (D25) |
| `GET/POST /api/config`, `POST /api/config/test` | fixed (D24) / works |
| `GET /api/sessions`, `GET /api/sessions/{id}` | works; now with `busy`, `deletion` |
| `POST /api/sessions/{id}/stop` | new |
| `GET /api/sessions/{id}/usage` | works |
| `POST /api/chat` | fixed (D21); empty/oversize message and bad id → 400 without creating a session |
| `DELETE /api/jobs/{id}`, `DELETE /api/sessions/{id}`, `GET /api/deletions/{kind}/{id}` | works (confirmation required); fixed (D11, D22) |
| `GET /api/jobs` | fixed (D10): `limit/offset/status/q`, `X-Total-Count`, `deletion` |
| `POST /api/jobs` | fixed (D6): non-list designs → 400 (was 500); path must be absolute and hold HDL (bounded walk); bad example/top names refused |
| `POST /api/jobs/{id}/retry` | new |
| `GET /api/jobs/{id}`, `/events`, `/cancel` | works |
| `POST /api/batch/scan` | fixed (D9) |
| `POST /api/inspect` | new |
| `POST /api/upload`, `DELETE /api/uploads/{id}` | fixed (D1–D5) / new (D8) |
| downloads (`/download`, `?partial=1`, per-design), `POST /package` | works; per-design no longer creates lock files for unknown ids |
| `layout.png`, `stage-views`, `stage-views/{stage}`, `log/{stage}`, `reports`, `images`, `images/{f}` | works (traversal attempts 404) |
| `GET /api/platforms`, `/api/params`, `/api/examples`, SPA fallback | works |

## Defects found and fixed

| # | Defect (root cause) | Fix | Files |
|---|---|---|---|
| D1 | Uploading a folder with more than 1000 files failed: Starlette's multipart default `max_files=1000`; the browser sent the whole folder in one request | server parses with `max_files=2000`; browser sends batches (400 files / 48 MB) into one upload via `?into=`, with progress; a failed batch removes the partial upload | `server.py`, `webui/src/lib/api.ts` |
| D2 | No size limits on uploads | per-file 256 MB / per-upload 4 GB (`R2G_UPLOAD_MAX_FILE_MB`, `R2G_UPLOAD_MAX_TOTAL_MB`), Content-Length pre-check, 413, nothing half-written kept | `server.py` |
| D3 | Binary files with a `.v` suffix accepted | NUL-byte check on HDL files, listed under `rejected` with the reason | `server.py` |
| D4 | Two names that sanitise alike (`a b.v`, `a_b.v`) silently overwrote each other | the second is rejected with the reason, never overwritten | `server.py` |
| D5 | An upload request without files returned a 422 validation list (the UI showed "[object Object]") | clean 400 messages; the client also flattens list-shaped `detail` | `server.py`, `api.ts` |
| D6 | `POST /api/jobs` with `designs:"x"` → 500; a path without HDL (e.g. `/etc`) or a relative path was accepted and failed later as "内部错误" | type checks, absolute path, bounded HDL probe (refuses a folder with >200k entries), example/top name checks | `tools.py`, `project.py` |
| D7 | A single uploaded folder was only checked after submitting: an ambiguous or missing top failed the design at "prepare" | `/api/inspect` right after upload; UI shows the top or the candidates to choose from, or the problem; the design is submitted from the folder (so the folder-name tie-break applies) | `batch.py`, `project.py`, `server.py`, `Home.tsx` |
| D8 | Uploads never cleaned up (re-picking a folder, abandoning the page) | `DELETE /api/uploads/{id}` (refused while a job uses it); the page deletes its unsubmitted uploads on replace/clear/leave/tab close (`keepalive`) | `server.py`, `Home.tsx`, `api.ts` |
| D9 | Batch scan with an empty path scanned the **server's working directory**; relative paths resolved against it; `/etc` gave "0 designs" with no message | absolute existing folder required; bounded walk; "no designs found" message | `batch.py`, `Home.tsx` |
| D10 | Job list showed only the newest 50 jobs, no filter or search | `limit/offset/status/q` + `X-Total-Count`; filter tabs, search, load more | `jobs.py`, `server.py`, `Jobs.tsx` |
| D11 | Every trash button polled `/api/deletions/...` on mount: one 404 per row (50 requests + console errors per list load) | deletion status included in list/detail rows; a button polls only while its deletion is pending | `jobs.py`, `server.py`, `DeleteResource.tsx` |
| D12 | Deleting a job left its uploaded RTL copy behind | the job's own `R2G_HOME/uploads/<id>` removed with it unless another job reads it; server folders never touched; dialog text says so | `deletion.py`, `DeleteResource.tsx` |
| D13 | "Stop whole job" stopped immediately with no confirmation | confirmation dialog stating what is cancelled/stopped/kept (shared `ConfirmDialog`) | `ConfirmDialog.tsx`, `JobDetail.tsx` |
| D14 | No way to retry failed/cancelled designs other than re-uploading | `POST /api/jobs/{id}/retry` + tool `retry_designs`: new job, same spec, `retry_of` provenance, user's original top | `tools.py`, `pipeline.py`, `server.py`, `JobDetail.tsx`, `system.md` |
| D15 | Deleting a job from its own page left the page on "加载中…" forever (its poll got 404 first, unmounting the trash button that would navigate) ; same for any deleted/unknown job id | 404 stops polling: own deletion → back to the list, otherwise "不存在或已被删除" | `JobDetail.tsx`, `DeleteResource.tsx` |
| D16 | "Download finished part" had no error handling (unhandled rejection); a packaging error was never shown | error shown; `package_error` displayed | `JobDetail.tsx` |
| D17 | Layout tab of the design panel showed a second collapsed toggle instead of the layouts | `defaultExpanded` | `StageViews.tsx`, `JobDetail.tsx` |
| D18 | Design panel: no Escape, unlabeled close button, log never refreshed while running, header squeezed the download button to one char per line | Esc + labels; log follows a running stage (4 s) + refresh; header layout | `JobDetail.tsx` |
| D19 | A plain syntax error read "Slang 转换失败（rc=1），见 agent_logs/frontend.log": when the Slang fallback also fails, the original error was discarded | reason gives `file:line:col` + message from Slang (or yosys) | `pipeline.py`, `i18n-en.ts` |
| D20 | Job page silently showed stale state while the server was unreachable | warning banner while polls fail | `JobDetail.tsx` |
| D21 | **A dropped chat connection locked the conversation until the server restarted** ("该对话正在处理上一条消息"): the turn ran inside the HTTP generator holding the session lock; on disconnect Starlette abandoned the generator without closing it. Also no way to stop an answer, and switching chats mid-stream could write deltas into the new chat | a turn runs on its own thread (`TurnRun`/`TurnRegistry`), the response only follows it (keep-alives); it finishes and is saved even if the browser leaves; `POST /api/sessions/{id}/stop` (partial text kept, pending tool calls answered); UI stop button, stream token guards, "still answering" banner + auto refresh, 409 for a second message | `agent_loop.py`, `server.py`, `Chat.tsx`, `api.ts` |
| D22 | Chat attachment path was the upload root, so the design was named after the upload id; a folder without modules was accepted; unsent attachments and attachments of deleted conversations stayed on disk | path = the folder; `inspect` before attaching; unsent/removed attachments deleted; deleting a conversation removes folders attached only in it | `Chat.tsx`, `deletion.py` |
| D23 | Custom parameters were sent as strings (`"0.4"`) into the job record and dataset index | numbers sent | `ParamsPicker.tsx` |
| D24 | Settings accepted `workers 0`, negative cores, unknown provider, non-http endpoint; `"abc"` or bad JSON → 500, possibly after half-applying | `config.apply_update`: validate everything, then apply; 400 with the field named; UI validates inline and disables Save | `config.py`, `server.py`, `Settings.tsx` |
| D25 | CPU suggestion used the host's 192 threads although the server was limited to 8 | `sched_getaffinity` | `server.py` |
| D26 | A local OpenAI-compatible endpoint (vLLM/llama.cpp) without key showed "模型未配置"; the sidebar did not refresh after saving | `needs_key` for localhost endpoints; sidebar reloads on save | `config.py`, `Layout.tsx`, `Settings.tsx` |
| D27 | A clean server stop (TERM) counted as a crash: three restarts failed a design; a stage killed by the shutdown could be recorded as a failed design; recovery passed the *inferred* top as "用户指定" | shutdown marks the server's running designs `graceful_stop` and raises `ShuttingDown` instead of failing them; recovery uses `requested_top` | `pipeline.py`, `jobs.py` |
| D28 | A signoff-only job (output `signoff`) always recorded `package_error` (packaging looked for `dataset/`) | not packaged | `pipeline.py` |
| D29 | With only `R2G_ENV_FILE` (how the 203 deployment is configured), the ORFS flow dir was unknown: free-space checks skipped the ORFS disk and deleting an interrupted job could miss its ORFS directories | `orfs_flow_dir` reads `R2G_ENV_FILE` too | `housekeeping.py` |
| D30 | Per-design download of an unknown job id created a lock file per id | job existence checked first | `package.py` |
| D31 | After `kill -9` of the server, recovery stopped only the recorded process group; GNU `timeout` puts the ORFS `make` tree in its own group, so it kept running and holding run_orfs.sh's workspace lock, and the restarted design failed immediately | recovery stops the old stage through its execution marker (`processes.stop_orphan` with `execution_id`/`process_start`, the same path cancel uses), legacy group stop only as fallback | `pipeline.py` |
| D32 | Deleting a finished job failed at random on a shared host ("cannot enumerate execution children: pid=…"): another of the user's processes (an EDA tool mid-exec) was unreadable for a moment and the inspection failed closed, turning the request into `error` | a transient inspection failure keeps the deletion `pending` (retried every 2 s, the reason shown under the trash button) instead of failing it; 5 deletions that had failed this way then completed | `deletion.py`, `DeleteResource.tsx` |
| D33 | Deleting a job left empty `flow/<kind>/<platform>/<design>/` folders in the ORFS checkout | removed when (and only when) empty after the job's variant is gone | `deletion.py` |

## Test results

- `agent/tests`: **297 passed** (was 269; new file `agent/tests/test_ui_hardening.py`, 28 tests, each
  pinning a defect above; a selection run against the pre-change code fails as expected).
- `webui`: `tsc -b` clean; bundle rebuilt into `agent/r2g_agent/web/dist` (new hashed asset names — the
  old `index-1oXwH8jT.css` / `index-CHKhcKKL.js` are deleted, the new ones untracked).
- End-to-end (Playwright + HTTP + process/disk checks on 203): 25 scenarios, about 110 checks. The final
  full pass on the final code: 93/96, the 3 failures being test-script issues (a check that read the page
  before it loaded, a check that deleted the wrong conversation, user-typed Chinese chat titles counted as
  untranslated UI); fixed in the script and re-run green. `image`, `platforms` and `big_job` scenarios
  green in separate runs. No uncaught page errors; the only console errors are the deliberate 4xx probes.
  The driver script (`e2e.py`, plus `mock_llm.py`, `procs.sh`) is in `/tmp/r2gui/` on 208, not in the repo
  (it is tied to this 203 setup).
- Real flows run on 203 during testing: about 60 small designs (nangate45, sky130hd, gf180); never more
  than 3 concurrent designs, all on 8 pinned cores. **Cleanup**: every test job and conversation was
  deleted through the API (which removed its ORFS variant dirs), the instance and the mock endpoint were
  stopped, `/data/yangao/r2g_ui_test_20261125` was removed, and three ORFS `designs/` variant folders plus
  empty `flow/*/nangate45/<test design>` folders left by deletions made *before* the D29/D33 fixes were
  removed by hand. Nothing else on 203 was touched.

## Remaining known issues

- **Chat with a real model was not exercised**: the only configured model on 203 goes through a relay
  that must not be touched. All chat mechanics were tested against a scripted OpenAI-compatible endpoint.
  Run one real conversation (e.g. "列出内置示例", then stop a long answer) before showing judges.
- A **server path** given in batch mode (or by the model) is read as-is: any absolute folder the server
  user can read can be scanned or submitted. Fine for the local, 127.0.0.1-bound design; an exposed
  deployment would need an allow-list of roots.
- Uploads go through Starlette's temporary files (`/tmp` of the server) before landing in `R2G_HOME`;
  each request is ≤48 MB from the UI, but a script could send up to the 4 GB cap in one request.
- Uploads abandoned while the browser is offline (no `pagehide` delivery) still stay in
  `R2G_HOME/uploads`; `r2g clean` does not list uploads yet.
- Stopping a job ends running designs for good; a stopped job cannot be resumed in place (re-run creates
  a new job, by design).
- A chat answer that is stopped while a long tool runs (e.g. `fetch_rtl` cloning) stops after that tool
  returns; the tool itself is not interrupted.
- The README screenshots (`docs/agent/img/*.png`) predate these changes (the job page still shows the
  older "取消" label); the README text was updated.
- Not committed (as instructed). `agent/r2g_agent/tools.py` and `agent/tests/test_replay.py` contain
  another worker's uncommitted replay changes; this work was layered on top of them.

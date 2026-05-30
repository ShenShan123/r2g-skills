# agent-r2g entry (optional)

This repository is the r2g RTL-to-GDS flow.

- Technical flow details: read `r2g-rtl2gds/SKILL.md`.
- Teaching tasks: if the user provides a `TEACHING_POLICY.md`, read it FIRST and
  treat it as the top constraint. It is the teaching-mode switch.

Core rules (always): never fabricate RTL/logs/reports/GDS/DEF/ODB/DRC/LVS/RCX/SPEF/CSV;
never skip a failed stage; every pass/fail needs a real file/log/report path; never
claim signoff/tapeout-ready without real evidence; never edit files under
`scripts/teaching/` or `scripts/ledger/` (grading trust base).

Do not rely on old conversation memory.

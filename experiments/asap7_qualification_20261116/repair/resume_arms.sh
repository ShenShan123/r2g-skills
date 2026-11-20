#!/usr/bin/env bash
# resume_arms.sh [--dry-run] -- bring every unfinished ASAP7 repair arm on THIS host back after a stop or reboot.
#
# Checkpoints are the artifacts themselves: a design is done when its runs/<tag>/result.json exists (the driver skips
# it), signed when signoff/<tag>/signoff.json exists (the signer skips it). Anything interrupted is redone from
# scratch, so resuming never trusts a half-written run. An arm takes part only if it has launch.env (written by
# run_arm_{216,203}.sh) or serve.env (serve_arm_216.sh); a PAUSE or DONE file in the arm directory opts it out.
#
#   216: driver incomplete -> RESUME=1 run_arm_216.sh (driver + signer); else signer incomplete -> signer only;
#        serve.env arms (flows on 203) -> puller + signer.
#   203: driver incomplete -> RESUME=1 run_arm_203.sh.
# Runs from cron @reboot (after a delay, only while RESUME_ENABLED exists) and by hand. Idempotent: an arm whose
# driver / signer / puller is alive is left alone. Stale signoff locks are cleared only when no signer is running.
set -uo pipefail
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
if [ -d /proj/workarea/yangao/asap7_baseline ]; then HOST=216; B=/proj/workarea/yangao/asap7_baseline
elif [ -d /data/yangao/asap7_baseline ]; then HOST=203; B=/data/yangao/asap7_baseline
else echo "no asap7_baseline on this host"; exit 1; fi
R=$B/repairs
log() { echo "$(date '+%F %T') [$HOST] $*"; }
alive() { pgrep -u "$(id -u)" -f -- "$1" >/dev/null; }          # pattern must not occur in this script's own argv
run() { if [ $DRY = 1 ]; then log "DRY: $*"; else log "RUN: $*"; ( cd $B && eval "$*" ) 2>&1 | sed 's/^/    /'; fi; }

if [ $HOST = 216 ] && ! alive "[s]ignoff_batch.py"; then
  n=$(find $R/*/signoff -maxdepth 1 -name '*.lock' 2>/dev/null | wc -l)
  [ "$n" -gt 0 ] && { log "clearing $n stale signoff locks (no signer running)"; [ $DRY = 1 ] || find $R/*/signoff -maxdepth 1 -name '*.lock' -delete; }
fi

for A in $R/*/; do
  A=${A%/}; NAME=$(basename $A)
  [ -f $A/PAUSE ] || [ -f $A/DONE ] && continue
  nsel=$(grep -c . $A/select 2>/dev/null || echo 0)
  nrun=$(ls $A/runs/*/result.json 2>/dev/null | wc -l)
  nsig=$(ls $A/signoff/*/signoff.json 2>/dev/null | wc -l)
  if [ -f $A/launch.env ]; then
    set -a; . $A/launch.env; set +a
    if [ "$nrun" -lt "$nsel" ]; then
      if alive "[o]rfs_spec.py.*--out $A "; then log "$NAME driver alive ($nrun/$nsel)"
      else
        log "$NAME resume driver ($nrun/$nsel done)"
        [ $HOST = 216 ] && run "RESUME=1 PERIOD_TAG=$PERIOD_TAG WORKERS=$WORKERS SIGN_JOBS=$SIGN_JOBS ${SPEC:+SPEC=$SPEC} bash $R/run_arm_216.sh $NAME"
        [ $HOST = 203 ] && run "RESUME=1 PERIOD_TAG=$PERIOD_TAG WORKERS=$WORKERS ${SPEC:+SPEC=$SPEC} bash $R/run_arm_203.sh $NAME"
        continue
      fi
    fi
    if [ $HOST = 216 ] && [ "$nsig" -lt "$nrun" ] && ! alive "[s]ignoff_batch.py --runs $A/runs "; then
      log "$NAME resume signer ($nsig/$nrun signed)"
      run "module load calibre/2025.1_16.10 >/dev/null 2>&1; setsid nohup python3 signoff_batch.py --runs $A/runs --out $A/signoff --jobs ${SIGN_JOBS:-3} --drc-budget 21600 --watch >> $A/signoff.out 2>&1 < /dev/null &"
    fi
  elif [ $HOST = 216 ] && [ -f $A/serve.env ]; then
    . $A/serve.env
    alive "[p]ull_203.py.*repairs/$NAME/runs" || { log "$NAME resume puller"; RESUME_PULL=1; }
    alive "[s]ignoff_batch.py --runs $A/runs " || { log "$NAME resume signer ($nsig/$nrun signed)"; RESUME_SIG=1; }
    if [ -n "${RESUME_PULL:-}" ] && [ -n "${RESUME_SIG:-}" ]; then run "bash $R/serve_arm_216.sh $NAME $JOBS"
    elif [ -n "${RESUME_PULL:-}" ]; then run "setsid nohup python3 pull_203.py --remote-runs /data/yangao/asap7_baseline/repairs/$NAME/runs --local-runs $A/runs --loop 120 --settle 300 >> $A/pull.log 2>&1 < /dev/null &"
    elif [ -n "${RESUME_SIG:-}" ]; then run "module load calibre/2025.1_16.10 >/dev/null 2>&1; setsid nohup python3 signoff_batch.py --runs $A/runs --out $A/signoff --jobs $JOBS --drc-budget 21600 --watch >> $A/signoff.out 2>&1 < /dev/null &"
    fi
    unset RESUME_PULL RESUME_SIG
  fi
done
log "resume pass done"

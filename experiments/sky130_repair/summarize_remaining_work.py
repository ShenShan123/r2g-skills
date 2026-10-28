"""Build a per-design work inventory without changing results or launching jobs."""
from collections import Counter
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))


def summarize(snapshot):
    scope_path=HERE/'repair_scope_20261027.json'
    decisions={r['design']:r for r in json.loads(scope_path.read_text())['decisions']} if scope_path.exists() else {}
    closed_candidates={}
    for path in HERE.glob('*_closeout_failure_20261027.json'):
        decision=json.loads(path.read_text())
        if (decision.get('disposition')=='bounded_candidate_failed_no_further_retry'
                and decision.get('baseline_hashes_rechecked') is True
                and decision.get('artifacts_retained') is True
                and decision.get('physical_dual_clean') is False):
            closed_candidates[decision['design']]=(decision,path.name)
    timeout_preflights={}
    for path in HERE.glob('area_*_timeout_preflight_20261027.json'):
        result=json.loads(path.read_text());selected=result.get('normal_next',{})
        if selected.get('returncode')==0 and selected.get('stdout','').strip().split('\t')==['fixed_area_route_relief','floorplan','route']:
            timeout_preflights[result['case']]=path.name
    rows=[]
    for original in snapshot['designs']:
        if original['physical_verdict_verified']:continue
        row={k:original.get(k) for k in ('design','category','current_status','attempted_strategies','latest_attempt_status','reason','receipt')}
        cid=row['design'].split('__')[0];category=row['category']
        row['evidence_scope']='Campaign receipt state from the timestamped snapshot; not a live process claim or functional equivalence proof.'
        if category.startswith('timeout_') or category=='memory_exhaustion':
            row.update(priority='defer_resource_heavy', next_action='Retain original failure; require a reusable stage-specific hypothesis before spending another full budget. No shrinking RAM or relaxing acceptance.')
        elif category=='lvs_pin_mismatch':
            row.update(priority='reuse_port_buffer', next_action='Evaluate normal recipe eligibility and lifecycle scope using retained shared-top-port-net evidence; then strict physical replay in an available slot.', evidence=['remaining_lvs_screen_20261027.json','remaining_port_aliases_20261027.json'])
        elif category=='drc_violation':
            row.update(priority='independent_pdn_replay', next_action='Await the already launched ordinary-selection replay; require actual final DRC/LVS and source/SDC audit before adding a rescue.', evidence=['pdn_published_and_normal_selected_20261027.json'])
        elif category in ('routing_congestion','placement_capacity'):
            row.update(priority='reuse_area_when_supported', next_action='Use witnessed route/placement-specific evidence. The new area recipe covers route congestion only. Retain earlier unsuccessful area trials; do not repeat the same ineffective edit blindly.')
        elif category=='frontend_async_load_flop':
            row.update(priority='semantic_hold', next_action='Keep original reset behavior and retained counterexamples; no unconditional flop substitution.')
        else:
            row.update(priority='review_existing_input_strategy', next_action='Review existing source-input/frontend evidence for a reusable policy; distinguish missing meaningful assets from recoverable path or ordering problems. No new bespoke rewrite by default.')
        if cid in ('C01292','C03782'):
            row.update(priority='meaningful_asset_hold', next_action='Missing meaningful initialization remains unresolved; recover real assets or leave the design unresolved.')
        if cid in ('C03951','C03956'):
            row.update(priority='semantic_hold', next_action='Retain original initialization/feedback semantics; constant or wire replacements are not accepted.')
        if cid=='C03259':
            row.update(priority='defer_bespoke_candidate', next_action='Retain policy-27 physical-power candidate and prior failed attempts; no priority rerun under the reusable-recipe preference.')
        if cid=='C03878':
            row.update(priority='defer_bespoke_candidate', next_action='CTS investigation stopped under user priority; retain bounded tool diagnostics and original latch semantics.')
        if cid=='C03391' and (HERE/'pdn_c03391_remote_source_prepared_20261027.json').exists():
            prepared=json.loads((HERE/'pdn_c03391_remote_source_prepared_20261027.json').read_text())
            if prepared.get('source_files_verified') is True and prepared.get('original_clock_unchanged') is True:
                row.update(priority='pdn_residual_remote_source_prepared',
                    next_action='Original-byte source-normalized subject prepared on 216. Complete ordinary PDN recipe selection/application there before any physical execution; preserve original header/include evidence and the existing campaign. No source export is needed.',
                    evidence=['pdn_c03391_input_readiness_20261027.json','pdn_c03391_remote_source_prepared_20261027.json','pdn_c03391_scope_preflight_20261027.json'])
                selected_path=HERE/'pdn_c03391_remote_normal_selected_20261027.json'
                if selected_path.exists():
                    selected=json.loads(selected_path.read_text())
                    if selected.get('normal_recipe_selected') is True and selected.get('forced_selection') is False:
                        row.update(priority='pdn_residual_remote_normal_prepared',
                            next_action='Ordinary next/apply on the isolated 216 project selected the published PDN recipe. Prepare compatible frozen-toolchain execution and wait for an authorized available slot; preserve source-list audit and do not interrupt existing campaigns.')
                        row['evidence'].append(selected_path.name)
                        staged=HERE/'native_normal_replay_staging_20261027.json'
                        if staged.exists():
                            state=json.loads(staged.read_text())
                            if state.get('policy_revision')==28 and state.get('additional_workers_started')==0:
                                row.update(priority='pdn_residual_native_execution_staged',
                                    next_action='The native adapter is staged for the existing supervisor next natural child phase, subject to the narrowed user scope. It materializes the already normal-selected project inside an existing worker, preserving prior evidence and strict signoff. Verify actual receipt/process before claiming it started; no extra worker or campaign restart.')
                                row['evidence'].append(staged.name)
        if cid=='C02802' and (HERE/'native_followup_evidence_20261027.json').exists():
            review=json.loads((HERE/'native_followup_evidence_20261027.json').read_text())['memory_guard_review']
            if review.get('largest_actual_memory_bits')==524288 and review.get('attempted_memory_guard')==262144:
                row.update(priority='defer_resource_capacity',
                    next_action='The first raised guard still rejects an original 524288-bit RAM. Retain original memories; no blind capacity escalation or RAM shrinking. Revisit with reusable memory-mapping/hierarchy evidence and an appropriate resource budget.',
                    evidence=['native_followup_evidence_20261027.json'])
        if category=='timeout_5' and cid in timeout_preflights:
            row.update(priority='area_timeout_preflight_candidate',
                next_action='Prepare full original sources/assets and reserve one representative ordinary area-recipe replay after a slot is available. Frozen timeout is not a fresh A/B control; final physical acceptance remains unproven.',
                evidence=['timeout_route_full_log_screen_20261027.json',timeout_preflights[cid]])
            prepared=HERE/('area_'+cid.lower()+'_normal_prepared_20261027.json')
            if prepared.exists():
                row.update(priority='prepared_area_timeout_candidate',
                    next_action='Original-source ordinary replay is prepared. Inspect its actual execution receipt/process first; if not started, wait for a free existing slot and verify no duplicate remote attempt. Frozen timeout is not a fresh A/B control.')
                row['evidence'].append(prepared.name)
        if row['design'] in decisions:
            decision=decisions[row['design']]
            row['repair_disposition']=decision['disposition']
            if decision['disposition']=='abandon':
                row.update(priority='abandoned_by_user_scope',next_action='No further repair attempts in this campaign. Retain original failure and all evidence; user requested abandoning difficult or distant rescues.',scope_reason=decision['reason'])
            else:
                row['attempt_policy']='One existing, supported candidate; no additional bespoke development after failure.'
            row.setdefault('evidence',[]).append(scope_path.name)
        if row.get('repair_disposition')=='candidate' and row['design'] in closed_candidates:
            closed,evidence=closed_candidates[row['design']]
            # A historic closeout must not hide a later running attempt or a
            # different failure. Physical results themselves remain untouched.
            if (original.get('current_status')=='not_clean'
                    and original.get('latest_attempt_status')=='fail'
                    and original.get('receipt')==closed.get('receipt')
                    and (original.get('attempted_strategies') or [None])[-1]==closed.get('strategy')
                    and (original.get('latest_result') or {}).get('failure_class')==closed.get('failure_class')):
                row.update(original_scope_disposition='candidate',repair_disposition='completed_unsuccessful',
                    priority='bounded_candidate_failed_no_further_retry',
                    next_action='Attempt completed unsuccessfully. Retain all evidence; no further retry under the user closeout scope.',
                    closeout_reason=closed['reason'])
                row.setdefault('evidence',[]).append(evidence)
        if (cid=='C03391' and row.get('repair_disposition')=='candidate'
                and original.get('latest_attempt_status') in ('flow_running','signoff_running')
                and (original.get('attempted_strategies') or [None])[-1]=='normal_recipe_pin_pdn_clearance'):
            row.update(priority='pdn_residual_native_execution_started',
                next_action='The timestamped native receipt records ordinary PDN recipe execution. Inspect its existing worker/container; await strict final DRC/LVS and source/SDC audit without a duplicate launch.')
        rows.append(row)
    assert len(rows)==snapshot['baseline_total']-snapshot['physical_combined_dual_clean']
    return dict(snapshot_at=snapshot['at'],baseline_dual_clean=snapshot['baseline_dual_clean'],
        unique_rescues=snapshot['physical_new_dual_clean'],combined_dual_clean=snapshot['physical_combined_dual_clean'],
        unresolved=len(rows),categories=dict(Counter(r['category'] for r in rows)),
        work_priorities=dict(Counter(r['priority'] for r in rows)),
        repair_dispositions=dict(Counter(r.get('repair_disposition','unreviewed') for r in rows)),
        scope='Work priorities only. Deferred and not-attempted designs remain unresolved; this inventory never changes a physical verdict.',designs=rows)


if __name__=='__main__':
    source=HERE/'status_snapshot_20261027_latest.json'
    if not source.exists():source=HERE/'status_snapshot_20261027.json'
    snapshot=json.loads(source.read_text())
    from summarize_verified_replays import summarize as verified_replays
    verified=verified_replays()
    if verified['remote_snapshot_at']!=snapshot['at']:raise ValueError('remote snapshot changed during summary')
    local=verified['local_rescues_awaiting_reconciliation']
    reconciled=snapshot['physical_new_dual_clean']
    for row in snapshot['designs']:
        if row['design'] in local:
            row.update(physical_verdict_verified=True,current_status='dual_clean_local_pending_reconciliation')
    snapshot['physical_new_dual_clean']=verified['total_verified_unique_rescues']
    snapshot['physical_combined_dual_clean']=verified['total_verified_dual_clean']
    output=summarize(snapshot)
    output.update(reconciled_rescues_on_216=reconciled,local_strict_rescues_awaiting_reconciliation=list(local),
        local_verification_at=verified['at'],
        status_scope='Remote strict snapshot plus independently source/config/SDC-validated completed local replays; remote campaign receipts are not changed by this summary.')
    (HERE/'remaining_work_20261027.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps({k:v for k,v in output.items() if k!='designs'}))

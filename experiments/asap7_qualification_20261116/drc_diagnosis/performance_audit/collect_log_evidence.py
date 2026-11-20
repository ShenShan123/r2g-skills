"""Read public Calibre log output; no rule extraction or rule changes."""
from pathlib import Path
import hashlib,json,re
out=Path(__file__).resolve().parent
root=out.parent.parent
rows=[]
for path in sorted(root.rglob('run.log')):
    if '/drc/' not in str(path):continue
    text=path.read_text(errors='replace');lines=text.splitlines()
    ops=list(re.finditer(r'CPU TIME = (\d+)\s+REAL TIME = (\d+).*?OPS COMPLETE = (\d+) OF (\d+)\s+ELAPSED TIME = (\d+)',text))
    if not ops:continue
    at86=next((m for m in ops if m[3]=='86'),None)
    if not at86:continue
    after=next((m for m in ops if m.start()>at86.start()),None)
    rules=re.findall(r'DRC RuleCheck (\S+) COMPLETED\. Number of Results = ([^\n]+)',text)
    before_rules=re.findall(r'DRC RuleCheck (\S+) COMPLETED\. Number of Results = ([^\n]+)',text[:after.start() if after else len(text)])
    after_rule_names=re.findall(r'DRC RuleCheck (\S+) COMPLETED',text[after.end():])[:2] if after else []
    value=lambda p: (re.search(p,text)[1] if re.search(p,text) else None)
    rows.append({'path':str(path.relative_to(root)), 'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                 'last_operation':int(ops[-1][3]),'total_operations':int(ops[-1][4]),
                 'time_reaching_op86_s':int(at86[5]),
                 'next_block_end_operation':int(after[3]) if after else None,
                 'next_block_reported_real_s':int(after[2]) if after else None,
                 'next_block_rule_names':after_rule_names,
                 'last_completed_rule':rules[-1][0] if rules else None,
                 'last_completed_rule_before_block_end':before_rules[-1][0] if before_rules else None,
                 'user_cpu_s':float(value(r'User time \(seconds\): ([\d.]+)')),
                 'system_cpu_s':float(value(r'System time \(seconds\): ([\d.]+)')),
                 'cpu_percent':value(r'Percent of CPU this job got: (\d+%)'),
                 'peak_rss_kib':int(value(r'Maximum resident set size \(kbytes\): (\d+)')),
                 'swaps':int(value(r'\n\s*Swaps: (\d+)')),
                 'exit_status':int(value(r'Exit status: (\d+)')),
                 'op86_log_line':text[:at86.start()].count('\n')+1})
record={'scope':'All archived ASAP7 DRC run.log files; logging boundaries do not reveal encrypted inner operations',
        'conclusion':'Timeout is inside operation block after ACTIVE.FIN.EX.1, associated in completed controls with ACTIVE.W.1 and ACTIVE.W.2; FIN geometry is not an established cause.',
        'rows':rows}
(out/'log_evidence.json').write_text(json.dumps(record,indent=2)+'\n')
for row in rows:
 print(row['path'],row['last_operation'],row['time_reaching_op86_s'],row['next_block_reported_real_s'],row['cpu_percent'],row['peak_rss_kib'])

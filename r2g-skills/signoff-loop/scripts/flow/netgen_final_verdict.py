#!/usr/bin/env python3
"""Accept Netgen's final top-level verdict, never a successful child cell."""
import argparse
from pathlib import Path
import re


def final_verdict(text):
    values=re.findall(r'^Final result:[ \t]*(?:\r?\n[ \t]*)?([^\r\n]*)',text,re.M)
    if not values:return 'unknown'
    value=values[-1].strip()
    if value in ('Circuits match uniquely.','Netlists match uniquely.'):return 'clean'
    if value=='Top level cell failed pin matching.':return 'pin_mismatch'
    if 'do not match' in value:return 'mismatch'
    return 'unknown'


def classify(log,report=None,returncode=0):
    # A present report must itself be conclusive. A matching child or a prior
    # successful invocation cannot rescue an incomplete final report.
    log_result=final_verdict(log)
    report_result=final_verdict(report) if report is not None else None
    if report_result is not None and report_result=='unknown':return 'unknown','incomplete_final_report'
    if log_result!='unknown' and report_result is not None and log_result!=report_result:
        return 'unknown','conflicting_final_verdicts'
    result=report_result if report_result is not None else log_result
    if result=='clean':
        if returncode:return 'unknown','tool_exit_nonzero'
        return 'clean','match'
    if result in ('mismatch','pin_mismatch'):return 'mismatch',result
    return 'unknown','missing_final_verdict'


def main():
    p=argparse.ArgumentParser();p.add_argument('--log',type=Path,required=True);p.add_argument('--report',type=Path,required=True)
    p.add_argument('--returncode',type=int,required=True);a=p.parse_args()
    log=a.log.read_text(errors='replace') if a.log.is_file() else ''
    report=a.report.read_text(errors='replace') if a.report.is_file() else None
    print('\t'.join(classify(log,report,a.returncode)))


if __name__=='__main__':main()

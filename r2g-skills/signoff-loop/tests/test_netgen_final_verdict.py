from netgen_final_verdict import classify,final_verdict


def test_child_success_does_not_override_final_top_failure():
    text='Netlists match uniquely.\nFinal result: Top level cell failed pin matching.\n'
    assert classify(text,text)==('mismatch','pin_mismatch')
    assert classify(text.replace('Top level cell failed pin matching.','Netlists do not match.'))==('mismatch','mismatch')


def test_child_failure_does_not_override_actual_final_unique_match():
    text='child mismatch\nFinal result: Circuits match uniquely.\n'
    assert classify(text,text)==('clean','match')
    assert final_verdict('Final result: \nCircuits match uniquely.\n')=='clean'


def test_missing_incomplete_and_warning_verdicts_cannot_pass():
    for text in ('Netlists match uniquely.\n','Final result:\n','Final result: Circuits match with warnings.\n'):
        assert classify(text,text)[0]!='clean'
    assert classify('Final result: Circuits match uniquely.\n','')[0]!='clean'


def test_conflicting_report_or_failed_process_cannot_pass():
    clean='Final result: Circuits match uniquely.\n';bad='Final result: Netlists do not match.\n'
    assert classify(clean,bad)==('unknown','conflicting_final_verdicts')
    assert classify(clean,clean,124)==('unknown','tool_exit_nonzero')


def test_last_final_verdict_is_authoritative():
    text='Final result: Circuits match uniquely.\nFinal result: Netlists do not match.\n'
    assert classify(text,text)==('mismatch','mismatch')

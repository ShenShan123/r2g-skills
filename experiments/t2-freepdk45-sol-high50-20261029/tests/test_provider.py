from pathlib import Path
import json
import sys
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import sol_provider as P


def test_model_effort_and_no_experiment_output_quota():
    b=P.payload([{'role':'user','content':'Return JSON'}])
    assert b['model']=='gpt-6.1-sol' and b['reasoning']=={'effort':'high'}
    assert b['max_output_tokens']==128000 and b['store'] is False
    assert not any(k in b for k in ['temperature','top_p','previous_response_id','tools'])


def test_reasoning_is_counted_once():
    r={'usage':{'input_tokens':10,'output_tokens':30,'total_tokens':40,
        'output_tokens_details':{'reasoning_tokens':25},'input_tokens_details':{'cached_tokens':5}}}
    u=P.usage(r);assert u['total_tokens']==40 and u['completion_tokens']==30 and u['reasoning_tokens']==25


@pytest.mark.parametrize('u',[{}, {'input_tokens':1,'output_tokens':None,'total_tokens':1},
    {'input_tokens':True,'output_tokens':1,'total_tokens':2},
    {'input_tokens':1,'output_tokens':2,'total_tokens':4}])
def test_missing_or_inconsistent_usage_is_unknown(u):assert P.usage({'usage':u}) is None


def test_list_endpoint_is_normalized_without_editing_keyfile(tmp_path):
    path=tmp_path/'env';text='export GPT_MODEL=gpt-6.1-sol\nexport GPT_API_KEY="fixture-not-real"\nexport GPT_BASE_URL=https://api.openai.com/v1/models\n'
    path.write_text(text);key,url=P.credentials(path)
    assert key=='fixture-not-real' and url=='https://api.openai.com/v1/responses' and path.read_text()==text


def test_provider_never_silently_uses_other_endpoint(tmp_path):
    path=tmp_path/'env';path.write_text('GPT_MODEL=gpt-6.1-sol\nGPT_API_KEY=fixture\nGPT_BASE_URL=https://example.org/v1\n')
    with pytest.raises(ValueError):P.credentials(path)

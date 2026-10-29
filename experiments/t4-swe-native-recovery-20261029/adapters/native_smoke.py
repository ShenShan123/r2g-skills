"""Real native tools/executor with a SCRIPTED, offline provider. Not paper data."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PREP=ROOT.parent/'t4-swe-agent-adaptation-20261026'
os.environ['LITELLM_LOCAL_MODEL_COST_MAP']='True'
sys.path.insert(0,str(PREP/'sources/SWE-agent'))
from bridge import FullMemory
from host import SoftwareState,build_agent
from local_sandbox import native_environment
from provision import record
from sweagent.agent.problem_statement import TextProblemStatement


class ScriptedProvider:
    model='deepseek-flash'
    blocked=False
    calls=0
    def request(self,body,**kwargs):
        i=self.calls;self.calls+=1
        if i==0:
            name,args='bash',{'command':"python -c 'import a; assert a.answer()==1; print(\"BEFORE_OK\")'"}
        elif i==1:
            name,args='str_replace_editor',{'command':'str_replace','path':'/testbed/a.py',
                                         'old_str':'return 1','new_str':'return 2'}
        elif i==2:
            name,args='bash',{'command':"python -c 'import a; assert a.answer()==2; print(\"AFTER_OK\")'"}
        else:
            name,args='submit',{}
        if i>6:
            raise RuntimeError('offline native smoke failed to terminate')
        return {'id':'SCRIPTED-OFFLINE-'+str(i),'model':self.model,
            'choices':[{'message':{'content':'SCRIPTED UNIT FIXTURE, NOT REAL MODEL',
                'tool_calls':[{'id':'fixture-'+str(i),'type':'function',
                               'function':{'name':name,'arguments':json.dumps(args)}}]}}],
            'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}}


def main():
    import yaml
    work=ROOT/'reports/native-real-tools-smoke-r3'
    work.mkdir(exist_ok=False)
    repo=work/'repo';repo.mkdir()
    (repo/'a.py').write_text('def answer(): return 1\n')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','a.py'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=offline-fixture',
                    '-c','user.email=fixture@example.invalid','commit','-qm','offline fixture'],check=True)
    home=work/'home';home.mkdir()
    qualification=json.loads((ROOT/'qualification/sphinx-doc__sphinx-9673/qualification.json').read_text())
    envpath=Path(qualification['environment_prefix'])
    env=native_environment(repo,home,envpath,work/'server.log')
    memory=FullMemory('swe-agent',arm='native',root=work)
    provider=ScriptedProvider()
    try:
        env.start()
        task={'instance_id':'SCRIPTED_OFFLINE_FIXTURE','repo':'OFFLINE_FIXTURE','base_commit':'fixture'}
        state=SoftwareState(memory,task,lambda:env.communicate('cd /testbed && git --no-pager diff HEAD',check='raise'))
        config=yaml.safe_load((ROOT/'inputs/native.yaml').read_text())['agent']
        agent=build_agent(config,memory,provider,state)
        result=agent.run(env=env,problem_statement=TextProblemStatement(
            text='Offline unit fixture: change answer() from 1 to 2. Not a SWE-bench task.',
            id='SCRIPTED_OFFLINE_FIXTURE'),output_dir=work/'trajectory')
        assert result.info['exit_status']=='submitted',result.info
        assert 'return 2' in (repo/'a.py').read_text()
        observations='\n'.join(str(s.get('observation','')) for s in result.trajectory)
        assert 'BEFORE_OK' in observations and 'AFTER_OK' in observations
        from run_swe import validated_submission
        patch,receipt=validated_submission(agent,repo)
        assert '+def answer(): return 2' in patch
        assert patch.endswith('\n')
        record(work/'receipt.json',{'status':'PASS','paper_data':False,
            'provider':'SCRIPTED_OFFLINE_NO_NETWORK','actual_provider_requests':0,
            'fixture_decisions':provider.calls,'native_steps':len(result.trajectory),
            'native_tools':'unmodified default bundles; original edit, bash, submit and review',
            'runtime':'real SWE-ReX inside Bubblewrap, not DummyDeployment',
            'exact_submission':receipt})
    finally:
        env.close();memory.close()


if __name__=='__main__':
    main()

"""SWE-ReX's real LocalRuntime in a filesystem-isolated, unprivileged sandbox.

Only a disposable root and this task's repository are writable. The provider,
credentials, experiment ledgers, other arms and evaluator inputs are not mounted.
The native agent/parser/tools remain outside and talk to the native runtime.
"""
import asyncio
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request

from swerex.deployment.remote import RemoteDeployment
from sweagent.environment.swe_env import SWEEnv

PREP = Path('/data1/zhangdy/tehm-experiments/t4-swe-agent-adaptation-20261026')
PYTHON = PREP/'envs/runtime/bin/python'


def sandbox_args(repo, home, *, env=None):
    repo, home = Path(repo).resolve(), Path(home).resolve()
    if not repo.is_dir() or not home.is_dir() or repo == home:
        raise ValueError('sandbox needs distinct, existing disposable directories')
    cmd = ['/usr/bin/bwrap', '--unshare-all', '--share-net', '--die-with-parent',
           '--new-session', '--clearenv', '--ro-bind','/usr','/usr',
           '--ro-bind','/lib','/lib', '--ro-bind','/lib64','/lib64',
           '--symlink','usr/bin','/bin', '--symlink','usr/sbin','/sbin',
           '--ro-bind','/opt/anaconda3','/opt/anaconda3',
           '--ro-bind','/etc/ssl','/etc/ssl', '--ro-bind','/etc/resolv.conf','/etc/resolv.conf',
           '--ro-bind','/etc/hosts','/etc/hosts', '--proc','/proc','--dev','/dev',
           '--ro-bind','/etc/alternatives','/etc/alternatives',
           '--tmpfs','/tmp', '--bind',str(repo),'/testbed', '--bind',str(home),'/root',
           '--ro-bind',str(PREP/'envs/runtime'),str(PREP/'envs/runtime'),
           '--setenv','HOME','/root', '--setenv','PATH','/usr/bin:/bin',
           '--setenv','LANG','C.UTF-8', '--setenv','LC_ALL','C.UTF-8',
           '--setenv','OPENBLAS_NUM_THREADS','1', '--setenv','OMP_NUM_THREADS','1',
           '--setenv','PYTHONDONTWRITEBYTECODE','1', '--chdir','/testbed']
    if env:
        env = Path(env).resolve()
        # Same prefix preserves Python/conda absolute paths. It is read-only.
        cmd += ['--ro-bind',str(env),'/root/envs/testbed',
                '--setenv','TASK_ENV','/root/envs/testbed',
                '--setenv','PATH','/root/envs/testbed/bin:/usr/bin:/bin']
    bootstrap=Path(__file__).resolve().parents[1]/'bootstrap'
    if bootstrap.is_dir():
        cmd+=['--ro-bind',str(bootstrap),'/bootstrap']
    runtimes=Path(__file__).resolve().parents[1]/'python-runtimes'
    if runtimes.is_dir():
        cmd+=['--ro-bind',str(runtimes),'/managed-python']
    return cmd


def isolated_command(repo, home, command, *, env=None, timeout=1800):
    if env:
        command='export PYTHONPATH=/testbed:'+task_site(env)+'; '+command
    return subprocess.run(sandbox_args(repo,home,env=env)+['--','/bin/bash','-c',command],
                          capture_output=True, text=True, timeout=timeout)


def task_site(env):
    paths=list(Path(env).glob('lib/python*/site-packages'))
    if len(paths)!=1:
        raise ValueError('exactly one task Python package directory required')
    return '/root/envs/testbed/'+str(paths[0].relative_to(env))


class BwrapDeployment(RemoteDeployment):
    """Transport adapter, not an alternate repair executor."""
    def __init__(self, repo, home, env, log):
        self.repo, self.home, self.task_env = Path(repo), Path(home), Path(env)
        self.log = Path(log)
        self.process = None
        with socket.socket() as s:
            s.bind(('127.0.0.1',0))
            port = s.getsockname()[1]
        super().__init__(host='http://127.0.0.1',port=port,auth_token=secrets.token_hex(32),timeout=600)

    async def start(self):
        rc = self.home/'.bashrc'
        rc.write_text('export PATH=/root/envs/testbed/bin:/usr/bin:/bin\n'
                      'export HOME=/root USER=tehm-agent LOGNAME=tehm-agent\n'
                      'export GIT_PAGER=cat PAGER=cat TERM=dumb GIT_TERMINAL_PROMPT=0\n'
                      'export PYTHONDONTWRITEBYTECODE=1 OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1\n'
                      'export ROOT=/testbed\nexport PYTHONPATH=/testbed:'+task_site(self.task_env)+'\ncd /testbed\n')
        self.log.parent.mkdir(parents=True,exist_ok=True)
        self._out = self.log.open('x')
        cmd = sandbox_args(self.repo,self.home,env=self.task_env)+[
            '--',str(PYTHON),'-m','swerex.server','--host','127.0.0.1',
            '--port',str(self._config.port),'--auth-token',self._config.auth_token]
        self.process = subprocess.Popen(cmd,stdout=self._out,stderr=subprocess.STDOUT,
                                        start_new_session=True,env={'PATH':'/usr/bin:/bin'})
        try:
            for _ in range(120):
                if self.process.poll() is not None:
                    raise RuntimeError('isolated SWE-ReX runtime exited; see '+str(self.log))
                try:
                    req = urllib.request.Request(f'http://127.0.0.1:{self._config.port}/is_alive',
                                                  headers={'X-API-Key':self._config.auth_token})
                    with urllib.request.urlopen(req,timeout=1) as r:
                        if r.status == 200:
                            break
                except (OSError,TimeoutError):
                    await asyncio.sleep(.25)
            else:
                raise RuntimeError('isolated SWE-ReX startup timed out')
            await super().start()
        except BaseException:
            await self.stop()
            raise

    async def is_alive(self, **kwargs):
        return await self.runtime.is_alive(**kwargs)

    async def stop(self):
        try:
            if self._runtime is not None:
                await super().stop()
        finally:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=15)
            if hasattr(self,'_out'):
                self._out.close()


def native_environment(repo, home, env, log):
    deployment = BwrapDeployment(repo,home,env,log)
    return SWEEnv(deployment=deployment, repo=None,
        post_startup_commands=['cd /testbed','export ROOT=/testbed',
                              'export PYTHONPATH=/testbed:'+task_site(env)])

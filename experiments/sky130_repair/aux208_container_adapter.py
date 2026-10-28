#!/usr/bin/env python3
"""Narrow Podman CLI adapter for two isolated Docker verification cases on 208.

The original checker command and paths inside the image are preserved. Only
host bind sources, SELinux mount labels, container names and the engine change.
No arbitrary host mounts or mutable image tags are accepted.
"""
import json
import os
from pathlib import Path
import re
import sys
import uuid

# Docker reserializes the imported Podman configuration. All six rootfs layer
# digests and every Config field were independently verified unchanged.
SOURCE_IMAGE='5d92bbe588a0d7cf374d4bfa4bda2f1e3b18d325c30c84366284090e8f56d48d'
IMAGE='sha256:1e2bff384b86712a4eaef8ad00b66452e0cc10935ea808430fb50639c5ea9cba'
ORIGINAL='/proj/workarea/yangao'


def translated(args,root,token):
    if not re.fullmatch(r'[a-zA-Z0-9_]+',token):raise ValueError('invalid execution token')
    root=root.resolve()
    prefix='aux208_'+token+'_'
    if args[:2]==['rm','-f']:
        if len(args)!=3 or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',args[2]):raise ValueError('invalid removal target')
        return ['rm','-f',prefix+args[2]]
    if not args or args[0]!='run':raise ValueError('only run and exact named rm are supported')
    result=['run','--cpus=8','--label','r2g.aux208.run='+token]
    image_seen=False;named=False;i=1
    while i<len(args):
        value=args[i]
        if image_seen:
            result.extend(args[i:]);break
        if value in ('r2g-orfs:26Q3','localhost/r2g-orfs:26Q3'):
            if not named:result.extend(['--name',prefix+uuid.uuid4().hex[:10]])
            result.append(IMAGE);image_seen=True;i+=1;continue
        if value=='--name':
            name=args[i+1]
            if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',name):raise ValueError('invalid container name')
            result.extend(['--name',prefix+name]);named=True;i+=2;continue
        if value=='-v':
            parts=args[i+1].split(':')
            if len(parts) not in (2,3):raise ValueError('unsupported bind mount')
            host,target=parts[:2];options=parts[2].split(',') if len(parts)==3 else []
            mapped={ORIGINAL+'/rtl-certified-handoff-0002':root/'package',
                    ORIGINAL+'/r2g/signoff-loop':root/'environment/r2g/signoff-loop',
                    ORIGINAL+'/miniconda3/envs/eda':root/'environment/miniconda3/envs/eda'}
            source=mapped.get(host,Path(host)).resolve()
            if not source.is_relative_to(root):raise ValueError('host mount escapes isolated auxiliary directory: '+host)
            if not source.exists():raise ValueError('missing host bind source: '+str(source))
            options=[o for o in options if o not in ('z','Z')]
            if host in mapped and 'ro' not in options:raise ValueError('frozen input must be mounted read-only')
            mount=str(source)+':'+target+(':'+','.join(options) if options else '')
            result.extend(['-v',mount]);i+=2;continue
        if value in ('-e','--memory','--memory-swap'):
            result.extend([value,args[i+1]]);i+=2;continue
        if value=='--rm' or value.startswith(('--memory=','--memory-swap=')):
            result.append(value);i+=1;continue
        raise ValueError('unexpected container option: '+value)
    if not image_seen:raise ValueError('expected frozen image was not supplied')
    return result


def main():
    root=Path(os.environ['R2G_AUX208_ROOT']);token=os.environ['R2G_AUX208_TOKEN']
    args=translated(sys.argv[1:],root,token)
    with (root/'container_commands.jsonl').open('a') as f:
        f.write(json.dumps(dict(token=token,argv=['docker',*args]))+'\n')
    os.execv('/usr/bin/docker',['docker',*args])


if __name__=='__main__':main()

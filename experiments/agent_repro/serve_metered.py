#!/usr/bin/env python3
"""Serve an isolated metered Agent using a local installation's saved model config.

Credentials use the application's normal resolver and remain only in this process;
they are never copied into the experiment home or inherited by EDA subprocesses.
"""
import argparse
import os
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-home', type=Path, required=True)
    parser.add_argument('--home', type=Path, required=True)
    parser.add_argument('--port', type=int, default=8772)
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--cores', type=int, default=4)
    args = parser.parse_args()
    source = args.source_home.resolve()
    destination = args.home.resolve()
    if source == destination:
        parser.error('experiment home must differ from the existing application home')

    os.environ['R2G_HOME'] = str(source)
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'agent'))
    from r2g_agent import config, llm
    cfg = config.load()
    source_provider = cfg.model.provider
    key = config.api_key(cfg.model)
    if not key and source_provider != 'ollama':
        parser.error('configured model credential unavailable from standard application resolver')
    # Pin the existing credential in memory. The isolated process must not write a
    # credentials.yaml, nor put the credential in a subprocess environment.
    def resolve_key(mc):
        return key if mc.provider == source_provider else None
    config.api_key = resolve_key
    llm.api_key = resolve_key
    key_name = cfg.model.resolved().api_key_env
    if key_name:
        os.environ.pop(key_name, None)

    os.environ['R2G_HOME'] = str(destination)
    destination.mkdir(parents=True, exist_ok=True)
    cfg.workers, cfg.cores_per_design = args.workers, args.cores
    cfg.host, cfg.port, cfg.keep_intermediate = '127.0.0.1', args.port, True
    config.save(cfg)
    from r2g_agent import server
    server.init_runtime(cfg, resume=True)
    import uvicorn
    uvicorn.run(server.app, host=cfg.host, port=cfg.port, log_level='info')


if __name__ == '__main__':
    main()

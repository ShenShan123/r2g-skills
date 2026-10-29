"""Reassemble provider SSE completely; no retry or partial action execution."""
import json


def read_response(response, raw_path):
    if 'text/event-stream' not in response.headers.get('content-type', response.headers.get('Content-Type', '')):
        raw = response.read()
        raw_path.write_bytes(raw)
        return json.loads(raw), True
    identity = {}
    message = {'role': 'assistant', 'content': ''}
    tools = {}
    finish = None
    usage = None
    complete = False
    with raw_path.open('xb') as raw:
        for line in response:
            raw.write(line)
            raw.flush()
            line = line.strip()
            if not line or line.startswith(b':'):
                continue
            if not line.startswith(b'data:'):
                raise ValueError('unexpected SSE field')
            payload = line[5:].strip()
            if payload == b'[DONE]':
                complete = True
                break
            chunk = json.loads(payload)
            if chunk.get('error'):
                raise ValueError('provider error in SSE; see raw response')
            for key in ['id', 'model']:
                if chunk.get(key):
                    if key in identity and identity[key] != chunk[key]:
                        raise ValueError('SSE identity changed')
                    identity[key] = chunk[key]
            if chunk.get('usage') is not None:
                if usage is not None and usage != chunk['usage']:
                    raise ValueError('conflicting SSE usage')
                usage = chunk['usage']
            for choice in chunk.get('choices', []):
                if choice.get('index', 0) != 0:
                    raise ValueError('unexpected multi-choice response')
                if choice.get('finish_reason'):
                    finish = choice['finish_reason']
                delta = choice.get('delta', {})
                for key in ['content', 'reasoning_content']:
                    if delta.get(key) is not None:
                        if not isinstance(delta[key], str):
                            raise ValueError('non-text SSE field')
                        message[key] = message.get(key, '') + delta[key]
                for call in delta.get('tool_calls', []):
                    index = call['index']
                    target = tools.setdefault(index, {'id': '', 'type': 'function',
                                                       'function': {'name': '', 'arguments': ''}})
                    if call.get('id'):
                        target['id'] += call['id']
                    if call.get('type') not in (None, 'function'):
                        raise ValueError('unexpected SSE tool type')
                    for key in ['name', 'arguments']:
                        target['function'][key] += call.get('function', {}).get(key) or ''
    if tools:
        if sorted(tools) != list(range(len(tools))):
            raise ValueError('non-contiguous SSE tool indices')
        message['tool_calls'] = [tools[i] for i in sorted(tools)]
        if any(not t['id'] or not t['function']['name'] for t in message['tool_calls']):
            raise ValueError('incomplete SSE tool identity')
    return {**identity, 'choices': [{'index': 0, 'message': message, 'finish_reason': finish}],
            'usage': usage}, complete and finish is not None

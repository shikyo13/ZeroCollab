"""Classify local model output and validate structured calls before execution."""
import json
import re
import uuid

from jsonschema import Draft202012Validator


def visible_text(text):
    # Some servers fail to separate reasoning when parsing is disabled. Never
    # publish protocol tokens or execute commands encoded in ordinary content.
    if '</think>' in text and '<think>' not in text.split('</think>', 1)[0]:
        text = text.split('</think>', 1)[1]
    text = re.sub(r'<think>.*?(?:</think>|$)', '', text, flags=re.S)
    malformed = bool(re.search(r'</?(?:tool_call|function)(?:[=>\s])', text))
    if malformed:
        raise ValueError('Local server returned tool markup as text instead of structured tool_calls; no textual tool call was executed')
    return text.replace('</think>', '').strip()


def validate_calls(calls, tools):
    schemas = {t['function']['name']: t['function'].get('parameters', {}) for t in tools}
    validated = []
    for call in calls:
        name = call.get('function', {}).get('name')
        if name not in schemas:
            raise ValueError('Local model requested an unavailable tool: ' + str(name))
        try:
            params = json.loads(call['function'].get('arguments') or '{}')
        except (ValueError, TypeError) as exc:
            raise ValueError('Invalid JSON arguments for ' + name) from exc
        if not isinstance(params, dict):
            raise ValueError('Tool arguments must be an object: ' + name)
        schema = {**schemas[name], 'additionalProperties': False}
        errors = list(Draft202012Validator(schema).iter_errors(params))
        if errors:
            raise ValueError('Invalid arguments for ' + name + ': ' + errors[0].message)
        call = {**call, 'id': call.get('id') or 'call_' + uuid.uuid4().hex}
        validated.append((call, name, params))
    return validated

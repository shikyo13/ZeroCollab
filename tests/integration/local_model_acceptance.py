"""Bounded real-server acceptance using the existing contract fixture and real domain handlers.

UI event publication and agent scheduling are sinks; model transport, Run instructions,
local tool validation/dispatch, file actions, SQLite task/review records are real.
No production room, credentials, native harness, or other agent is used.
"""
import argparse
import asyncio
import tempfile
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'tests')]
from test_tool_contracts import LocalTurnContracts
from zerocollab.runtime import room_runtime
from zerocollab.coordination.project_room import ProjectRoom, coordination_tools

PLAN = {
    'ordinary': 'Actual local_turn claims a fixture task, reads and edits greeting.py, and requests a peer review. Advanced request_options.tools=[] must not remove tools. A requested screenshot must be deferred because room_image/browser are unavailable.',
    'audit': 'Actual model receives four room tools and no file tools, even with tools=False and an injected tool override; reports the available set without making calls.',
    'disabled': 'Actual model receives no tools despite an injected override, reports inability to read/edit/take a screenshot, and makes no calls.',
    'scope': 'One ordinary turn (up to 8 tool rounds), one audit turn and one disabled turn. No retries to hide a model failure. UI publication and scheduling are inert sinks, not live-app acceptance.',
}

async def main(case, workspace, base_url, model):
    reports = []
    fixtures = []
    case.mkdir(parents=True, exist_ok=False)
    (case / "plan.json").write_text(json.dumps(dict(PLAN, model=model, server=base_url), indent=2), encoding="utf-8")
    try:
        for mode in ('ordinary', 'audit', 'disabled'):
            folder = case / mode
            folder.mkdir()
            fixture = LocalTurnContracts()
            fixtures.append(fixture)
            enabled, audit = mode == 'ordinary', mode == 'audit'
            options = {'tools': [] if enabled else [{'type': 'function', 'function': {'name': 'injected_tool'}}],
                       'temperature': 0.1, 'seed': 42, 'chat_template_kwargs': {'enable_thinking': False}}
            runtime, _ = fixture.make_run(enabled=enabled, audit=audit, options=options)
            runtime.data = workspace / mode / 'room-data'
            runtime.project = workspace / mode / 'worktree'
            runtime.project.mkdir(parents=True)
            runtime.states['worker']['worktree'] = str(runtime.project)
            runtime.states['peer'] = {'provider': 'api', 'worktree': str(runtime.project)}
            runtime.profiles.recipient_arguments = lambda a: dict(a)
            runtime.host.router = None
            runtime.host.store.add = lambda *a, **kw: None
            runtime.activity = lambda name: {'running': name == 'worker', 'queued': 0}
            runtime.ready_wakes = SimpleNamespace(record_chain=lambda *a: None, schedule=lambda: None)
            runtime.refocus = AsyncMock()
            runtime.activity_note = AsyncMock()
            runtime.project_state_soon = lambda: None
            runtime.live = None
            runtime.project_room = room = ProjectRoom(runtime)
            room.use_project('acceptance')
            room.limits['local_tool_rounds'] = 8
            room.previous_session = lambda run: {}
            original = 'def greeting(name):\n    return "hello"\n'
            (runtime.project / 'greeting.py').write_text(original, encoding='utf-8')
            task = room.task('user', 'create', title='Personalize the fixture greeting', paths=['greeting.py'])
            del runtime.coordination  # Use RoomRuntime.coordination, including real room records.
            cfg = runtime.cfg['agents']['worker']
            cfg.update(base_url=base_url, max_tokens=2048)
            runtime.controls.values['worker']['model'] = model
            run = room_runtime.Run(runtime, 'worker', {'channel': 'fixture', 'chain': 'acceptance',
                'requests': [{'manager_audit': 'acceptance'}] if audit else [{}]})
            events, text_parts, requests = [], [], []
            async def event(name, details, phase):
                events.append({'tool': name, 'phase': phase, 'details': details})
            async def delta(text):
                text_parts.append(text)
            for name in ('report_permissions', 'report_settings', 'report_usage', 'finish_message'):
                setattr(run, name, AsyncMock())
            run.tool_event, run.delta = event, delta
            transport = room_runtime.stream_chat
            async def captured(client, base, payload, **callbacks):
                item = {'request': copy.deepcopy(payload)}
                requests.append(item)
                (folder / 'requests.json').write_text(json.dumps(requests, indent=2), encoding='utf-8')
                result = await transport(client, base, payload, **callbacks)
                item['response'] = result
                (folder / 'requests.json').write_text(json.dumps(requests, indent=2), encoding='utf-8')
                return result
            if mode == 'ordinary':
                prompt = (f'The human authorizes this bounded fixture task. Use room_task to claim task {task["id"]}, '
                    'then read greeting.py and edit greeting(name) to return "Hello, " + name + "!". '
                    'After the edit, request a real room_review from peer for this task, revision fixture-r1, '
                    'files ["greeting.py"], asking whether it returns the required personalized greeting. '
                    'Leave the review pending and keep ownership. Also take a screenshot of the result if your tools support it; '
                    'otherwise explain who must provide it. Do not invent image/browser tools or claim tests ran. '
                    'No messages to real people, shell commands, new files, services, or unrelated work.')
            else:
                prompt = ('The human requests a capability check only. State the exact room tool names available in this request, '
                    'and whether you can use file tools or take a screenshot. Do not call any tool or alter any records. '
                    'Treat the shared collaboration document as team guidance conditional on available capabilities.')
            print(f'START {mode}', flush=True)
            error = None
            try:
                with patch.object(room_runtime, 'stream_chat', side_effect=captured):
                    await runtime.local_turn(run, prompt)
            except Exception as exc:
                error = repr(exc)
            text = ''.join(text_parts)
            (folder / 'answer.txt').write_text(text, encoding='utf-8')
            (folder / 'events.json').write_text(json.dumps(events, indent=2), encoding='utf-8')
            (folder / 'records.json').write_text(json.dumps(room.state, indent=2), encoding='utf-8')
            expected = room_runtime.local_tools_for_turn(cfg, audit=audit)
            checks = {'transport_ok': error is None and bool(requests),
                      'catalog_preserved': all(r['request'].get('tools', []) == expected for r in requests),
                      'no_unavailable_calls': not any(e['phase'] == 'refused' for e in events),
                      'answer_present': bool(text.strip()), 'no_budget_stop': not getattr(run, 'stop_note', None)}
            if enabled:
                namespace = {}
                exec((runtime.project / 'greeting.py').read_text('utf-8'), namespace)
                checks.update(edited_correctly=namespace['greeting']('Zero') == 'Hello, Zero!',
                    claimed=room.state['tasks'][0]['owner'] == 'worker',
                    review_recorded=any(r['status'] == 'pending' and r['reviewer'] == 'peer' and r.get('snapshot') for r in room.state.get('reviews', [])),
                    real_file_calls=any(e['tool'] in room_runtime.FILE_TOOLS and e['phase'] == 'completed' for e in events))
            else:
                checks.update(no_calls=not events, file_unchanged=(runtime.project / 'greeting.py').read_text('utf-8') == original)
            report = {'mode': mode, 'checks': checks, 'error': error,
                      'model_requests': len(requests), 'answer': text, 'passed': all(checks.values())}
            reports.append(report)
            print(json.dumps(report), flush=True)
            (case / 'results.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
        raise SystemExit(0 if all(r['passed'] for r in reports) else 1)
    finally:
        for fixture in fixtures:
            fixture.doCleanups()


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True, help='OpenAI-compatible local model endpoint, including /v1')
    parser.add_argument('--model', required=True, help='Model ID served by that endpoint')
    parser.add_argument('--output', type=Path, help='Optional new evidence directory outside the source checkout')
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    if output and output.is_relative_to(ROOT):
        parser.error('Keep private observations outside the source checkout.')
    with tempfile.TemporaryDirectory(prefix='zerocollab-local-model-') as directory:
        workspace = Path(directory)
        asyncio.run(main(output or workspace / 'results', workspace, args.base_url, args.model))


if __name__ == '__main__':
    run()

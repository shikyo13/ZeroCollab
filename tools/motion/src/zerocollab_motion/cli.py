"""File-oriented CLI, also used by the desktop's optional recording adapter."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from .engine import Engine
from . import scene as schema, studio


def parser():
    p = argparse.ArgumentParser(prog='zerocollab-motion', description='Standalone scene and media tools; no ZeroCollab app required.')
    p.add_argument('--version', action='version', version='zerocollab-motion 0.1.0')
    p.add_argument('action', choices=('list', 'get', 'validate', 'save', 'patch', 'preview', 'frame', 'export', 'inspect', 'import-asset',
                                     'capabilities', 'record-probe', 'record-encode', 'mcp'))
    p.add_argument('scene', nargs='?')
    p.add_argument('--workspace', default='.zerocollab-motion')
    p.add_argument('--output', help='Output directory; each job gets a unique subdirectory')
    p.add_argument('--profile', default='webm')
    p.add_argument('--composition')
    p.add_argument('--at-ms', type=float, action='append', dest='times')
    p.add_argument('--asset-id')
    p.add_argument('--scene-id', help='New scene id for a copied or patched scene')
    p.add_argument('--kind', default='image')
    p.add_argument('--ffmpeg', help='Explicit encoder executable; otherwise use PATH')
    p.add_argument('--browser', help='Explicit Chromium executable, useful for restricted MCP environments')
    p.add_argument('--asset-dir', help='Explicit directory for legacy /uploads asset references')
    p.add_argument('--request', help='JSON request file, or - for stdin (16 MiB maximum)')
    p.add_argument('--reduced', action='store_true')
    p.add_argument('--overwrite', action='store_true')
    return p


def request(path):
    with (sys.stdin if path == '-' else Path(path).open('r', encoding='utf-8')) as stream:
        raw = stream.read(16 * 1024 * 1024 + 1)
    if len(raw) > 16 * 1024 * 1024:
        raise ValueError('Request exceeds 16 MiB')
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('Request must be a JSON object')
    return value


async def execute(args):
    engine = Engine(args.workspace, ffmpeg=args.ffmpeg, asset_dir=args.asset_dir)
    try:
        data = request(args.request) if args.request else {}
        reference = data.get('scene', args.scene)
        if args.action == 'list':
            found, invalid = studio.catalogue(engine.workspace)
            return {'scenes': [studio.summary(ref, source, value) for ref, (source, value) in found.items()], 'invalid': invalid}
        if args.action == 'validate':
            value = data.get('scene') if isinstance(data.get('scene'), dict) else engine.workspace.load(reference)
            issues = schema.issues(value)
            return {'valid': not issues, 'issues': issues}
        if args.action == 'save':
            value = engine.workspace.load(reference)
            if args.scene_id:
                value['id'] = args.scene_id
            return {'scene': studio.save_scene(engine.workspace, value, args.overwrite)}
        if args.action == 'get':
            return {'scene': engine.workspace.load(reference)}
        if args.action == 'patch':
            from .authoring import apply_patch
            value = apply_patch(engine.workspace.load(reference), data.get('ops'))
            if args.scene_id:
                value['id'] = args.scene_id
            return {'scene': engine.workspace.load(value)}
        if args.action == 'capabilities':
            return await asyncio.to_thread(studio.require_tools, engine.workspace, args.profile)
        if args.action == 'import-asset':
            return await asyncio.to_thread(engine.import_asset, reference, args.asset_id, args.kind)
        if args.action == 'record-probe':
            from .recording_transport import probe_transport
            binary = studio.ffmpeg_path(engine.workspace)
            await asyncio.to_thread(probe_transport, binary)
            return {'available': True}
        if args.action == 'record-encode':
            return (await engine.wait(engine.record_encode(data)))['result']
        options = {'output': args.output, 'reduced': args.reduced, 'composition': args.composition}
        if args.action == 'preview':
            job = engine.preview(reference, **options)
        elif args.action == 'frame':
            job = engine.frame(reference, data.get('times_ms', args.times), **options)
        elif args.action == 'export':
            job = engine.export(reference, profile=args.profile, **options)
        elif args.action == 'inspect':
            job = engine.inspect(reference)
        else:
            raise ValueError('Unsupported operation')
        result = await engine.wait(job)
        return result.get('result', result)
    finally:
        await engine.close()


def main():
    args = parser().parse_args()
    if args.browser:
        os.environ['ZEROCOLLAB_MOTION_BROWSER'] = str(Path(args.browser).expanduser().resolve())
    if args.action == 'mcp':
        from .mcp_server import serve
        serve(args.workspace, ffmpeg=args.ffmpeg, asset_dir=args.asset_dir)
        return
    try:
        result = asyncio.run(execute(args))
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, default=str))
    except KeyboardInterrupt:
        raise SystemExit(130)
    except Exception as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()

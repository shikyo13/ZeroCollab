"""Optional stdio MCP adapter over the same standalone engine used by the CLI."""
import argparse
import asyncio
import os
from pathlib import Path
from contextlib import asynccontextmanager
from typing import Any
from mcp.server.fastmcp import FastMCP
from .engine import Engine
from . import scene as schema, studio


def create_server(workspace, *, ffmpeg=None, asset_dir=None):
    engine = Engine(workspace, ffmpeg=ffmpeg, asset_dir=asset_dir)

    @asynccontextmanager
    async def lifespan(server):
        try:
            yield {}
        finally:
            await engine.close()

    server = FastMCP('ZeroCollab Motion', lifespan=lifespan)

    @server.tool()
    async def motion_list() -> dict[str, Any]:
        """List bundled templates and scenes in this tool's workspace."""
        found, invalid = studio.catalogue(engine.workspace)
        return {'scenes': [studio.summary(ref, source, value) for ref, (source, value) in found.items()], 'invalid': invalid}

    @server.tool()
    async def motion_validate(scene: dict[str, Any]) -> dict[str, Any]:
        """Validate a data-only scene without starting a browser or encoder."""
        issues = schema.issues(scene)
        return {'valid': not issues, 'issues': issues}

    @server.tool()
    async def motion_save(scene: dict[str, Any], overwrite: bool = False) -> dict[str, Any]:
        """Save validated scene JSON in the tool workspace; never change bundled templates."""
        return {'scene': studio.save_scene(engine.workspace, engine.workspace.load(scene), overwrite)}

    @server.tool()
    async def motion_import_asset(path: str, asset_id: str, kind: str = 'image') -> dict[str, Any]:
        """Register an explicit existing local file and return its scene asset record."""
        return await asyncio.to_thread(engine.import_asset, path, asset_id, kind)

    @server.tool()
    async def motion_get(scene: str) -> dict[str, Any]:
        """Read validated JSON for a bundled template, workspace scene or explicit local file."""
        return {'scene': engine.workspace.load(scene)}

    @server.tool()
    async def motion_patch(scene: dict[str, Any], ops: list[dict[str, Any]]) -> dict[str, Any]:
        """Apply JSON Patch to a copy and validate it; use motion_save to persist the result."""
        from .authoring import apply_patch
        return {'scene': engine.workspace.load(apply_patch(engine.workspace.load(scene), ops))}

    @server.tool()
    async def motion_preview(scene: str, output: str | None = None, composition: str | None = None,
                             reduced: bool = False) -> dict[str, Any]:
        """Queue a self-contained HTML preview; poll motion_job_status for the file."""
        return engine.preview(scene, output=output, composition=composition, reduced=reduced)

    @server.tool()
    async def motion_frame(scene: str, times_ms: list[float] | None = None, output: str | None = None,
                           composition: str | None = None, reduced: bool = False) -> dict[str, Any]:
        """Queue one to six PNG previews; poll motion_job_status for actual artifact paths."""
        return engine.frame(scene, times_ms, output=output, composition=composition, reduced=reduced)

    @server.tool()
    async def motion_export(scene: str, profile: str = 'webm', output: str | None = None,
                            composition: str | None = None, reduced: bool = False) -> dict[str, Any]:
        """Queue a frame-accurate video export; the engine owns browser, audio and encoder cleanup."""
        return engine.export(scene, profile=profile, output=output, composition=composition, reduced=reduced)

    @server.tool()
    async def motion_inspect(path: str) -> dict[str, Any]:
        """Queue measured video/audio inspection of an explicit local media file."""
        return engine.inspect(path)

    @server.tool()
    async def motion_capabilities(profile: str = 'webm') -> dict[str, Any]:
        """Probe this tool's own browser/encoder for the requested profile."""
        return await asyncio.to_thread(studio.require_tools, engine.workspace, profile)

    @server.tool()
    async def motion_record_encode(request: dict[str, Any]) -> dict[str, Any]:
        """Encode an explicit JPEG spool request (folder, samples, fps, duration_ms); no app/capture access."""
        return engine.record_encode(request)

    @server.tool()
    async def motion_job_status(job_id: str) -> dict[str, Any]:
        """Read a queued/running/completed job and its retained artifact result."""
        return engine.status(job_id)

    @server.tool()
    async def motion_job_cancel(job_id: str, generation: int) -> dict[str, Any]:
        """Cancel this instance's exact job generation; it joins owned workers before releasing admission."""
        return engine.cancel(job_id, generation)

    return server


def serve(workspace, *, ffmpeg=None, asset_dir=None):
    create_server(workspace, ffmpeg=ffmpeg, asset_dir=asset_dir).run(transport='stdio')


def main():
    parser = argparse.ArgumentParser(description='Standalone motion MCP server (stdio)')
    parser.add_argument('--workspace', default='.zerocollab-motion')
    parser.add_argument('--ffmpeg')
    parser.add_argument('--browser')
    parser.add_argument('--asset-dir')
    args = parser.parse_args()
    if args.browser:
        os.environ['ZEROCOLLAB_MOTION_BROWSER'] = str(Path(args.browser).expanduser().resolve())
    serve(args.workspace, ffmpeg=args.ffmpeg, asset_dir=args.asset_dir)


if __name__ == '__main__':
    main()

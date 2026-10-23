# ZeroCollab Motion

An independent Python tool for scene authoring, previews, frame rendering, video/audio export, media inspection and browser-recording encoding. It requires no ZeroCollab app, room, UI token or agent runtime. The CLI and stdio MCP server share one engine and job lifecycle.

## Install

Install this directory in a separate Python 3.11+ environment:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
```

Rendering uses an installed Chromium browser (Edge/Chrome/Chromium); exporting and inspecting use FFmpeg on PATH or an explicit `--ffmpeg <executable>`. They are discovered by this tool, never through the app's executable or profile. No dependency or model is downloaded automatically. The wheel includes the compiled renderer, scene templates, starter audio, fonts and licenses; Node is only needed to rebuild renderer source.

## CLI

```powershell
zerocollab-motion list --workspace D:\Media\demo
zerocollab-motion validate scene.json
zerocollab-motion save scene.json --workspace D:\Media\demo
zerocollab-motion preview project:my-scene --workspace D:\Media\demo
zerocollab-motion frame scene.json --at-ms 0 --at-ms 500
zerocollab-motion export scene.json --profile webm --ffmpeg C:\Tools\ffmpeg.exe
zerocollab-motion inspect D:\Media\clip.webm --ffmpeg C:\Tools\ffmpeg.exe
zerocollab-motion import-asset D:\Media\image.png --asset-id hero --kind image
```

Commands emit JSON containing actual artifact paths, or a JSON error on stderr and a failing exit code. `--output` selects an output root; each job gets a unique child directory. The default workspace is `.zerocollab-motion`; saved scenes live in `scenes/`, outputs in `outputs/`. `bundled:<id>`, `project:<id>` and explicit scene-file paths are accepted. `get` returns scene JSON. `patch --request request.json` accepts `{ "scene": "path-or-reference", "ops": [...] }` using JSON Patch and returns a validated copy. `save --scene-id new-id` copies a scene; overwriting requires `--overwrite`.

`import-asset` returns a scene asset record with a file reference and SHA-256; the source stays in place. Its explicit registry belongs to the tool workspace. Old `/uploads/` scene references require `--asset-dir`. For CLI request bodies, `--request -` reads stdin. The tool does not read an app file registry or room credentials. Link finished files through the app's ordinary attachment/file-reference features.

Local voice synthesis remains optional. Configure `<workspace>/voices/registry.json` with explicit worker Python/model paths; see [audio reference](Docs/motion-audio.md). Without those dependencies, silent scenes and supplied audio files remain usable; voice scenes return a tool error.

## MCP

Run `zerocollab-motion-mcp --workspace D:\Media\demo --ffmpeg C:\Tools\ffmpeg.exe`, or `zerocollab-motion mcp` with the same options. Configure that executable and argv in any stdio MCP client. No network daemon is required. If the client restricts PATH/Windows environment discovery, pass `--browser <absolute-Chromium-executable>` (or configure `ZEROCOLLAB_MOTION_BROWSER` in its server environment).

Tools cover list/get/validate/patch/save/import, HTML previews, PNG frames, export, inspect, capabilities, recording encode and job status/cancel. Render/export/inspect/recording tools return a job id and integer generation. Poll `motion_job_status`; cancel with the exact id/generation via `motion_job_cancel`. The engine releases admission only after owned workers are joined. Jobs are scoped to that server instance; output files persist after it exits. Shut down the client normally to join outstanding work.

## Optional desktop recording adapter

The app retains live project-browser capture and input privacy. It invokes this CLI only when recording is requested. Configure the app's local TOML with an explicit argv list, for example:

```toml
[tools.motion]
command = ["C:\\Tools\\Motion\\.venv\\Scripts\\zerocollab-motion.exe", "--ffmpeg", "C:\\Tools\\ffmpeg.exe"]
```

Otherwise the app looks for `zerocollab-motion` on PATH (or a separately installed Python package). A missing tool disables recording encoding with an actionable error; core app startup, chat, board and team do not depend on it. `record_cancel` cancels capture/encoding. Closing/resizing the browser cancels its take. `record-probe` verifies transport support. `record-encode --request request.json` accepts `{ "folder": "absolute-spool-directory", "samples": [[0, "00000000.jpg"]], "fps": 30, "duration_ms": 100 }`. The capture adapter owns final publication; the encoder returns `clip.partial.webm` plus `record-result.json`.

## Source and checks

`src/zerocollab_motion/kit/` owns renderer TypeScript/JavaScript and bundled assets. Run `npm ci` and `npm run build:core` here to rebuild the core. The optional `adapters/zerocollab/` snapshot adapter consumes an explicit product checkout: `npm run build:product-adapter -- <checkout>`. It only builds the included scripted product demo bundle; ordinary tool installation and rendering have no app-source dependency.

Run `python -m unittest discover -s tests`. Set `ZEROCOLLAB_TEST_FFMPEG` to an explicit encoder to include native audio tests. Earlier design/room-Motion contracts under `Docs/` are historical references; this README and the CLI/MCP schemas describe the extracted interface.

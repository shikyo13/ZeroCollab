# Chat file references (no copies)

Task 67dbfd14, Zero #14949: "I don't want to create copies... I want to paste the path to the file." Code: `local_files.py` (parsing and the registry) and `app.py` (the routes). Frontend's composer (27b553aa) and harness's agent line (36b5063c) build on this.

A browser never reveals a dragged file's real path, so a reference starts from a path the user pastes. A drop in a browser offers "paste its path" or an explicit "Upload a copy". There is no silent upload.

## Accepted references

Any one of these, from a paste or Explorer's "Copy as path":

| Form | Example |
|-|-|
| Windows path, quoted or not | `"C:\Users\Zero\Downloads\m2-vp9-res_1080p.mp4"` |
| Forward slashes | `C:/Users/Zero/Downloads/m2-vp9-res_1080p.mp4` |
| UNC path | `\\server\share\videos\demo.mp4` |
| file URI | `file:///C:/Users/Zero/Downloads/m2-vp9-res_1080p.mp4` or `file://server/share/demo.mp4` |

Refused, with the reason in `error`: relative paths, folders, missing files, device paths (`\\.\` and `\\?\`), and reserved device names (`CON`, `NUL`, `COM1` and so on). Several pasted paths, one per line, mean one request each.

## Register: POST /api/files/reference

Body `{"ref": "<one reference>"}`, with the same authentication as other `/api/` calls. Nothing is copied.

200 returns the attachment to put on the message:

```json
{"id": "3f9c0a1b2c3d4e5f", "name": "m2-vp9-res_1080p.mp4",
 "url": "/api/files/ref/3f9c0a1b2c3d4e5f/m2-vp9-res_1080p.mp4",
 "kind": "video", "size": 5182024, "type": "video/mp4",
 "path": "C:\\Users\\Zero\\Downloads\\m2-vp9-res_1080p.mp4",
 "ref": "file:///C:/Users/Zero/Downloads/m2-vp9-res_1080p.mp4"}
```

- `id` is stable per file: the same file pasted again gets the same id (case-insensitive on Windows).
- `kind` follows the upload rules: `image` (png, jpg, jpeg, gif, webp, bmp, svg) embeds, `video` (webm, mp4) plays inline, and `file` is a download link.
- `url` ends with the real file name, so suffix-based renderers keep working. The path itself never appears in a URL.

400 returns `{"error": "<why>"}`, for example "No file at ..." or "... is not a regular file".

## Serve: GET /api/files/ref/{id}/{name}

The original, read in place and read-only, with the same headers as `/uploads/` (sandbox CSP, nosniff).
- Images and video are served inline, and video supports Range for seeking.
- Anything else downloads under its real name.
- `X-File-Changed: 1` means the file's size or modified time differ from when it was pasted. It's still served, as it is now.
- 404 `{"error": "The original file is gone: <path>"}` when it was moved or deleted. Nothing is re-copied.
- In the web room, `<video src>` and `<img src>` authenticate with the session cookie. The desktop app adds its usual `token` query parameter.

The registry is `data/file_references.json`. It keeps up to 5000 references, forgetting the oldest, and never touches the files.

## Message shape

Put the returned object in the message's `attachments`, with `url`, `name`, `kind`, `size`, `type` and `path`. The text may also carry `[name](url)`.

Agents need the original path. Harness's line should come from the registry, not from the client's `path`: `local_files.Registry.lookup(id)` on the `id` in the url returns the path and whether it changed. The line reads "File: name is at <original path> (video|file, size)".

## Explicit uploads

`POST /api/upload` is unchanged for callers, but it now stores by content: `/uploads/<first 16 hex of sha256><ext>`. The same bytes uploaded again reuse the stored copy (`"deduplicated": true`), and the response also returns `sha256`. Existing uploads keep their names; nothing is deleted.

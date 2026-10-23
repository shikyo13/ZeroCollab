"""Video analysis for agents (Zero #14935, task 53e691c7): measure a video so an agent can judge its own
export against a reference instead of guessing from a few stills.

Consolidates harness e940694 (probe, cuts and soft transitions, shots, silences, sheet, frames, compare,
identity cache) with QA measures (EBU R128 loudness, dense sampling over a chosen range so small icon motion is
visible, Zero #14956) and motion-design's (frame-difference motion, holds, motion chart, per-shot sheet).
Scene scores find cuts; frame differences measure movement, because camera pushes and UI reflow barely move
the scene score but show clearly as frame differences.

Everything runs through the room's FFmpeg (motion_studio.ffmpeg_path) and its sibling ffprobe; nothing is
downloaded. Callers pass a resolved local file inside the room's upload or export folders and run these
functions off the event loop. Results are cached by file identity in a bounded LRU.
"""
import re
import subprocess
import threading
import time
from itertools import pairwise
from collections import OrderedDict
from pathlib import Path

# Scene-change scores (FFmpeg's select 'scene', 0..1): above HARD is a cut, above SOFT a visible transition.
CUT_HARD, CUT_SOFT = 0.3, 0.08
MOTION_WIDTH = 160            # scene scores are measured on a small copy: cheap, and ranks cuts the same way
# Movement: mean absolute difference of consecutive grey samples (0-255) at SAMPLE_FPS on a SAMPLE_W x SAMPLE_H copy.
SAMPLE_FPS, SAMPLE_W, SAMPLE_H = 10, 160, 90
MOVING = 2.0                  # a sample above this counts as movement
HOLD_S = 1.0                  # a still stretch at least this long counts as a hold
CHART_W, CHART_H, CHART_PAD = 1200, 220, 30
CHART_TICK_S, CHART_TICK_LEN = 5, 8   # a time tick every 5 s along the axis, so humps match sheet times
CHART_BG, CHART_GRID, CHART_CUT, CHART_LINE = (16, 20, 23), (58, 74, 82), (240, 198, 116), (84, 217, 237)
SHOT_SHEET_MAX = 30           # the per-shot sheet shows at most this many shots (the first ones)
SHEET_FRAMES, SHEET_COLUMNS, SHEET_WIDTH = 8, 4, 480
DENSE_MAX_TILES, DENSE_COLUMNS, DENSE_WIDTH = 48, 6, 320   # dense sheet: up to 8 rows of 6 small tiles
DENSE_MAX_FPS = 10
SILENCE_DB, SILENCE_SECONDS = -45, 0.5
MAX_SECONDS = 600             # longer videos are measured over their first ten minutes
TIMEOUT = 180
POLL_S = 0.2                  # a cancellable FFmpeg checks its stop event this often; bounds the time to stop
MAX_TIMES = 6
CACHE_LIMIT = 32              # analyses kept in memory; least recently used is dropped first
# Analysis is background work: on Windows FFmpeg runs below normal priority so the desktop stays responsive
# (Zero #15103). The constant only exists on Windows; elsewhere 0 leaves the default.
WORKER_PRIORITY = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
_cache = OrderedDict()
_cache_lock = threading.Lock()

PTS_TIME = re.compile(r'pts_time:([0-9.]+)')
SCENE_SCORE = re.compile(r'lavfi\.scene_score=([0-9.]+)')
YAVG = re.compile(r'lavfi\.signalstats\.YAVG=([0-9.]+)')
EBU = {'integrated_lufs': re.compile(r'I:\s+(-?[0-9.]+) LUFS'), 'loudness_range_lu': re.compile(r'LRA:\s+([0-9.]+) LU')}


def ffprobe_path(ffmpeg):
    """ffprobe ships beside ffmpeg in every build the room accepts; never a separate PATH lookup."""
    ffmpeg = Path(ffmpeg)
    # 'ffmpeg.EXE' from shutil.which keeps its suffix; a bare 'ffmpeg' path on Windows still needs '.exe'.
    for name in (ffmpeg.name.lower().replace('ffmpeg', 'ffprobe'), 'ffprobe.exe', 'ffprobe'):
        probe_exe = ffmpeg.with_name(name)
        if probe_exe.is_file():
            return str(probe_exe)
    raise FileNotFoundError(f'ffprobe was not found next to {ffmpeg}')


class Cancelled(Exception):
    """The caller's stop event was set: the running FFmpeg was killed and reaped, and nothing was cached."""


def _check(stop):
    """Raise Cancelled once the caller's stop event is set; free when there is no event."""
    if stop is not None and stop.is_set():
        raise Cancelled()


def _communicate(args, stop=None, data=None, text=True):
    """Run one FFmpeg or ffprobe at WORKER_PRIORITY and return the finished CompletedProcess.

    This thread owns the process: with a stop event (threading.Event) it checks the event before every wait
    (at most POLL_S apart) and kills and reaps the process once it is set, so no other thread ever needs the
    handle. A stop that lands as the process finishes still raises Cancelled. TIMEOUT applies either way.
    Any other error also kills the process, as subprocess.run does."""
    _check(stop)  # never start new work after a cancel
    decoding = {'text': True, 'encoding': 'utf-8', 'errors': 'replace'} if text else {}
    with subprocess.Popen(args, stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=WORKER_PRIORITY,
                          **decoding) as proc:
        deadline = time.monotonic() + TIMEOUT
        pending = data
        try:
            while True:
                cancelled = stop is not None and stop.is_set()
                if cancelled or time.monotonic() >= deadline:
                    proc.kill()
                    proc.communicate()  # reap it and close the pipes before reporting
                    if cancelled:
                        raise Cancelled()
                    raise subprocess.TimeoutExpired(args, TIMEOUT)
                wait = max(0.0, deadline - time.monotonic())
                try:
                    out, err = proc.communicate(pending, timeout=wait if stop is None else min(POLL_S, wait))
                except subprocess.TimeoutExpired:
                    pending = None  # input is sent once; a retry that passes it again raises ValueError
                    continue
                _check(stop)  # finished, but the job was cancelled meanwhile: its output must not be used
                return subprocess.CompletedProcess(args, proc.returncode, out, err)
        except BaseException:
            if proc.poll() is None:
                proc.kill()
            raise


def _run(args, stop=None):
    done = _communicate(args, stop)
    if done.returncode:
        raise RuntimeError((done.stderr.strip().splitlines() or ['FFmpeg failed'])[-1][:300])
    return done


def _ffmpeg(ffmpeg, *args, stop=None):
    return _run([str(ffmpeg), '-hide_banner', '-nostats', *args], stop)


def probe(ffmpeg, path, *, stop=None):
    out = _run([ffprobe_path(ffmpeg), '-v', 'error', '-show_entries',
                'format=duration,bit_rate,format_name:stream=codec_type,codec_name,width,height,avg_frame_rate',
                '-of', 'default=noprint_wrappers=0', str(path)], stop).stdout
    sections, current = [], None
    for line in out.splitlines():
        if line in ('[STREAM]', '[FORMAT]'):
            current = {}
        elif line in ('[/STREAM]', '[/FORMAT]'):
            sections.append(current)
        elif current is not None and '=' in line:
            key, value = line.split('=', 1)
            current[key] = value
    fmt = next((s for s in sections if 'format_name' in s), {})
    video = next((s for s in sections if s.get('codec_type') == 'video'), {})
    audio = next((s for s in sections if s.get('codec_type') == 'audio'), None)
    num, _, den = video.get('avg_frame_rate', '0/1').partition('/')
    return {'container': fmt.get('format_name'), 'duration': round(float(fmt.get('duration') or 0), 3),
            'bitrate_kbps': round(int(fmt.get('bit_rate') or 0) / 1000), 'video_codec': video.get('codec_name'),
            'width': int(video.get('width') or 0), 'height': int(video.get('height') or 0),
            'fps': round(int(num) / int(den), 3) if num.isdigit() and den.isdigit() and int(den) else None,
            'audio_codec': audio.get('codec_name') if audio else None}


def scene_scores(ffmpeg, path, seconds, *, stop=None):
    """[(time, score)] for every frame, in one decode of a small copy."""
    log = _ffmpeg(ffmpeg, '-t', str(seconds), '-i', str(path), '-an', '-vf',
                  f"scale={MOTION_WIDTH}:-2,select='gte(scene,0)',metadata=print:key=lavfi.scene_score",
                  '-f', 'null', '-', stop=stop).stderr
    return [(float(t), float(s)) for t, s in zip(PTS_TIME.findall(log), SCENE_SCORE.findall(log))]


def runs(flags):
    """Lengths of every run of consecutive True values, in order."""
    found, current = [], 0
    for flag in [*flags, False]:
        if flag:
            current += 1
        elif current:
            found.append(current)
            current = 0
    return found


def pacing(scores, duration):
    """Cuts, shot lengths and per-second motion from one list of scene scores."""
    hard = [round(t, 2) for t, s in scores if s > CUT_HARD]
    soft = [round(t, 2) for t, s in scores if CUT_SOFT < s <= CUT_HARD]
    bounds = [0.0, *hard, duration]
    shots = [round(b - a, 2) for a, b in zip(bounds, bounds[1:]) if b > a]
    per_second = {}
    for t, s in scores:
        per_second.setdefault(int(t), []).append(s)
    motion = [round(sum(v) / len(v), 4) for _, v in sorted(per_second.items())]
    return {'cuts': hard, 'transitions': soft, 'shots': len(shots),
            'mean_shot_s': round(sum(shots) / len(shots), 2) if shots else 0.0,
            'longest_shot_s': max(shots, default=0.0),
            'cuts_per_minute': round(len(hard) * 60 / duration, 1) if duration else 0.0,
            'scene_per_second': motion}


def frame_motion(ffmpeg, path, seconds, *, stop=None):
    """Per-sample movement: mean absolute grey difference between consecutive SAMPLE_FPS samples."""
    log = _ffmpeg(ffmpeg, '-t', str(seconds), '-i', str(path), '-an', '-vf',
                  f'fps={SAMPLE_FPS},scale={SAMPLE_W}:{SAMPLE_H},format=gray,tblend=all_mode=difference,'
                  'signalstats,metadata=print:key=lavfi.signalstats.YAVG', '-f', 'null', '-', stop=stop).stderr
    return [float(v) for v in YAVG.findall(log)]


def motion_profile(energy):
    """Share of samples moving, mean movement, and still holds of at least HOLD_S (to 1/SAMPLE_FPS s)."""
    holds = [n / SAMPLE_FPS for n in runs(e <= MOVING for e in energy) if n / SAMPLE_FPS >= HOLD_S]
    return {'mean_motion': round(sum(energy) / len(energy), 2) if energy else 0.0,
            'moving_share': round(sum(e > MOVING for e in energy) / len(energy), 3) if energy else 0.0,
            'holds': len(holds), 'longest_hold_s': round(max(holds, default=0.0), 1)}


def loudness(ffmpeg, path, seconds, *, stop=None):
    """Mean/peak volume, silences and EBU R128 integrated loudness and range, in one audio pass."""
    log = _ffmpeg(ffmpeg, '-t', str(seconds), '-i', str(path), '-vn', '-af',
                  f'silencedetect=n={SILENCE_DB}dB:d={SILENCE_SECONDS},volumedetect,ebur128=framelog=quiet',
                  '-f', 'null', '-', stop=stop).stderr
    mean, peak = (re.search(rf'{key}: (-?[0-9.]+) dB', log) for key in ('mean_volume', 'max_volume'))
    starts = [float(t) for t in re.findall(r'silence_start: (-?[0-9.]+)', log)]
    ends = [float(t) for t in re.findall(r'silence_end: ([0-9.]+)', log)]
    summary = log[log.rfind('Summary:'):] if 'Summary:' in log else ''
    result = {'mean_db': float(mean.group(1)) if mean else None, 'max_db': float(peak.group(1)) if peak else None,
              'silences': [[round(max(s, 0), 2), round(e, 2)] for s, e in zip(starts, ends)]}
    result.update({k: float(m.group(1)) for k, rx in EBU.items() if (m := rx.search(summary))})
    return result


def _tile(ffmpeg, path, out, rate, columns, width, tiles, start=0.0, length=None, stop=None):
    rows = -(-tiles // columns)
    window = ['-ss', str(start), *(['-t', str(length)] if length else [])]
    _ffmpeg(ffmpeg, '-loglevel', 'error', '-y', *window, '-i', str(path), '-an', '-vf',
            f'fps={rate},scale={width}:-2,tile={columns}x{rows}', '-frames:v', '1', str(out), stop=stop)
    return out


def contact_sheet(ffmpeg, path, duration, out, *, stop=None):
    """SHEET_FRAMES evenly spaced frames across the whole video."""
    return _tile(ffmpeg, path, out, f'{SHEET_FRAMES}/{max(duration, 0.1)}', SHEET_COLUMNS, SHEET_WIDTH, SHEET_FRAMES,
                 stop=stop)


def dense_sheet(ffmpeg, path, start, end, fps, out, *, stop=None):
    """Every 1/fps seconds over [start, end), for small motion (icon wiggles, typing). At most DENSE_MAX_TILES."""
    length = end - start
    tiles = round(length * fps)
    if not 0 < fps <= DENSE_MAX_FPS:
        raise ValueError(f'fps must be above 0 and at most {DENSE_MAX_FPS}')
    if length <= 0 or tiles > DENSE_MAX_TILES:
        raise ValueError(f'choose a range that gives 1 to {DENSE_MAX_TILES} frames (got {tiles}); narrow it or lower fps')
    return _tile(ffmpeg, path, out, fps, DENSE_COLUMNS, DENSE_WIDTH, tiles, start, length, stop)


def frames(ffmpeg, path, times, out_dir, stem, *, stop=None):
    """One PNG per requested time (seconds), named <stem>-<n>.png in out_dir."""
    if not 1 <= len(times) <= MAX_TIMES:
        raise ValueError(f'ask for 1 to {MAX_TIMES} frame times (t_ms)')
    outs = []
    for n, t in enumerate(times):
        out = Path(out_dir) / f'{stem}-{n}.png'
        _ffmpeg(ffmpeg, '-loglevel', 'error', '-y', '-ss', str(float(t)), '-i', str(path), '-frames:v', '1', str(out),
                stop=stop)
        outs.append(out)
    return outs


def _encode_rgb(ffmpeg, pixels, width, height, out, stop=None):
    done = _communicate([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'rawvideo',
                         '-pix_fmt', 'rgb24', '-s', f'{width}x{height}', '-i', '-', str(out)],
                        stop, bytes(pixels), text=False)
    if done.returncode:
        raise RuntimeError(done.stderr.decode('utf-8', 'replace').strip()[-300:] or 'FFmpeg failed')
    return out


def motion_chart(ffmpeg, energy, cuts, duration, out, *, stop=None):
    """Movement over time with cuts marked: holds read flat, camera moves as humps, cuts as yellow lines."""
    pixels = bytearray(bytes(CHART_BG) * CHART_W * CHART_H)
    top = max(max(energy, default=0.0), MOVING * 2)
    span_x, span_y = CHART_W - 2 * CHART_PAD, CHART_H - 2 * CHART_PAD

    def x_of(t):
        return CHART_PAD + round(span_x * min(t, duration) / duration) if duration else CHART_PAD

    def y_of(v):
        return CHART_H - CHART_PAD - round(span_y * min(v, top) / top)

    def put(x, y, colour):
        if 0 <= x < CHART_W and 0 <= y < CHART_H:
            start = (y * CHART_W + x) * 3
            pixels[start:start + 3] = bytes(colour)

    for x in range(CHART_PAD, CHART_W - CHART_PAD):
        put(x, y_of(MOVING), CHART_GRID)
    for tick in range(0, int(duration) + 1, CHART_TICK_S):
        for y in range(CHART_H - CHART_PAD, CHART_H - CHART_PAD + CHART_TICK_LEN):
            put(x_of(tick), y, CHART_GRID)
    for t in cuts:
        for y in range(CHART_PAD, CHART_H - CHART_PAD):
            put(x_of(t), y, CHART_CUT)
    points = [(x_of((i + 1) / SAMPLE_FPS), y_of(v)) for i, v in enumerate(energy)]
    for (x0, y0), (x1, y1) in pairwise(points):
        for x in range(x0, x1 + 1):
            for y in range(min(y0, y1), max(y0, y1) + 1):
                put(x, y, CHART_LINE)
    return _encode_rgb(ffmpeg, pixels, CHART_W, CHART_H, out, stop)


def shot_sheet(ffmpeg, path, cuts, duration, fps, out, *, stop=None):
    """The middle frame of each shot (first SHOT_SHEET_MAX), tiled in order; returns the shot ranges shown.
    When the returned list is shorter than analyze()['shots'], the sheet was truncated; say so to the reader."""
    shots = [(a, b) for a, b in pairwise([0.0, *cuts, duration]) if b > a][:SHOT_SHEET_MAX]
    picks = '+'.join(f'eq(n,{round((a + b) / 2 * fps)})' for a, b in shots)
    rows = -(-len(shots) // SHEET_COLUMNS)
    _ffmpeg(ffmpeg, '-loglevel', 'error', '-y', '-i', str(path), '-an', '-vf',
            f"select='{picks}',scale={SHEET_WIDTH}:-2,tile={SHEET_COLUMNS}x{rows}", '-fps_mode', 'vfr',
            '-frames:v', '1', str(out), stop=stop)
    return [[round(a, 2), round(b, 2)] for a, b in shots]


def analyze(ffmpeg, path, *, stop=None):
    """probe + pacing + loudness for one file, cached by (path, size, mtime) in a bounded LRU. Setting stop
    raises Cancelled from the running pass; only a complete analysis is ever cached."""
    _check(stop)  # a cancelled job gets no result, not even a cached one
    path = Path(path)
    stat = path.stat()
    key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    with _cache_lock:  # inspect runs analyses on two worker threads; the LRU's lookup and reorder must be atomic
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return cached
    info = probe(ffmpeg, path, stop=stop)
    seconds = min(info['duration'], MAX_SECONDS) or MAX_SECONDS
    energy = frame_motion(ffmpeg, path, seconds, stop=stop)
    result = {'probe': info, 'measured_s': seconds, **pacing(scene_scores(ffmpeg, path, seconds, stop=stop), seconds),
              **motion_profile(energy), 'frame_motion': [round(e, 2) for e in energy]}
    result['audio'] = loudness(ffmpeg, path, seconds, stop=stop) if info['audio_codec'] else None
    with _cache_lock:
        _check(stop)  # under the lock: a job cancelled at any point before publication caches nothing
        _cache[key] = result
        while len(_cache) > CACHE_LIMIT:
            _cache.popitem(last=False)
    return result


def headline(analysis):
    """The numbers worth comparing, without the per-second series."""
    audio = analysis['audio'] or {}
    p = analysis['probe']
    return {'resolution': f"{p['width']}x{p['height']}", 'fps': p['fps'], 'duration': p['duration'],
            'cuts_per_minute': analysis['cuts_per_minute'], 'mean_shot_s': analysis['mean_shot_s'],
            'transitions': len(analysis['transitions']), 'mean_motion': analysis['mean_motion'],
            'moving_share': analysis['moving_share'], 'holds': analysis['holds'],
            'longest_hold_s': analysis['longest_hold_s'],
            'audio': p['audio_codec'] is not None, 'integrated_lufs': audio.get('integrated_lufs')}


def compare(ours, reference):
    """Two analyses side by side, for 'how far from the bar are we'."""
    return {'ours': headline(ours), 'reference': headline(reference)}

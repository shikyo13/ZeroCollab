"""Fixed same-authority JPEG/timestamp transport for browser recordings."""
import subprocess
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

PROBE_TIMEOUT_SECONDS = 15
TRANSPORT_CACHE_SIZE = 8
SOURCE_TIMEBASE_FPS = 1000
MS_PER_SECOND = 1000
PROBE_SIZE = 32
PROBE_GAP_MS = 150
PROBE_DURATION_MS = 250
PROBE_FPS = 60
PROBE_FRAMES = 15
PROBE_HELD_FRAME = 7
PROBE_CHANGED_FRAME = 11
PROBE_DOMINANT_CHANNEL_MIN = 180
PROBE_OTHER_CHANNEL_MAX = 70
MAX_PROBE_ERROR_CHARS = 400
_transport_cache = OrderedDict()
_cache_lock = threading.Lock()

def concat_file(folder, samples, duration_ms):
    selected = [(max(0., t), name) for t, name in samples if t < duration_ms]
    if not selected:
        raise ValueError('Recording has no usable frames')
    # First pixels fill the lead-in; subsequent source gaps hold the prior JPEG.
    selected[0] = (0., selected[0][1])
    lines = ['ffconcat version 1.0']
    for i, (at, name) in enumerate(selected):
        until = selected[i + 1][0] if i + 1 < len(selected) else duration_ms
        if until <= at:
            continue
        lines += [f"file '{name}'", f'option framerate {SOURCE_TIMEBASE_FPS}', f'duration {(until-at)/MS_PER_SECOND:.9f}']
    lines += [f"file '{selected[-1][1]}'", f'option framerate {SOURCE_TIMEBASE_FPS}']
    path = folder / 'input.ffconcat'
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return path


def encode_args(binary, folder, fps, frames, destination):
    from zerocollab_motion.encode import output_args
    return [binary, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-f', 'concat', '-safe', '0', '-i', str(folder / 'input.ffconcat'),
            '-vf', f'fps={fps}', '-frames:v', str(frames),
            *output_args('webm'), '-progress', str(folder / 'progress.txt'), str(destination)]


def encoded_frames(folder):
    counts = [int(line[6:]) for line in (folder / 'progress.txt').read_text().splitlines()
              if line.startswith('frame=') and line[6:].strip().isdigit()]
    return counts[-1] if counts else 0


def probe_transport(binary):
    """Tiny exact-binary JPEG/concat/fps capability check, run off the room loop."""
    stamp = (binary, Path(binary).stat().st_mtime_ns)
    with _cache_lock:
        if stamp in _transport_cache:
            _transport_cache.move_to_end(stamp)
            return
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix='zc-record-probe-') as tmp:
        folder = Path(tmp)
        Image.new('RGB', (PROBE_SIZE, PROBE_SIZE), 'red').save(folder / '00000000.jpg')
        Image.new('RGB', (PROBE_SIZE, PROBE_SIZE), 'blue').save(folder / '00000001.jpg')
        concat_file(folder, [(0, '00000000.jpg'), (PROBE_GAP_MS, '00000001.jpg')], PROBE_DURATION_MS)
        result = subprocess.run(encode_args(binary, folder, PROBE_FPS, PROBE_FRAMES, folder / 'clip.webm'),
                                capture_output=True, timeout=PROBE_TIMEOUT_SECONDS)
        if result.returncode or encoded_frames(folder) != PROBE_FRAMES:
            raise ValueError('Configured FFmpeg cannot record JPEG/concat/fps WebM: ' +
                             result.stderr.decode(errors='replace')[-MAX_PROBE_ERROR_CHARS:])
        result = subprocess.run([binary, '-hide_banner', '-loglevel', 'error', '-i',
                                 str(folder / 'clip.webm'), str(folder / 'decoded-%02d.png')],
                                capture_output=True, timeout=PROBE_TIMEOUT_SECONDS)
        if result.returncode:
            raise ValueError('Configured FFmpeg cannot decode the recording transport')
        pictures = sorted(folder.glob('decoded-*.png'))
        if len(pictures) != PROBE_FRAMES:
            raise ValueError('Recording transport did not preserve output frame count')
        for index, channel in [(0, 0), (PROBE_HELD_FRAME, 0), (PROBE_CHANGED_FRAME, 2)]:
            with Image.open(pictures[index]) as picture:
                color = picture.convert('RGB').getpixel((PROBE_SIZE//2, PROBE_SIZE//2))
                if color[channel] < PROBE_DOMINANT_CHANNEL_MIN or color[2 if channel == 0 else 0] > PROBE_OTHER_CHANNEL_MAX:
                    raise ValueError('Recording transport did not preserve source gap holds')
    with _cache_lock:
        _transport_cache[stamp] = None
        while len(_transport_cache) > TRANSPORT_CACHE_SIZE:
            _transport_cache.popitem(last=False)

"""Bounded audio decode through the Exporter's exact encoder authority.

The caller resolves and authorizes original paths and owns the job directory.
No input is copied, no executable is discovered, and media cannot fetch URLs.
"""
from collections import OrderedDict
import hashlib
from pathlib import Path
import tempfile
import wave

from motion_audio_mix import CHANNELS, SAMPLE_BYTES, SAMPLE_RATE, MAX_INPUT_BYTES
from motion_audio_process import run_owned
from motion_render import file_io

INPUT_FORMATS = ('wav', 'mp3', 'flac', 'ogg', 'aac', 'aiff', 'mov', 'matroska')
DECODE_THREADS = 2
PROBE_SECONDS = 0.2
PROBE_TIMEOUT_SECONDS = 15
MAX_PROBE_ENTRIES = 8
_probes = OrderedDict()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def decode_args(ffmpeg, source, destination, max_bytes):
    """Fixed PCM transport. A time cap bounds even compressed or looping inputs."""
    samples = max_bytes // (CHANNELS * SAMPLE_BYTES)
    if samples < 1:
        raise ValueError('No decoded audio budget remains')
    return [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-protocol_whitelist', 'file', '-format_whitelist', ','.join(INPUT_FORMATS),
            '-threads', str(DECODE_THREADS), '-i', str(source), '-map', '0:a:0',
            # Decode one extra sample so an over-budget source is refused,
            # rather than silently shortened and then looped as a different song.
            '-vn', '-sn', '-dn', '-t', format((samples + 1) / SAMPLE_RATE, '.9f'),
            '-threads', str(DECODE_THREADS), '-filter_threads', str(DECODE_THREADS),
            # Raw PCM is sample-relative. Codec delay/container timestamps must
            # not consume the output time budget (Opus/WebM lost 312 samples).
            '-af', 'asetpts=N/SR/TB',
            '-ac', str(CHANNELS), '-ar', str(SAMPLE_RATE), '-c:a', 'pcm_f32le',
            '-f', 'f32le', str(destination)]


async def decode(ffmpeg, source, sha256, destination, *, max_bytes=MAX_INPUT_BYTES,
                 check_cancel=lambda: None):
    """Hash the original before and after decode; atomically publish derived PCM."""
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not 0 < max_bytes <= MAX_INPUT_BYTES:
        raise ValueError('Invalid decoded audio budget')
    check_cancel()
    if await file_io(digest, source) != sha256:
        raise ValueError('Audio source changed from its asset hash')
    with tempfile.NamedTemporaryFile(prefix='.decode-', suffix='.pcm.part',
                                     dir=destination.parent, delete=False) as temporary:
        partial = Path(temporary.name)

    def check():
        check_cancel()
        if partial.stat().st_size > max_bytes:
            raise ValueError('Decoded audio exceeds its disk budget')

    try:
        code = await run_owned(decode_args(ffmpeg, source, partial, max_bytes), check)
        if code:
            raise ValueError('The configured encoder could not decode this audio stream')
        size = partial.stat().st_size
        if not size or size % (CHANNELS * SAMPLE_BYTES) or size > max_bytes:
            raise ValueError('The decoder returned invalid or oversized PCM')
        if await file_io(digest, source) != sha256:
            raise ValueError('Audio source changed while it was decoded')
        check_cancel()
        await file_io(partial.replace, destination)
        return {'samples': size // (CHANNELS * SAMPLE_BYTES), 'bytes': size,
                'sample_rate': SAMPLE_RATE, 'channels': CHANNELS}
    finally:
        await file_io(lambda: partial.unlink(missing_ok=True))


def _fixture(path):
    # A short non-silent square wave verifies decode and mux, not just inventories.
    frames = round(PROBE_SECONDS * SAMPLE_RATE)
    block = b''.join((4000 if i % 48 < 24 else -4000).to_bytes(2, 'little', signed=True)
                     * CHANNELS for i in range(frames))
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(block)


async def probe(ffmpeg, profile_name, work, check_cancel=lambda: None):
    """Tiny native audio round trip on the already-authorized binary, cached by hash."""
    from motion_encode import profile

    selected = profile(profile_name)
    if not selected.audio:
        raise ValueError(f'{profile_name} cannot export authored sound')
    key = (await file_io(digest, ffmpeg), selected.audio, selected.muxer)
    check_cancel()
    if key in _probes:
        _probes.move_to_end(key)
        return dict(_probes[key])
    root = Path(work).resolve()
    with tempfile.TemporaryDirectory(prefix='.audio-probe-', dir=root) as folder:
        source = Path(folder) / 'fixture.wav'
        encoded = Path(folder) / ('encoded.' + selected.extension)
        pcm = Path(folder) / 'decoded.pcm'
        await file_io(_fixture, source)
        args = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-protocol_whitelist', 'file', '-format_whitelist', 'wav', '-i', str(source),
                '-vn', '-c:a', selected.audio, '-threads', str(DECODE_THREADS),
                '-f', selected.muxer, str(encoded)]
        if await run_owned(args, check_cancel, timeout=PROBE_TIMEOUT_SECONDS):
            raise ValueError('The configured encoder failed its audio encode/mux probe')
        receipt = await decode(ffmpeg, encoded, await file_io(digest, encoded), pcm,
                               max_bytes=round(PROBE_SECONDS * SAMPLE_RATE) * CHANNELS * SAMPLE_BYTES,
                               check_cancel=check_cancel)
        if receipt['samples'] != round(PROBE_SECONDS * SAMPLE_RATE):
            raise ValueError('The audio round trip changed the sample duration')
        if not any(await file_io(pcm.read_bytes)):
            raise ValueError('The audio round trip returned silence')
        result = {'encoder_sha256': key[0], 'audio_encoder': selected.audio,
                  'muxer': selected.muxer, 'audio_probed': True, **receipt}
    check_cancel()
    _probes[key] = result
    while len(_probes) > MAX_PROBE_ENTRIES:
        _probes.popitem(last=False)
    return dict(result)

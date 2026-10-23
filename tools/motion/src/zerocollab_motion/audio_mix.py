"""Blockwise PCM mixing on the composition clock.

Inputs are already resolved, hash-checked stereo float32 PCM at SAMPLE_RATE.
The preparation owner supplies shared-core volume samples and verified voice
intervals. This module performs no discovery, synthesis, downloads or easing.
NumPy is loaded only in the isolated audio worker, never on the room loop.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
from fractions import Fraction
import logging
import math
import os
from pathlib import Path
import tempfile
import wave

SAMPLE_RATE = 48000
CHANNELS = 2
SAMPLE_BYTES = 4
BLOCK_SAMPLES = 4096
MAX_SOUNDS = 64
MAX_INPUT_BYTES = 256 * 1024 * 1024
MAX_SECONDS = 3600
MAX_VOLUME_FRAMES = MAX_SECONDS * 240 + 1
HEADROOM = 0.5
PEAK_LIMIT = 0.95
PCM_SCALE = 32767
DEFAULT_DUCK_GAIN = 0.25
DEFAULT_ATTACK_MS = 80
DEFAULT_RELEASE_MS = 250
MS_PER_SECOND = 1000
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Sound:
    id: str
    pcm: Path
    start: int  # Composition frames.
    duration: int
    offset: int = 0
    trim: int = 0
    rate: float = 1
    volume: tuple = (1,)
    duck: dict = field(default_factory=dict)
    loop: bool = False
    # Intervals are global composition samples, not inferred from the clip box.
    voice_intervals: tuple = ()


def sample_boundary(frame, fps):
    """First sample at or beyond a frame boundary; never round a clip early."""
    return math.ceil(Fraction(frame * SAMPLE_RATE * fps['den'], fps['num']))


def validate(sounds, fps, duration):
    if not 1 <= len(sounds) <= MAX_SOUNDS:
        raise ValueError(f'Audio mix supports 1 to {MAX_SOUNDS} simultaneous source records')
    if len({s.id for s in sounds}) != len(sounds):
        raise ValueError('Audio source ids must be unique')
    seconds = Fraction(duration * fps['den'], fps['num'])
    if not 0 < seconds <= MAX_SECONDS:
        raise ValueError('Audio duration exceeds the mix budget')
    total_bytes = 0
    gain_bytes = 0
    for sound in sounds:
        size = sound.pcm.stat().st_size
        total_bytes += size
        gain_bytes += len(sound.volume) * SAMPLE_BYTES
        if not size or size % (CHANNELS * SAMPLE_BYTES):
            raise ValueError(f'{sound.id}: invalid stereo float32 PCM')
        if not (sound.start >= 0 and sound.duration > 0 and sound.start + sound.duration <= duration):
            raise ValueError(f'{sound.id}: clip outside composition')
        if not (sound.trim >= 0 and sound.offset >= 0 and 0.1 <= sound.rate <= 8):
            raise ValueError(f'{sound.id}: invalid source time transform')
        if len(sound.volume) not in (1, sound.duration + 1) or len(sound.volume) > MAX_VOLUME_FRAMES:
            raise ValueError(f'{sound.id}: expected one gain or one shared-core sample per frame boundary')
        if not all(isinstance(v, (int, float)) and math.isfinite(v) and 0 <= v <= 1 for v in sound.volume):
            raise ValueError(f'{sound.id}: invalid volume envelope')
    if total_bytes + gain_bytes > MAX_INPUT_BYTES:
        raise ValueError('Decoded audio exceeds the mix memory/disk budget')


def duck_envelope(samples, sound, by_id, np):
    gain = np.ones(len(samples), dtype=np.float64)
    if not sound.duck:
        return gain
    floor = sound.duck.get('gain', DEFAULT_DUCK_GAIN)
    attack = sound.duck.get('attack_ms', DEFAULT_ATTACK_MS) * SAMPLE_RATE / MS_PER_SECOND
    release = sound.duck.get('release_ms', DEFAULT_RELEASE_MS) * SAMPLE_RATE / MS_PER_SECOND
    for name in sound.duck['against']:
        voice = by_id.get(name)
        if voice is None:
            raise ValueError(f'{sound.id}: unresolved duck target {name}')
        for begin, end in voice.voice_intervals:
            active = (samples >= begin) & (samples < end)
            gain[active] = np.minimum(gain[active], floor)
            if attack:
                leading = (samples >= begin - attack) & (samples < begin)
                ramp = 1 - (1 - floor) * (samples[leading] - begin + attack) / attack
                gain[leading] = np.minimum(gain[leading], ramp)
            if release:
                trailing = (samples >= end) & (samples < end + release)
                ramp = floor + (1 - floor) * (samples[trailing] - end) / release
                gain[trailing] = np.minimum(gain[trailing], ramp)
    return gain


def source_block(pcm, sound, local_frames, fps, np):
    position = (sound.trim + (local_frames + sound.offset) * sound.rate) * SAMPLE_RATE * fps['den'] / fps['num']
    if sound.loop:
        position %= len(pcm)
    left = np.floor(position).astype(np.int64)
    valid = (left >= 0) & (left < len(pcm))
    left = np.clip(left, 0, len(pcm) - 1)
    right = (left + 1) % len(pcm) if sound.loop else np.minimum(left + 1, len(pcm) - 1)
    weight = (position - np.floor(position))[:, None]
    block = pcm[left] * (1 - weight) + pcm[right] * weight
    block[~valid] = 0
    return block


def _write_mix(sounds, fps, duration, output, check_cancel):
    """Write exact-duration 48k stereo WAV in bounded blocks, with local curves.

    Rate changes follow the source-time contract (and change pitch). TTS's own
    synthesis speed must be consumed once by preparation, then rate=1 here.
    The caller owns the output path; mix() publishes it atomically on success.
    """
    import numpy as np

    validate(sounds, fps, duration)
    count = sample_boundary(duration, fps)
    by_id = {s.id: s for s in sounds}
    curves = {s.id: np.asarray(s.volume, dtype=np.float32) for s in sounds}
    peak_before_limit = 0.0
    with ExitStack() as resources:
        sources = {}
        for sound in sounds:
            pcm = np.memmap(sound.pcm, dtype='<f4', mode='r').reshape(-1, CHANNELS)
            resources.callback(pcm._mmap.close)
            sources[sound.id] = pcm
        out = resources.enter_context(wave.open(str(output), 'wb'))
        out.setnchannels(CHANNELS)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        for begin in range(0, count, BLOCK_SAMPLES):
            check_cancel()
            indices = np.arange(begin, min(begin + BLOCK_SAMPLES, count), dtype=np.float64)
            block = np.zeros((len(indices), CHANNELS), dtype=np.float64)
            for sound in sounds:
                low, high = sample_boundary(sound.start, fps), sample_boundary(sound.start + sound.duration, fps)
                active = (indices >= low) & (indices < high)
                if not active.any():
                    continue
                chosen = indices[active]
                local = chosen * fps['num'] / (SAMPLE_RATE * fps['den']) - sound.start
                curve = curves[sound.id]
                frame = np.clip(local, 0, len(curve) - 1)
                left = np.floor(frame).astype(np.int64)
                right = np.minimum(left + 1, len(curve) - 1)
                gains = curve[left] + (curve[right] - curve[left]) * (frame - left)
                gains *= duck_envelope(chosen, sound, by_id, np)
                block[active] += source_block(sources[sound.id], sound, local, fps, np) * gains[:, None]
            if not np.isfinite(block).all():
                raise ValueError('Decoded source contains non-finite PCM')
            block *= HEADROOM
            peak_before_limit = max(peak_before_limit, float(np.abs(block).max()))
            # A bounded safety limiter, not a claim of final loudness mastering.
            np.clip(block, -PEAK_LIMIT, PEAK_LIMIT, out=block)
            out.writeframesraw((block * PCM_SCALE).astype('<i2').tobytes())
    LOG.info('Mixed %d sources into %d samples; peak before limiter %.3f', len(sounds), count, peak_before_limit)
    return {'sample_rate': SAMPLE_RATE, 'samples': count, 'channels': CHANNELS,
            'peak_before_limit': peak_before_limit, 'headroom': HEADROOM}


def mix(sounds, fps, duration, output, check_cancel=lambda: None):
    output = Path(output)
    with tempfile.NamedTemporaryFile(prefix='.mix-', suffix='.wav.part', dir=output.parent, delete=False) as temporary:
        partial = Path(temporary.name)
    try:
        receipt = _write_mix(sounds, fps, duration, partial, check_cancel)
        check_cancel()
        os.replace(partial, output)
        return receipt
    finally:
        partial.unlink(missing_ok=True)


def worker_main():
    """Trusted job request transport for the isolated NumPy worker."""
    import json
    import sys

    request, output, receipt = map(Path, sys.argv[1:])
    try:
        with request.open('rb') as stream:
            raw = stream.read(MAX_INPUT_BYTES + 1)
        if len(raw) > MAX_INPUT_BYTES:
            raise ValueError('Mix request exceeds its budget')
        plan = json.loads(raw)
        sounds = [Sound(**{**row, 'pcm': Path(row['pcm'])}) for row in plan['sounds']]
        result = mix(sounds, plan['fps'], plan['duration'], output)
    except Exception as error:
        LOG.exception('Offline PCM mixing failed')
        receipt.write_text(json.dumps({'error': str(error)}), 'utf-8')
        return 1
    receipt.write_text(json.dumps(result), 'utf-8')
    return 0


if __name__ == '__main__':
    import sys
    sys.exit(worker_main())

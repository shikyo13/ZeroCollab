"""Original procedural starter sounds, dedicated to CC0-1.0. No sampled material."""
from array import array
import hashlib
import json
import math
from pathlib import Path
import wave

ROOT = Path(__file__).resolve().parent
RATE = 48000


def write(name, seconds, sample):
    pcm = array('h')
    for index in range(round(seconds * RATE)):
        value = round(max(-1, min(1, sample(index / RATE))) * 32767)
        pcm.extend((value, value))
    path = ROOT / name
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(pcm.tobytes())
    return {'file': name, 'seconds': seconds, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def bed(t):
    chords = ((220, 261.6256, 329.6276), (174.6141, 220, 261.6256),
              (130.8128, 164.8138, 195.9977), (195.9977, 246.9417, 293.6648))
    chord = chords[min(3, int(t / 2))]
    phase = t % 2
    pad = sum(math.sin(math.tau * note * t) for note in chord) / 3
    pad *= min(1, phase / .08, (2 - phase) / .12) * .12
    beat = t % .5
    note = chord[int(t * 2) % 3] * 2
    pluck = math.sin(math.tau * note * beat) * math.exp(-beat * 15) * .08
    return (pad + pluck) * min(1, t / .02, (8 - t) / .02)


if __name__ == '__main__':
    rows = [write('calm-bed.wav', 8, bed),
            write('soft-click.wav', .09, lambda t: .32 * math.sin(math.tau * 1200 * t)
                  * math.exp(-t * 90) * min(1, t / .002)),
            write('confirm.wav', .35, lambda t: .18 * (math.sin(math.tau * 660 * t)
                  + .5 * math.sin(math.tau * 880 * t)) * math.exp(-t * 14) * min(1, t / .004))]
    (ROOT / 'starter-assets.json').write_text(json.dumps({
        'license': 'CC0-1.0', 'source': 'make-starters.py',
        'description': 'Original synthesized tones; no third-party recordings or samples.',
        'assets': rows}, indent=2) + '\n', encoding='utf-8')

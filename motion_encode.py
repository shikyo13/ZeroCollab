"""Explicit S3 output profiles. Discovery stays in motion_studio.encoder_check().

The authority passes inventories from its exact configured binary here; profiles
are available only after a tiny native encode/decode check on that same binary.
No downloads, PATH searches, hardware guesses or user-supplied FFmpeg options.
"""
import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Profile:
    extension: str
    mime: str
    encoder: str
    muxer: str
    flags: tuple
    alpha: bool = False
    filters: tuple = ()
    sequence: bool = False
    audio: str = ''


PROFILES = {
    'webm': Profile('webm', 'video/webm', 'libvpx-vp9', 'webm',
                    ('-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', '30', '-deadline', 'good',
                     '-cpu-used', '4', '-row-mt', '1', '-tile-columns', '2'), audio='libopus'),
    'vp9-alpha': Profile('webm', 'video/webm', 'libvpx-vp9', 'webm',
                        ('-pix_fmt', 'yuva420p', '-b:v', '0', '-crf', '30', '-deadline', 'good',
                         '-cpu-used', '4', '-auto-alt-ref', '0'), alpha=True, audio='libopus'),
    'av1': Profile('webm', 'video/webm', 'libaom-av1', 'webm',
                   ('-pix_fmt', 'yuv420p', '-b:v', '0', '-crf', '35', '-cpu-used', '8'), audio='libopus'),
    'prores4444': Profile('mov', 'video/quicktime', 'prores_ks', 'mov',
                          ('-pix_fmt', 'yuva444p10le', '-profile:v', '4', '-alpha_bits', '16'),
                          alpha=True, audio='pcm_s16le'),
    'gif': Profile('gif', 'image/gif', 'gif', 'gif',
                   ('-filter_complex', '[0:v]split[v][p];[p]palettegen=stats_mode=single:reserve_transparent=1[pal];'
                    '[v][pal]paletteuse=new=1', '-loop', '0'),
                   alpha=True, filters=('split', 'palettegen', 'paletteuse')),
    'png': Profile('png', 'image/png', 'png', 'image2', ('-pix_fmt', 'rgba', '-update', '1'), alpha=True),
    'png-sequence': Profile('zip', 'application/zip', 'png', 'image2',
                            ('-pix_fmt', 'rgba', '-start_number', '0'), alpha=True, sequence=True),
}


def profile(name):
    if not isinstance(name, str) or name not in PROFILES:
        raise ValueError('Unknown export format; choose ' + ', '.join(PROFILES))
    return PROFILES[name]


def output_args(name, *, audio=False):
    selected = profile(name)
    if audio and not selected.audio:
        raise ValueError(f'{name} does not support audio')
    return ['-c:v', selected.encoder, *selected.flags, '-threads', '2',
            *(['-c:a', selected.audio] if audio else ['-an']), '-f', selected.muxer]


def inventory(listing):
    return {parts[1] for line in listing.splitlines() if len(parts := line.split()) >= 2}


def missing(name, inventories):
    selected = profile(name)
    needs = [('encoders', selected.encoder), ('decoders', 'png'),
             ('demuxers', 'image2pipe'), ('muxers', selected.muxer)]
    needs.extend(('filters', name) for name in selected.filters)
    return [f'{category}: {value}' for category, value in needs
            if value not in inventories.get(category, set())]


def probe_profiles(path, inventories):
    """Reports truthful encode/decode capability, including alpha, per profile.

    A successful FFmpeg listing alone does not prove that the build's pixel
    conversion, filters and muxer work together. The 32x32 fixture has transparent
    and opaque pixels; alpha profiles must preserve both in the decoded image.
    """
    from PIL import Image

    image = Image.new('RGBA', (32, 32), (220, 40, 20, 255))
    image.paste((0, 0, 0, 0), (0, 0, 16, 32))
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    png = buffer.getvalue()
    results = {}
    with tempfile.TemporaryDirectory(prefix='zerocollab-codec-probe-') as folder:
        root = Path(folder).resolve()
        # Validate the exact owned cleanup boundary before TemporaryDirectory
        # recursively removes it. No input can choose this directory.
        if root.parent != Path(tempfile.gettempdir()).resolve():
            raise RuntimeError('unexpected codec probe directory')
        for name, selected in PROFILES.items():
            absent = missing(name, inventories)
            result = {'available': False, 'extension': selected.extension, 'mime': selected.mime,
                      'codec': selected.encoder, 'alpha': selected.alpha,
                      'audio_encoder': selected.audio if selected.audio in inventories.get('encoders', set()) else '',
                      'audio_probed': False}
            if absent:
                result['reason'] = 'Missing ' + ', '.join(absent)
                results[name] = result
                continue
            target = root / (name + ('-%06d.png' if selected.sequence else '.' + selected.extension))
            try:
                encoded = subprocess.run([path, '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'image2pipe', '-framerate', '30', '-c:v', 'png', '-i', 'pipe:0',
                    '-frames:v', '1', *output_args(name), str(target)],
                    input=png, capture_output=True, timeout=15)
                if encoded.returncode:
                    raise ValueError(encoded.stderr.decode(errors='replace')[-400:] or 'encode failed')
                if selected.sequence:
                    target = root / (name + '-000000.png')
                # FFmpeg's native VP9 decoder drops the alpha side stream. Use
                # libvpx for the round trip that verifies the advertised alpha.
                decoder = ['-c:v', 'libvpx-vp9'] if name == 'vp9-alpha' else []
                decoded = subprocess.run([path, '-hide_banner', '-loglevel', 'error', *decoder,
                    '-i', str(target), '-frames:v', '1', '-pix_fmt', 'rgba', '-c:v', 'png',
                    '-f', 'image2pipe', 'pipe:1'], capture_output=True, timeout=15)
                if decoded.returncode:
                    raise ValueError(decoded.stderr.decode(errors='replace')[-400:] or 'decode failed')
                with Image.open(io.BytesIO(decoded.stdout)) as frame:
                    if frame.size != (32, 32):
                        raise ValueError('codec changed the frame dimensions')
                    if selected.alpha:
                        low, high = frame.convert('RGBA').getchannel('A').getextrema()
                        if low != 0 or high != 255:
                            raise ValueError('codec did not preserve transparent and opaque pixels')
                result['available'] = True
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                result['reason'] = 'Native format probe failed: ' + str(exc)[:400]
            results[name] = result
    return results

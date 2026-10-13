"""Prepare a shared-core sound plan once and keep narration pinned through export."""
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import asdict, dataclass, replace
from fractions import Fraction
import json
import math
from pathlib import Path

from motion_audio_cache import DEFAULT_VOICE, Voice, VoiceCache
from motion_audio_media import decode, digest, probe
from motion_audio_mix import MAX_INPUT_BYTES, MAX_SOUNDS, SAMPLE_RATE, Sound, sample_boundary
from motion_audio_process import run_owned
from motion_render import file_io

MAX_REGISTRY_BYTES = 1024 * 1024
MAX_VOICES = 16
PLAN_VERSION = 1
MIX_WORKER = Path(__file__).with_name('motion_audio_mix.py')
SOUND_TYPES = frozenset(('audio', 'video', 'voiceover'))


def contains_sound(composition):
    """Cheap structural hint only; the core decides actual audible visibility."""
    def visit(layers):
        return any(not layer.get('hidden') and
                   (layer['type'] in SOUND_TYPES and not (layer['type'] == 'video' and layer.get('muted')) or
                    layer['type'] == 'group' and visit(layer.get('layers', ()))) for layer in layers)
    return any(visit(clip['layers']) for track in composition['tracks']
               if not track.get('hidden') and not track.get('muted') for clip in track['clips'])


def require_worker(runtime):
    """Cheap admission check; never start Chromium or probe an engine here."""
    try:
        cache = getattr(runtime, '_motion_voice_cache', None)
        voices = cache.registry if cache is not None else registry(
            Path(runtime.data).resolve() / 'motion' / 'voices' / 'registry.json')
        worker = voices[DEFAULT_VOICE]
        if not all(path.is_file() for path in (worker.python, worker.model, worker.aux)):
            raise ValueError('Worker files are missing')
    except (AttributeError, KeyError, OSError, TypeError, ValueError):
        raise ValueError('Offline audio worker is not installed; provision motion/voices/registry.json and kokoro:af-heart') from None


def registry(path):
    """Read a host-installed registry, never a scene's model, interpreter or URL."""
    with Path(path).open('rb') as stream:
        raw = stream.read(MAX_REGISTRY_BYTES + 1)
    if len(raw) > MAX_REGISTRY_BYTES:
        raise ValueError('Voice registry exceeds its budget')
    rows = json.loads(raw)
    if not isinstance(rows, dict) or not 1 <= len(rows) <= MAX_VOICES:
        raise ValueError('Voice registry has an invalid number of entries')
    voices = {}
    for name, row in rows.items():
        if row['engine'] not in ('kokoro', 'piper'):
            raise ValueError(f'Unsupported local voice engine for {name}')
        fields = dict(row)
        for key in ('python', 'model', 'aux'):
            fields[key] = Path(fields[key]).resolve()
            if not fields[key].is_file():
                raise ValueError(f'Local voice {name} is missing {key}')
        voices[name] = Voice(**fields)
    return voices


async def voice_cache(runtime):
    existing = getattr(runtime, '_motion_voice_cache', None)
    if existing is not None:
        return existing
    root = Path(runtime.data).resolve() / 'motion' / 'voices'
    try:
        voices = await file_io(registry, root / 'registry.json')
    except FileNotFoundError:
        raise ValueError('Offline audio worker is not installed: motion/voices/registry.json is missing') from None
    # No await between checking and assigning after load. Concurrent callers
    # share one cache and one synthesis lock rather than racing voice workers.
    if getattr(runtime, '_motion_voice_cache', None) is None:
        runtime._motion_voice_cache = VoiceCache(root / 'cache', voices)
    return runtime._motion_voice_cache


def voice_intervals(metadata, clip, fps):
    """Map engine word/phrase intervals to the mix's exact global sample clock."""
    origin = Fraction((clip['from'] - clip.get('offset', 0)) * SAMPLE_RATE * fps['den'], fps['num'])
    low, high = sample_boundary(clip['from'], fps), sample_boundary(clip['from'] + clip['duration'], fps)
    words = metadata.get('words') or [{'start_sample': 0, 'end_sample': metadata['samples']}]
    out = []
    for word in words:
        begin = max(low, math.ceil(origin + Fraction(word['start_sample'] * SAMPLE_RATE, metadata['sample_rate'])))
        end = min(high, math.ceil(origin + Fraction(word['end_sample'] * SAMPLE_RATE, metadata['sample_rate'])))
        if end > begin:
            out.append((begin, end))
    return tuple(out)


@dataclass(frozen=True)
class Prepared:
    wav: Path | None
    captions: dict
    receipt: dict


async def mix_worker(python, sounds, fps, duration, work, check_cancel):
    request, receipt, output = (work / name for name in ('mix-request.json', 'mix-receipt.json', 'soundtrack.wav'))
    rows = [{**asdict(sound), 'pcm': str(sound.pcm)} for sound in sounds]
    await file_io(request.write_text, json.dumps({'sounds': rows, 'fps': fps, 'duration': duration}), 'utf-8')
    code = await run_owned([str(python), str(MIX_WORKER), str(request), str(output), str(receipt)], check_cancel)
    metadata = json.loads(await file_io(receipt.read_text, 'utf-8'))
    if code:
        raise ValueError(metadata.get('error') or 'Offline audio mixing failed')
    check_cancel()
    return output, metadata


async def page_plan(page, job, work, check_cancel):
    """Ask the exact picture core once, using the owned render worker."""
    from motion_render import ChromiumWorker, FrameClock, RenderIdentity

    fps = job['fps']
    clock = FrameClock(fps['num'], fps['den'])
    identity_hash = await file_io(digest, page)
    identity = RenderIdentity(job['scene_id'], identity_hash, identity_hash, identity_hash,
                              job['width'], job['height'], clock)
    async with ChromiumWorker(page, work / 'audio-plan-profile', identity, check_cancel,
                              frame_seek=True, transparent=job.get('transparent', False)) as worker:
        data = await worker.evaluate("({plan:window.__audioPlan(),assets:JSON.parse(document.getElementById('zm-options').textContent).frame_input.assets.map(({id,ref,sha256})=>({id,ref,sha256}))})")
    return data['plan'], {asset['id']: asset for asset in data['assets']}


@asynccontextmanager
async def prepare(plan, assets, fps, duration, ffmpeg, work, cache, resolve_asset,
                  *, profile_name='webm', check_cancel=lambda: None):
    """Lease generated voices, decode originals, then mix in a bounded CPU worker.

    resolve_asset is the same registry/uploads/kit authority as the picture.
    plan and resolved asset hashes come from the generated, hash-pinned page.
    """
    if plan.get('version') != PLAN_VERSION or not isinstance(plan.get('sounds'), list):
        raise ValueError('Renderer returned an unsupported audio plan')
    if not plan['sounds']:
        yield Prepared(None, {}, {})
        return
    if len(plan['sounds']) > MAX_SOUNDS:
        raise ValueError('Audio plan exceeds the source budget')
    worker_voice = cache.registry.get(DEFAULT_VOICE)
    if worker_voice is None:
        raise ValueError(f'Offline audio worker {DEFAULT_VOICE} is unavailable')
    capability = await probe(ffmpeg, profile_name, work, check_cancel)
    sources, captions, decoded = [], {}, {}
    remaining = MAX_INPUT_BYTES
    async with AsyncExitStack() as leases:
        for index, item in enumerate(plan['sounds']):
            check_cancel()
            clip = item['clip']
            if item['type'] == 'voiceover':
                source, metadata = await leases.enter_async_context(cache.acquire(
                    item['text'], item['voice'], item['rate'], check_cancel))
                captions[item['id']] = {key: metadata[key] for key in
                                       ('text', 'spoken_text', 'sample_rate', 'words', 'timing_quality')}
                identity = metadata['wav_sha256']
                intervals, rate = voice_intervals(metadata, clip, fps), 1
            else:
                asset = assets[item['asset']]
                source = resolve_asset(asset['ref'])
                identity = asset['sha256']
                intervals, rate = (), item['rate']
            key = (str(source), identity)
            if key not in decoded:
                pcm = work / f'audio-{index}.pcm'
                facts = await decode(ffmpeg, source, identity, pcm, max_bytes=remaining, check_cancel=check_cancel)
                remaining -= facts['bytes']
                decoded[key] = pcm, facts
            pcm, facts = decoded[key]
            if item.get('role') == 'voice' and not intervals:
                # A prerecorded voice has no word timings. Duck under its
                # actual available source span, without claiming word alignment.
                source_frames = Fraction(facts['samples'] * fps['num'], SAMPLE_RATE * fps['den'])
                available = (source_frames - item['trim']) / Fraction(str(rate)) - clip.get('offset', 0)
                end = min(clip['duration'], max(0, available))
                if end:
                    intervals = ((sample_boundary(clip['from'], fps), sample_boundary(clip['from'] + end, fps)),)
            sources.append(Sound(id=item['id'], pcm=pcm, start=clip['from'], duration=clip['duration'],
                                 offset=clip.get('offset', 0), trim=item['trim'], rate=rate,
                                 volume=tuple(item['volume']), duck=item.get('duck') or {},
                                 loop=item['loop'], voice_intervals=intervals))
        # Schema targets may be valid but hidden/muted in this plan. Such a
        # voice makes no sound and must neither duck music nor fail the mix.
        audible = {sound.id for sound in sources if any(sound.volume)}
        for index, sound in enumerate(sources):
            if sound.duck:
                targets = [name for name in sound.duck['against'] if name in audible]
                sources[index] = replace(sound, duck={**sound.duck, 'against': targets} if targets else {})
        wav, receipt = await mix_worker(worker_voice.python, sources, fps, duration, work, check_cancel)
        yield Prepared(wav, captions, {'capability': capability, **receipt})

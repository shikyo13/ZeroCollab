"""Bounded immutable narration cache. A lease pins a WAV until export finishes."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import tempfile

from motion_audio_process import run_owned
from motion_render import file_io

DEFAULT_VOICE = 'kokoro:af-heart'
MAX_CACHE_BYTES = 256 * 1024 * 1024
MAX_CACHE_ENTRIES = 64
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
MAX_ENTRY_BYTES = 32 * 1024 * 1024
KEY = re.compile(r'^[0-9a-f]{64}$')
WORKER = Path(__file__).with_name('motion_audio_voice.py')
LOG = logging.getLogger(__name__)


def digest(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


@dataclass(frozen=True)
class Voice:
    python: Path
    engine: str
    engine_voice: str
    engine_version: str
    model: Path
    model_sha256: str
    aux: Path
    aux_sha256: str

    def request(self, id, text, rate):
        return {'voice': id, 'text': text, 'rate': rate, 'engine': self.engine,
                'engine_voice': self.engine_voice, 'model': str(self.model), 'model_sha256': self.model_sha256,
                ('voices' if self.engine == 'kokoro' else 'config'): str(self.aux),
                ('voices_sha256' if self.engine == 'kokoro' else 'config_sha256'): self.aux_sha256}


class VoiceCache:
    def __init__(self, root, registry):
        self.root = Path(root).resolve()
        self.registry = registry  # Host-created Voice records, never scene author data.
        self.pins = {}
        self.lock = asyncio.Lock()  # Bounded model memory: one synthesis at a time.

    def _remove(self, path):
        # Recursive cleanup is confined to this cache and never touches models or originals.
        target = path.resolve()
        if target.parent != self.root or not (KEY.fullmatch(target.name) or target.name.startswith('.voice-job-')):
            raise ValueError('Unexpected voice cache cleanup boundary')
        shutil.rmtree(target)

    def _read(self, key):
        entry = self.root / key
        receipt = entry / 'receipt.json'
        wav = entry / 'voice.wav'
        if not receipt.is_file() or not wav.is_file() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
            return None
        try:
            metadata = json.loads(receipt.read_text(encoding='utf-8'))
            if metadata['cache_key'] != key or digest(wav) != metadata['wav_sha256']:
                return None
            os.utime(entry, None)
            return wav, metadata
        except (OSError, ValueError, KeyError):
            return None

    def _evict(self, reserve=0):
        entries = [p for p in self.root.iterdir() if KEY.fullmatch(p.name) and p.is_dir()]
        sizes = {p: sum(f.stat().st_size for f in p.iterdir() if f.is_file()) for p in entries}
        for entry in sorted(entries, key=lambda p: p.stat().st_mtime):
            if sum(sizes.values()) + reserve <= MAX_CACHE_BYTES and len(sizes) + bool(reserve) <= MAX_CACHE_ENTRIES:
                return
            if entry.name not in self.pins:
                self._remove(entry)
                del sizes[entry]
        if sum(sizes.values()) + reserve > MAX_CACHE_BYTES or len(sizes) + bool(reserve) > MAX_CACHE_ENTRIES:
            raise ValueError('Active narration exceeds the voice cache budget')

    async def _create(self, key, request, voice, check_cancel):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = Path(tempfile.mkdtemp(prefix='.voice-job-', dir=self.root))
        try:
            request_path, wav, receipt = (temporary / name for name in ('request.json', 'voice.wav', 'receipt.json'))
            await file_io(request_path.write_text, json.dumps(request), 'utf-8')
            check_cancel()
            code = await run_owned([str(voice.python), str(WORKER), str(request_path), str(wav), str(receipt)], check_cancel)
            if not receipt.exists() or receipt.stat().st_size > MAX_RECEIPT_BYTES:
                raise ValueError('Voice worker did not return a bounded receipt')
            metadata = json.loads(await file_io(receipt.read_text, 'utf-8'))
            if code != 0:
                raise ValueError(metadata.get('error', 'Local voice synthesis failed'))
            if metadata['engine_version'] != voice.engine_version:
                raise ValueError('Voice engine version changed from its registry pin')
            if wav.stat().st_size + receipt.stat().st_size > MAX_ENTRY_BYTES:
                raise ValueError('Narration cache entry exceeds its disk budget')
            metadata.update(cache_key=key, wav_sha256=await file_io(digest, wav))
            await file_io(receipt.write_text, json.dumps(metadata), 'utf-8')
            await file_io(request_path.unlink)
            check_cancel()
            final = self.root / key
            if final.exists():
                # Invalid cache entry was found under this key; replace only our own directory.
                await file_io(self._remove, final)
            await file_io(os.rename, temporary, final)
            LOG.info('Prepared narration cache entry %s (%d samples)', key, metadata['samples'])
        finally:
            if temporary.exists():
                await file_io(self._remove, temporary)

    @asynccontextmanager
    async def acquire(self, text, voice_id=DEFAULT_VOICE, rate=1, check_cancel=lambda: None):
        voice = self.registry.get(voice_id)
        if voice is None:
            raise ValueError(f'Local voice {voice_id} is unavailable; select an installed voice')
        request = voice.request(voice_id, text, rate)
        identity = {**request, 'engine_version': voice.engine_version,
                    'adapter_sha256': await file_io(digest, WORKER), 'python_sha256': await file_io(digest, voice.python)}
        key = hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        async with self.lock:
            self.pins[key] = self.pins.get(key, 0) + 1
            try:
                check_cancel()
                cached = await file_io(self._read, key)
                if cached is None:
                    if self.pins[key] > 1:
                        raise ValueError('An active narration cache entry changed; refusing to replace it')
                    self.root.mkdir(parents=True, exist_ok=True)
                    await file_io(self._evict, MAX_ENTRY_BYTES)
                    await self._create(key, request, voice, check_cancel)
                    cached = await file_io(self._read, key)
                if cached is None:
                    raise ValueError('Narration cache verification failed')
                await file_io(self._evict)
            except BaseException:
                self.pins[key] -= 1
                if not self.pins[key]:
                    del self.pins[key]
                raise
        try:
            check_cancel()
            yield cached
        finally:
            # Pin bookkeeping must precede every await: another synthesis may
            # hold the lock when this export is cancelled. Never strand its pin.
            self.pins[key] -= 1
            if not self.pins[key]:
                del self.pins[key]
            # The acquire holding the lock already trims when preparation ends.
            # Do not delay cancellation behind that worker just for maintenance.
            if not self.lock.locked():
                async with self.lock:
                    try:
                        await file_io(self._evict)
                    except (OSError, ValueError):
                        LOG.warning('Could not trim narration cache on release', exc_info=True)

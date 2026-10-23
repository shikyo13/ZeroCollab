"""Offline voice worker entry point, run in a separate owned Python process.

The host creates the private request file from a pinned local voice registry.
Scene authors supply a registry id and text, never model paths or engine args.
This worker neither downloads models nor selects a replacement voice.
"""
import hashlib
import importlib.metadata
import json
import logging
from pathlib import Path
import re
import socket
import sys
import wave

MAX_TEXT = 4000
MAX_VOICE_SECONDS = 300
CPU_THREADS = 4
PCM_SCALE = 32767
WORD = re.compile(r"^[A-Za-z]+(?:[-'’][A-Za-z]+)*[.,!?;:]*$")
BRAND = re.compile(r'\bZeroCollab\b', re.IGNORECASE)
PUNCTUATION = set('.,!?;:_^$')
LOG = logging.getLogger(__name__)


def spoken_text(text):
    return BRAND.sub('Zero Collab', text)


def verified_file(path, expected):
    with Path(path).open('rb') as source:
        digest = hashlib.file_digest(source, 'sha256').hexdigest()
    if digest != expected:
        raise ValueError('Pinned voice model/config changed')
    return str(path)


def caption_mapping(text, phonemes, sample_rate):
    """Map simple lexical tokens only when the emitted boundaries account for all.

    Normalized numbers and ambiguous tokens use a phrase cue, without a word
    highlight. 'word' means engine phoneme boundaries mapped to lexical tokens,
    not an independent acoustic alignment measurement.
    """
    groups, current = [], []
    for item in phonemes:
        phone = item['phoneme']
        if phone.isspace() or phone in PUNCTUATION:
            if current:
                groups.append(current)
                current = []
        else:
            current.append(item)
    if current:
        groups.append(current)
    tokens = text.split()
    counts = [len(spoken_text(token).split()) for token in tokens]
    if groups and all(WORD.fullmatch(token) for token in tokens) and sum(counts) == len(groups):
        words, position = [], 0
        for token, count in zip(tokens, counts):
            mapped = groups[position:position + count]
            words.append({'text': token, 'start_sample': mapped[0][0]['start_sample'],
                          'end_sample': mapped[-1][-1]['end_sample']})
            position += count
        return words, 'word'
    if groups:
        return [{'text': text, 'start_sample': groups[0][0]['start_sample'],
                 'end_sample': groups[-1][-1]['end_sample']}], 'phrase'
    return [], 'none'


def kokoro(request, text):
    import onnxruntime as ort
    from kokoro_onnx import Kokoro

    options = ort.SessionOptions()
    options.intra_op_num_threads = CPU_THREADS
    options.inter_op_num_threads = 1
    session = ort.InferenceSession(verified_file(request['model'], request['model_sha256']),
                                   sess_options=options, providers=['CPUExecutionProvider'])
    engine = Kokoro.from_session(session, verified_file(request['voices'], request['voices_sha256']))
    if not engine.has_timings:
        raise ValueError('Kokoro model lacks phoneme durations required by this voice registry')
    audio, rate, timings = engine.create_timed(text, voice=request['engine_voice'], speed=request['rate'],
                                              lang='en-us', continuous=True)
    phones = [{'phoneme': t.phoneme, 'start_sample': round(t.start * rate),
               'end_sample': round(t.end * rate)} for t in timings]
    return audio, rate, phones, importlib.metadata.version('kokoro-onnx')


def piper(request, text):
    import numpy as np
    from piper import PiperVoice, SynthesisConfig

    model = verified_file(request['model'], request['model_sha256'])
    config = verified_file(request['config'], request['config_sha256'])
    engine = PiperVoice.load(model, config_path=config, use_cuda=False, include_alignments=True)
    blocks, phones, offset = [], [], 0
    rate = engine.config.sample_rate
    settings = SynthesisConfig(length_scale=1 / request['rate'], normalize_audio=False)
    for chunk in engine.synthesize(text, settings, include_alignments=True):
        blocks.append(chunk.audio_float_array)
        cursor = offset
        for item in chunk.phoneme_alignments or ():
            phones.append({'phoneme': item.phoneme, 'start_sample': cursor,
                           'end_sample': cursor + item.num_samples})
            cursor += item.num_samples
        offset += len(chunk.audio_float_array)
        if offset > MAX_VOICE_SECONDS * rate:
            raise ValueError('Narration exceeds the voice duration budget')
    return np.concatenate(blocks), rate, phones, importlib.metadata.version('piper-tts')


ENGINES = {'kokoro': kokoro, 'piper': piper}


def synthesize(request, output):
    import numpy as np

    text = request['text']
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise ValueError('Voice text is empty or exceeds the narration limit')
    if not 0.5 <= request['rate'] <= 2:
        raise ValueError('Voice rate must be between 0.5 and 2')
    spoken = spoken_text(text)
    audio, rate, phones, version = ENGINES[request['engine']](request, spoken)
    if len(audio) > MAX_VOICE_SECONDS * rate or not np.isfinite(audio).all():
        raise ValueError('Voice output exceeds its duration budget or contains invalid samples')
    with wave.open(str(output), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((np.clip(audio, -1, 1) * PCM_SCALE).astype('<i2').tobytes())
    words, quality = caption_mapping(text, phones, rate)
    return {'text': text, 'spoken_text': spoken, 'sample_rate': rate, 'samples': len(audio),
            'words': words, 'timing_quality': quality, 'phonemes': phones,
            'engine': request['engine'], 'engine_version': version, 'voice': request['voice'],
            'model_sha256': request['model_sha256'], 'peak': float(np.abs(audio).max()),
            'mapping': 'verified lexical boundary count' if quality == 'word' else 'phrase or unavailable'}


def refuse_network(*args, **kwargs):
    raise RuntimeError('Network is disabled in the voice worker')


if __name__ == '__main__':
    socket.socket.connect = refuse_network
    socket.create_connection = refuse_network
    request_path, output_path, receipt_path = map(Path, sys.argv[1:])
    try:
        receipt = synthesize(json.loads(request_path.read_text(encoding='utf-8')), output_path)
    except Exception as error:
        LOG.exception('Offline voice preparation failed')
        receipt_path.write_text(json.dumps({'error': str(error)}), encoding='utf-8')
        sys.exit(1)
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')

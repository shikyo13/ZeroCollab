# Motion audio plan, revision 3

Owner Systems, task 6e90b5b7. Backend owns scene validation and room_motion fields.
Zero14804 requires an acceptable real product demo and full capability parity. Exporting a file alone
does not meet that standard. This proposal precedes implementation of the mix.

## Local voice evaluation

Read-only voice inventory found Microsoft David Desktop and Microsoft Zira Desktop. No Piper command
was on PATH. Evaluation installed piper-tts 1.8.0 and its alignment dependencies in an isolated venv
under motion-audio-source/evaluation, not the room's venv. No service, paid API, auth or live setting changed.

The same 23-word sample was synthesized through all three voices:

| Candidate | Audio | Timing result | Quality status |
|---|---|---|---|
| Windows David | 10.643 s, 22050 Hz | 23 engine word-start events | Sample generated; listening verdict pending |
| Windows Zira | 10.343 s, 22050 Hz | 23 engine word-start events | Sample generated; listening verdict pending |
| Piper LJ Speech high | 9.497 s, 22050 Hz | 151 phoneme alignments, total exactly 209408 audio samples | Sample generated; listening verdict pending |
| Kokoro Heart | 9.023 s, 24000 Hz | 151 phoneme durations | Selected by Zero in #15021 |
| Kokoro Sarah | 8.713 s, 24000 Hz | 151 phoneme durations | Audition alternative |
| Kokoro Michael | 9.823 s, 24000 Hz | 151 phoneme durations | Audition alternative |

Files and metadata are in motion-audio-source/evaluation. David/Zira events give starts, not measured
word ends. Piper requires include_alignments on both load and synthesize. A first synthesis omitted
the latter; the aligned sample corrects that evaluation mistake. Piper splits ZeroCollab into two
phoneme word groups. Mapping to the caption transcript must explicitly handle pronunciation/normalization;
unverified token matching must never become an exact-timing claim.

Zero selected Heart in #15021. The registry default is kokoro:af-heart (engine voice af_heart).
One-time public downloads were authorized; synthesis ran with CPUExecutionProvider only and socket
connections refused. Piper remains explicitly selectable; an unavailable Kokoro voice is refused.
The spoken override maps ZeroCollab to Zero Collab while captions retain the original spelling.

Licensing facts are separate from voice quality:

- Current Piper's package/source is GPL-3.0-or-later: https://github.com/OHF-Voice/piper1-gpl/blob/main/setup.py
- The older rhasspy engine declares MIT: https://github.com/rhasspy/piper/blob/master/LICENSE.md
- Voice repository README declares MIT: https://huggingface.co/rhasspy/piper-voices/raw/main/README.md
- LJ model card identifies public-domain training data: https://huggingface.co/rhasspy/piper-voices/raw/main/en/en_US/ljspeech/high/MODEL_CARD
- Exact downloaded voice/config/card/README hashes and URLs are in evaluation/download-receipt.json.
  No voice/runtime is packaged by this evaluation. Desktop retains packaging authority.

Chosen-worker release inventory, not yet packaged:

| Component | Pin | Licence source |
|---|---|---|
| Kokoro weights/voices | evaluation/kokoro-download-receipt.json, model-files-v1.1 release | Apache-2.0 model card: https://huggingface.co/hexgrad/Kokoro-82M |
| kokoro-onnx | 0.6.1 | MIT: https://github.com/thewh1teagle/kokoro-onnx/blob/main/LICENSE |
| phonemizer | 3.4.0 | GPL v3 text in the installed package licenses/LICENSE |
| espeakng-loader | 0.2.4 | MIT wrapper: https://github.com/thewh1teagle/espeakng-loader/blob/main/LICENSE |
| eSpeak NG shared library/data | From the loader wheel, loaded by phonemizer | GPL v3 text: https://github.com/espeak-ng/espeak-ng/blob/master/COPYING |
| ONNX Runtime | 1.31.0 CPU | MIT package licence |
| NumPy | 2.5.4 | BSD package licence and bundled notices |

Run the voice engine in a separate owned process. Record the bundled notices in the release inventory
before distribution, per PM99ff4604. Apache model weights do not describe every dependency's licence.
The documentation's English v1.1 model URL returned 404; the actual official release asset used is
kokoro-v1.0.onnx. Its observed outputs are waveform and duration. Model/voice/card/licence URL and SHA256
pins are recorded in kokoro-download-receipt.json; no second model copy was created.

Timing API sources:
https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/ALIGNMENTS.md
https://learn.microsoft.com/en-us/dotnet/api/system.speech.synthesis.speakprogresseventargs.audioposition

## Proposed scene fields

Keep the existing audio layer and its asset/trim/rate/volume. Clip from/duration/offset and trim use
composition frames; source time is (trim + (local frame + offset) * rate) * den / num seconds, matching
the video time contract. Rate is initially constant per clip; volume uses the shared core's sampleField.

Add these optional fields to audio layers:

- role: voice | music | sfx. Default sfx; role only determines explicit ducking groups.
- loop: boolean, default false. Loop source media within the clip, then truncate at clip duration.
- duck: {against: [voice layer IDs], gain: 0..1, attack_ms, release_ms}. Music defaults are proposed
  gain 0.25, attack 80 ms, release 250 ms only when duck is explicitly requested. No global automatic mix.

Add a voiceover layer:

    {id, type: "voiceover", text, voice, rate: 1, volume: 1,
     captions: {enabled: true}}

voice is an opaque registry ID, never a file path or URL. Examples under evaluation:
piper:en-us-ljspeech-high, windows:david, windows:zira. Backend bounds text, voice IDs, rate and volume.
Voice synthesis happens once during export/preview preparation, not per frame or seek. Text that does
not fit its clip after synthesis is refused with required duration; words are not silently cut off.

Generated timing sidecar uses sample units:

    {sample_rate, samples, text, spoken_text, timing_quality,
     words: [{text, start_sample, end_sample}], engine, voice, model_sha256}

timing_quality distinguishes engine_word_starts from verified_phoneme_word_mapping. Word-end values
derived from the next event are identified as such. For unavailable or ambiguous alignment, the tool
reports that state; it does not invent word timings. Caption cues are placed using the clip's rational
clock and the same trim/rate transform as sound. Backend and Motion agree the generated caption layer
and word-highlight format before it becomes schema or renderer behavior.

## Mix and export plan

1. Resolve source audio/video uploads with the existing allowed-root and hash rules. Voice registry
   models are immutable local inputs with pinned hashes; no agent-supplied paths or FFmpeg args.
2. Produce/cache WAV plus timing receipt keyed by text, normalized pronunciation, voice/model bytes,
   engine and converter versions, rate and synthesis parameters. Publish atomically, verify PCM hash,
   keep active job inputs pinned, and bound retained cache storage. A replay uses the resolved WAV.
3. Assemble 48000 Hz stereo PCM for the exact composition duration. Apply source trim/offset/rate,
   clip boundaries, looping and volume envelopes. Sample envelopes through the existing motion core,
   not a second Python easing implementation; interpolate audio gain between frame samples.
4. Duck only the named music layers under the actual voice intervals with attack/release envelopes.
   Mix voice/music/SFX with headroom and a final limiter. Keep the result and captions on the same clock.
5. Reuse the exact encoder chosen by require_tools. Explicitly probe audio support on that binary;
   audio_probed=false today is not proof of a working mix. WebM uses Opus; ProRes uses PCM where
   supported. GIF, PNG and PNG sequences refuse authored audio, with guidance to choose a video profile.
6. Join off-loop preparation and owned synthesis/encoder subprocesses on cancel, project switch or
   job replacement. No persistent model service or user browser. Preserve existing final publication fence.

Starter beds/SFX will be original procedural audio with an explicit CC0 source/license record, reviewed
for use by demo-marketing. They are assets, not proof that the final soundtrack or demo is acceptable.

## Ownership and proof

- Systems: local TTS adapter/cache, audio plan/mix, encoder integration and cancellation.
- Backend: paired schema/tool/asset metadata and early capability/fit warnings.
- Motion: caption rendering and shared envelope sampling; no independent audio clock.
- Demo-marketing: listening decision and soundtrack/story fit using real footage/components.

Smallest implementation proof is a real 20-second composition with voice per shot, a music bed audibly
ducked under it, a click SFX and captions following verified timing. Native duration/stream checks support
that listening review, but do not replace it. Final product-demo acceptance remains the user's real
45-60 second demo and the parity scorecard.

## Current implementation boundary

Owned source in motion-audio-source/source is split into voice, process, cache and PCM mix modules.
The cache key includes text, rate, model/config hashes, engine version, worker and Python hashes. Entries
are immutable, leased while active, capped at 64 entries / 256 MiB with 32 MiB reserved per generation.
It caches generated WAVs, not copies of source media. CPU synthesis is serialized, capped at four threads.

Native checks: brand-aware Heart produced 217800 samples at 24 kHz and 23 caption tokens. Mapping labels
simple lexical boundary accounting as word timing; ambiguous normalization uses phrase timing with no
highlight. This is engine-derived timing, not an independent acoustic measurement. Cached replay reused
the exact WAV. PCM checks covered exact sample count, explicit ducking and cancelled atomic publication.
Repeated startup cancellation left no live worker and closed Windows job/process handles. Filesystem
work joins even after repeated cancellation before temporary-directory cleanup.

## Export integration and host provisioning

The integration source now uses the reviewed S7 audioPlan and caption seam. It reads one plan through an
owned low-priority page, releases that page, prepares sound once, then renders with the generated caption
sidecars. Silent scenes retain the existing path. The encoder receives PNGs plus the prepared WAV with
explicit video/audio maps; no output is published if preparation fails. Jobs remain busy and cancellable
through preparation. Generated voice leases last through encoding.

The host installs `motion/voices/registry.json` under `runtime.data`. It is a bounded dictionary of opaque
voice ids to Voice records: `python`, `engine`, `engine_voice`, `engine_version`, `model`, `model_sha256`,
`aux`, `aux_sha256`. Paths are host-owned absolute files, never scene fields. `aux` is Kokoro's voices bin
or Piper's config. The default id is `kokoro:af-heart`, engine voice `af_heart`. The interpreter needs the
recorded offline engine dependencies and NumPy. Missing registry, model or engine is an explicit refusal;
the adapter does not download, change settings or silently switch engines. Piper remains explicitly
selectable. Packaging must carry all component notices listed above and provision this registry against
stable installed model/interpreter locations. Evaluation paths are not portable release defaults.

Media decode uses the same resolved originals and encoder path as the picture. It restricts input to file
protocol and ordinary media demuxers, reads only audio stream 0, checks the asset hash before and after,
and produces bounded 48 kHz stereo float32 PCM. Decoding one sample beyond the remaining budget detects
oversize sources instead of silently shortening them. Derived PCM lives only in the owned job directory.
The same binary passes a tiny encode/mux/decode audio probe before mixing; successful probes cache by
encoder hash and codec/muxer with at most eight entries.

Native integration proof: 600 frames at 640x360, 30 fps, 20 seconds; af_heart with the brand pronunciation
override, a generated test music bed ducked under voice intervals, and 23 word-timing caption tokens.
The export took 45.1 seconds. The WebM decoded 600 video frames and exactly 960000 stereo samples at
48 kHz. Opus reports 20.008 seconds in container metadata due to packet framing; decoded PCM is exactly
20 seconds. The native proof uses a pinned previously generated narration cache entry. It is a scripted
audio transport fixture, not a product demo, listening approval, real-time rendering or installed acceptance.

Remaining acceptance: independent integration review, stable host provisioning, live room_motion export,
caption preparation in the preview/frame path, and the real narrated product demo. Starter music/SFX and
final loudness mastering are separate from this generated-tone transport fixture.

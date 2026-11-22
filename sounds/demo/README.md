# Original audio proposals

These are audition proposals, not selected production music or wired runtime assets. No desktop playback was performed during generation. Listening and comfort checks remain necessary before selection.

All sounds were synthesized from original oscillators and seeded noise using Python's standard library. No source recordings, existing game audio, licensed samples, model/API output, or third-party music were used. The existing MP3 assets remain untouched.

| File | Length | Sample peak | RMS |
| --- | --- | --- | --- |
| `unlock-warm-pluck.wav` | 0.86s | -8.87 dBFS | -21.79 dBFS |
| `unlock-wooden-spark.wav` | 0.92s | -9.37 dBFS | -25.42 dBFS |
| `playful-mystery-loop.wav` | 16.00s | -13.98 dBFS | -30.95 dBFS |

- `unlock-warm-pluck.wav`: soft ascending D-major plucks, a small bright reward rather than a fanfare.
- `unlock-wooden-spark.wav`: two rounded wooden taps followed by a gentle major-third-and-octave sparkle.
- `playful-mystery-loop.wav`: sixteen seconds at a sparse sixty-beat-per-minute grid; quiet D-Dorian color, four low plucks, a few soft wooden/plucked questions and a breathing pad. No jump scares, clown effects or busy continuous lead.

Format: uncompressed PCM WAV, 44,100 Hz, two channels, signed 16-bit little endian. The chimes begin and end at zero and have softened attacks and releases. Peak normalization leaves more than eight decibels of digital headroom. No sample clips. The loop pad frequencies and breathing envelope complete whole cycles over sixteen seconds; one-shot tails finish before the wrap. The exact quantized seam measurements and hashes are below. Numerical continuity does not substitute for listening on actual speakers/headphones. These measurements are sample peak, not a certified loudness or intersample true-peak measurement.

```json
[
  {
    "file": "unlock-warm-pluck.wav",
    "duration_seconds": 0.86,
    "sample_peak_dbfs": -8.874,
    "rms_dbfs": -21.793,
    "dc_offset": [
      -6.861345479931314e-06,
      -6.146806393948083e-06
    ],
    "clipped_samples": 0,
    "sha256": "cf4a86b374042cb4691ab49f82251466ddf9422a38e57f9d94a1ed7f600a9084"
  },
  {
    "file": "unlock-wooden-spark.wav",
    "duration_seconds": 0.92,
    "sample_peak_dbfs": -9.371,
    "rms_dbfs": -25.423,
    "dc_offset": [
      -2.931107621943705e-05,
      -2.8469383181827984e-05
    ],
    "clipped_samples": 0,
    "sha256": "a9e5b9035a875bfa4692073c1406e12e2a0f996f4e5979364a47a489b2a9ebce"
  },
  {
    "file": "playful-mystery-loop.wav",
    "duration_seconds": 16.0,
    "sample_peak_dbfs": -13.98,
    "rms_dbfs": -30.952,
    "dc_offset": [
      -1.6305452031072846e-08,
      -3.330291263640873e-09
    ],
    "clipped_samples": 0,
    "sha256": "8a0a838ac61a17f47ebcb20496e6642ceeeefbf32ecb0e6139a440a94bb6ef3d",
    "wrap_sample_delta_counts": [
      41,
      41
    ],
    "wrap_slope_change_counts": [
      0,
      0
    ]
  }
]
```

## Reproduction recipe

Save the following standard-library script to a temporary file and run it with an EMPTY output directory argument. It refuses to overwrite the four proposal files. The script is documented here rather than installed as an unused game helper. It never plays audio.

```python
"""Original AlibAi audio proposals. Standard library only; no playback."""

from array import array
from hashlib import sha256
import json
import math
from pathlib import Path
import random
import sys
import wave

RATE = 44100
TAU = 2.0 * math.pi


def note(midi):
    return 440.0 * 2.0 ** ((midi - 69) / 12.0)


def smoothstep(value):
    value = min(1.0, max(0.0, value))
    return value * value * (3.0 - 2.0 * value)


def empty(seconds):
    count = round(seconds * RATE)
    return [0.0] * count, [0.0] * count


def add_tone(channels, start, midi, duration, amplitude, pan=0.0, wooden=False, seed=0):
    frequency = note(midi)
    gains = (math.sqrt((1.0 - pan) / 2.0), math.sqrt((1.0 + pan) / 2.0))
    start_frame = round(start * RATE)
    length = round(duration * RATE)
    rng = random.Random(seed)
    noise = 0.0
    for index in range(length):
        frame = start_frame + index
        if frame >= len(channels[0]):
            break
        time = index / RATE
        attack = smoothstep(time / (0.003 if wooden else 0.006))
        release = smoothstep((duration - time) / 0.035)
        if wooden:
            noise = 0.88 * noise + 0.12 * rng.uniform(-1.0, 1.0)
            value = (math.sin(TAU * frequency * time) * math.exp(-time / 0.040)
                     + 0.27 * math.sin(TAU * frequency * 2.41 * time) * math.exp(-time / 0.025)
                     + 0.10 * math.sin(TAU * frequency * 3.92 * time) * math.exp(-time / 0.016)
                     + 0.08 * noise * math.exp(-time / 0.012))
        else:
            phase = TAU * frequency * time
            value = (math.sin(phase) + 0.20 * math.sin(2.0 * phase)
                     + 0.055 * math.sin(3.0 * phase)) * math.exp(-4.7 * time / duration)
        value *= amplitude * attack * release
        channels[0][frame] += value * gains[0]
        channels[1][frame] += value * gains[1]


def add_periodic_pad(channels, seconds):
    # Quantized cycle counts make the breathing pad exactly periodic over the loop.
    frequencies = [round(note(midi) * seconds) / seconds for midi in (50, 53, 57, 59)]
    for frame in range(len(channels[0])):
        time = frame / RATE
        breath = 0.75 + 0.25 * math.cos(TAU * time / 8.0)
        left = right = 0.0
        for voice, frequency in enumerate(frequencies):
            amplitude = (0.007, 0.0045, 0.004, 0.0025)[voice]
            phase = TAU * frequency * time
            offset = 0.025 * (voice + 1)
            left += amplitude * math.sin(phase - offset)
            right += amplitude * math.sin(phase + offset)
        channels[0][frame] += left * breath
        channels[1][frame] += right * breath


def encode(channels, peak, *, loop):
    count = len(channels[0])
    for channel in channels:
        average = sum(channel) / count
        for index in range(count):
            channel[index] -= average
            if not loop:
                channel[index] *= smoothstep(index / (RATE * 0.003))
                channel[index] *= smoothstep((count - 1 - index) / (RATE * 0.045))
    raw_peak = max(abs(value) for channel in channels for value in channel)
    multiplier = peak / raw_peak
    samples = array('h')
    for index in range(count):
        for channel in channels:
            value = round(channel[index] * multiplier * 32767)
            assert -32767 < value < 32767
            samples.append(value)
    if sys.byteorder != 'little':
        samples.byteswap()
    return samples.tobytes()


def write_and_check(destination, channels, peak, *, loop=False):
    pcm = encode(channels, peak, loop=loop)
    with wave.open(str(destination), 'wb') as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(pcm)
    with wave.open(str(destination), 'rb') as audio:
        assert (audio.getnchannels(), audio.getsampwidth(), audio.getframerate(), audio.getcomptype()) == (2, 2, RATE, 'NONE')
        count = audio.getnframes()
        assert audio.readframes(count) == pcm
    samples = array('h')
    samples.frombytes(pcm)
    if sys.byteorder != 'little':
        samples.byteswap()
    measured_peak = max(abs(value) for value in samples) / 32768.0
    rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768.0
    dc = [sum(samples[channel::2]) / count / 32768.0 for channel in (0, 1)]
    assert measured_peak < 0.4
    assert max(abs(value) for value in dc) < 0.001
    result = {'file': destination.name, 'duration_seconds': count / RATE,
              'sample_peak_dbfs': round(20 * math.log10(measured_peak), 3),
              'rms_dbfs': round(20 * math.log10(rms), 3),
              'dc_offset': dc, 'clipped_samples': 0,
              'sha256': sha256(destination.read_bytes()).hexdigest()}
    if loop:
        jumps = [abs(samples[channel] - samples[-2 + channel]) for channel in (0, 1)]
        slopes = []
        for channel in (0, 1):
            wrap_delta = samples[channel] - samples[-2 + channel]
            start_delta = samples[2 + channel] - samples[channel]
            end_delta = samples[-2 + channel] - samples[-4 + channel]
            slopes.append(max(abs(wrap_delta - start_delta), abs(wrap_delta - end_delta)))
        assert max(slopes) <= 12, slopes
        result.update(wrap_sample_delta_counts=jumps, wrap_slope_change_counts=slopes)
    else:
        assert samples[0] == samples[1] == samples[-2] == samples[-1] == 0
    return result


def generate(output):
    names = ('unlock-warm-pluck.wav', 'unlock-wooden-spark.wav', 'playful-mystery-loop.wav', 'README.md')
    assert not any((output / name).exists() for name in names), 'Refusing to overwrite an existing proposal'
    output.mkdir(parents=True, exist_ok=True)

    warm = empty(0.86)
    for start, midi, duration, amplitude, pan in (
        (0.0, 62, 0.40, 0.19, -0.12), (0.075, 66, 0.43, 0.16, -0.03),
        (0.15, 69, 0.48, 0.14, 0.06), (0.225, 74, 0.58, 0.11, 0.13),
    ):
        add_tone(warm, start, midi, duration, amplitude, pan)

    wooden = empty(0.92)
    add_tone(wooden, 0.0, 62, 0.16, 0.22, -0.05, wooden=True, seed=661101)
    add_tone(wooden, 0.115, 69, 0.18, 0.17, 0.06, wooden=True, seed=661102)
    add_tone(wooden, 0.23, 66, 0.51, 0.12, -0.06)
    add_tone(wooden, 0.30, 74, 0.56, 0.09, 0.09)

    loop = empty(16.0)
    add_periodic_pad(loop, 16.0)
    for start, midi in ((0.50, 38), (4.50, 45), (8.50, 48), (12.50, 45)):
        add_tone(loop, start, midi, 1.25, 0.095, -0.03)
    for event, (start, midi) in enumerate(((1.25, 62), (2.50, 65), (6.25, 69), (6.75, 64), (10.50, 62), (14.0, 59), (14.75, 57))):
        add_tone(loop, start, midi, 0.65, 0.035, (-0.15, 0.12, -0.08, 0.16)[event % 4])
    for event, start in enumerate((3.5, 7.5, 11.5, 15.0)):
        add_tone(loop, start, 57, 0.16, 0.015, (-0.10, 0.10)[event % 2], wooden=True, seed=661200 + event)

    results = [write_and_check(output / names[0], warm, 0.36),
               write_and_check(output / names[1], wooden, 0.34),
               write_and_check(output / names[2], loop, 0.20, loop=True)]
    table = '\n'.join(f"| `{item['file']}` | {item['duration_seconds']:.2f}s | {item['sample_peak_dbfs']:.2f} dBFS | {item['rms_dbfs']:.2f} dBFS |" for item in results)
    recipe = Path(__file__).read_text(encoding='utf-8')
    readme = f'''# Original audio proposals

These are audition proposals, not selected production music or wired runtime assets. No desktop playback was performed during generation. Listening and comfort checks remain necessary before selection.

All sounds were synthesized from original oscillators and seeded noise using Python's standard library. No source recordings, existing game audio, licensed samples, model/API output, or third-party music were used. The existing MP3 assets remain untouched.

| File | Length | Sample peak | RMS |
| --- | --- | --- | --- |
{table}

- `unlock-warm-pluck.wav`: soft ascending D-major plucks, a small bright reward rather than a fanfare.
- `unlock-wooden-spark.wav`: two rounded wooden taps followed by a gentle major-third-and-octave sparkle.
- `playful-mystery-loop.wav`: sixteen seconds at a sparse sixty-beat-per-minute grid; quiet D-Dorian color, four low plucks, a few soft wooden/plucked questions and a breathing pad. No jump scares, clown effects or busy continuous lead.

Format: uncompressed PCM WAV, 44,100 Hz, two channels, signed 16-bit little endian. The chimes begin and end at zero and have softened attacks and releases. Peak normalization leaves more than eight decibels of digital headroom. No sample clips. The loop pad frequencies and breathing envelope complete whole cycles over sixteen seconds; one-shot tails finish before the wrap. The exact quantized seam measurements and hashes are below. Numerical continuity does not substitute for listening on actual speakers/headphones. These measurements are sample peak, not a certified loudness or intersample true-peak measurement.

```json
{json.dumps(results, indent=2)}
```

## Reproduction recipe

Save the following standard-library script to a temporary file and run it with an EMPTY output directory argument. It refuses to overwrite the four proposal files. The script is documented here rather than installed as an unused game helper. It never plays audio.

```python
{recipe.rstrip()}
```
'''
    (output / 'README.md').write_text(readme, encoding='utf-8')
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    generate(Path(sys.argv[1]))
```

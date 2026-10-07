"""Bounded raster markup shared by agent tools; no network or chat side effects."""
import io
import math
import re
import warnings

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

IMAGE_LIMIT = 5 * 1024 * 1024
PIXEL_LIMIT = 20_000_000
FORMATS = {'PNG', 'JPEG', 'GIF', 'WEBP'}


def normalize_marks(marks):
    if not isinstance(marks, list) or len(marks) > 20:
        raise ValueError('Provide at most 20 box, arrow or pin marks.')
    result = []
    for raw in marks:
        if not isinstance(raw, dict) or raw.get('kind') not in ('box', 'arrow', 'pin'):
            raise ValueError('Each mark needs kind box, arrow or pin.')
        mark = {'kind': raw['kind']}
        for key in ('x1', 'y1', 'x2', 'y2'):
            value = raw.get(key, raw.get(key.replace('2', '1')) if raw['kind'] == 'pin' else None)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError('Mark coordinates must be finite numbers from 0 to 1.')
            mark[key] = value
        color = raw.get('color', '#ff4d4f')
        if not isinstance(color, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('Mark color must be a six-digit hex color.')
        note = raw.get('note', '')
        if not isinstance(note, str) or len(note) > 300:
            raise ValueError('Each mark note must be text of at most 300 characters.')
        mark.update(color=color.lower(), note=note.strip())
        result.append(mark)
    return result


def render_markup(source, marks=None, summary=''):
    """Return (PNG bytes, text note); leave source bytes unchanged.

    Coordinates match the browser editor: normalized x1/y1/x2/y2, pin at
    x1/y1. Animated inputs deliberately produce one annotated still frame.
    """
    marks = normalize_marks([] if marks is None else marks)
    if not isinstance(summary, str) or len(summary) > 1000:
        raise ValueError('Summary must be text of at most 1000 characters.')
    if not isinstance(source, bytes) or not source or len(source) > IMAGE_LIMIT:
        raise ValueError('Use a nonempty image no larger than 5 MB.')
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(source)) as original:
                if original.format not in FORMATS:
                    raise ValueError('Use a PNG, JPEG, GIF or WebP image.')
                if original.width * original.height > PIXEL_LIMIT:
                    raise ValueError('Image exceeds 20 million pixels; share a smaller screenshot.')
                image = ImageOps.exif_transpose(original).convert('RGB')
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('Image could not be decoded safely.') from exc
    width, height = image.size
    draw = ImageDraw.Draw(image)
    line = max(2, round(min(width, height) / 250))
    badge = max(9, round(min(width, height) / 45))
    font = ImageFont.load_default(size=round(badge * 1.1))
    notes = [summary.strip()] if summary.strip() else []
    for index, mark in enumerate(marks, 1):
        x1, y1 = mark['x1'] * (width - 1), mark['y1'] * (height - 1)
        x2, y2 = mark['x2'] * (width - 1), mark['y2'] * (height - 1)
        color = mark['color']
        if mark['kind'] == 'box':
            rect = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
            draw.rectangle(rect, outline='#0b0d10', width=line + 2)
            draw.rectangle(rect, outline=color, width=line)
        elif mark['kind'] == 'arrow':
            draw.line((x1, y1, x2, y2), fill='#0b0d10', width=line + 2)
            draw.line((x1, y1, x2, y2), fill=color, width=line)
            angle, head = math.atan2(y2 - y1, x2 - x1), line * 5
            draw.polygon([(x2, y2)] + [(x2 - head * math.cos(angle + delta), y2 - head * math.sin(angle + delta))
                                      for delta in (-math.pi / 7, math.pi / 7)], fill=color)
        bx, by = (min(x1, x2), min(y1, y2)) if mark['kind'] == 'box' else (x1, y1)
        # Keep edge pins and their numbers legible within the image.
        bx, by = max(badge, min(width - badge, bx)), max(badge, min(height - badge, by))
        draw.ellipse((bx - badge, by - badge, bx + badge, by + badge), fill=color, outline='#0b0d10', width=1)
        rgb = tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))
        ink = '#0b0d10' if sum(c * w for c, w in zip(rgb, (.2126, .7152, .0722))) > 140 else '#ffffff'
        draw.text((bx, by), str(index), font=font, fill=ink, anchor='mm')
        position = f"({mark['x1']:.3f},{mark['y1']:.3f})"
        if mark['kind'] != 'pin':
            position += f" to ({mark['x2']:.3f},{mark['y2']:.3f})"
        notes.append(f"#{index} {mark['kind']} {position}: {mark['note'] or '(no note)'}")
    output = io.BytesIO()
    image.save(output, format='PNG')
    data = output.getvalue()
    if len(data) > IMAGE_LIMIT:
        raise ValueError('Annotated PNG exceeds 5 MB; share a smaller screenshot.')
    return data, '\n'.join(notes)

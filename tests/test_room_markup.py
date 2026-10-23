import io
import unittest
from unittest.mock import patch

from PIL import Image
from zerocollab.integrations.room_markup import render_markup, normalize_marks


def screenshot(size=(320, 180), format='PNG'):
    data = io.BytesIO()
    Image.new('RGB', size, '#253040').save(data, format=format)
    return data.getvalue()


class MarkupTests(unittest.TestCase):
    def test_pixels_notes_and_original_preserved(self):
        source = screenshot()
        original = bytes(source)
        marks = [dict(kind='box', x1=.1, y1=.1, x2=.5, y2=.5, note='Increase spacing'),
                 dict(kind='arrow', x1=.6, y1=.6, x2=.8, y2=.8, color='#22d3ee', note='Target'),
                 dict(kind='pin', x1=.9, y1=.1, note='Contrast')]
        png, note = render_markup(source, marks, 'Review')
        self.assertEqual(source, original)
        self.assertNotEqual(png, source)
        with Image.open(io.BytesIO(png)) as image:
            self.assertEqual(image.size, (320, 180))
            self.assertEqual(image.format, 'PNG')
            self.assertEqual(image.getpixel((100, 18)), (255, 77, 79))
        self.assertIn('#1 box (0.100,0.100) to (0.500,0.500): Increase spacing', note)
        self.assertIn('#3 pin (0.900,0.100): Contrast', note)

    def test_supported_rasters_and_plain_screenshot(self):
        for format in ('PNG', 'JPEG', 'GIF', 'WEBP'):
            with self.subTest(format=format):
                png, note = render_markup(screenshot(format=format), summary='Before')
                self.assertTrue(png.startswith(b'\x89PNG'))
                self.assertEqual(note, 'Before')

    def test_invalid_coordinates_and_marks(self):
        for value in (-.1, 1.1, float('nan'), float('inf'), True, '0.2', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_marks([dict(kind='pin', x1=value, y1=.5)])
        for marks in ({}, [None], [dict(kind='circle')], [dict(kind='pin', x1=.2, y1=.2)] * 21):
            with self.assertRaises(ValueError):
                normalize_marks(marks)

    def test_image_and_note_limits(self):
        for source in (b'', b'not an image', b'<svg></svg>', screenshot(format='BMP')):
            with self.assertRaises(ValueError):
                render_markup(source)
        with patch('zerocollab.integrations.room_markup.PIXEL_LIMIT', 100), self.assertRaises(ValueError):
            render_markup(screenshot())
        with self.assertRaises(ValueError):
            render_markup(screenshot(), summary='x' * 1001)
        with self.assertRaises(ValueError):
            normalize_marks([dict(kind='pin', x1=.5, y1=.5, note='x' * 301)])
        with self.assertRaises(ValueError):
            normalize_marks([dict(kind='pin', x1=.5, y1=.5, color='red')])


if __name__ == '__main__':
    unittest.main()

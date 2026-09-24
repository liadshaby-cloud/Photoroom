import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('downloader', Path(__file__).with_name('downloader.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


class Tests(unittest.TestCase):
    def test_png_validation(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / 'image.png'
            f.write_bytes(b'html error')
            with self.assertRaises(ValueError):
                app.png_size(f)

    def test_png_dimensions(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / 'image.png'
            f.write_bytes(
                b'\x89PNG\r\n\x1a\n' +
                struct.pack('>I', 13) +
                b'IHDR' +
                struct.pack('>II', 1276, 718)
            )
            self.assertEqual(app.png_size(f), (1276, 718))

    def test_filenames(self):
        self.assertEqual(app.safe_name('../שלום/א'), '_שלום_א')


if __name__ == '__main__':
    unittest.main()

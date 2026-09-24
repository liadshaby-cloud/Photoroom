import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('downloader', Path(__file__).with_name('downloader.py'))
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)

class FakeChrome:
    def __init__(self):
        self.page = 0
    def front_guard(self): pass
    def rewind(self): self.page = 0
    def collection(self):
        return None, (["א", "ב"] if self.page == 0 else ["ב", "ג"]), []
    def label(self, button): return button
    def signature(self): return self.page
    def scroll(self, direction): self.page = 1
    def save(self, label, target, settle):
        target.write_bytes(b'\x89PNG\r\n\x1a\n' + struct.pack('>I',13) + b'IHDR' + struct.pack('>II',1276,718) + label.encode())
        return (1276, 718)

class Tests(unittest.TestCase):
    def test_resume_and_multiple_pages(self):
        with tempfile.TemporaryDirectory() as root:
            downloads = Path(root) / 'Downloads'
            downloads.mkdir()
            with patch.object(app, 'Chrome', FakeChrome), patch.object(app.Path, 'home', return_value=Path(root)), patch.object(app.time, 'sleep'), patch('sys.argv', ['downloader', '--session', 'demo']):
                app.main()
                first = {p.name: p.read_bytes() for p in downloads.glob('*.png')}
                self.assertEqual(len(first), 3)
                app.main()
                self.assertEqual(first, {p.name: p.read_bytes() for p in downloads.glob('*.png')})
                file = next(downloads.glob('*.png'))
                file.write_bytes(b'changed')
                with self.assertRaisesRegex(RuntimeError, 'חסר או השתנה'):
                    app.main()
    def test_png_validation(self):
        with tempfile.TemporaryDirectory() as root:
            f = Path(root) / 'image.png'
            f.write_bytes(b'html error')
            with self.assertRaises(ValueError): app.png_size(f)
    def test_filenames(self):
        self.assertEqual(app.safe_name('../שלום/א'), '_שלום_א')

if __name__ == '__main__':
    unittest.main()

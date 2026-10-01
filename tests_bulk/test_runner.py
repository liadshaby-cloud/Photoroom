import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from PIL import Image
from bulk.runner import Journal, collect, identity, output_name, process_page, URL


class BulkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'input.png'
        Image.new('RGBA', (12, 8), (255, 0, 0, 0)).save(self.source)
        self.output = self.root / 'results'

    def download(self, payload=None):
        mock = MagicMock()
        mock.save_as.side_effect = lambda target: Path(target).write_bytes(
            self.source.read_bytes() if payload is None else payload)
        return mock

    def test_more_than_ninety_and_exclude_output(self):
        for n in range(125):
            (self.root / f'{n}.jpg').write_bytes(b'source')
        self.output.mkdir()
        (self.output / 'already.png').write_bytes(b'output')
        result = collect([self.root, self.source], self.output)
        self.assertEqual(len(result), 126)
        self.assertNotIn(self.output / 'already.png', result)

    def test_same_name_in_different_folders(self):
        other = self.root / 'other' / self.source.name
        other.parent.mkdir()
        other.write_bytes(self.source.read_bytes())
        self.assertNotEqual(output_name(other, identity(other)), output_name(self.source, identity(self.source)))

    def test_changed_source_has_new_identity(self):
        before = identity(self.source)
        Image.new('RGB', (10, 10)).save(self.source)
        self.assertNotEqual(before, identity(self.source))

    def test_download_validate_and_resume(self):
        journal = Journal(self.output)
        key = identity(self.source)
        target, size, transparent = journal.save(self.source, key, self.download())
        self.assertEqual(size, [12, 8])
        self.assertTrue(transparent)
        self.assertTrue(Journal(self.output).verified(key))
        with Image.open(target) as image:
            self.assertEqual(image.format, 'PNG')
            image.verify()

    def test_modified_result_not_silently_skipped(self):
        journal = Journal(self.output)
        key = identity(self.source)
        target, _, _ = journal.save(self.source, key, self.download())
        target.write_bytes(b'changed')
        with self.assertRaises(RuntimeError):
            Journal(self.output).verified(key)

    def test_unknown_existing_file_not_overwritten(self):
        journal = Journal(self.output)
        key = identity(self.source)
        target = self.output / output_name(self.source, key)
        target.write_bytes(b'keep me')
        with self.assertRaises(RuntimeError):
            journal.save(self.source, key, self.download())
        self.assertEqual(target.read_bytes(), b'keep me')

    def test_html_error_not_recorded_as_image(self):
        journal = Journal(self.output)
        key = identity(self.source)
        with self.assertRaises(Exception):
            journal.save(self.source, key, self.download(b'<html>error</html>'))
        self.assertFalse(journal.verified(key))
        self.assertFalse(list(self.output.glob('*.png')))

    def test_truncated_png_rejected(self):
        journal = Journal(self.output)
        key = identity(self.source)
        with self.assertRaises(Exception):
            journal.save(self.source, key, self.download(self.source.read_bytes()[:30]))
        self.assertFalse(journal.verified(key))

    def test_failed_download_never_recorded(self):
        journal = Journal(self.output)
        download = MagicMock()
        download.save_as.side_effect = RuntimeError('network failure')
        key = identity(self.source)
        with self.assertRaises(RuntimeError):
            journal.save(self.source, key, download)
        self.assertFalse(journal.verified(key))

    def test_upload_and_download_via_public_controls(self):
        page = MagicMock()
        result = process_page(page, self.source, 1234)
        page.goto.assert_called_once_with(URL, wait_until='domcontentloaded', timeout=1234)
        chooser = page.expect_file_chooser.return_value.__enter__.return_value
        chooser.value.set_files.assert_called_once_with(str(self.source), timeout=1234)
        self.assertEqual(result, page.expect_download.return_value.__enter__.return_value.value)
        calls = page.mock_calls
        upload = next(i for i, c in enumerate(calls) if 'set_files' in c[0])
        wait = next(i for i, c in enumerate(calls) if 'wait_for' in c[0])
        download = next(i for i, c in enumerate(calls) if c[0] == 'expect_download')
        self.assertLess(upload, wait)
        self.assertLess(wait, download)

    def test_processing_failure_does_not_download(self):
        page = MagicMock()
        page.get_by_role.return_value.first.wait_for.side_effect = TimeoutError('processing failed')
        with self.assertRaises(TimeoutError):
            process_page(page, self.source, 1234)
        page.expect_download.assert_not_called()


if __name__ == '__main__':
    unittest.main()

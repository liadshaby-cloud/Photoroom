import unittest
from pathlib import Path
from unittest.mock import patch
from downloader import Chrome

class DestinationTests(unittest.TestCase):
    def setUp(self):
        self.browser = Chrome.__new__(Chrome)
        self.browser.attr = lambda node, key, default=None: node.get(key, default)
        self.browser.walk = self.walk
        self.destination = Path.home() / 'Downloads'
    def walk(self, node):
        yield node
        for child in node.get('children', []):
            yield from self.walk(child)
    def panel(self, name, url=None):
        return {'AXIdentifier': 'save-panel', 'children': [
            {'AXIdentifier':'where popup', 'AXValue':name},
            {'AXIdentifier':'ColumnView', 'children': [{'AXURL':url}] if url else []}]}
    def test_wrong_folder_rejected(self):
        self.assertFalse(self.browser.downloads_selected(self.panel('Documents'),self.destination))
    def test_same_name_wrong_path_rejected(self):
        self.assertFalse(self.browser.downloads_selected(self.panel('Downloads','file:///tmp/Downloads/photo.png'),self.destination))
    def test_correct_path(self):
        self.assertTrue(self.browser.downloads_selected(self.panel('Downloads',(self.destination/'photo.png').as_uri()),self.destination))
    def test_waits_for_navigation(self):
        browser=self.browser
        browser.app={}
        browser.front_guard=lambda: None
        panels=iter([self.panel('Documents'), self.panel('Downloads'),self.panel('Downloads')])
        browser.walk=lambda _: iter([next(panels)])
        browser.downloads_selected=lambda panel, dest: panel['children'][0]['AXValue']=='Downloads'
        with patch('downloader.time.sleep') as sleep:
            browser.wait_for_downloads(self.destination)
            self.assertEqual(sleep.call_count,2)
    def test_navigation_timeout_fails_closed(self):
        browser=self.browser
        browser.app={}
        browser.front_guard=lambda: None
        browser.walk=lambda _: iter([self.panel('Documents')])
        browser.downloads_selected=lambda *_: False
        with patch('downloader.time.monotonic',side_effect=[0,0,13]), patch('downloader.time.sleep'):
            with self.assertRaisesRegex(RuntimeError,'לא נלחץ'):
                browser.wait_for_downloads(self.destination)

if __name__=='__main__': unittest.main()

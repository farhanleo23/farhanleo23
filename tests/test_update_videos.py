import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from update_videos import Video, card_svg, parse_channel_page, parse_feed, refresh


CHANNEL = {'key': 'personal', 'name': 'Example', 'handle': 'Example', 'id': 'UCexample'}


class VideoUpdaterTests(unittest.TestCase):
    def test_feed_checks_channel_and_rejects_invalid_ids(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>UCexample</yt:channelId><entry><yt:videoId>bad</yt:videoId><title>Bad</title></entry><entry><yt:videoId>abcdefghijk</yt:videoId><title>A &amp; B</title></entry></feed>'''
        self.assertEqual(parse_feed(feed, 'UCexample'), [Video('abcdefghijk', 'A & B')])
        with self.assertRaises(ValueError):
            parse_feed(feed, 'UCsomeoneelse')

    def test_page_fallback_reads_only_selected_channel_videos(self):
        lockup = {'lockupViewModel': {'contentId': 'abcdefghijk', 'metadata': {'lockupMetadataViewModel': {'title': {'content': 'A video'}}}}}
        data = {'metadata': {'channelMetadataRenderer': {'externalId': 'UCexample'}}, 'contents': {'twoColumnBrowseResultsRenderer': {'tabs': [{'tabRenderer': {'selected': True, 'content': {'items': [lockup, lockup]}}}]}}, 'recommendations': {'videoRenderer': {'videoId': 'zzzzzzzzzzz', 'title': {'simpleText': 'Unrelated'}}}}
        page = 'var ytInitialData = ' + json.dumps(data) + ';'
        self.assertEqual(parse_channel_page(page, 'UCexample'), [Video('abcdefghijk', 'A video')])
        with self.assertRaises(ValueError):
            parse_channel_page(page, 'UCsomeoneelse')

    def test_card_escapes_titles_and_embeds_thumbnail(self):
        svg = card_svg(Video('abcdefghijk', '<script> & "quoted"'), 'School & AI', b'\xff\xd8example', 'dark')
        root = ET.fromstring(svg)
        self.assertEqual(root.find('{http://www.w3.org/2000/svg}title').text, '<script> & "quoted"')
        self.assertNotIn('<script>', svg)
        self.assertIn('data:image/jpeg;base64,', svg)
        with self.assertRaises(ValueError):
            card_svg(Video('abcdefghijk', 'Title'), 'Example', b'<html>Error</html>', 'dark')

    def test_failed_refresh_retains_existing_published_cards(self):
        original = '<!-- BEGIN PERSONAL-VIDEOS -->\n<a href="https://www.youtube.com/watch?v=abcdefghijk">Existing</a>\n<!-- END PERSONAL-VIDEOS -->\n'
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text(original)
            def unavailable(_):
                raise urllib.error.URLError('temporarily unavailable')
            self.assertEqual(refresh(root, fetcher=unavailable, channels=[CHANNEL]), [])
            self.assertEqual((root / 'README.md').read_text(), original)

    def test_repeat_refresh_does_not_change_files(self):
        feed = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015"><yt:channelId>UCexample</yt:channelId><entry><yt:videoId>abcdefghijk</yt:videoId><title>A &amp; B</title></entry></feed>'''
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Keep this introduction.\n<!-- BEGIN PERSONAL-VIDEOS -->\n<!-- END PERSONAL-VIDEOS -->\n')
            def public_source(url):
                return b'\xff\xd8example' if url.endswith('.jpg') else feed
            refresh(root, fetcher=public_source, channels=[CHANNEL])
            before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            refresh(root, fetcher=public_source, channels=[CHANNEL])
            after = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
            self.assertTrue((root / 'README.md').read_text().startswith('Keep this introduction.'))
            self.assertIn('A &amp; B', (root / 'README.md').read_text())


if __name__ == '__main__':
    unittest.main()

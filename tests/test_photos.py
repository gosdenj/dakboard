"""Photos scraper tests.

The parser is tested against a REAL captured page (trimmed) rather than a
hand-written fixture, so the shapes match what Google actually serves.
"""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dakboard import photos  # noqa: E402


# A real AF_initDataCallback block, trimmed to two items.
REAL_BLOCK = {
    "ds:1": [None, [
        ["AF1QipNmpJAlhxumRlu6re4SEIlz",
         ["https://lh3.googleusercontent.com/pw/AP1GczNUNuva0hpWf0Fu63ZGGHNvXoCWXsASvP",
          2976, 3968, None], 1760000000000, "dedup", 0, 1761000000000],
        ["AF1QipMCHhEUg8dwW4UOI9KgOaP-",
         ["https://lh3.googleusercontent.com/pw/AP1GczM0_Q0fYRA7N-5Z8QgjbTb9v97v07P-3A",
          1920, 1080, None], 1761000000000, "dedup2", 0, 1762000000000],
    ], "", ["albumIdHere", "Public"], None, 5]
}


def make_html(block):
    payload = json.dumps(block)
    return (
        "<html><head>"
        '<meta property="og:title" content="Public · Nov 8, 2016 – Dec 1, 2019 📸">'
        "</head><body>"
        "AF_initDataCallback({key: 'ds:1', hash: '3', data:" + payload +
        ", sideChannel: {}});"
        "</body></html>"
    )


class PhotosParsingTests(unittest.TestCase):
    def test_parses_items_from_a_block(self):
        items, token, title = photos._parse_page(REAL_BLOCK["ds:1"])
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["w"], 2976)
        self.assertEqual(items[0]["h"], 3968)
        self.assertTrue(items[0]["url"].startswith("https://lh3.googleusercontent.com/"))
        self.assertIsNone(token)
        self.assertEqual(title, "Public")

    def test_next_page_token_is_read(self):
        block = list(REAL_BLOCK["ds:1"])
        block[2] = "THE_TOKEN"
        items, token, _title = photos._parse_page(block)
        self.assertEqual(token, "THE_TOKEN")

    def test_empty_token_means_no_more_pages(self):
        block = list(REAL_BLOCK["ds:1"])
        block[2] = ""
        _items, token, _title = photos._parse_page(block)
        self.assertIsNone(token)

    def test_non_list_payload_is_safe(self):
        items, token, title = photos._parse_page("nonsense")
        self.assertEqual(items, [])
        self.assertIsNone(token)
        self.assertIsNone(title)

    def test_items_without_a_url_are_skipped(self):
        block = list(REAL_BLOCK["ds:1"])
        block[1] = [["AF1QipX", ["https://lh3.googleusercontent.com/pw/OK", 10, 10]],
                     ["AF1QipY", "not-a-list"],
                     "garbage"]
        items, _t, _ti = photos._parse_page(block)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["url"].startswith("https://lh3.googleusercontent.com/"))

    def test_items_from_a_foreign_host_are_skipped(self):
        """Only lh3 URLs are usable, so anything else is dropped."""
        block = list(REAL_BLOCK["ds:1"])
        block[1] = [["AF1QipX", ["https://example.test/x", 10, 10]]]
        items, _t, _ti = photos._parse_page(block)
        self.assertEqual(items, [])

    def test_init_blocks_are_extracted(self):
        blocks = photos._init_blocks(make_html(REAL_BLOCK["ds:1"]))
        self.assertEqual(len(blocks), 1)
        items, _token, _title = photos._parse_page(blocks[0])
        self.assertEqual(len(items), 2)

    def test_batch_response_is_decoded(self):
        inner = json.dumps(REAL_BLOCK["ds:1"])
        body = ")]}'\n" + json.dumps([["wrb.fr", "snAcKc", inner, None, None, "generic"]])
        result = photos._parse_batch(body)
        self.assertIsNotNone(result)
        items, _token, title = photos._parse_page(result)
        self.assertEqual(len(items), 2)
        self.assertEqual(title, "Public")

    def test_batch_response_with_junk_is_safe(self):
        self.assertIsNone(photos._parse_batch("not json"))
        self.assertIsNone(photos._parse_batch(")]}'\ngarbage"))

    def test_share_context(self):
        key, auth = photos._share_context(
            "https://photos.google.com/share/AF1QipABC?key=XYZ")
        self.assertEqual(key, "AF1QipABC")
        self.assertEqual(auth, "XYZ")

    def test_share_context_needs_both_parts(self):
        self.assertEqual(photos._share_context("https://photos.google.com/"), (None, None))
        self.assertEqual(photos._share_context("https://photos.google.com/share/ABC"),
                         (None, None))

    def test_normalise_link(self):
        self.assertEqual(photos.normalise_link("photos.app.goo.gl/abc"),
                         "https://photos.app.goo.gl/abc")
        self.assertEqual(photos.normalise_link("http://photos.app.goo.gl/abc"),
                         "https://photos.app.goo.gl/abc")
        self.assertEqual(photos.normalise_link("  https://photos.app.goo.gl/abc  "),
                         "https://photos.app.goo.gl/abc")

    def test_image_url_is_sized(self):
        item = {"url": "https://lh3.googleusercontent.com/pw/ABC"}
        self.assertTrue(photos.image_url(item, 1920, 1080).endswith("=w1920-h1080-c"))
        self.assertTrue(photos.image_url(item, 800, 600, crop=False).endswith("=w800-h600"))

    def test_empty_link_raises(self):
        with self.assertRaises(photos.PhotosError):
            photos.fetch_album("")


if __name__ == "__main__":
    unittest.main(verbosity=2)

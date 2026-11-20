import json
import unittest

from base import RedlineCase, ROOT

VECTORS = json.loads((ROOT / "tests" / "fixtures" / "url_vectors.json").read_text())["vectors"]


class UrlRules(RedlineCase):
    def test_shared_vectors(self):
        for url, expected in VECTORS:
            with self.subTest(url=url[:80]):
                self.assertEqual(self.m._looks_like_public_https_url(url), expected)

    def test_non_strings_and_length_boundary(self):
        f = self.m._looks_like_public_https_url
        for bad in (None, 5, b"https://example.com", ["https://example.com"]):
            self.assertFalse(f(bad))
        base = "https://example.com/"
        self.assertTrue(f(base + "a" * (500 - len(base))))
        self.assertFalse(f(base + "a" * (501 - len(base))))

    def test_every_non_public_range_edge(self):
        f = self.m._looks_like_public_https_url
        edges = {  # (last blocked, first public after it) for each reserved block
            "10.255.255.255": "11.0.0.0", "100.127.255.255": "100.128.0.0", "127.255.255.255": "128.0.0.0",
            "169.254.255.255": "169.255.0.0", "172.31.255.255": "172.32.0.0", "192.168.255.255": "192.169.0.0",
            "198.19.255.255": "198.20.0.0", "192.0.0.255": "192.0.1.0", "192.0.2.255": "192.0.3.0",
            "198.51.100.255": "198.51.101.0", "203.0.113.255": "203.0.114.0",
        }
        for blocked, public in edges.items():
            with self.subTest(blocked=blocked):
                self.assertFalse(f(f"https://{blocked}/x"))
                self.assertTrue(f(f"https://{public}/x"))
        self.assertFalse(f("https://0.0.0.0/x"))
        self.assertFalse(f("https://224.0.0.0/x"))
        self.assertTrue(f("https://223.255.255.255/x"))


class UrlHelpers(RedlineCase):
    def test_dns_label_rules_in_isolation(self):
        f = self.m._valid_dns_label
        for ok in ("a", "example", "a-b", "a1", "1a", "x" * 63):
            self.assertTrue(f(ok), ok)
        for bad in ("", "-a", "a-", "a_b", "a b", "a.b", "x" * 64, "b\u00fccher", "\u0661"):
            self.assertFalse(f(bad), bad)

    def test_ipv4_parser_in_isolation(self):
        f = self.m._parse_ipv4
        self.assertEqual(f("1.2.3.4"), 0x01020304)
        self.assertEqual(f("255.255.255.255"), 0xFFFFFFFF)
        self.assertEqual(f("0.0.0.0"), 0)
        for bad in ("1.2.3", "1.2.3.4.5", "1.2.3.256", "01.2.3.4", "1.2.3.", ".1.2.3", "a.b.c.d", "1.2.3.0x4", "1.2.3.-4", "\u0661.2.3.4", ""):
            self.assertIsNone(f(bad), bad)


class UrlIdentity(RedlineCase):
    def test_canonical_url_merges_spellings_that_reach_the_same_page(self):
        c = self.m._canonical_url
        same = ["https://example.com/terms", "HTTPS://Example.COM/terms/", "https://example.com:443/terms",
                "https://example.com./terms", "https://example.com/terms#section-4"]
        for u in same:
            self.assertEqual(c(u), "https://example.com/terms", u)
        self.assertEqual(c("https://example.com"), c("https://example.com/"))
        self.assertEqual(c("https://example.com?a=1"), "https://example.com?a=1")
        self.assertEqual(c("https://EXAMPLE.com?Q=A"), "https://example.com?Q=A")  # a query right after the host keeps its case

    def test_canonical_url_keeps_what_distinguishes_pages(self):
        c = self.m._canonical_url
        self.assertNotEqual(c("https://example.com/Terms"), c("https://example.com/terms"))
        self.assertNotEqual(c("https://example.com/t?v=1"), c("https://example.com/t?v=2"))
        self.assertNotEqual(c("https://example.com/t"), c("https://example.org/t"))

    def test_site_and_same_site_rule(self):
        m = self.m
        self.assertEqual(m._site_of("https://WWW.Example.com:443/x"), "example.com")
        self.assertEqual(m._site_of("https://sub.example.com/x"), "sub.example.com")
        self.assertEqual(m._site_of("https://example.com?x=1"), "example.com")
        self.assertTrue(m._same_site_or_archive("https://www.example.com/a", "https://example.com/b"))
        self.assertTrue(m._same_site_or_archive("https://web.archive.org/web/2024/https://x.io/t", "https://example.com/b"))
        self.assertTrue(m._same_site_or_archive("https://archive.org/x", "https://example.com/b"))
        self.assertFalse(m._same_site_or_archive("https://example.org/a", "https://example.com/b"))
        self.assertFalse(m._same_site_or_archive("https://sub.example.com/a", "https://example.com/b"))
        self.assertFalse(m._same_site_or_archive("https://evilarchive.org/a", "https://example.com/b"))


if __name__ == "__main__":
    unittest.main()

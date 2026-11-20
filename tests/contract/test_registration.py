import unittest

from base import RedlineCase, Revert, TINY, URL, V1, collapse

ZERO = "0x" + "00" * 20


class Registration(RedlineCase):
    def test_happy_path_records_document_and_baseline(self):
        did = self.register()
        self.assertEqual(did, 1)
        d = self.doc(1)
        self.assertEqual((d.name, d.publisher, d.kind, d.url, d.latest_url, d.site_url), ("Northwind ToS", "Northwind", "TERMS", URL, URL, URL))
        self.assertEqual(d.owner, self.alice)
        self.assertEqual((d.latest_rev_id, d.revision_count, d.created_at, d.last_revision_at),
                         (1, 1, self.chain.now, self.chain.now))
        for field in ("better", "neutral", "worse", "unclear", "high", "silent", "rewrites"):
            self.assertEqual(d[field], 0, field)
        h = self.m._digest(V1)
        self.assertEqual(d.latest_hash, h)
        self.assertEqual(d.chain_head, self.m._chain("", h))

        r = self.rev(1)
        self.assertEqual((r.doc_id, r.seq, r.effect, r.severity, r.judged, r.appealed), (1, 0, "BASELINE", "NONE", False, False))
        self.assertEqual((r.text_hash, r.chain_hash, r.text_len), (h, d.chain_head, len(V1)))
        self.assertEqual((r.added_units, r.removed_units, r.retained_bp, r.churn_bp), (0, 0, 10000, 0))
        self.assertEqual((r.raw_effect, r.topics, r.quote, r.prev_effect, r.appeal_outcome), ("", "", "", "", ""))
        self.assertEqual(r.appeal_by.as_hex, ZERO)
        self.assertEqual(self.c.get_revision_text(1), V1)
        self.assertEqual(self.c.get_redline(1), '{"added":[],"removed":[],"added_omitted":0,"removed_omitted":0}')
        self.assertEqual([x.id for x in self.c.list_revisions(1, 0, 50)], [1])

    def test_lookup_views_and_events(self):
        self.register()
        self.assertEqual(self.c.find_document(URL), 1)
        self.assertEqual(self.c.find_document("https://EXAMPLE.com/terms/#x"), 1)
        self.assertEqual(self.c.find_document("https://example.com/other"), 0)
        self.assertEqual(self.c.find_document("http://example.com/terms"), 0)
        self.assertEqual(self.c.find_document("not a url"), 0)
        # _canonical_url assumes a validated URL; garbage that would canonicalise to a tracked page must not match it
        for garbage in ("httpx://example.com/terms", "12345678example.com/terms", "https://example.com/terms\n"):
            self.assertEqual(self.c.find_document(garbage), 0, garbage)
        self.assertEqual(self.c.list_owner_documents(self.alice.as_hex), [1])
        self.assertEqual(self.c.list_owner_documents(self.bob.as_hex), [])
        self.assertEqual([d.id for d in self.c.list_documents(0, 10)], [1])
        name, args, blob = self.chain.events[-1]
        self.assertEqual((name, args), ("DocumentRegistered", (1,)))
        self.assertEqual(blob["chain_head"], self.doc(1).chain_head)

    def test_no_counter_moves_for_a_baseline(self):
        self.register()
        s = self.stats()
        self.assertEqual((s["total_documents"], s["total_revisions"]), (1, 1))
        for k in ("much_better", "better", "neutral", "worse", "much_worse", "unclear", "severity_low",
                  "severity_medium", "severity_high", "silent_flags", "rewrites", "format_only", "appeals", "appeals_revised"):
            self.assertEqual(s[k], 0, k)

    def test_inputs_are_cleaned(self):
        self.page(V1)
        did = self.c.register_document("  Acme \n\t Terms\x00 ", " Acme  \x07Corp ", " terms ", URL)
        d = self.doc(did)
        self.assertEqual((d.name, d.publisher, d.kind), ("Acme Terms", "Acme Corp", "TERMS"))

    def test_long_names_are_truncated(self):
        self.page(V1)
        did = self.c.register_document("N" * 400, "P" * 400, "TERMS", URL)
        d = self.doc(did)
        self.assertEqual((len(d.name), len(d.publisher)), (self.m.MAX_NAME_CHARS, self.m.MAX_PUBLISHER_CHARS))

    def test_every_kind_is_accepted_and_others_are_not(self):
        for i, kind in enumerate(("TERMS", "PRIVACY", "PRICING", "LICENSE", "POLICY", "OTHER")):
            self.assertEqual(self.register(kind=kind, url=f"https://example.com/k{i}"), i + 1)
        with self.revert("kind must be one of"):
            self.register(kind="CONTRACT", url="https://example.com/x")
        with self.revert("kind must be one of"):
            self.register(kind="", url="https://example.com/x")

    def test_validation_errors(self):
        self.page(V1)
        cases = [
            (("ab", "Acme", "TERMS", URL), "name must be at least 3"),
            (("   ", "Acme", "TERMS", URL), "name must be at least 3"),
            (("Acme ToS", "A", "TERMS", URL), "publisher must name"),
            (("Acme ToS", "!!", "TERMS", URL), "publisher must name"),
            (("Acme ToS", "  ", "TERMS", URL), "publisher must name"),
            (("Acme <<<ToS", "Acme", "TERMS", URL), r"<<<"),
            (("Acme ToS", "Ac>>>me", "TERMS", URL), r"<<<"),
            (("Acme ToS", "Acme", "TERMS", "http://example.com/t"), "public https"),
            (("Acme ToS", "Acme", "TERMS", "https://10.0.0.1/t"), "public https"),
            (("Acme ToS", "Acme", "TERMS", "https://example.com:8080/t"), "public https"),
            (("Acme ToS", "Acme", "TERMS", "https://u:p@example.com/t"), "public https"),
            (("Acme ToS", "Acme", "TERMS", "https://localhost/t"), "public https"),
        ]
        for args, pattern in cases:
            with self.subTest(args=args):
                with self.revert(pattern):
                    self.c.register_document(*args)
        self.assertEqual(self.stats()["total_documents"], 0)

    def test_minimum_lengths_are_inclusive(self):
        self.page(V1)
        did = self.c.register_document("abc", "ab", "TERMS", URL)
        self.assertEqual((self.doc(did).name, self.doc(did).publisher), ("abc", "ab"))

    def test_duplicate_pages_are_refused_whatever_the_spelling(self):
        self.register()
        for dup in (URL, "https://EXAMPLE.com/terms", "https://example.com/terms/", "https://example.com:443/terms#x"):
            with self.subTest(dup=dup):
                with self.revert(r"already tracked as document #1"):
                    self.c.register_document("Other name", "Other", "TERMS", dup, sender=self.bob)
        self.assertEqual(self.register(url="https://example.com/Terms"), 2)  # path case is significant
        self.assertEqual(self.register(url="https://example.com/terms?lang=fr"), 3)

    def test_per_owner_cap(self):
        cap = self.m.MAX_DOCS_PER_OWNER
        for i in range(cap):
            self.register(url=f"https://example.com/d{i}")
        with self.revert("maximum number of documents"):
            self.register(url="https://example.com/one-too-many")
        self.assertEqual(self.register(url="https://example.com/bobs", sender=self.bob), cap + 1)
        self.assertEqual(len(self.c.list_owner_documents(self.alice.as_hex)), cap)

    def test_unreadable_page_is_refused_and_leaves_no_trace(self):
        self.chain.web.clear()
        with self.revert("EXTERNAL|could not be fetched"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.page("   \n\t  ")
        with self.revert("could not be fetched"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.assertEqual(self.stats()["total_documents"], 0)
        self.assertEqual(self.register(), 1)  # no id was burned

    def test_too_little_text(self):
        self.page(TINY)
        with self.revert("too little readable text"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.page("x" * (self.m.MIN_TEXT_CHARS - 1))
        with self.revert("too little readable text"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.page("x" * self.m.MIN_TEXT_CHARS)
        self.assertEqual(self.c.register_document("Acme ToS", "Acme", "TERMS", URL), 1)

    def test_text_is_normalised_before_it_is_stored(self):
        raw = "Line one.\n\n   Line   two.\t\tLine three\x00\x07 here. " + "word " * 40
        self.page(raw)
        did = self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        stored = self.c.get_revision_text(self.doc(did).latest_rev_id)
        self.assertEqual(stored, collapse(raw.replace("\x00", "").replace("\x07", "")))
        self.assertNotIn("  ", stored)

    def test_text_is_truncated_to_the_tracked_region(self):
        limit = self.m.MAX_TEXT_CHARS
        self.page(("sentence number one here. " * 1000))
        did = self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        r = self.rev(self.doc(did).latest_rev_id)
        self.assertLessEqual(r.text_len, limit)
        self.assertEqual(len(self.c.get_revision_text(r.id)), r.text_len)
        self.assertFalse(self.c.get_revision_text(r.id).endswith(" "))

    def test_baseline_text_is_exactly_what_hashes_to_the_chain(self):
        self.register()
        text = self.c.get_revision_text(1)
        self.assertEqual(self.m._digest(text), self.rev(1).text_hash)

    def test_a_transaction_without_a_usable_timestamp_is_refused(self):
        self.page(V1)
        self.chain.raw_override = {}
        with self.revert("no transaction timestamp"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.chain.raw_override = {"datetime": ""}
        with self.revert("no transaction timestamp"):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        for junk in ("garbage", "2026-13-45T99:99:99Z", 12345):
            self.chain.raw_override = {"datetime": junk}
            with self.revert("malformed transaction timestamp"):
                self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.assertEqual(self.stats()["total_documents"], 0)

    def test_timestamp_formats(self):
        self.page(V1)
        for i, (raw, expected) in enumerate((("2026-01-01T00:00:00Z", 1767225600), ("2026-01-01T00:00:00+00:00", 1767225600),
                                              ("2026-01-01T02:00:00+02:00", 1767225600), ("2026-01-01T00:00:00.750000Z", 1767225600))):
            self.chain.raw_override = {"datetime": raw}
            did = self.c.register_document("Acme ToS", "Acme", "TERMS", f"https://example.com/t{i}")
            self.assertEqual(self.doc(did).created_at, expected, raw)

    def test_registration_uses_the_transaction_clock(self):
        self.chain.advance(12345)
        self.register()
        self.assertEqual(self.doc(1).created_at, self.chain.now)

    def test_registration_validators(self):
        self.register()
        honest = self.chain.run_validator()
        self.assertTrue(honest)
        self.page(V1 + " edited")
        self.assertFalse(self.chain.run_validator())  # a validator that sees another page
        self.page(V1)
        self.assertTrue(self.chain.run_validator())
        self.chain.web.clear()
        self.assertFalse(self.chain.run_validator())  # validator cannot read it, leader could
        for bad in ("nope", 7, None, [], {}, {"ok": True}):
            if bad is None:
                continue
            self.assertFalse(self.chain.run_validator(leader_result=bad), bad)
        self.assertFalse(self.chain.run_validator(raw=self.m.gl.vm.UserError("boom")))

    def test_registration_agrees_on_a_failed_fetch_only_if_the_validator_also_fails(self):
        self.chain.web.clear()
        with self.assertRaises(Revert):
            self.c.register_document("Acme ToS", "Acme", "TERMS", URL)
        self.assertEqual(self.chain.leader_result(), {"ok": False})
        self.assertTrue(self.chain.run_validator())
        self.page(V1)
        self.assertFalse(self.chain.run_validator())

    def test_unknown_ids_in_views(self):
        for call in (lambda: self.c.get_document(1), lambda: self.c.get_revision(1),
                     lambda: self.c.get_revision_text(1), lambda: self.c.get_redline(1)):
            with self.assertRaises(Revert):
                call()
        self.assertEqual(self.c.list_revisions(1, 0, 10), [])
        self.assertEqual(self.c.list_documents(0, 10), [])
        with self.revert("owner is not a valid address"):
            self.c.list_owner_documents("nonsense")


class ListViews(RedlineCase):
    def test_pagination_bounds(self):
        for i in range(7):
            self.register(url=f"https://example.com/d{i}")
        ids = lambda off, lim: [d.id for d in self.c.list_documents(off, lim)]
        self.assertEqual(ids(0, 3), [1, 2, 3])
        self.assertEqual(ids(3, 3), [4, 5, 6])
        self.assertEqual(ids(6, 3), [7])
        self.assertEqual(ids(7, 3), [])
        self.assertEqual(ids(1000, 3), [])
        self.assertEqual(ids(0, 0), [])
        self.assertEqual(len(ids(0, 10**9)), 7)

    def test_page_size_is_capped(self):
        cap = self.m.MAX_PAGE_SIZE
        # 60 documents from three owners (20 each)
        owners = (self.alice, self.bob, self.carol)
        for i in range(cap + 10):
            self.register(url=f"https://example.com/p{i}", sender=owners[i % 3])
        self.assertEqual(len(self.c.list_documents(0, 10**9)), cap)
        self.assertEqual(len(self.c.list_documents(cap, 10**9)), 10)

    def test_limits_view_matches_the_constants(self):
        lim = self.c.get_limits()
        m = self.m
        pairs = {
            "max_name_chars": m.MAX_NAME_CHARS, "min_name_chars": m.MIN_NAME_CHARS,
            "max_publisher_chars": m.MAX_PUBLISHER_CHARS, "min_publisher_chars": m.MIN_PUBLISHER_CHARS,
            "max_url_chars": m.MAX_URL_CHARS, "min_text_chars": m.MIN_TEXT_CHARS, "max_text_chars": m.MAX_TEXT_CHARS,
            "max_unit_chars": m.MAX_UNIT_CHARS, "max_docs_per_owner": m.MAX_DOCS_PER_OWNER,
            "max_revisions_per_doc": m.MAX_REVISIONS_PER_DOC, "min_append_interval_seconds": m.MIN_APPEND_INTERVAL_SECONDS,
            "min_retained_bp": m.MIN_RETAINED_BP, "heavy_churn_bp": m.HEAVY_CHURN_BP,
            "prompt_max_units": m.PROMPT_MAX_UNITS, "prompt_max_side_chars": m.PROMPT_MAX_SIDE_CHARS,
            "min_quote_chars": m.MIN_QUOTE_CHARS, "max_quote_chars": m.MAX_QUOTE_CHARS, "max_page_size": m.MAX_PAGE_SIZE,
        }
        for key, value in pairs.items():
            self.assertEqual(lim[key], value, key)
        self.assertEqual(lim["kinds"], "TERMS,PRIVACY,PRICING,LICENSE,POLICY,OTHER")
        self.assertEqual(self.c.get_version(), m.VERSION)
        self.assertEqual(lim["version"], m.VERSION)


if __name__ == "__main__":
    unittest.main()

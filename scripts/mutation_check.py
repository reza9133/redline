#!/usr/bin/env python3
"""Mutation check: inject deliberate bugs into contracts/redline.py and prove the
test-suite notices every one of them.

    python3 scripts/mutation_check.py            # run all mutations
    python3 scripts/mutation_check.py -k cooldown   # only mutations whose id contains "cooldown"
    python3 scripts/mutation_check.py --resume /tmp/m.tsv --budget 240   # resumable, time-boxed chunks

Known EQUIVALENT mutants (deliberately not listed, they cannot change behaviour):
  * dropping the min(10000, ...) cap on churn: the numerator is bounded by the denominator by construction;
  * building the topic table for ALL units instead of the changed ones: the extra entries are never read;
  * dropping the bool check in _normalize_effect: str(True) = "TRUE" is not on the scale anyway;
  * dropping the explicit "@" or "%" check on the URL authority: a host, an IPv4 literal or a port containing
    either character is rejected by the DNS-label / IPv4 / port rules anyway (the explicit check is kept on purpose);
  * dropping the "Z" -> "+00:00" rewrite when parsing the timestamp: Python >= 3.11 understands "Z" itself
    (the rewrite is kept for older interpreters);
  * dropping the page-size cap in list_revisions: a document holds at most MAX_REVISIONS_PER_DOC (25) revisions,
    below MAX_PAGE_SIZE (50), so the cap can never bind (it is kept in case the limits change);
  * dropping the offset clamp in the list views: an offset past the end yields an empty range either way;
  * "trusting" the leader's kind AFTER it was already compared with the validator's own plan;
  * dropping ONE of the three summary caps: each is redundant with the others (the list below caps all three).

Each mutation replaces an exact piece of source text. A mutation that SURVIVES (the
suite stays green) is either a hole in the tests or an equivalent mutant (a change
that cannot alter behaviour); both must be examined by a human.
Exit code 0 only if every mutation is killed (2 = not finished yet, run again).
"""
import argparse
import concurrent.futures
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "contracts" / "redline.py"

# (id, exact text to find, replacement, nth occurrence to change or 0 for all)
M = [
    # ---- append: guards and ordering -------------------------------------------------
    ("cooldown-boundary", "now - int(doc.last_revision_at) < MIN_APPEND_INTERVAL_SECONDS", "now - int(doc.last_revision_at) <= MIN_APPEND_INTERVAL_SECONDS", 0),
    ("cooldown-removed", "now - int(doc.last_revision_at) < MIN_APPEND_INTERVAL_SECONDS", "False", 0),
    ("cooldown-from-baseline", "now - int(doc.last_revision_at) < MIN_APPEND_INTERVAL_SECONDS", "now - int(doc.created_at) < MIN_APPEND_INTERVAL_SECONDS", 0),
    ("cap-boundary", "int(doc.revision_count) >= MAX_REVISIONS_PER_DOC", "int(doc.revision_count) > MAX_REVISIONS_PER_DOC", 0),
    ("cap-removed", "int(doc.revision_count) >= MAX_REVISIONS_PER_DOC", "False", 0),
    ("site-rule-removed", "if not is_owner and not _same_site_or_archive(url, str(doc.site_url)):", "if False:", 0),
    ("site-rule-for-owner-too", "if not is_owner and not _same_site_or_archive(url, str(doc.site_url)):", "if not _same_site_or_archive(url, str(doc.site_url)):", 0),
    ("site-vs-registered-url", "_same_site_or_archive(url, str(doc.site_url))", "_same_site_or_archive(url, str(doc.url))", 0),
    ("site-vs-latest-url", "_same_site_or_archive(url, str(doc.site_url))", "_same_site_or_archive(url, str(doc.latest_url))", 0),
    ("site-url-never-moves", "            doc.site_url = url  # only the owner moves the site; an archive permalink is a reference, not a move\n", "            pass\n", 0),
    ("site-url-moved-by-archive", "if is_owner and not _is_archive(url):", "if is_owner:", 0),
    ("site-url-moved-by-anyone", "if is_owner and not _is_archive(url):", "if not _is_archive(url):", 0),
    ("reg-site-url", "            site_url=url,\n", "            site_url=\"\",\n", 0),
    ("is-owner-inverted", "is_owner = doc.owner == sender", "is_owner = doc.owner != sender", 0),
    ("append-url-check-removed", "        doc = self._get_doc(int(doc_id))\n        if not _looks_like_public_https_url(url):", "        doc = self._get_doc(int(doc_id))\n        if False:", 0),
    ("unchanged-not-refused", 'if agreed_kind == "unchanged":', "if False:", 0),
    ("blocked-not-refused", 'if agreed_kind == "blocked":', "if False:", 0),
    ("unusable-result-guard", 'if agreed_kind not in ("format_only", "judge") or not snapshot:', "if False:", 0),
    ("hash-recheck-removed", 'if text_hash != result.get("hash"):', "if False:", 0),
    ("plan-recheck-removed", 'if plan["kind"] != agreed_kind:', "if False:", 0),
    ("invalid-rating-not-refused", 'if raw_effect not in _SCALE:\n                raise gl.vm.UserError("[EXTERNAL] validators returned an invalid rating")\n            quote', 'if False:\n                raise gl.vm.UserError("[EXTERNAL] validators returned an invalid rating")\n            quote', 0),
    # ---- plan / redline -------------------------------------------------------------------
    ("plan-unchanged-shortcut", "if new_text == prev_text:", "if False:", 0),
    ("plan-format-only-removed", "if not removed and not added:", "if False:", 0),
    ("plan-blocked-for-owner", "elif rewrite and not is_owner:", "elif rewrite:", 0),
    ("plan-blocked-removed", "elif rewrite and not is_owner:", "elif False:", 0),
    ("rewrite-boundary", "rewrite = retained_bp < MIN_RETAINED_BP", "rewrite = retained_bp <= MIN_RETAINED_BP", 0),
    ("retained-uses-added", "10000 * (old_chars - removed_chars) // max(1, old_chars)", "10000 * (old_chars - added_chars) // max(1, old_chars)", 0),
    ("retained-denominator", "10000 * (old_chars - removed_chars) // max(1, old_chars)", "10000 * (old_chars - removed_chars) // max(1, new_chars)", 0),
    ("churn-sentence-level", "10000 * _word_delta_chars(removed, added) // max(1, old_chars + new_chars)", "10000 * (removed_chars + added_chars) // max(1, old_chars + new_chars)", 0),
    ("word-delta-no-abs", "len(word) * abs(count)", "len(word) * count", 0),
    ("word-delta-one-sided", "balance[word] = balance.get(word, 0) - 1", "balance[word] = balance.get(word, 0)", 0),
    ("diff-case-sensitive", "key = unit.lower()", "key = unit", 0),
    ("diff-added-never-matched", "if old_left.get(key, 0) > 0:", "if False:", 0),
    ("diff-removed-never-matched", "if new_left.get(key, 0) > 0:", "if False:", 0),
    ("diff-no-decrement", "old_left[key] -= 1", "pass", 0),
    ("split-no-list-marker", 'if ch == "." and index != last and _is_list_marker(text, index, start):', "if False:", 0),
    ("list-marker-digits-4", "len(token) <= 3:", "len(token) <= 4:", 0),
    ("list-marker-letters", "return len(token) == 1 and token.isalpha()", "return False", 0),
    ("list-marker-token-start", "token = text[start:index] if cut < 0 else text[cut + 1:index]", "token = text[cut + 1:index]", 0),
    ("split-needs-no-space", '(index == last or text[index + 1] == " ")', "True", 0),
    ("split-semicolon", 'if ch in ".!?;" and', 'if ch in ".!?" and', 0),
    ("chop-hard-cut-only", "if cut < MAX_UNIT_CHARS // 2:", "if False:", 0),
    ("chop-limit", "while len(unit) > MAX_UNIT_CHARS:", "while len(unit) > MAX_UNIT_CHARS + 50:", 0),
    ("shown-no-priority", "ordered = [u for u in units if tagged[u]] + [u for u in units if not tagged[u]]", "ordered = list(units)", 0),
    ("shown-count-cap", "len(chosen) >= PROMPT_MAX_UNITS or used + len(unit) > PROMPT_MAX_SIDE_CHARS", "used + len(unit) > PROMPT_MAX_SIDE_CHARS", 0),
    ("shown-char-cap", "len(chosen) >= PROMPT_MAX_UNITS or used + len(unit) > PROMPT_MAX_SIDE_CHARS", "len(chosen) >= PROMPT_MAX_UNITS", 0),
    ("shown-omitted-count", "return chosen, len(units) - len(chosen)", "return chosen, 0", 0),
    ("topics-substring-match", 'if any((" " + phrase + " ") in padded for phrase in phrases):', "if any(phrase in padded for phrase in phrases):", 0),
    ("topics-case", "padded = \" \" + _words(unit) + \" \"", "padded = \" \" + unit + \" \"", 0),
    ("topics-order", "for tag, _phrases in _TOPICS:\n        if any(tag in tagged[unit]", "for tag, _phrases in reversed(_TOPICS):\n        if any(tag in tagged[unit]", 0),
    ("words-keep-punctuation", '"".join(ch if ch.isalnum() else " " for ch in text.lower())', '"".join(ch for ch in text.lower())', 0),
    # ---- policy ----------------------------------------------------------------------------------
    ("sev-much-worse", 'if effect == "MUCH_WORSE":\n        severity = "HIGH"', 'if effect == "MUCH_WORSE":\n        severity = "MEDIUM"', 0),
    ("sev-worse-heavy", 'severity = "HIGH" if heavy else "MEDIUM"', 'severity = "MEDIUM"', 0),
    ("sev-worse-never-medium", 'severity = "HIGH" if heavy else "MEDIUM"', 'severity = "HIGH"', 0),
    ("sev-better-heavy", 'elif effect == "BETTER":\n        severity = "MEDIUM" if heavy else "LOW"', 'elif effect == "BETTER":\n        severity = "LOW"', 0),
    ("sev-much-better", 'elif effect == "MUCH_BETTER":\n        severity = "MEDIUM"', 'elif effect == "MUCH_BETTER":\n        severity = "LOW"', 0),
    ("sev-silent-guard-off", "silent = heavy  # \"cosmetic\" on a redline that touches sensitive ground", "silent = False", 0),
    ("sev-silent-no-severity", 'severity = "MEDIUM" if silent else "LOW"', 'severity = "LOW"', 0),
    ("sev-unclear-heavy", '    else:  # UNCLEAR\n        severity = "MEDIUM" if heavy else "LOW"', '    else:  # UNCLEAR\n        severity = "LOW"', 0),
    ("sev-rewrite-floor", 'if rewrite and severity == "LOW":', "if False:", 0),
    ("sev-heavy-topics-only", "heavy = len(topics) > 0 or churn_bp >= HEAVY_CHURN_BP", "heavy = len(topics) > 0", 0),
    ("sev-heavy-churn-only", "heavy = len(topics) > 0 or churn_bp >= HEAVY_CHURN_BP", "heavy = churn_bp >= HEAVY_CHURN_BP", 0),
    ("sev-heavy-boundary", "churn_bp >= HEAVY_CHURN_BP", "churn_bp > HEAVY_CHURN_BP", 0),
    ("finalize-no-quote-rule", 'if raw_effect != EFFECT_NEUTRAL and not quote:', "if False:", 0),
    ("finalize-neutral-too", "if raw_effect != EFFECT_NEUTRAL and not quote:", "if not quote:", 0),
    ("finalize-keeps-model-summary", 'f"No verbatim quote from the redline backed the model\'s {raw_effect} rating, so it was not recorded.",', "summary,", 0),
    ("agree-wider", "abs(_SCALE.index(a) - _SCALE.index(b)) <= 1", "abs(_SCALE.index(a) - _SCALE.index(b)) <= 2", 0),
    ("agree-strict", "abs(_SCALE.index(a) - _SCALE.index(b)) <= 1", "abs(_SCALE.index(a) - _SCALE.index(b)) <= 0", 0),
    ("agree-no-scale-check", "if a not in _SCALE or b not in _SCALE:\n        return False", "if False:\n        return False", 0),
    ("effect-group-worse", 'if effect in ("MUCH_WORSE", "WORSE"):\n        return "worse"', 'if effect in ("MUCH_WORSE",):\n        return "worse"', 0),
    ("effect-group-better", 'if effect in ("MUCH_BETTER", "BETTER"):\n        return "better"', 'if effect in ("BETTER",):\n        return "better"', 0),
    # ---- quotes -----------------------------------------------------------------------------------------
    ("quote-min-length", "MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS", "0 <= len(quote) <= MAX_QUOTE_CHARS", 0),
    ("quote-max-length", "MIN_QUOTE_CHARS <= len(quote) <= MAX_QUOTE_CHARS", "MIN_QUOTE_CHARS <= len(quote)", 0),
    ("quote-case-sensitive", "return any(needle in unit.lower() for unit in added) or any(needle in unit.lower() for unit in removed)", "return any(quote in unit for unit in added) or any(quote in unit for unit in removed)", 0),
    ("quote-removed-side-ignored", "return any(needle in unit.lower() for unit in added) or any(needle in unit.lower() for unit in removed)", "return any(needle in unit.lower() for unit in added)", 0),
    ("quote-added-side-ignored", "return any(needle in unit.lower() for unit in added) or any(needle in unit.lower() for unit in removed)", "return any(needle in unit.lower() for unit in removed)", 0),
    ("quote-anywhere", "return any(needle in unit.lower() for unit in added) or any(needle in unit.lower() for unit in removed)", 'return needle in " ".join(added + removed).lower()', 0),
    ("quote-no-strip-quotes", """.strip('"\\u201c\\u201d')""", "", 0),
    ("quote-no-truncate", "candidate = candidate[:MAX_QUOTE_CHARS].strip()", "candidate = candidate.strip()", 0),
    ("quote-no-collapse", "candidate = _collapse_whitespace(raw).strip(", "candidate = str(raw).strip(", 0),
    ("effect-no-normalize", 'label = str(value).strip().upper().replace(" ", "_").replace("-", "_")', "label = str(value)", 0),
    ("summary-never-capped", ", MAX_SUMMARY_CHARS)", ", 10**6)", 0),
    ("text-field-none", 'return "" if value is None else str(value)', "return str(value)", 0),
    ("delimiters-not-neutralised", 'return text.replace("<<<", "\\u2039\\u2039\\u2039").replace(">>>", "\\u203a\\u203a\\u203a")', "return text", 0),
    ("name-not-neutralised-in-prompt", "DOCUMENT: {_neutralize_delimiters(name)}", "DOCUMENT: {name}", 0),
    ("injection-marker-ignore", '    "ignore previous instructions",\n', "", 0),
    ("injection-marker-rate", '    "rate this as",\n', "", 0),
    ("injection-marker-brackets", '    "<<<",\n    ">>>",\n', "", 0),
    ("injection-never-flagged", "injection_flagged=_injection_flagged(plan[\"removed\"] + plan[\"added\"]),", "injection_flagged=False,", 0),
    # ---- consensus (append) ------------------------------------------------------------------------------------
    ("val-append-agreement-off", 'return _effects_agree(judged["effect"], str(leader_effect))', "return True", 1),
    ("val-appeal-agreement-off", 'return _effects_agree(judged["effect"], str(leader_effect))', "return True", 2),
    ("val-append-quote-check-off", 'if quote and not _quote_ok(quote, plan["shown_added"], plan["shown_removed"]):', "if False:", 0),
    ("val-appeal-quote-check-off", "if quote and not _quote_ok(quote, added, removed):", "if False:", 0),
    ("val-kind-check-off", 'if data.get("kind") != plan["kind"]:', "if False:", 0),
    ("val-hash-check-off", 'if data.get("hash") != _digest(mine):', "if False:", 0),
    ("val-snapshot-check-off", 'if data.get("snapshot") != mine:', "if False:", 0),
    ("val-fetch-failure-believed", 'return data.get("reason") == "fetch" and mine is None', "return True", 0),
    ("val-fetch-failure-reason", 'return data.get("reason") == "fetch" and mine is None', "return mine is None", 0),
    ("val-model-failure-ok", 'if judged is None:\n                return False\n            return _effects_agree(judged["effect"], str(leader_effect))\n\n        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)\n\n        if not result.get("ok"):', 'if judged is None:\n                return True\n            return _effects_agree(judged["effect"], str(leader_effect))\n\n        result = gl.vm.run_nondet_unsafe(leader_fn, validator_fn)\n\n        if not result.get("ok"):', 0),
    ("val-leader-rating-unchecked", 'if leader_effect not in _SCALE:\n                return False\n            # The leader', 'if False:\n                return False\n            # The leader', 0),
    ("val-return-type-unchecked", "if not isinstance(leaders_res, gl.vm.Return):\n                return False\n            data = leaders_res.calldata\n            if not isinstance(data, dict):\n                return False\n            mine", "data = leaders_res.calldata\n            mine", 0),
    ("val-leader-model-failure-ok", 'return data.get("reason") == "fetch" and mine is None', 'return data.get("reason") in ("fetch", "model")', 0),
    ("leader-appeal-model-failure-as-ok", 'return {"ok": False, "reason": "model"}\n            return {\n                "ok": True,', 'return {"ok": True, "effect": "NEUTRAL", "summary": "", "quote": ""}\n            return {\n                "ok": True,', 0),
    ("val-fetch-none-guard", "            if mine is None:\n                return False\n            plan = _plan(prev_text, mine, is_owner)", "            plan = _plan(prev_text, mine or '', is_owner)", 0),
    ("val-registration-unchecked", "return leader_fn() == leaders_res.calldata", "return True", 0),
    ("leader-model-failure-as-ok", 'return {"ok": False, "reason": "model"}\n            out["effect"]', 'return {"ok": True, "kind": "judge", "hash": out["hash"], "snapshot": snapshot, "effect": "NEUTRAL", "summary": "", "quote": ""}\n            out["effect"]', 0),
    ("leader-format-only-calls-model", 'if plan["kind"] == "format_only":\n                return out\n            judged', 'if False:\n                return out\n            judged', 0),
    # ---- registration --------------------------------------------------------------------------------------------------
    ("reg-name-min", "if len(name) < MIN_NAME_CHARS:", "if len(name) < 1:", 0),
    ("reg-publisher-alnum", "or not any(ch.isalnum() for ch in publisher)", "", 0),
    ("reg-publisher-min", "if len(publisher) < MIN_PUBLISHER_CHARS", "if len(publisher) < 1", 0),
    ("reg-kind-check", "if kind not in _KINDS:", "if False:", 0),
    ("reg-kind-upper", "kind = _clip(kind, 20).upper()", "kind = _clip(kind, 20)", 0),
    ("reg-delimiters", "if _has_prompt_delimiters(name) or _has_prompt_delimiters(publisher):", "if _has_prompt_delimiters(name):", 0),
    ("reg-url-check", "        if not _looks_like_public_https_url(url):\n            raise gl.vm.UserError(\n                \"[EXPECTED] url must be a public https:// URL (no private IPs, odd ports, or userinfo)\"\n            )\n        sender = gl.message.sender_address\n        mine", "        sender = gl.message.sender_address\n        mine", 0),
    ("reg-owner-cap-boundary", "len(mine) >= MAX_DOCS_PER_OWNER", "len(mine) > MAX_DOCS_PER_OWNER", 0),
    ("reg-owner-cap-removed", "len(mine) >= MAX_DOCS_PER_OWNER", "False", 0),
    ("reg-duplicate-check", "if existing is not None:", "if False:", 0),
    ("reg-fingerprint-raw-url", "fingerprint = _digest(_canonical_url(url))", "fingerprint = _digest(url)", 0),
    ("reg-fingerprint-not-stored", "self.fingerprints[fingerprint] = u256(doc_id)", "pass", 0),
    ("reg-min-text-boundary", "if len(text) < MIN_TEXT_CHARS:", "if len(text) <= MIN_TEXT_CHARS:", 0),
    ("reg-min-text-removed", "if len(text) < MIN_TEXT_CHARS:", "if False:", 0),
    ("reg-fetch-failure-ignored", "if text is None:\n            raise", "if False:\n            raise", 0),
    ("reg-name-not-truncated", "[:MAX_NAME_CHARS]\n        publisher", "\n        publisher", 0),
    ("reg-owner-not-recorded", "            owner=sender,\n            name=name,", "            owner=Address(_ZERO_ADDRESS_HEX),\n            name=name,", 0),
    ("reg-owner-index", "self.owner_docs.get_or_insert_default(sender).append(u256(doc_id))", "pass", 0),
    ("reg-doc-index", "self.doc_ids.append(u256(doc_id))", "pass", 0),
    ("reg-baseline-chain", "chain_head = _chain(\"\", text_hash)", "chain_head = text_hash", 0),
    ("reg-event-removed", "DocumentRegistered(u256(doc_id), chain_head=chain_head).emit()", "pass", 0),
    ("reg-created-at", "created_at=u256(now),\n            last_revision_at=u256(now),", "created_at=u256(0),\n            last_revision_at=u256(now),", 0),
    ("reg-baseline-severity", "severity=SEVERITY_NONE,", 'severity="LOW",', 0),
    # ---- append: persistence --------------------------------------------------------------------------------------------
    ("chain-formula", 'return _digest(prev_chain_hash + ":" + text_hash)', 'return _digest(text_hash + ":" + prev_chain_hash)', 0),
    ("chain-head-not-updated", "        doc.chain_head = chain_hash\n", "", 0),
    ("last-revision-not-updated", "        doc.last_revision_at = u256(now)\n", "", 0),
    ("latest-url-not-updated", "        doc.latest_url = url\n", "", 0),
    ("latest-hash-not-updated", "        doc.latest_hash = text_hash\n", "", 0),
    ("latest-rev-not-updated", "        doc.latest_rev_id = u256(rev_id)\n", "", 0),
    ("revision-count-not-updated", "        doc.revision_count = u256(seq + 1)\n", "", 0),
    ("rev-list-not-updated", "self.doc_revs.get_or_insert_default(u256(int(doc_id))).append(u256(rev_id))", "pass", 0),
    ("rev-text-not-stored", "        self.rev_texts[u256(rev_id)] = snapshot\n", "", 0),
    ("rev-redline-not-stored", "        self.rev_redlines[u256(rev_id)] = _redline_json(plan)\n", "", 0),
    ("rev-seq-wrong", "            seq=u256(seq),", "            seq=u256(seq + 1),", 0),
    ("rev-submitter-wrong", "            url=url,\n            submitter=sender,\n            text_hash=text_hash,\n            chain_hash=chain_hash,", "            url=url,\n            submitter=doc.owner,\n            text_hash=text_hash,\n            chain_hash=chain_hash,", 0),
    ("rev-topics-not-stored", 'topics=",".join(plan["topics"]),', 'topics="",', 0),
    ("rev-judged-always", "judged=judged_by_model,", "judged=True,", 0),
    ("rev-silent-not-stored", "silent_flag=silent,", "silent_flag=False,", 0),
    ("rev-rewrite-not-stored", 'rewrite=bool(plan["rewrite"]),', "rewrite=False,", 0),
    ("rev-created-at", "            created_at=u256(now),\n            appealed=False,\n            appeal_outcome=\"\",\n            appeal_by=zero,\n            appeal_at=u256(0),\n            prev_effect=\"\",\n        )\n        self.rev_texts[u256(rev_id)] = snapshot", "            created_at=u256(0),\n            appealed=False,\n            appeal_outcome=\"\",\n            appeal_by=zero,\n            appeal_at=u256(0),\n            prev_effect=\"\",\n        )\n        self.rev_texts[u256(rev_id)] = snapshot", 0),
    ("rev-added-units", 'added_units=u256(len(plan["added"])),', 'added_units=u256(len(plan["removed"])),', 0),
    ("rev-id-not-incremented", "        rev_id = int(self.next_rev_id)\n        self.next_rev_id = u256(rev_id + 1)\n        seq", "        rev_id = int(self.next_rev_id)\n        seq", 0),
    ("format-only-summary-model", 'summary = "Only capitalisation or whitespace changed; no sentence differs."', 'summary = "x"', 0),
    ("format-only-counter", "            self.n_format_only = u256(int(self.n_format_only) + 1)\n", "            pass\n", 0),
    ("rewrite-doc-counter", "            doc.rewrites = u256(int(doc.rewrites) + 1)\n", "", 0),
    ("rewrite-global-counter", "            self.n_rewrites = u256(int(self.n_rewrites) + 1)\n", "", 0),
    ("append-event-removed", "RevisionRecorded(u256(rev_id), u256(int(doc_id)), effect=effect, severity=severity).emit()", "pass", 0),
    ("account-not-called", "        self._account(doc, effect, severity, silent, 1)\n\n        RevisionRecorded", "        RevisionRecorded", 0),
    # ---- accounting -----------------------------------------------------------------------------------------------------
    ("acct-effect-bump", "        self._bump_effect(effect, delta)\n        self._bump_severity", "        self._bump_severity", 0),
    ("acct-severity-bump", "        self._bump_severity(severity, delta)\n        if silent:", "        if silent:", 0),
    ("acct-global-silent", "            self.n_silent = u256(int(self.n_silent) + delta)\n", "", 0),
    ("acct-doc-silent", "            doc.silent = u256(int(doc.silent) + delta)\n", "", 0),
    ("acct-better", "            doc.better = u256(int(doc.better) + delta)", "            doc.neutral = u256(int(doc.neutral) + delta)", 0),
    ("acct-worse", "            doc.worse = u256(int(doc.worse) + delta)", "            doc.unclear = u256(int(doc.unclear) + delta)", 0),
    ("acct-neutral", "            doc.neutral = u256(int(doc.neutral) + delta)\n        else:", "            doc.better = u256(int(doc.better) + delta)\n        else:", 0),
    ("acct-unclear", "            doc.unclear = u256(int(doc.unclear) + delta)\n        if severity", "            doc.neutral = u256(int(doc.neutral) + delta)\n        if severity", 0),
    ("acct-high", '        if severity == "HIGH":\n            doc.high = u256(int(doc.high) + delta)', '        if severity == "MEDIUM":\n            doc.high = u256(int(doc.high) + delta)', 0),
    ("bump-effect-start", "base = 0 if current is None else int(current)\n        self.effect_counts[effect]", "base = 0\n        self.effect_counts[effect]", 0),
    ("bump-severity-start", "base = 0 if current is None else int(current)\n        self.severity_counts[severity]", "base = 0\n        self.severity_counts[severity]", 0),
    # ---- appeal -----------------------------------------------------------------------------------------------------------------
    ("appeal-baseline-allowed", "if int(rev.seq) == 0:", "if False:", 0),
    ("appeal-format-only-allowed", "if not rev.judged:", "if False:", 0),
    ("appeal-repeat-allowed", "if rev.appealed:", "if False:", 0),
    ("appeal-flag-not-set", "        rev.appealed = True\n", "", 0),
    ("appeal-outcome-inverted", "outcome = APPEAL_CONFIRMED if new_effect == old_effect else APPEAL_REVISED", "outcome = APPEAL_CONFIRMED if new_effect != old_effect else APPEAL_REVISED", 0),
    ("appeal-by-not-set", "        rev.appeal_by = gl.message.sender_address\n", "", 0),
    ("appeal-at-not-set", "        rev.appeal_at = u256(now)\n", "", 0),
    ("appeal-count", "        self.n_appeals = u256(int(self.n_appeals) + 1)\n", "", 0),
    ("appeal-revised-count", "            self.n_appeals_revised = u256(int(self.n_appeals_revised) + 1)\n", "", 0),
    ("appeal-old-not-removed", "            self._account(doc, old_effect, old_severity, old_silent, -1)\n", "", 0),
    ("appeal-new-not-added", "            self._account(doc, new_effect, new_severity, new_silent, 1)\n", "", 0),
    ("appeal-prev-effect", "            rev.prev_effect = old_effect\n", "", 0),
    ("appeal-raw-effect", "            rev.raw_effect = raw_effect\n", "", 0),
    ("appeal-effect", "            rev.effect = new_effect\n", "", 0),
    ("appeal-severity", "            rev.severity = new_severity\n", "", 0),
    ("appeal-silent", "            rev.silent_flag = new_silent\n", "", 0),
    ("appeal-summary", "            rev.summary = new_summary\n", "", 0),
    ("appeal-quote", "            rev.quote = new_quote\n", "", 0),
    ("appeal-severity-ignores-rewrite", "topics, int(rev.churn_bp), bool(rev.rewrite))", "topics, int(rev.churn_bp), False)", 0),
    ("appeal-severity-ignores-topics", "new_severity, new_silent = _severity(new_effect, topics,", "new_severity, new_silent = _severity(new_effect, [],", 0),
    ("appeal-uses-other-doc", "doc = self._get_doc(int(rev.doc_id))\n        stored", "doc = self._get_doc(1)\n        stored", 0),
    ("appeal-event-removed", "RevisionAppealed(u256(int(rev_id)), outcome=outcome).emit()", "pass", 0),
    ("appeal-invalid-rating-allowed", 'if raw_effect not in _SCALE:\n            raise gl.vm.UserError("[EXTERNAL] validators returned an invalid rating")\n        quote = _ground_quote(str(result.get("quote", "")), added, removed)', 'if False:\n            raise gl.vm.UserError("[EXTERNAL] validators returned an invalid rating")\n        quote = _ground_quote(str(result.get("quote", "")), added, removed)', 0),
    ("appeal-unreadable-redline", 'raise gl.vm.UserError("[EXTERNAL] the stored redline is unreadable")', "pass", 0),
    # ---- URLs / identity ------------------------------------------------------------------------------------------------------------
    ("url-multicast-mask", "(0xE0000000, 3),", "(0xE0000000, 4),", 0),
    ("url-cgnat-mask", "(0x64400000, 10),", "(0x64400000, 11),", 0),
    ("url-172-mask", "(0xAC100000, 12),", "(0xAC100000, 13),", 0),
    ("url-198-mask", "(0xC6120000, 15),", "(0xC6120000, 16),", 0),
    ("url-link-local-removed", "    (0xA9FE0000, 16),  # 169.254.0.0/16 link-local, cloud metadata\n", "", 0),
    ("url-loopback-removed", "    (0x7F000000, 8),  # 127.0.0.0/8 loopback\n", "", 0),
    ("url-zero-net-removed", "    (0x00000000, 8),  # 0.0.0.0/8 \"this network\"\n", "", 0),
    ("url-testnet3-removed", "    (0xCB007100, 24),  # 203.0.113.0/24 TEST-NET-3\n", "", 0),
    ("url-leading-zero", 'if len(part) > 1 and part[0] == "0":', "if False:", 0),
    ("url-octet-range", "if octet > 255:", "if octet > 256:", 0),
    ("url-ipv4-parts", "if len(parts) != 4:", "if len(parts) < 3:", 0),
    ("url-hex-host", 'if last.isdigit() or last.startswith("0x"):', "if last.isdigit():", 0),
    ("url-port", 'if colon and port not in ("", "443"):', 'if colon and port not in ("", "443", "8443"):', 0),
    ("url-backslash", 'if "\\\\" in url or url[:8].lower() != "https://":', 'if url[:8].lower() != "https://":', 0),
    ("url-scheme", 'url[:8].lower() != "https://"', 'url[:7].lower() != "http://" and url[:8].lower() != "https://"', 0),
    ("url-controls", "if any(ord(ch) < 33 or ord(ch) > 126 for ch in url):", "if any(ord(ch) < 32 or ord(ch) > 126 for ch in url):", 0),
    ("url-non-ascii", "if any(ord(ch) < 33 or ord(ch) > 126 for ch in url):", "if any(ord(ch) < 33 for ch in url):", 0),
    ("url-length", "0 < len(url) <= MAX_URL_CHARS", "0 < len(url) <= MAX_URL_CHARS + 1", 0),
    ("url-blocked-suffix", "return not host.endswith(_BLOCKED_SUFFIXES)", "return True", 0),
    ("url-blocked-hosts", 'host in _BLOCKED_HOSTS', "False", 0),
    ("url-dot-required", 'if not host or "." not in host or host in _BLOCKED_HOSTS:', "if not host or host in _BLOCKED_HOSTS:", 0),
    ("url-trailing-dot-multi", 'if host.endswith("."):\n        host = host[:-1]\n    if not host or "." not in host', 'host = host.rstrip(".")\n    if not host or "." not in host', 0),
    ("url-label-hyphen", 'if label[0] == "-" or label[-1] == "-":', "if False:", 0),
    ("url-label-length", "if not (1 <= len(label) <= 63):", "if not (1 <= len(label) <= 64):", 0),
    ("url-tld-length", "if len(last) < 2:", "if len(last) < 1:", 0),
    ("url-label-charset", "ch.isascii() and (ch.isalnum() or ch == \"-\")", "(ch.isalnum() or ch == \"-\")", 0),
    ("url-authority-stop-query", 'if ch in "/?#":\n            return rest[:index]', 'if ch in "/":\n            return rest[:index]', 0),
    ("canon-no-lower", "authority = rest[:cut].lower()", "authority = rest[:cut]", 0),
    ("canon-fragment", 'base = url.split("#", 1)[0]', "base = url", 0),
    ("canon-port", 'if authority.endswith(":443"):\n        authority = authority[:-4]', "pass", 0),
    ("canon-trailing-slash", 'if tail.endswith("/"):\n        tail = tail[:-1]', "pass", 0),
    ("canon-trailing-dot", 'if authority.endswith("."):\n        authority = authority[:-1]\n    if tail', "if tail", 0),
    ("canon-cut-query", 'if ch in "/?":\n            cut = index', 'if ch in "/":\n            cut = index', 0),
    ("site-www", 'return host[4:] if host.startswith("www.") else host', "return host", 0),
    ("site-port", 'host = _authority_of(url).lower().split(":", 1)[0]', "host = _authority_of(url).lower()", 0),
    ("site-archive", "_site_of(new_url) == _site_of(site_url) or _is_archive(new_url)", "_site_of(new_url) == _site_of(site_url)", 0),
    ("site-archive-any", "_site_of(new_url) == _site_of(site_url) or _is_archive(new_url)", "True", 0),
    ("site-same-removed", "_site_of(new_url) == _site_of(site_url) or _is_archive(new_url)", "_is_archive(new_url)", 0),
    ("site-archive-set", "def _is_archive(url: str) -> bool:\n    return _site_of(url) in _ARCHIVE_SITES", "def _is_archive(url: str) -> bool:\n    return _site_of(url) == \"web.archive.org\"", 0),
    # ---- hygiene / fetch ------------------------------------------------------------------------------------------------------------------
    ("fetch-no-truncate", "[:MAX_TEXT_CHARS].strip()", ".strip()", 0),
    ("fetch-no-printable-filter", 'printable = "".join(ch for ch in str(raw) if ch.isprintable() or ch.isspace())', "printable = str(raw)", 0),
    ("fetch-no-collapse", "text = _collapse_whitespace(printable)[:MAX_TEXT_CHARS].strip()", "text = printable[:MAX_TEXT_CHARS].strip()", 0),
    ("fetch-exception-escapes", "    except Exception:\n        return None\n    printable", "    except ZeroDivisionError:\n        return None\n    printable", 0),
    ("judge-exception-escapes", "    except Exception:\n        return None\n    if effect is None:", "    except ZeroDivisionError:\n        return None\n    if effect is None:", 0),
    ("clip-no-filter", 'ch in ("\\t", "\\n") or (ord(ch) >= 32 and ord(ch) != 127)', "True", 0),
    ("clip-no-limit", "return printable.strip()[:limit]", "return printable.strip()", 0),
    ("now-missing-ok", 'if not raw:\n        raise gl.vm.UserError("[EXPECTED] no transaction timestamp available")', "if False:\n        pass", 0),
    # ---- views ----------------------------------------------------------------------------------------------------------------------------------
    ("view-doc-page-cap", "end = min(start + min(int(limit), MAX_PAGE_SIZE), total)\n        return [self.documents", "end = min(start + int(limit), total)\n        return [self.documents", 0),
    ("view-find-invalid", "if not _looks_like_public_https_url(url):\n            return u256(0)", "if False:\n            return u256(0)", 0),
    ("view-find-canonical", "found = self.fingerprints.get(_digest(_canonical_url(url)))", "found = self.fingerprints.get(_digest(url))", 0),
    ("view-owner-invalid", 'raise gl.vm.UserError("[EXPECTED] owner is not a valid address")', "return []", 0),
    ("view-text-unknown", "        self._get_rev(int(rev_id))\n        text = self.rev_texts", "        text = self.rev_texts", 0),
    ("view-redline-unknown", "        self._get_rev(int(rev_id))\n        stored = self.rev_redlines", "        stored = self.rev_redlines", 0),
    ("view-stats-severity", 'severity_n("MEDIUM"),', 'severity_n("LOW"),', 0),
    ("view-stats-total", '"total_revisions": int(self.next_rev_id) - 1,', '"total_revisions": int(self.next_rev_id),', 0),
    ("view-limits-interval", '"min_append_interval_seconds": MIN_APPEND_INTERVAL_SECONDS,', '"min_append_interval_seconds": 0,', 0),
    # ---- constants the fixtures and tests pin down ------------------------------------------------------------------------------------------
    ("const-retained", "MIN_RETAINED_BP = 2000", "MIN_RETAINED_BP = 6000", 0),
    ("const-heavy-churn", "HEAVY_CHURN_BP = 2500", "HEAVY_CHURN_BP = 100", 0),
    ("const-min-text", "MIN_TEXT_CHARS = 120", "MIN_TEXT_CHARS = 5000", 0),
    ("const-prompt-units", "PROMPT_MAX_UNITS = 24", "PROMPT_MAX_UNITS = 2", 0),
    ("const-min-quote", "MIN_QUOTE_CHARS = 8", "MIN_QUOTE_CHARS = 30", 0),
    ("const-unit-chars", "MAX_UNIT_CHARS = 400", "MAX_UNIT_CHARS = 40", 0),
    ("const-docs-per-owner", "MAX_DOCS_PER_OWNER = 20", "MAX_DOCS_PER_OWNER = 2", 0),
    ("const-revisions-per-doc", "MAX_REVISIONS_PER_DOC = 25", "MAX_REVISIONS_PER_DOC = 3", 0),
    ("const-page-size", "MAX_PAGE_SIZE = 50", "MAX_PAGE_SIZE = 5", 0),
    ("const-name-max", "MAX_NAME_CHARS = 100", "MAX_NAME_CHARS = 10", 0),
    ("const-cooldown", "MIN_APPEND_INTERVAL_SECONDS = 60", "MIN_APPEND_INTERVAL_SECONDS = 600", 0),
]


def apply(source: str, find: str, repl: str, nth: int) -> str:
    count = source.count(find)
    if count == 0:
        raise SystemExit(f"mutation text not found (stale mutation?): {find[:70]!r}")
    if nth == 0:
        return source.replace(find, repl)
    if nth > count:
        raise SystemExit(f"mutation wants occurrence {nth} but there are only {count}: {find[:70]!r}")
    index = -1
    for _ in range(nth):
        index = source.index(find, index + 1)
    return source[:index] + repl + source[index + len(find):]


def run_one(item):
    mid, find, repl, nth, source = item
    mutated = apply(source, find, repl, nth)
    if mutated == source:
        return mid, "NOOP"
    try:
        compile(mutated, f"<mutant {mid}>", "exec")
    except SyntaxError:
        return mid, "INVALID"  # a mutant that does not even compile proves nothing: fix the mutation
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "redline.py"
        path.write_text(mutated)
        env = dict(os.environ, REDLINE_CONTRACT=str(path), PYTHONDONTWRITEBYTECODE="1")
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(ROOT / "tests" / "contract"), "-f", "-q"],
                env=env, cwd=str(ROOT), capture_output=True, text=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            return mid, "killed"  # a mutation that hangs the suite is detected too
    return mid, ("survived" if proc.returncode == 0 else "killed")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="only mutations whose id contains this text")
    ap.add_argument("-j", type=int, default=max(1, (os.cpu_count() or 2)), help="parallel workers")
    ap.add_argument("--resume", metavar="FILE", help="append each outcome to FILE and skip ids already recorded there")
    ap.add_argument("--budget", type=float, default=0, help="stop starting new mutations after this many seconds (use with --resume)")
    args = ap.parse_args()
    source = SOURCE.read_text()
    ids = [m[0] for m in M]
    assert len(ids) == len(set(ids)), "duplicate mutation ids"
    selected = [m for m in M if args.k in m[0]]

    done: dict = {}
    if args.resume and Path(args.resume).exists():
        for line in Path(args.resume).read_text().splitlines():
            if line.strip():
                mid, outcome = line.split("\t")
                done[mid] = outcome
    todo = [m for m in selected if m[0] not in done]

    started = time.time()
    stopped_early = False
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.j) as pool:
        pending = {}
        queue = list(todo)
        while queue or pending:
            while queue and len(pending) < args.j and not (args.budget and time.time() - started > args.budget):
                m = queue.pop(0)
                pending[pool.submit(run_one, (m[0], m[1], m[2], m[3], source))] = m[0]
            if not pending:
                stopped_early = bool(queue)
                break
            finished, _ = concurrent.futures.wait(list(pending), return_when=concurrent.futures.FIRST_COMPLETED)
            for fut in finished:
                mid, outcome = fut.result()
                pending.pop(fut)
                done[mid] = outcome
                print(f"[{len(done):3d}/{len(selected)}] {outcome:8s} {mid}", flush=True)
                if args.resume:
                    with open(args.resume, "a") as handle:
                        handle.write(f"{mid}\t{outcome}\n")
            if args.budget and time.time() - started > args.budget and not pending:
                stopped_early = bool(queue)
                break

    results = {m[0]: done[m[0]] for m in selected if m[0] in done}
    survivors = [k for k, v in results.items() if v == "survived"]
    noops = [k for k, v in results.items() if v in ("NOOP", "INVALID")]
    killed = [k for k, v in results.items() if v == "killed"]
    remaining = len(selected) - len(results)
    print(f"\n{len(killed)} killed, {len(survivors)} survived, {len(noops)} no-ops, {remaining} not run yet")
    for k in survivors:
        print("  SURVIVED:", k)
    for k in noops:
        print("  NOOP    :", k)
    if remaining:
        return 2  # incomplete: run again with the same --resume file
    return 1 if survivors or noops else 0


if __name__ == "__main__":
    sys.exit(main())

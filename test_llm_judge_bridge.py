#!/usr/bin/env python3
"""Offline tests for llm_judge_bridge.py -- no server needed, everything
mocked. Until now this module only had ad hoc verification scripts, never a
committed test file; that gap is exactly how the truncation-direction bug
(oldest-knots-first instead of most-recent) shipped and stayed live until a
benchmark sweep happened to surface it. These tests exist so that class of
mistake gets caught here next time, not three sessions later.
"""

import json
import sys
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_judge_bridge as ljb                                    # noqa: E402


def fake_response(text, reasoning=""):
    payload = {"choices": [{"message": {"content": text,
                                        "reasoning_content": reasoning},
                            "finish_reason": "stop"}]}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(payload).encode()
    return Resp()


KNOTS = ["Alex works at TechCorp", "Sam has a dog named Rex",
        "The meeting is Friday", "Priya's car is in the garage",
        "The class was cancelled"]


class TestParsing(unittest.TestCase):
    def test_basic_order_and_selection(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("5, 4, 1")):
            judge = ljb.make_llm_judge()
            out = judge("what did she cancel?", KNOTS, topk=10)
        self.assertEqual(out, [KNOTS[4], KNOTS[3], KNOTS[0]])

    def test_none_response_is_empty(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("0")):
            judge = ljb.make_llm_judge()
            out = judge("irrelevant", KNOTS, topk=10)
        self.assertEqual(out, [])

    def test_dedupe_out_of_range_and_junk_tokens(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("2, 2, 99, banana, 3")):
            judge = ljb.make_llm_judge()
            out = judge("q", KNOTS, topk=10)
        self.assertEqual(out, [KNOTS[1], KNOTS[2]])

    def test_topk_caps_result(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("1,2,3,4,5")):
            judge = ljb.make_llm_judge()
            out = judge("q", KNOTS, topk=2)
        self.assertEqual(len(out), 2)


class TestErrorHandling(unittest.TestCase):
    def test_empty_content_is_reported_as_error_not_raised(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("", reasoning="x" * 500)):
            judge = ljb.make_llm_judge()
            out = judge("q", KNOTS, topk=10)
        self.assertEqual(out, [])
        self.assertEqual(len(judge.errors), 1)
        self.assertIn("reasoning-budget", judge.errors[0])

    def test_network_error_is_captured_not_raised(self):
        import urllib.error
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("refused")):
            judge = ljb.make_llm_judge()
            out = judge("q", KNOTS, topk=10)
        self.assertEqual(out, [])
        self.assertEqual(judge.errors, ["URLError"])

    def test_probe_unreachable_returns_false_not_raise(self):
        self.assertFalse(ljb.probe("http://127.0.0.1:1/health", timeout=1))


class TestCachingAndInstrumentation(unittest.TestCase):
    def test_repeat_query_is_cached_not_re_sent(self):
        calls = {"n": 0}

        def counting(*a, **kw):
            calls["n"] += 1
            return fake_response("1")
        with mock.patch("urllib.request.urlopen", side_effect=counting):
            judge = ljb.make_llm_judge()
            for _ in range(9):      # 1 real query + 8 control-seed repeats
                judge("same query", KNOTS, topk=10)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(judge.calls, 9)
        self.assertEqual(judge.network_calls, 1)

    def test_error_recorded_once_per_unique_failure_not_per_replay(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("")):
            judge = ljb.make_llm_judge()
            for _ in range(5):
                judge("repeatedly failing query", KNOTS, topk=10)
        self.assertEqual(judge.calls, 5)
        self.assertEqual(judge.network_calls, 1)
        self.assertEqual(len(judge.errors), 1)

    def test_seconds_accumulates_only_for_real_network_calls(self):
        import time

        def slow(*a, **kw):
            time.sleep(0.02)
            return fake_response("1")
        with mock.patch("urllib.request.urlopen", side_effect=slow):
            judge = ljb.make_llm_judge()
            judge("q1", KNOTS, topk=10)
            judge("q1", KNOTS, topk=10)     # cached, must not add time
            judge("q2", KNOTS, topk=10)
        self.assertGreaterEqual(judge.seconds, 0.03)   # ~2 real calls
        self.assertLess(judge.seconds, 0.5)             # not 3x either


class TestCandidatePoolTruncation(unittest.TestCase):
    """The bug this file exists to stop from recurring: a truncated pool
    must keep the most recently learned knots, never the oldest."""

    def test_truncated_flag_set_when_pool_exceeds_max_candidates(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("1")):
            judge = ljb.make_llm_judge(max_candidates=3)
            judge("q", KNOTS, topk=10)      # 5 knots > max_candidates 3
        self.assertTrue(judge.truncated)

    def test_truncation_keeps_most_recent_not_oldest_knots(self):
        many = ["fact number %d" % i for i in range(500)]
        seen_pool = {}

        def capture(request, *a, **kw):
            body = json.loads(request.data)
            seen_pool["listing"] = body["messages"][1]["content"]
            return fake_response("1")
        with mock.patch("urllib.request.urlopen", side_effect=capture):
            judge = ljb.make_llm_judge(max_candidates=10)
            judge("q", many, topk=10)
        # Parse "N. fact" lines back out of the FACTS block rather than
        # substring-matching the raw text -- a substring check near the end
        # of a numbered list is fragile (the last line has no trailing
        # newline to anchor on).
        block = seen_pool["listing"].split("FACTS:\n", 1)[1]
        block = block.split("\n\nLINE:", 1)[0]
        shown = {line.split(". ", 1)[1] for line in block.split("\n") if line}
        self.assertEqual(shown, {"fact number %d" % i for i in range(490, 500)})

    def test_no_truncation_when_pool_fits(self):
        with mock.patch("urllib.request.urlopen",
                        return_value=fake_response("1")):
            judge = ljb.make_llm_judge(max_candidates=200)
            judge("q", KNOTS, topk=10)      # 5 knots, well under the cap
        self.assertFalse(judge.truncated)


if __name__ == "__main__":
    unittest.main()

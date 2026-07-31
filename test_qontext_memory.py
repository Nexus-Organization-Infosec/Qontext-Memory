#!/usr/bin/env python3
"""
Test suite for qontext_memory. Standard library only.

    python test_qontext_memory.py           # all tests
    python test_qontext_memory.py -v        # verbose

Covers the API contract, extraction quality, ranking, bounded growth,
persistence (including corrupt files and old formats), unicode, adversarial
input, and thread safety.
"""

import json
import os
import random
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from qontext_memory import (  # noqa: E402
    DEFAULT_BUDGET, MAX_ENTRY_CHARS, QontextMemory, QuipuMemory, extract,
)


class TestExtraction(unittest.TestCase):
    def test_extracts_a_fact(self):
        knots = extract("People call me Marta.")
        self.assertEqual(len(knots), 1)
        self.assertIn("Marta", knots[0])

    def test_rewrites_to_third_person(self):
        knots = extract("I work as a nurse.")
        self.assertTrue(knots[0].startswith("the user works"), knots)

    def test_names_its_subject(self):
        # design rule 1: a knot with a bare "I" is unanswerable alone
        for knot in extract("I live in Utrecht. My dog is called Bikkel."):
            self.assertNotRegex(knot, r"\bI\b")
            self.assertNotRegex(knot, r"\bmy\b")

    def test_keeps_the_payload(self):
        # design rule 2: never trim the name/day/number out
        knots = extract("The demo is on Friday at 10:00, which is soon.")
        self.assertTrue(any("Friday" in k for k in knots), knots)

    def test_strips_openers(self):
        knots = extract("Oh, by the way, my cat is called Muis.")
        self.assertFalse(knots[0].lower().startswith("oh"), knots)
        self.assertIn("Muis", knots[0])

    def test_opener_only_sentence_is_dropped(self):
        self.assertEqual(extract("Heads up:"), [])
        self.assertEqual(extract("By the way,"), [])

    def test_ignores_chatter(self):
        self.assertEqual(extract("Hmm, ok."), [])
        self.assertEqual(extract("Ha, that's funny."), [])

    def test_entry_length_capped(self):
        long_fact = "My project is called " + "x" * 500
        for knot in extract(long_fact):
            self.assertLessEqual(len(knot), MAX_ENTRY_CHARS)

    def test_splits_on_newlines(self):
        knots = extract("I live in Antwerp\nMy manager is Priya")
        self.assertEqual(len(knots), 2, knots)

    def test_never_splits_inside_a_quotation(self):
        """Prose puts full stops inside quotes; splitting there strands the
        payload on the far side of the cut."""
        for knot in extract('"Go home. Now," she said, and the demo is on Friday.'):
            quotes = knot.count('"')
            self.assertEqual(quotes % 2, 0, "unbalanced quote in %r" % knot)

    def test_long_sentence_keeps_the_payload(self):
        """Design rule 2 under a long sentence: fit by clause, never slice."""
        long_one = ("She had spent the entire afternoon walking the length of "
                    "the old canal without speaking to anyone at all, and the "
                    "sprint demo is on Friday at 10:00")
        knots = extract(long_one)
        self.assertTrue(any("Friday" in k for k in knots), knots)
        for knot in knots:
            self.assertLessEqual(len(knot), MAX_ENTRY_CHARS)

    def test_bare_dialogue_gets_a_speaker(self):
        # the line has to be a fact first — a weekday makes it one
        knots = extract('"I will meet you at the shrine on Friday."',
                        subject="Sabine")
        self.assertTrue(knots, "nothing extracted")
        self.assertTrue(knots[0].startswith("Sabine says"), knots)
        self.assertIn("Friday", knots[0])

    def test_is_pure(self):
        text = "People call me Marta."
        self.assertEqual(extract(text), extract(text))


class TestObserve(unittest.TestCase):
    def setUp(self):
        self.mem = QontextMemory()

    def test_only_user_text_creates_knots(self):
        self.mem.observe("assistant", "Your name is Marta and you are a nurse.")
        self.assertEqual(self.mem.entries(), [])

    def test_assistant_text_still_counts_as_observed(self):
        self.mem.observe("assistant", "hello there")
        self.assertEqual(self.mem.stats()["observed_chars"], len("hello there"))

    def test_returns_new_knots(self):
        added = self.mem.observe("user", "People call me Marta.")
        self.assertEqual(len(added), 1)

    def test_no_duplicates(self):
        self.mem.observe("user", "People call me Marta.")
        self.mem.observe("user", "People call me Marta.")
        self.assertEqual(len(self.mem.entries()), 1)

    def test_speaker_matching_is_forgiving(self):
        self.mem.observe("User", "People call me Marta.")
        self.mem.observe(" user ", "I live in Utrecht.")
        self.assertEqual(len(self.mem.entries()), 2)

    def test_add_stores_directly(self):
        self.assertTrue(self.mem.add("the user prefers metric units"))
        self.assertIn("the user prefers metric units", self.mem.entries())
        self.assertFalse(self.mem.add("the user prefers metric units"))

    def test_add_rejects_junk(self):
        self.assertFalse(self.mem.add(""))
        self.assertFalse(self.mem.add("hi"))
        self.assertFalse(self.mem.add(None))


class TestAdversarialInput(unittest.TestCase):
    """A memory layer must never be the thing that crashes the agent."""

    def setUp(self):
        self.mem = QontextMemory()

    def test_none_and_empty(self):
        for value in (None, "", "   ", "\n\n"):
            self.assertEqual(self.mem.observe("user", value), [])

    def test_non_string_types(self):
        for value in (42, 3.14, True, ["a", "b"], {"a": 1}, b"bytes here"):
            self.mem.observe("user", value)      # must not raise
        self.mem.observe(None, "People call me Marta.")

    def test_control_characters(self):
        self.mem.observe("user", "My name is \x00\x01Marta\x7f.")
        self.assertTrue(all(isinstance(e, str) for e in self.mem.entries()))

    def test_very_long_input(self):
        self.mem.observe("user", "I live in Utrecht. " * 5000)
        for knot in self.mem.entries():
            self.assertLessEqual(len(knot), MAX_ENTRY_CHARS)

    def test_regex_hostile_input(self):
        self.mem.observe("user", "My repo is called (((([[[[\\\\****+++?????")
        self.mem.pack("(((([[[[", 300)      # must not raise

    def test_fuzz(self):
        rnd = random.Random(1234)
        alphabet = "abc XYZ 123 .,!?:'\"-\n\t\x00é中🙂\\/()[]{}*+?|"
        for _ in range(300):
            text = "".join(rnd.choice(alphabet) for _ in range(rnd.randint(0, 200)))
            self.mem.observe("user", text)
            self.mem.pack(text[:40], rnd.randint(0, 500))
        self.assertLessEqual(len(self.mem), self.mem.max_entries)

    def test_unicode_survives(self):
        self.mem.observe("user", "People call me Zoë and I live in Ürümqi.")
        blob = self.mem.serialize()
        again = QontextMemory.deserialize(blob)
        self.assertEqual(again.entries(), self.mem.entries())
        self.assertIn("Zoë", " ".join(again.entries()))

    def test_emoji(self):
        self.mem.observe("user", "My cat is called 🐈 Muis.")
        self.assertTrue(self.mem.entries())
        QontextMemory.deserialize(self.mem.serialize())


class TestPack(unittest.TestCase):
    def setUp(self):
        self.mem = QontextMemory()
        for text in ("People call me Marta.",
                     "I work as a nurse.",
                     "I live in Utrecht.",
                     "My dog is called Bikkel.",
                     "The demo is on Friday at 10:00."):
            self.mem.observe("user", text)

    def test_relevant_fact_is_packed(self):
        self.assertIn("nurse", self.mem.pack("What is the user's job?", 300))
        self.assertIn("Bikkel", self.mem.pack("What is the dog called?", 300))

    def test_never_exceeds_budget(self):
        for budget in (0, 1, 5, 20, 50, 120, 300, 10000):
            self.assertLessEqual(len(self.mem.pack("job", budget)), budget)

    def test_zero_budget_is_empty(self):
        self.assertEqual(self.mem.pack("job", 0), "")

    def test_negative_budget_is_empty(self):
        self.assertEqual(self.mem.pack("job", -50), "")

    def test_empty_memory_is_empty_string(self):
        self.assertEqual(QontextMemory().pack("anything", 300), "")

    def test_empty_query_does_not_raise(self):
        self.mem.pack("", 300)
        self.mem.pack(None, 300)

    def test_default_budget(self):
        self.assertLessEqual(len(self.mem.pack("job")), DEFAULT_BUDGET)

    def test_synonyms_bridge_question_and_statement(self):
        # question says "job", the stored knot says "works as"
        self.assertIn("nurse", self.mem.pack("What does the user do for work?", 300))

    def test_irrelevant_query_does_not_dump_everything(self):
        packed = self.mem.pack("What is the airspeed of an unladen swallow?", 300)
        self.assertLessEqual(len(packed.split("\n")), 2)

    def test_reserved_slice_survives_an_uninformative_query(self):
        """With PACK_RESERVE set, standing facts ride along even when the
        query shares no words with anything — the roleplay case."""
        import qontext_memory as qm
        mem = QontextMemory()
        mem.observe("user", "People call me Marta and I am allergic to shellfish.")
        mem.observe("user", "The tram was busy and the sky was grey.")
        previous = qm.PACK_RESERVE
        try:
            qm.PACK_RESERVE = 0.5
            packed = mem.pack("I lean over and kiss her cheek", 300).lower()
            self.assertIn("marta", packed)
            for budget in (0, 40, 120, 300):
                self.assertLessEqual(
                    len(mem.pack("something unrelated entirely", budget)), budget)
        finally:
            qm.PACK_RESERVE = previous

    def test_reserved_slice_does_not_duplicate(self):
        import qontext_memory as qm
        mem = QontextMemory()
        for text in ("People call me Marta.", "I work as a nurse.",
                     "The demo is on Friday at 10:00."):
            mem.observe("user", text)
        previous = qm.PACK_RESERVE
        try:
            qm.PACK_RESERVE = 0.5
            lines = [l for l in mem.pack("What is the user's job?", 300).split("\n") if l]
            self.assertEqual(len(lines), len(set(lines)))
        finally:
            qm.PACK_RESERVE = previous

    def test_facts_about_other_people_do_not_outrank_the_user(self):
        """A long conversation is full of near-misses. When the query names
        the user, a knot about somebody else answers a different question."""
        mem = QontextMemory()
        mem.observe("user", "I work as a nurse, mostly night shifts.")
        mem.observe("user", "We track tasks in Trello.")
        mem.observe("user", "My dog is called Bikkel.")
        for other in ("Fenna works as a teacher.", "Sem works as a teacher.",
                      "Kasper tracks their tasks in Jira.",
                      "Ruben tracks their tasks in Basecamp.",
                      "My neighbour's dog is called Rex."):
            mem.observe("user", other)
        for question, expected in (("What is the user's own job?", "nurse"),
                                   ("Where does the user track tasks?", "trello"),
                                   ("What is the user's own dog called?", "bikkel")):
            lines = [l for l in mem.pack(question, 300).split("\n") if l]
            self.assertTrue(lines, question)
            self.assertIn(expected, lines[0].lower(),
                          "%r ranked %r first" % (question, lines[0]))

    def test_no_specific_occupations_in_the_synonym_table(self):
        """An occupation is payload, not a relation word. 'teacher' once sat
        in the job group — from tuning against a conversation whose user was
        a teacher — and any knot about anyone teaching outranked the answer."""
        import qontext_memory as qm
        for group in ("job", "role", "work", "profession"):
            words = qm.SYNONYMS.get(qm._stem(group), set())
            for occupation in ("teacher", "teaching", "engineer", "nurse",
                               "baker", "doctor"):
                self.assertNotIn(qm._stem(occupation), words,
                                 "%r is payload, not a synonym of %r"
                                 % (occupation, group))

    def test_explain_marks_packed_entries(self):
        rows = self.mem.explain("What is the user's job?", 300)
        self.assertTrue(any(in_pack for _, in_pack, _ in rows))
        self.assertEqual(len(rows), len(self.mem.entries()))


class TestBoundedGrowth(unittest.TestCase):
    def test_respects_max_entries(self):
        mem = QontextMemory(max_entries=10)
        for i in range(200):
            mem.add("the user's lucky number is %d hundred" % i)
        self.assertLessEqual(len(mem), 10)

    def test_eviction_keeps_used_knots(self):
        mem = QontextMemory(max_entries=5)
        mem.add("the user works as a nurse in Utrecht")
        for _ in range(3):
            mem.pack("What is the user's job?", 300)      # bump its hit count
        for i in range(20):
            mem.add("filler knot number %d about nothing" % i)
        self.assertIn("the user works as a nurse in Utrecht", mem.entries())

    def test_survives_a_flood_of_chatter(self):
        """The failure this guards against: the user says their name on turn
        one, chats for three thousand turns, and the memory has thrown the
        name away because it evicted oldest-first."""
        mem = QontextMemory(max_entries=60)
        mem.observe("user", "People call me Marta and I work as a nurse.")
        mem.observe("user", "My dog is called Bikkel.")
        mem.observe("user", "Our project is codenamed heron-nest.")
        for i in range(2000):
            mem.observe("user", "The %s meeting about %s ran long, %d minutes."
                        % (("weekly", "daily", "monthly")[i % 3], "topic%d" % i,
                           30 + i % 40))
        for question, keyword in (("What is the user's name?", "marta"),
                                  ("What is the user's job?", "nurse"),
                                  ("What is the dog called?", "bikkel"),
                                  ("What is the project codenamed?", "heron")):
            self.assertIn(keyword, mem.pack(question, 300).lower(),
                          "lost %r to chatter" % keyword)

    def test_restatement_supersedes(self):
        mem = QontextMemory()
        mem.observe("user", "I work as a nurse.")
        mem.observe("user", "I work as a nurse, night shifts.")
        mem.observe("user", "I work as a nurse mostly.")
        # one knot survives, and it is the most recent statement of the fact
        self.assertEqual(len(mem), 1, mem.entries())
        self.assertIn("mostly", mem.entries()[0])

    def test_distinct_facts_are_not_superseded(self):
        mem = QontextMemory()
        mem.observe("user", "My dog is called Bikkel.")
        mem.observe("user", "My cat is called Muis.")
        mem.observe("user", "I live in Utrecht.")
        self.assertEqual(len(mem), 3, mem.entries())

    def test_reworded_correction_supersedes(self):
        """The whole point of topic-aware supersession: the correction does
        not have to be worded like the thing it corrects."""
        for before, after, want, stale in (
            ("My manager is Priya.", "My manager is Tomas now, Priya left.",
             "tomas", "priya"),
            ("I code in Rust mostly.", "These days I code in Go.", "go", "rust"),
            ("My editor is Neovim.", "My editor is Helix these days.",
             "helix", "neovim"),
            ("The report is due March 3rd.",
             "The report is due April 7th instead.", "april", "march"),
            ("We track issues in Jira.", "We track issues in Linear now.",
             "linear", "jira"),
            ("I prefer long explanations.", "I prefer short explanations.",
             "short", "long"),
            ("I am allergic to shellfish.",
             "I am not allergic to shellfish after all.", "not", None),
        ):
            mem = QontextMemory()
            stale_only = QontextMemory()
            stale_only.observe("user", before)

            mem = QontextMemory()
            mem.observe("user", before)
            mem.observe("user", after)
            blob = " ".join(mem.entries()).lower()
            self.assertIn(want, blob, (before, after, mem.entries()))
            # the stale knot itself must be gone (the correction may still
            # mention the old value, as in "I code in Go, not Rust")
            self.assertFalse(set(stale_only.entries()) & set(mem.entries()),
                             (before, after, mem.entries()))
            if stale:
                self.assertNotIn(stale, blob, (before, after, mem.entries()))


class TestSupersessionSafety(unittest.TestCase):
    """Merging two distinct facts loses information silently, which is the
    worst thing this library can do. These pairs share vocabulary and must
    always both survive."""

    PAIRS = [
        ("My dog is called Bikkel.", "My cat is called Muis."),
        ("My daughter is called Lotte.", "My brother is called Sander."),
        ("My manager is Priya.", "My supervisor is Professor Aaltink."),
        ("I live in Antwerp.", "My mother lives in Rotterdam."),
        ("The demo is on Friday at 10:00.", "The retro is on Friday at 16:00."),
        ("The report is due March 3rd.", "The invoice is due March 25th."),
        ("I code in Rust.", "I write documentation in Markdown."),
        ("We deploy on Fly.io.", "We store everything in Postgres."),
        ("My laptop is a ThinkPad.", "My phone is a Pixel."),
        ("I speak Dutch and English.", "I am learning Portuguese."),
        ("Our client is Vandermeer BV.", "Our competitor is Nachtvogel."),
        ("My birthday is the 22nd of November.",
         "My daughter's birthday is the 3rd of June."),
        ("Answer me in bullet points.", "Write to me in English."),
        ("I am allergic to shellfish.", "I am vegetarian."),
        ("The kickoff is on Wednesday.", "The standup is at 09:15."),
        ("My GPU is AMD.", "My CPU is Intel."),
        ("I use Neovim for code.", "I use Figma for design."),
        ("The budget is 12000 euro.", "Parking costs 4 euro an hour."),
        # Numbers attached to a noun are identifiers, not payloads: these
        # pairs share almost every word and must still not merge.
        ("The manager of team 5 is unavailable.",
         "The manager of team 7 is unavailable."),
        ("Flight KL1234 leaves from gate B.",
         "Flight KL5678 leaves from gate C."),
        ("Server node 2 is the primary.", "Server node 9 is the replica."),
        # scale words describe different subjects: not the same preference
        ("I like short meetings.", "I like long walks."),
        ("Keep the summary brief.", "Keep the appendix detailed."),
    ]

    def test_shared_keyword_pairs_both_survive(self):
        for first, second in self.PAIRS:
            mem = QontextMemory()
            mem.observe("user", first)
            mem.observe("user", second)
            self.assertEqual(len(mem), 2,
                             "collapsed %r + %r -> %r"
                             % (first, second, mem.entries()))

    def test_supersession_survives_persistence(self):
        """Frames are recomputed on load, so a memory read back from disk
        must still recognise a correction — and still refuse a bad merge."""
        mem = QontextMemory()
        mem.observe("user", "My manager is Priya.")
        mem.observe("user", "My dog is called Bikkel.")
        reloaded = QontextMemory.deserialize(mem.serialize())
        reloaded.observe("user", "My manager is Tomas now.")
        blob = " ".join(reloaded.entries()).lower()
        self.assertIn("tomas", blob)
        self.assertNotIn("priya", blob)
        reloaded.observe("user", "My cat is called Muis.")
        self.assertIn("bikkel", " ".join(reloaded.entries()).lower())
        self.assertIn("muis", " ".join(reloaded.entries()).lower())

    def test_order_does_not_matter(self):
        """Whichever order two distinct facts arrive in, both survive."""
        for first, second in self.PAIRS:
            forward, backward = QontextMemory(), QontextMemory()
            forward.observe("user", first)
            forward.observe("user", second)
            backward.observe("user", second)
            backward.observe("user", first)
            self.assertEqual(len(forward), 2, (first, second))
            self.assertEqual(len(backward), 2, (second, first))

    def test_frames_stay_unique(self):
        """Structural invariant: supersession is supposed to guarantee that
        no two surviving knots say the same thing about the same thing. If
        two live knots ever share a non-empty frame, supersession leaked."""
        import qontext_memory as qm
        mem = QontextMemory()
        lines = [
            "People call me Wietse.", "Actually people call me Wiets.",
            "My manager is Priya.", "My manager is Tomas now.",
            "My dog is called Bikkel.", "My cat is called Muis.",
            "I live in Rotterdam.", "I live in Antwerp now.",
            "The demo is on Friday at 10:00.",
            "The demo moved to Tuesday at 09:00.",
            "The manager of team 5 is unavailable.",
            "The manager of team 7 is unavailable.",
            "I code in Rust.", "I code in Go these days.",
        ]
        for line in lines:
            mem.observe("user", line)
        frames = [qm._frame(text) for text in mem.entries()]
        frames = [f for f in frames if f]
        self.assertEqual(len(frames), len(set(frames)),
                         "duplicate frames survived: %r" % mem.entries())

    def test_fuzzed_input_never_breaks_supersession(self):
        rnd = random.Random(99)
        alphabet = "abc XYZ 123 .,!?:'\"-\n\t\x00é中🙂\\/()[]{}*+?|"
        for _ in range(200):
            mem = QontextMemory()
            for _ in range(6):
                text = "".join(rnd.choice(alphabet)
                               for _ in range(rnd.randint(0, 120)))
                mem.observe("user", text)     # must not raise
            self.assertEqual(len(mem.entries()), len(set(mem.entries())))

    def test_no_collapse_within_a_real_conversation(self):
        """The hard guarantee: every pair of facts from one conversation,
        cross-multiplied. None of them corrects another, so none may merge."""
        conversation = [
            "People call me Wietse.", "I'm situated in Antwerp.",
            "My role is lead data engineer.", "I'm employed at Kestrel.",
            "Most of what I build is in Go.", "My editor is Neovim.",
            "We store everything in Postgres.", "Deploys go out on Fly.io.",
            "Standup happens at 09:15.", "My manager is Priya.",
            "The repo is called amber-lattice.", "Beta ships October 14th.",
            "Our client is Vandermeer BV.", "The budget is 12000 euro.",
            "My daughter Lotte is six.", "We have a rabbit named Stroopwafel.",
            "I'm allergic to shellfish.", "My brother Sander visits soon.",
            "I'm in the CET timezone.", "My supervisor is Professor Aaltink.",
            "Issues live in Linear.", "Docs are in Notion.",
            "The retro is every second Friday at 16:00.",
            "Our competitor is Nachtvogel.",
        ]
        knots = []
        for line in conversation:
            mem = QontextMemory()
            mem.observe("user", line)
            knots.extend(mem.entries())
        knots = list(dict.fromkeys(knots))
        self.assertGreaterEqual(len(knots), 20)
        for i, a in enumerate(knots):
            for b in knots[i + 1:]:
                mem = QontextMemory()
                mem.add(a)
                mem.add(b)
                self.assertEqual(len(mem), 2,
                                 "collapsed %r + %r" % (a, b))

    def test_correction_inherits_standing(self):
        """A corrected fact keeps the retrieval count of what it replaced,
        so correcting a heavily used fact does not hand it to the evictor."""
        mem = QontextMemory(max_entries=3)
        mem.add("the user's manager is Priya")
        for _ in range(5):
            mem.pack("Who is the manager?", 300)
        mem.add("the user's manager is Tomas")
        for extra in ("the user's rabbit is called Stroopwafel",
                      "the user's thesis is on distributed consensus",
                      "the user's hotel is the Estrela in Lisbon"):
            mem.add(extra)
        self.assertTrue(any("Tomas" in e for e in mem.entries()),
                        mem.entries())

    def test_importance_ranks_identity_over_furniture(self):
        import qontext_memory as qm
        identity = qm._importance("People call the user Marta")
        allergy = qm._importance("the user is allergic to shellfish")
        preference = qm._importance("the user prefers bullet points")
        commitment = qm._importance("the demo is on Friday at 10:00")
        tool = qm._importance("the user's editor is Neovim")
        self.assertGreater(identity, preference)
        self.assertGreater(allergy, commitment)
        self.assertGreater(preference, tool)
        for value in (identity, allergy, preference, commitment, tool):
            self.assertGreaterEqual(value, 1.0)
            self.assertLessEqual(value, 5.0)

    def test_flagged_facts_outrank_their_category(self):
        import qontext_memory as qm
        plain = qm._importance("the meeting is at 10:00")
        flagged = qm._importance("remember the meeting is at 10:00")
        self.assertGreater(flagged, plain)

    def test_eviction_keeps_the_important_fact(self):
        """Under pressure, identity survives and scenery does not."""
        mem = QontextMemory(max_entries=4)
        mem.observe("user", "People call me Marta and I am allergic to shellfish.")
        for i in range(40):
            mem.observe("user", "The %s tram was busy and the cafe on street %d was loud."
                        % (("red", "blue", "green")[i % 3], i))
        blob = " ".join(mem.entries()).lower()
        self.assertIn("marta", blob)
        self.assertIn("shellfish", blob)

    def test_rejects_bad_max_entries(self):
        for bad in (0, -1, "ten", None, 2.5):
            with self.assertRaises(ValueError):
                QontextMemory(max_entries=bad)

    def test_forget_removes_matching(self):
        mem = QontextMemory()
        mem.add("the user lives in Rotterdam now")
        mem.add("the user works as a nurse")
        self.assertEqual(mem.forget("rotterdam"), 1)
        self.assertEqual(len(mem), 1)
        self.assertEqual(mem.forget("rotterdam"), 0)

    def test_forget_empty_pattern_is_noop(self):
        mem = QontextMemory()
        mem.add("the user works as a nurse")
        self.assertEqual(mem.forget(""), 0)
        self.assertEqual(len(mem), 1)

    def test_clear(self):
        mem = QontextMemory()
        mem.observe("user", "People call me Marta.")
        mem.clear()
        self.assertEqual(mem.entries(), [])
        self.assertEqual(mem.stats()["observed_chars"], 0)

    def test_forgotten_knot_can_be_relearned(self):
        mem = QontextMemory()
        mem.add("the user lives in Rotterdam now")
        mem.forget("rotterdam")
        self.assertTrue(mem.add("the user lives in Rotterdam now"))


class TestPersistence(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = Path(self.dir) / "qontext.qx"
        self.mem = QontextMemory()
        self.mem.observe("user", "People call me Marta and I work as a nurse.")
        self.mem.observe("user", "The demo is on Friday at 10:00.")

    def tearDown(self):
        for p in Path(self.dir).iterdir():
            p.unlink()
        os.rmdir(self.dir)

    def test_round_trip(self):
        again = QontextMemory.deserialize(self.mem.serialize())
        self.assertEqual(again.entries(), self.mem.entries())
        self.assertEqual(again.stats()["observed_chars"],
                         self.mem.stats()["observed_chars"])

    def test_save_and_load(self):
        self.mem.save(self.path)
        again = QontextMemory.load(self.path)
        self.assertEqual(again.entries(), self.mem.entries())

    def test_save_creates_parent_directory(self):
        nested = Path(self.dir) / "a" / "b" / "mem.qx"
        self.mem.save(nested)
        self.assertTrue(nested.is_file())
        nested.unlink()
        nested.parent.rmdir()
        nested.parent.parent.rmdir()

    def test_save_leaves_no_temp_files(self):
        self.mem.save(self.path)
        leftovers = [p.name for p in Path(self.dir).iterdir()
                     if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_load_missing_file_gives_empty_memory(self):
        mem = QontextMemory.load(Path(self.dir) / "nope.qx")
        self.assertEqual(mem.entries(), [])

    def test_load_corrupt_file_gives_empty_memory(self):
        self.path.write_bytes(b"\x00\x01 not json at all {{{")
        self.assertEqual(QontextMemory.load(self.path).entries(), [])

    def test_load_truncated_file_gives_empty_memory(self):
        self.mem.save(self.path)
        blob = self.path.read_bytes()
        self.path.write_bytes(blob[:len(blob) // 2])
        self.assertEqual(QontextMemory.load(self.path).entries(), [])

    def test_deserialize_rejects_non_memory(self):
        for junk in (b"[1,2,3]", b"\"a string\"", b"null", b"not json"):
            with self.assertRaises(ValueError):
                QontextMemory.deserialize(junk)

    def test_reads_v1_format(self):
        v1 = json.dumps({"e": ["the user works as a nurse",
                               "the demo is on Friday"], "o": 120}).encode()
        mem = QontextMemory.deserialize(v1)
        self.assertEqual(len(mem), 2)
        self.assertEqual(mem.stats()["observed_chars"], 120)

    def test_v1_round_trips_to_v2(self):
        v1 = json.dumps({"e": ["the user works as a nurse"], "o": 50}).encode()
        mem = QontextMemory.deserialize(v1)
        again = QontextMemory.deserialize(mem.serialize())
        self.assertEqual(again.entries(), mem.entries())

    def test_survives_garbage_rows(self):
        blob = json.dumps({"v": 2, "o": 5, "k": [
            ["a real knot about Utrecht", 1, 0, 0.0],
            None, 42, [], ["", 2, 0, 0.0],
        ]}).encode()
        mem = QontextMemory.deserialize(blob)
        self.assertEqual(len(mem), 1)

    def test_max_entries_survives_round_trip(self):
        mem = QontextMemory(max_entries=7)
        mem.add("the user works as a nurse")
        again = QontextMemory.deserialize(mem.serialize())
        self.assertEqual(again.max_entries, 7)

    def test_load_respects_max_entries_on_missing_file(self):
        mem = QontextMemory.load(Path(self.dir) / "nope.qx", max_entries=3)
        self.assertEqual(mem.max_entries, 3)

    def test_serialize_returns_bytes(self):
        self.assertIsInstance(self.mem.serialize(), bytes)


class TestStats(unittest.TestCase):
    def test_reports_density(self):
        mem = QontextMemory()
        mem.observe("user", "People call me Marta. " * 10)
        st = mem.stats()
        self.assertGreater(st["observed_chars"], st["stored_chars"])
        self.assertAlmostEqual(st["density"],
                               st["stored_chars"] / st["observed_chars"])

    def test_empty_memory_density_is_zero(self):
        self.assertEqual(QontextMemory().stats()["density"], 0.0)

    def test_legacy_keys_present(self):
        # live_agent.py and benchmark.py read these two by name
        st = QontextMemory().stats()
        self.assertIn("observed_chars", st)
        self.assertIn("stored_chars", st)


class TestDunders(unittest.TestCase):
    def setUp(self):
        self.mem = QontextMemory()
        self.mem.observe("user", "People call me Marta.")

    def test_len(self):
        self.assertEqual(len(self.mem), 1)

    def test_contains(self):
        self.assertIn(self.mem.entries()[0], self.mem)
        self.assertNotIn("something else entirely", self.mem)

    def test_iter(self):
        self.assertEqual(list(self.mem), self.mem.entries())

    def test_empty_memory_is_falsy_and_that_is_documented(self):
        """__len__ makes an empty memory falsy. That is standard Python, and
        it bit benchmark.py: `if live:` skipped a memory that simply had no
        knots yet. Callers must use `is not None`, and this test exists so
        the behaviour is deliberate rather than discovered."""
        self.assertFalse(bool(QontextMemory()))
        mem = QontextMemory()
        mem.observe("user", "People call me Marta.")
        self.assertTrue(bool(mem))

    def test_repr(self):
        self.assertIn("QontextMemory", repr(self.mem))

    def test_back_compat_alias(self):
        self.assertIs(QuipuMemory, QontextMemory)


class TestThreadSafety(unittest.TestCase):
    def test_concurrent_observe_and_pack(self):
        mem = QontextMemory(max_entries=200)
        errors = []

        def worker(n):
            try:
                for i in range(100):
                    mem.observe("user", "the user's code %d-%d is Rust" % (n, i))
                    mem.pack("Which language?", 300)
                    mem.entries()
                    mem.stats()
            except Exception as e:                    # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        self.assertLessEqual(len(mem), mem.max_entries)
        self.assertEqual(len(mem.entries()), len(set(mem.entries())))

    def test_concurrent_save(self):
        mem = QontextMemory()
        mem.observe("user", "People call me Marta and I work as a nurse.")
        d = tempfile.mkdtemp()
        path = Path(d) / "mem.qx"
        errors = []

        def saver():
            try:
                for _ in range(20):
                    mem.save(path)
                    QontextMemory.load(path)
            except Exception as e:                    # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=saver) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(QontextMemory.load(path).entries(), mem.entries())
        for p in Path(d).iterdir():
            p.unlink()
        os.rmdir(d)


class TestRealConversation(unittest.TestCase):
    """End to end on the benchmark conversation: the numbers that matter."""

    # The benchmark conversation, assistant turns included — density is
    # measured against everything observed, and in real use the assistant
    # says roughly half of it.
    CONV = [
        ("user", "Morning! Quick intro since we haven't talked before: people call me Marta."),
        ("assistant", "Nice to meet you, Marta. What shall we look at today?"),
        ("user", "First, some background. I'm based in Utrecht, near the old canal."),
        ("assistant", "Utrecht is lovely. Go on."),
        ("user", "I work as a nurse, mostly night shifts, so I reply at odd hours."),
        ("assistant", "Good to know, no rush on replies then."),
        ("user", "The traffic was horrible this morning, took me an hour to get home."),
        ("assistant", "An hour! That sounds exhausting after a night shift."),
        ("user", "Lately I code in Rust for fun, little command line things."),
        ("assistant", "Rust is a great choice for CLI tools."),
        ("user", "Oh before I forget: the sprint demo is on Friday at 10:00."),
        ("assistant", "Noted, sprint demo Friday at 10:00."),
        ("user", "Our project is codenamed heron-nest, silly name but it stuck."),
        ("assistant", "heron-nest, got it. Who picked that?"),
        ("user", "Long story. Anyway, my dog is called Bikkel and he ate my charger cable."),
        ("assistant", "Bikkel sounds like a handful!"),
        ("user", "He is. By the way, please keep explanations brief, I skim a lot."),
        ("assistant", "Will do, brief it is."),
        ("user", "Did you see the football match yesterday? What a mess."),
        ("assistant", "I heard it was quite the upset."),
        ("user", "Also important: the report is due March 3rd, hard deadline."),
        ("assistant", "March 3rd, noted as a hard deadline."),
        ("user", "We track tasks in Trello, in case you need to reference the board."),
        ("assistant", "Understood, Trello board it is."),
    ]
    QUESTIONS = [
        ("What is the user's name?", "marta"),
        ("Where does the user live?", "utrecht"),
        ("What is the user's job?", "nurse"),
        ("Which programming language does the user use?", "rust"),
        ("When is the sprint demo?", "friday"),
        ("What is the project codenamed?", "heron"),
        ("What is the dog's name?", "bikkel"),
        ("How should explanations be written?", "brief"),
        ("When is the report due?", "march"),
        ("Where are tasks tracked?", "trello"),
    ]

    def setUp(self):
        self.mem = QontextMemory()
        for speaker, text in self.CONV:
            self.mem.observe(speaker, text)

    def test_every_fact_is_recoverable(self):
        missed = [q for q, kw in self.QUESTIONS
                  if kw not in self.mem.pack(q, DEFAULT_BUDGET).lower()]
        self.assertEqual(missed, [], "facts not in pack: %s" % missed)

    def test_density_under_half(self):
        self.assertLess(self.mem.stats()["density"], 0.5)

    def test_survives_a_save_load_cycle(self):
        d = tempfile.mkdtemp()
        path = Path(d) / "mem.qx"
        self.mem.save(path)
        reloaded = QontextMemory.load(path)
        missed = [q for q, kw in self.QUESTIONS
                  if kw not in reloaded.pack(q, DEFAULT_BUDGET).lower()]
        self.assertEqual(missed, [])
        path.unlink()
        os.rmdir(d)


if __name__ == "__main__":
    unittest.main(verbosity=2 if "-v" in sys.argv else 1)

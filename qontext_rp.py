#!/usr/bin/env python3
"""
Qontext for roleplay: the core the SillyTavern extension will be a port of.

The chat library assumes clipped, factual, first-person text from one speaker.
Roleplay violates every part of that, and 4,569 turns of real logs said so in
detail (see RP_FINDINGS.md). This is the version built from what those
measurements actually showed — which is not what the first look suggested.

What the logs settled, and what this file therefore does:

  * Extraction IS the bottleneck, which reverses what the quiz benchmark
    said. On quiz questions extraction was worth 2 facts of 20 and retrieval
    10, so RP_FINDINGS.md concluded extraction was not the prize. Measured on
    the turn-shaped task that actually matches deployment, roleplay extraction
    alone takes recall from 4.1% to 6.3% across 11 logs, and 9.9% with the
    reserved slice and the cords. The earlier conclusion was true of the quiz
    and false of the real thing.

    The reason is that the chat admission rule rejects state. "I am at the
    harbour now, not the shrine" has no number, no date and no capital, so
    nothing admitted it — and location, condition, clothing and kinship are
    most of what a roleplay memory exists to carry.

  * Every speaker is their own subject. `speakers="all"` turned "I'm a
    vampire" from Carmine into "Carmine is a vampire", took quiz recall from
    8/20 to 10/20, and made supersession safe between characters — "Alice is
    at the tavern" and "Bob is at the tavern" stop sharing a topic frame.

  * The query is a roleplay turn, not a question. "I lean over and kiss her
    cheek" shares no vocabulary with the facts the reply needs. Measured on
    turn-shaped queries the pack carried 2.0% of them at budget 300. Reserving
    half the budget for standing facts, chosen by importance and ignoring the
    query entirely, nearly doubled it. That bet is wrong in assistant chat and
    right here, which is why it is a deployment setting rather than a tuning
    constant.

  * State changes constantly and old state is not garbage. Supersession in
    the chat library deletes; here it leaves a revision cord, so "she was at
    the shrine" remains reachable from "she is at the harbour now".

  * Capitalisation is not evidence. In fiction 3-49% of sentences carry a
    mid-sentence capital and they are character names in ordinary narration.
    A gazetteer built from the text itself replaces "any capital counts".

    from qontext_rp import RPMemory
    memory = RPMemory()
    memory.observe("Carmine", "I hate the harbour. Too many people.")
    memory.observe("User", "*I take her hand* Then we go inland.")
    memory.scene("*I take her hand* Then we go inland.", 600)

Nothing here is tuned against log8 or log12, which were excluded from all
roleplay measurement after a content screen.
"""

import re
from collections import Counter

from qontext_cords import CordMemory
from qontext_memory import (MAX_ENTRY_CHARS, MIN_SENTENCE, OPENERS, _attribute,
                            _coerce, _fit, _is_fact, _split_sentences, _stem,
                            _third_person, _trim, MARKERS)

# Half the budget carries standing facts regardless of the turn. Measured on
# turn-shaped queries: 5.4% -> 9.3% at budget 1200. It costs the assistant-chat
# suites, which is the correct trade for this deployment and the wrong one for
# the other, so it lives here rather than in the shipped library.
SCENE_RESERVE = 0.5
GAZETTEER_MIN = 2        # times a capitalised token must appear to count
DIALOGUE_MIN = 20        # a quoted line shorter than this is not worth a knot
# Cord expansion at retrieval.
#
# CORD_SHARE reserves part of the non-reserved budget for cord-reached knots.
# USE_CORDS decides whether the walk happens at all — these are genuinely two
# knobs, and conflating them cost a whole ablation: setting CORD_SHARE to zero
# gives the seeds the entire slice but the cords still fill whatever the seeds
# leave, so "cords off" measured with CORD_SHARE alone was not cords off.
USE_CORDS = True
CORD_SHARE = 0.4
MIN_CORD = 0.3

_QUOTED = re.compile(r"[\"“]([^\"”]{2,400})[\"”]")
_CAPITAL = re.compile(r"(?<![.!?\"“]\s)(?<!^)\b([A-Z][a-z]{2,})\b")

# Why roleplay needs its own admission rule at all.
#
# The chat extractor admits a sentence if a marker fires or if it carries a
# "payload" — a number, a date, a hyphenated coinage, a mid-sentence capital.
# Fiction has none of those in the sentences that matter most. "I am at the
# harbour now, not the shrine" is the single most important kind of statement
# in a roleplay session and the chat rule rejects it outright: no number, no
# capital, no marker. Measured on the toy scene below, the location change
# vanished and only "Plans changed" survived.
#
# So: state is payload. Place, possession, appearance, condition and kinship
# are recognised structurally rather than by vocabulary lists, because the
# nouns of any given setting cannot be known in advance.
_PLACE = re.compile(
    r"\b(?:at|in|on|near|inside|outside|beneath|underneath|under|behind|"
    r"beyond|across|toward|towards|into|onto|by)\s+"
    r"(?:the|a|an|his|her|their|my|your|our|this|that)\s+[a-z]{3,}")
_HOLDS = re.compile(
    r"\b(?:wearing|wears|wore|holding|holds|held|carrying|carries|carried|"
    r"owns|owned|keeps|kept|wields|wielded|hides|hiding|hid|drew|draws)\s+"
    r"(?:a|an|the|his|her|their|my|your)\s+[a-z]{3,}")
_KIN = re.compile(
    r"\b(?:sister|brother|mother|father|daughter|son|husband|wife|lover|"
    r"friend|enemy|master|servant|ally|mentor|captain|maker|sire|childe|"
    r"partner|betrothed|widow|cousin|uncle|aunt)\b", re.I)
_CONDITION = re.compile(
    r"\b(?:wounded|bleeding|hurt|dying|dead|alive|asleep|awake|drunk|sober|"
    r"afraid|frightened|angry|furious|calm|tired|exhausted|hungry|starving|"
    r"cold|warm|sick|healed|cursed|blessed|mortal|immortal|human|turned|"
    r"pregnant|married|engaged|free|captive|bound|silent|scared|ashamed)\b",
    re.I)
_PROMISE = re.compile(
    r"\b(?:promise|promised|swear|swore|vow|vowed|agree|agreed|refuse|"
    r"refused|forbid|forbade|allow|allowed|owe|owes|owed)\b", re.I)


def rp_is_fact(sentence, known=None):
    """Roleplay admission: state counts, and a capital on its own does not.

    `known` is a predicate over capitalised tokens — the gazetteer — so a name
    the story actually uses is payload while a sentence-initial word is not.
    """
    if _PLACE.search(sentence) or _HOLDS.search(sentence):
        return True
    if _KIN.search(sentence) or _CONDITION.search(sentence) \
            or _PROMISE.search(sentence):
        return True
    if re.search(r"\d", sentence):
        return True
    capitals = _CAPITAL.findall(sentence)
    if capitals and known is not None and any(known(c) for c in capitals):
        return True
    words = {_stem(w) for w in re.findall(r"[\w-]+", sentence.lower())}
    return bool(words & MARKERS)


def rp_extract(text, subject, known=None):
    """qontext_memory.extract, with roleplay rules and no bare dialogue.

    Quoted lines are deliberately skipped here: an utterance stored as if it
    were a narrated fact is a knot tied around nothing (15.2% of knots from
    real logs). RPMemory adds them separately, attributed to the speaker.
    """
    out = []
    for sentence in _split_sentences(_coerce(text)):
        stripped = sentence.strip()
        if not stripped:
            continue
        # A sentence that is nothing but a quotation belongs to dialogue.
        without_quotes = _QUOTED.sub(" ", stripped).strip(" .,!?:;-—*_")
        if len(without_quotes) < 12:
            continue
        cleaned = OPENERS.sub("", without_quotes + " ").strip(" .,!?:")
        cleaned = re.sub(r"[*_]", "", cleaned).strip()
        if len(cleaned) < MIN_SENTENCE or not rp_is_fact(cleaned, known):
            continue
        knot = _third_person(_trim(cleaned), subject)
        knot = _attribute(_fit(knot, MAX_ENTRY_CHARS), subject).strip()
        if len(knot) > MAX_ENTRY_CHARS:
            knot = _fit(knot, MAX_ENTRY_CHARS)
        if len(knot) >= MIN_SENTENCE:
            out.append(knot)
    return out


class RPMemory(CordMemory):
    """A Qontext memory that expects prose, characters and scenes."""

    def __init__(self, max_entries=800, reserve=SCENE_RESERVE,
                 use_cords=None, **kwargs):
        # 800 rather than 500: one log of twelve passed 500 knots in a single
        # session, and in roleplay the eviction that follows is the thing most
        # likely to lose a fact quietly.
        kwargs.setdefault("speakers", "all")
        super(RPMemory, self).__init__(max_entries=max_entries, **kwargs)
        self.reserve = max(0.0, min(1.0, float(reserve)))
        self.use_cords = USE_CORDS if use_cords is None else bool(use_cords)
        self._names = Counter()      # capitalised tokens seen in the prose
        self._cast = set()           # speakers we have actually heard from

    # ------------------------------------------------------------- writing

    def _gazetteer(self, text):
        for token in _CAPITAL.findall(text):
            self._names[token] += 1

    def known(self, token):
        """Is this capitalised word a real entity, or a sentence-initial word?

        Cold start is a genuine limitation: a name introduced once, early, is
        not yet known when its first sentence is judged. Speakers are always
        known, which covers the common case of a character naming themselves.
        """
        return (token in self._cast
                or self._names.get(token, 0) >= GAZETTEER_MIN)

    def _payload_is_real(self, knot):
        """Reject knots admitted only by a capital that names nothing.

        `_has_payload` treats a mid-sentence capital as a concrete payload.
        That is sound in chat, where capitals are names and places the user
        actually introduced, and unsound in fiction, where every noun in the
        setting is capitalised. 33.7% of knots from real logs got in this way.
        """
        if re.search(r"\d|\w-\w", knot):
            return True
        words = {_stem(w) for w in re.findall(r"[\w-]+", knot.lower())}
        if words & MARKERS:
            return True
        capitals = _CAPITAL.findall(knot)
        if not capitals:
            return True          # admitted by something other than a capital
        return any(self.known(token) for token in capitals)

    def _dialogue_knots(self, speaker, text):
        """Turn bare quoted lines into attributed facts.

        A quoted utterance stored as-is is a knot tied around nothing: 15.2%
        of knots from real logs were one. The fix needs a speaker, which the
        logs lack and which SillyTavern supplies on every message.
        """
        out = []
        for match in _QUOTED.finditer(text):
            said = match.group(1).strip()
            if len(said) < DIALOGUE_MIN or not rp_is_fact(said, self.known):
                continue
            body = _trim(said.rstrip(".!?,"))
            knot = "%s says %s" % (speaker, body[0].lower() + body[1:]
                                   if body[:1].isupper() else body)
            out.append(_fit(knot, MAX_ENTRY_CHARS))
        return out

    def observe(self, speaker, text):
        """Watch one message. Unlike the chat library, every speaker counts.

        Extraction is done here rather than by the parent because roleplay
        needs its own admission rule, and the cords are tied afterwards
        through CordMemory.tie_turn so the weave is built either way.
        """
        text = _coerce(text)
        if not text:
            return []
        name = _coerce(speaker).strip() or "the user"
        with self._lock:
            self._cast.add(name)
            self._gazetteer(text)
            self._observed += len(text)
            before = self._seq
            subject = "the user" if name.lower() in ("user", "you") else name
            added = []
            for knot in rp_extract(text, subject, self.known):
                if self._add(knot):
                    added.append(knot)
            # Dialogue is attributed separately: extract() has no concept of
            # who is speaking inside a quotation, and an unattributed line is
            # a knot tied around nothing.
            for knot in self._dialogue_knots(subject, text):
                if self._add(knot):
                    added.append(knot)
        self.tie_turn(before)
        return added

    # ------------------------------------------------------------- reading

    def scene(self, turn, budget=600):
        """The pack for a roleplay turn. This is what the extension calls.

        Three slices, in conversation order:

          reserved   standing facts by importance, ignoring the turn entirely
          seeds      knots the turn's own words reach
          cords      knots hanging off those seeds

        The reserve exists because a turn is a poor query; the cords exist
        because once a turn *does* land on something — a name, a place — the
        knots around it are usually the ones the reply needs.
        """
        budget = max(0, int(budget))
        if not budget:
            return ""
        with self._lock:
            if not self._knots:
                return ""
            chosen, total = [], 0
            taken = set()

            allowance = int(budget * self.reserve)
            for record in sorted(self._knots,
                                 key=lambda k: (k.get("imp", 1.0), k["seq"]),
                                 reverse=True):
                cost = len(record["text"]) + (1 if chosen else 0)
                if total + cost > allowance:
                    continue
                chosen.append(record)
                taken.add(record["seq"])
                total += cost
                if total >= allowance * 0.9:
                    break

            ranked = [(score, r) for score, r in self._ranked(turn)
                      if r["seq"] not in taken]
            share = CORD_SHARE if self.use_cords else 0.0
            seed_room = total + int((budget - total) * (1.0 - share))
            seeds = []
            for _score, record in ranked:
                cost = len(record["text"]) + (1 if chosen else 0)
                if total + cost > seed_room:
                    continue
                chosen.append(record)
                seeds.append(record["seq"])
                taken.add(record["seq"])
                total += cost

            reached = {}
            frontier = [(s, 1.0) for s in seeds] if self.use_cords else []
            for _hop in range(2 if self.use_cords else 0):
                nxt = []
                for seq, carried in frontier:
                    for other, weight in self.cords_of(seq):
                        if other in taken:
                            continue
                        strength = carried * weight
                        if strength >= MIN_CORD and \
                                strength > reached.get(other, 0.0):
                            reached[other] = strength
                            nxt.append((other, strength))
                frontier = nxt
                if not frontier:
                    break

            for seq, _strength in sorted(reached.items(),
                                         key=lambda pair: -pair[1]):
                record = self._by_seq.get(seq)
                if record is None:
                    continue
                cost = len(record["text"]) + (1 if chosen else 0)
                if total + cost > budget:
                    continue
                chosen.append(record)
                taken.add(seq)
                total += cost

            for record in chosen:
                record["hits"] += 1
            chosen.sort(key=lambda r: r["seq"])
            return "\n".join(r["text"] for r in chosen)

    def history_of(self, text):
        """What this knot replaced — state that changed rather than vanished."""
        with self._lock:
            record = next((r for r in self._knots if r["text"] == text), None)
            return self.revisions_of(record["seq"]) if record else []

    def cast(self):
        with self._lock:
            return sorted(self._cast)

    def entities(self, limit=20):
        with self._lock:
            return [(n, c) for n, c in self._names.most_common(limit)
                    if self.known(n)]

    def stats(self):
        base = super(RPMemory, self).stats()
        with self._lock:
            base["cast"] = len(self._cast)
            base["entities"] = sum(1 for n in self._names if self.known(n))
            base["reserve"] = self.reserve
        return base

#!/usr/bin/env python3
"""
Cords: the part of the quipu metaphor the library never actually implemented.

In a real quipu the meaning lives in the knots *and* in how they hang — which
cord is pendant on which, what sits next to what. QontextMemory stores only
the knots. They sit in a flat list, are found by word overlap with the query,
and are read out as a ranked list with no relation preserved between them.

That is fine for "what is the dog's name" and useless for "what was the
unresolved tension", because the second question shares no word with the knot
that answers it. Measured on a 44-turn conversation: the tension was stored
verbatim as `She'll be vindicated and too polite to say it`, and pack()
returned the recency fallback instead.

This adds the cords, without touching qontext_memory.py — that file is the
deliverable and its numbers have to stay reproducible.

Three kinds of thread, all recorded at write time:

    same-turn    two knots pulled from one message belong together
    adjacent     a knot and the one before it — conversational sequence
    revision     what a knot replaced, kept as a link instead of a deletion

and one change at read time: pack_weave() finds its best match the usual way,
then walks outward along the cords and returns the connected sub-weave *in
conversation order*, rather than a list ranked by relevance.

    from qontext_cords import CordMemory
    m = CordMemory()
    m.observe("user", "...")
    m.pack(query, 300)        # unchanged, for comparison
    m.pack_weave(query, 300)  # seed, then follow the cords

Whether the cords buy anything is an open question — that is what
qontext-bench/turing_bench.py --mode weave is for. Nothing here is on by
default anywhere else.
"""

from qontext_memory import (QontextMemory, DEFAULT_BUDGET,  # noqa: F401
                            RELEVANCE_FLOOR, FLOOR_MAX_KNOTS)

SAME_TURN = 1.0       # pulled from one message: strongest thread
ADJACENT = 0.55       # consecutive knots: the conversation moved this way
REVISION = 0.85       # this knot replaced that one; the change is the point
CORD_SHARE = 0.5      # share of the budget reserved for cord-reached knots
MIN_CORD = 0.3        # threads weaker than this are not followed


class CordMemory(QontextMemory):
    """QontextMemory that also remembers how its knots hang together."""

    def __init__(self, *args, **kwargs):
        super(CordMemory, self).__init__(*args, **kwargs)
        self._cords = {}       # seq -> {seq: weight}
        self._revised = {}     # seq -> [text, ...] this knot superseded
        self._last_turn = []   # seqs added by the previous observed message

    # ------------------------------------------------------------ weaving

    def _tie(self, left, right, weight):
        if left == right:
            return
        for a, b in ((left, right), (right, left)):
            row = self._cords.setdefault(a, {})
            if row.get(b, 0.0) < weight:
                row[b] = weight

    def observe(self, speaker, text):
        with self._lock:
            before = self._seq
        added = super(CordMemory, self).observe(speaker, text)
        self.tie_turn(before)
        return added

    def tie_turn(self, before):
        """Thread every knot added since sequence `before` into the weave.

        Separated from observe() so a subclass with its own extraction —
        qontext_rp.RPMemory, which needs roleplay admission rules — can add
        knots its own way and still get the cords.
        """
        with self._lock:
            # Knots the evictor already removed are skipped: a cord to a knot
            # that no longer exists is worse than no cord.
            fresh = [s for s in range(before + 1, self._seq + 1)
                     if s in self._by_seq]
            for i, left in enumerate(fresh):
                for right in fresh[i + 1:]:
                    self._tie(left, right, SAME_TURN)
            for left in self._last_turn:
                for right in fresh:
                    self._tie(left, right, ADJACENT)
            for seq in fresh:
                for text_dropped in self._revised.get(seq, []):
                    prior = next((r for r in self._knots
                                  if r["text"] == text_dropped), None)
                    if prior is not None:
                        self._tie(seq, prior["seq"], REVISION)
            if fresh:
                self._last_turn = fresh

    def _supersede(self, words, frame):
        """Record what was dropped before dropping it. Caller holds the lock."""
        doomed = []
        if frame and frame in self._by_frame:
            doomed = [k["text"] for k in self._by_frame[frame]]
        inherited, reinforced = super(CordMemory, self)._supersede(words, frame)
        if doomed:
            # _add assigns the next seq immediately after this returns.
            self._revised[self._seq + 1] = doomed
        return inherited, reinforced

    # ------------------------------------------------------------- reading

    def _drop(self, records):
        """Cut the cords of an evicted knot. Caller holds the lock.

        A thread to a knot that no longer exists costs budget on the walk and
        buys nothing, and left uncut they accumulate for the life of the
        memory.
        """
        doomed = {r["seq"] for r in records}
        super(CordMemory, self)._drop(records)
        for seq in doomed:
            for other in self._cords.pop(seq, {}):
                row = self._cords.get(other)
                if row:
                    row.pop(seq, None)
                    if not row:
                        self._cords.pop(other, None)
            self._revised.pop(seq, None)
        self._last_turn = [s for s in self._last_turn if s not in doomed]

    def cords_of(self, seq):
        """[(seq, weight)] hanging off this knot, strongest first."""
        with self._lock:
            row = self._cords.get(seq, {})
            return sorted(((s, w) for s, w in row.items() if w >= MIN_CORD),
                          key=lambda pair: -pair[1])

    def revisions_of(self, seq):
        with self._lock:
            return list(self._revised.get(seq, []))

    def pack_weave(self, query, budget=DEFAULT_BUDGET, hops=2):
        """Seed on relevance, then follow the cords. Conversation order out.

        The list pack() returns is ordered by how well each knot matches the
        query, which is an ordering the conversation never had. A sub-weave
        is returned in the order things were said, because "he said sixty
        last month too" only means anything after "sixty percent, and that's
        Tomas's estimate".
        """
        budget = max(0, int(budget))
        if not budget:
            return ""
        with self._lock:
            if not self._knots:
                return ""
            ranked = self._ranked(query)
            chosen, total, seeds = [], 0, []
            if ranked:
                floor = (ranked[0][0][0] * RELEVANCE_FLOOR
                         if len(self._knots) <= FLOOR_MAX_KNOTS else 0.0)
                seed_budget = int(budget * (1.0 - CORD_SHARE))
                for score, record in ranked:
                    if score[0] < floor:
                        break
                    cost = len(record["text"]) + (1 if chosen else 0)
                    if total + cost > max(seed_budget, 0):
                        continue
                    chosen.append(record)
                    seeds.append(record["seq"])
                    total += cost
                if not chosen:                  # one knot larger than the seed
                    score, record = ranked[0]   # share: keep it anyway
                    if len(record["text"]) <= budget:
                        chosen, seeds, total = [record], [record["seq"]], \
                            len(record["text"])
            else:
                # No knot shares a word with the query. pack() answers this
                # with the single newest knot, which for "what was the
                # unresolved tension" returned a deadline change — an answer
                # to a question nobody asked.
                #
                # Lexical relevance has nothing to say here, but the weave
                # does: seed on what the conversation itself treated as
                # important and let the cords supply the surroundings. The
                # result is a connected span of the conversation rather than
                # one arbitrary knot, which is at least the right *kind* of
                # object to hand someone asking about tension or direction.
                anchor = max(self._knots,
                             key=lambda k: (k.get("imp", 1.0), k["seq"]))
                if len(anchor["text"]) <= budget:
                    chosen, seeds, total = [anchor], [anchor["seq"]], \
                        len(anchor["text"])

            # Walk outward. A knot two hops away is reached at the product of
            # the threads, so a weak thread twice is weaker than a strong one
            # once — the weave decides reach, not a fixed radius.
            taken = {r["seq"] for r in chosen}
            frontier = [(s, 1.0) for s in seeds]
            reached = {}
            for _ in range(max(1, hops)):
                nxt = []
                for seq, carried in frontier:
                    for other, weight in self.cords_of(seq):
                        if other in taken:
                            continue
                        strength = carried * weight
                        if strength < MIN_CORD:
                            continue
                        if strength > reached.get(other, 0.0):
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

    def explain_weave(self, query, budget=DEFAULT_BUDGET):
        """[(seq, reached_how, text)] for the knots pack_weave would send."""
        text = self.pack_weave(query, budget)
        lines = [line for line in text.splitlines() if line]
        with self._lock:
            ranked = {id(r): s for s, r in self._ranked(query)}
            out = []
            for line in lines:
                record = next((r for r in self._knots if r["text"] == line),
                              None)
                if record is None:
                    continue
                how = "seed" if id(record) in ranked else "cord"
                out.append((record["seq"], how, line))
        return out

    def stats(self):
        base = super(CordMemory, self).stats()
        with self._lock:
            threads = sum(len(row) for row in self._cords.values()) // 2
            base["cords"] = threads
            base["revisions"] = sum(len(v) for v in self._revised.values())
        return base

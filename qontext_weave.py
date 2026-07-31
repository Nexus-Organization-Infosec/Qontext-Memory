"""
The vocabulary weave — a second cord, one level below the facts.

The fact knots record *what was said*. This records *which words hang
together*: every time two words appear in the same sentence, the thread
between them thickens. "cancel" pulls on "class", "morning", "phone";
"vampire" pulls on "blood", "fangs", "coffin".

Why it exists: a memory can only retrieve what the query's words can reach.
Ask "what did she cancel?" and the knot that says "didn't you have class
though?" is invisible, because the two share no word. Embeddings solve this
with knowledge from outside the conversation. So does a weave — except the
outside knowledge is the user's own accumulated text, counted rather than
trained, and it costs no dependency at all.

The point is that it *persists*. Inside one conversation the statistics are
mostly accidents. Across a hundred sessions it becomes a personal
distributional thesaurus, and session 100 gets to use what sessions 1-99
learned about how words go together.

    weave = WordWeave()
    weave.learn(some_text)              # repeat over any amount of text
    weave.prune()
    weave.related("cancel")             # [('class', 0.71), ('morning', 0.44), ...]
    weave.save("weave.qw")

Standard library only. Association is not meaning — co-occurrence links words
that share a situation, not a definition — so callers should weight these
lower than words the user actually typed.
"""

import json
import math
import re
from collections import Counter, defaultdict

MIN_WORD = 4          # shorter words carry too little to associate on
MIN_COUNT = 4         # a word seen fewer times than this is noise
MIN_PAIR = 3          # so is a pair
MAX_NEIGHBOURS = 12   # threads kept per word after pruning
WINDOW = 12           # words either side that count as "together"

# Words that co-occur with everything and therefore mean nothing here. This is
# deliberately shorter than the memory's STOP list: the weave needs ordinary
# nouns and verbs, it only wants the true connectives gone.
_NOISE = set("""about above after again against because before being below
between both cannot could does doing done down during each else even ever
every from further have having here hers herself himself into itself just
like made make many more most much must never once only other over own same
should some such than that their them then there these they thing think this
those through under until very well were what when where which while will
with would your yours yourself""".split())

_TOKEN = re.compile(r"[a-zA-Z][a-zA-Z'-]+")


def _tokens(text):
    for match in _TOKEN.finditer(text or ""):
        word = match.group(0).lower().strip("'-")
        if len(word) >= MIN_WORD and word not in _NOISE:
            yield word


class WordWeave:
    """Co-occurrence counts, and the associations they imply."""

    FORMAT_VERSION = 1

    def __init__(self):
        self._unigrams = Counter()
        self._pairs = Counter()
        self._related = {}         # pruned: word -> [(word, strength), ...]
        self._total = 0

    # ---------------------------------------------------------------- build

    def learn(self, text):
        """Count one piece of text. Call as often as you like."""
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", text or ""):
            words = list(_tokens(sentence))
            if len(words) < 2:
                continue
            self._unigrams.update(words)
            self._total += len(words)
            for i, left in enumerate(words):
                for right in words[i + 1:i + 1 + WINDOW]:
                    if left != right:
                        self._pairs[(left, right) if left < right
                                    else (right, left)] += 1
        self._related = {}          # invalidated

    def prune(self, max_neighbours=MAX_NEIGHBOURS):
        """Turn raw counts into the strongest threads, and drop the rest.

        Strength is positive pointwise mutual information, normalised to
        0-1: how much more often two words appear together than chance would
        explain. Rare-but-always-together beats common-and-sometimes.
        """
        vocabulary = {w for w, n in self._unigrams.items() if n >= MIN_COUNT}
        total = max(1, self._total)
        candidates = defaultdict(list)
        for (left, right), count in self._pairs.items():
            if count < MIN_PAIR or left not in vocabulary or right not in vocabulary:
                continue
            joint = count / total
            expected = (self._unigrams[left] / total) * (self._unigrams[right] / total)
            if expected <= 0:
                continue
            pmi = math.log(joint / expected)
            if pmi <= 0:
                continue                      # together less often than chance
            strength = pmi / -math.log(joint)  # normalised PMI, 0..1
            candidates[left].append((strength, right))
            candidates[right].append((strength, left))

        self._related = {}
        for word, neighbours in candidates.items():
            neighbours.sort(reverse=True)
            self._related[word] = [(other, round(strength, 4))
                                   for strength, other in
                                   neighbours[:max_neighbours]]
        return self

    # ---------------------------------------------------------------- read

    def related(self, word, limit=None, minimum=0.0):
        """[(word, strength)] — the threads leading away from this word."""
        if not self._related and self._pairs:
            self.prune()
        rows = self._related.get((word or "").lower(), ())
        rows = [(w, s) for w, s in rows if s >= minimum]
        return rows[:limit] if limit else list(rows)

    def expand(self, words, limit=6, minimum=0.20):
        """Every word the given words lead to. Used for query expansion."""
        out = set()
        for word in words:
            for other, _ in self.related(word, limit=limit, minimum=minimum):
                out.add(other)
        return out - set(words)

    def stats(self):
        return {"vocabulary": len(self._unigrams),
                "pairs": len(self._pairs),
                "woven": len(self._related),
                "tokens": self._total}

    def __len__(self):
        return len(self._related)

    # ---------------------------------------------------------------- persist

    def serialize(self):
        if not self._related and self._pairs:
            self.prune()
        return json.dumps({"v": self.FORMAT_VERSION, "r": self._related},
                          ensure_ascii=False).encode("utf-8")

    @classmethod
    def deserialize(cls, data):
        if isinstance(data, (bytes, bytearray)):
            data = bytes(data).decode("utf-8", "replace")
        weave = cls()
        try:
            loaded = json.loads(data)
        except ValueError:
            return weave
        related = loaded.get("r") if isinstance(loaded, dict) else None
        if isinstance(related, dict):
            weave._related = {w: [(o, float(s)) for o, s in rows]
                              for w, rows in related.items()
                              if isinstance(rows, list)}
        return weave

    def save(self, path):
        import pathlib
        pathlib.Path(path).write_bytes(self.serialize())
        return path

    @classmethod
    def load(cls, path):
        import pathlib
        try:
            return cls.deserialize(pathlib.Path(path).read_bytes())
        except OSError:
            return cls()

    def __repr__(self):
        return "<WordWeave %d words woven, %d pairs seen>" % (
            len(self._related), len(self._pairs))

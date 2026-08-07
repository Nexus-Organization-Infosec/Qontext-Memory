// Qontext for roleplay — the portable core, ported from qontext_rp.py.
//
// No SillyTavern dependency lives in this file, so it can be run under node
// against the Python implementation and checked for parity (parity.mjs). That
// matters more than usual here: every number in RP_FINDINGS.md was measured
// with the Python version, and a port that quietly diverges makes those
// numbers claims about a different program.
//
// Vocabulary tables are generated from the Python source rather than retyped.

import {
    STOP, MARKERS, CORE_MARKERS, FRAME_DROP, FLAGGED, DAYS, MONTHS,
    NUMBER_WORDS, AGREE, SYNONYMS, TOPIC_VOCAB, IMPORTANCE_TIERS,
    OPENERS_SOURCE, OPENERS_FLAGS,
} from './tables.js';

export const MAX_ENTRY_CHARS = 120;
export const MIN_SENTENCE = 8;
export const SUPERSEDE_SIMILARITY = 0.6;
export const RELEVANCE_FLOOR = 0.5;
export const FLOOR_MAX_KNOTS = 200;
export const LENGTH_NORM = 0.5;
export const REFERENCE_LENGTH = 40;
export const SUBJECT_FOCUS = 0.4;
export const SCENE_RESERVE = 0.5;   // roleplay default; 0.0 for assistant chat
export const GAZETTEER_MIN = 2;
export const DIALOGUE_MIN = 20;

// Burst density: how many other knots landed within BURST_WINDOW_MS of this
// one -- a flurry of knots in one frantic minute scores higher than a knot
// that arrived into a quiet stretch. Ported from qontext_memory.py's
// BURST_WEIGHT/BURST_WINDOW (there measured in seconds; here in
// milliseconds, since `ts` is `Date.now()`, not `time.time()`).
//
// Same status as the Python original: UNVERIFIED and off by default. It
// ships here so it can be felt in a real roleplay session, not because it
// has been measured on this port -- there is no JS-side benchmark that
// plays the role long_bench.py/turn_bench.py play for the Python library.
// BURST_WEIGHT=0 makes every burst-aware code path below a true no-op.
export const BURST_WINDOW_MS = 120000;   // 2 minutes
export const BURST_WEIGHT = 0;           // how strongly burst sways ranking/eviction (0 = off)

// Cords: how knots hang together, recorded at write time and walked at read
// time. Measured across 11 logs at budget 1200, turn-shaped queries:
//
//     reserve 0.0, cords off   6.3%      reserve 0.5, cords off   9.6%
//     reserve 0.0, cords on    8.5%      reserve 0.5, cords on    9.9%
//
// (chat pack() on the same logs: 4.1%.) Cords are worth 2.2 points on their
// own and 0.3 alongside the reserve, because both spend budget on knots the
// query did not ask for and their gains overlap.
export const USE_CORDS = true;
export const CORD_SHARE = 0.4;   // of the non-reserved slice
export const MIN_CORD = 0.3;
const SAME_TURN = 1.0;
const ADJACENT = 0.55;
const REVISION = 0.85;

const OPENERS = new RegExp(OPENERS_SOURCE, OPENERS_FLAGS.includes('i') ? 'gi' : 'g');
const WORD = /[\w-]+/g;
const CHAINED_POSSESSIVE = /\bthe (?:user|team)'s \w+'s\b/;

// ---------------------------------------------------------------- language

export function stem(w) {
    for (const suf of ['ing', 'ed']) {
        if (w.endsWith(suf) && w.length - suf.length >= 4) {
            w = w.slice(0, -suf.length);
            if (w[w.length - 1] === w[w.length - 2]) w = w.slice(0, -1);
            return w;
        }
    }
    if (w.endsWith('s') && w.length >= 4 && !w.endsWith('ss') && !w.endsWith('us')) {
        w = w.slice(0, -1);
    }
    return w;
}

export function words(text) {
    const out = new Set();
    for (const m of String(text ?? '').toLowerCase().matchAll(WORD)) {
        if (!STOP.has(m[0])) out.add(stem(m[0]));
    }
    return out;
}

const QUOTE_CLOSERS = { '"': '"', '“': '”', '‘': '’', "'": "'" };

function quoteDepth(text) {
    const depth = [];
    let inside = false, opener = null;
    for (const ch of text) {
        if (!inside && (ch === '"' || ch === '“' || ch === '‘')) {
            inside = true; opener = ch; depth.push(1); continue;
        }
        if (inside && ch === (QUOTE_CLOSERS[opener] ?? '"')) {
            inside = false; depth.push(1); continue;
        }
        depth.push(inside ? 1 : 0);
    }
    return depth;
}

// A quotation is one unbreakable unit. Prose puts sentence punctuation inside
// quotes constantly, and splitting there broke 17% of knots on real logs.
export function splitSentences(text) {
    const depth = quoteDepth(text);
    const out = [];
    let start = 0;
    const re = /(?<=[.!?:])\s+|\n+/g;
    for (const m of text.matchAll(re)) {
        if (m.index < depth.length && depth[m.index]) continue;
        const piece = text.slice(start, m.index).trim();
        if (piece) out.push(piece);
        start = m.index + m[0].length;
    }
    const tail = text.slice(start).trim();
    if (tail) out.push(tail);
    return out;
}

export function hasPayload(fragment) {
    const set = new Set((fragment.toLowerCase().match(/[\w'-]+/g) || []));
    for (const table of [DAYS, MONTHS, NUMBER_WORDS]) {
        for (const w of set) if (table.has(w)) return true;
    }
    if (/\d/.test(fragment)) return true;
    if (/\w-\w/.test(fragment)) return true;
    if (/(?<=[a-z,'"] )[A-Z]{2,}\b/.test(fragment.slice(1))) return true;
    return /(?<=[a-z,'"] )[A-Z][a-z]+/.test(fragment.slice(1));
}

export function isFact(sentence, markers = MARKERS) {
    if (hasPayload(sentence)) return true;
    for (const m of sentence.toLowerCase().matchAll(WORD)) {
        if (markers.has(stem(m[0]))) return true;
    }
    return false;
}

export function clauses(sentence) {
    const depth = quoteDepth(sentence);
    const parts = [];
    let start = 0;
    const re = /,\s+|;\s+|\s+(?:and|but|so|then|while|though)\s+/g;
    for (const m of sentence.matchAll(re)) {
        if (m.index < depth.length && depth[m.index]) continue;
        const piece = sentence.slice(start, m.index).trim();
        if (piece) parts.push(piece);
        start = m.index + m[0].length;
    }
    const tail = sentence.slice(start).trim();
    if (tail) parts.push(tail);
    return parts.length ? parts : [sentence];
}

export function trim(sentence) {
    const hadPayload = hasPayload(sentence);
    const depth = quoteDepth(sentence);
    for (const sep of [',', ' and ', ' while ', ' though ', ' but ']) {
        let idx = sentence.indexOf(sep);
        while (idx !== -1) {
            if (idx < depth.length && depth[idx]) {
                idx = sentence.indexOf(sep, idx + 1);
                continue;
            }
            const left = sentence.slice(0, idx).trim();
            const rest = sentence.slice(idx + sep.length).trim();
            if (left.length >= 12 && (hasPayload(left) || !hadPayload)
                && isFact(left) && !isFact(rest, CORE_MARKERS)) {
                return left;
            }
            idx = sentence.indexOf(sep, idx + 1);
        }
    }
    return sentence;
}

// Never truncate: pick the clause span carrying the payload that fits.
export function fit(sentence, limit) {
    if (sentence.length <= limit) return sentence;
    const parts = clauses(sentence);
    let best = null;
    for (let start = 0; start < parts.length; start++) {
        let span = '';
        for (let end = start; end < parts.length; end++) {
            span = span ? span + ', ' + parts[end] : parts[end];
            if (span.length > limit) break;
            if (hasPayload(span) && (best === null || span.length > best.length)) {
                best = span;
            }
        }
    }
    if (best) return best;
    for (const clause of [...parts].sort((a, b) => b.length - a.length)) {
        if (clause.length <= limit) return clause;
    }
    const cut = sentence.slice(0, limit);
    return cut.includes(' ') ? cut.slice(0, cut.lastIndexOf(' ')) : cut;
}

export function thirdPerson(sentence, subject = 'the user') {
    const possessive = subject + "'s";
    let s = ' ' + sentence + ' ';
    s = s.replace(/\bI'm\b/g, subject + ' is');
    s = s.replace(/\bI've\b/g, subject + ' has');
    s = s.replace(/\bI'll\b/g, subject + ' will');
    s = s.replace(/\bI am\b/g, subject + ' is');
    s = s.replace(/\bI have\b/g, subject + ' has');
    s = s.replace(/\bI do\b/g, subject + ' does');
    s = s.replace(/\bI go\b/g, subject + ' goes');
    s = s.replace(/\bwe are\b/gi, 'the team is');
    s = s.replace(/\bwe have\b/gi, 'the team has');
    s = s.replace(/\bI\b/g, subject);
    s = s.replace(/\bmy\b/gi, possessive);
    s = s.replace(/\bmine\b/gi, possessive);
    s = s.replace(/\bme\b/g, subject);
    s = s.replace(/\bwe're\b/gi, 'the team is');
    s = s.replace(/\bwe\b/gi, 'the team');
    s = s.replace(/\bour\b/gi, "the team's");
    const agree = [...AGREE].join('|');
    s = s.replace(new RegExp(`\\bthe (user|team) (${agree})\\b`, 'g'),
                  (m, who, verb) => `the ${who} ${verb}s`);
    if (subject !== 'the user') {
        const esc = subject.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        s = s.replace(new RegExp(`\\b(${esc}) (${agree})\\b`, 'g'),
                      (m, who, verb) => `${who} ${verb}s`);
    }
    return s.replace(/\s+/g, ' ').trim();
}

export function attribute(knot, subject) {
    const stripped = knot.trim();
    if (stripped[0] !== '"' && stripped[0] !== '“') return knot;
    return `${subject} says ${stripped}`;
}

// ------------------------------------------------------- roleplay admission
//
// The chat rule admits on a marker or a payload — number, date, coinage,
// mid-sentence capital. Roleplay's most important sentences have none of
// those: "I am at the harbour now, not the shrine" was dropped entirely and
// only "Plans changed" survived. State is payload here, recognised
// structurally, because the nouns of a setting cannot be known in advance.

const PLACE = /\b(?:at|in|on|near|inside|outside|beneath|underneath|under|behind|beyond|across|toward|towards|into|onto|by)\s+(?:the|a|an|his|her|their|my|your|our|this|that)\s+[a-z]{3,}/;
const HOLDS = /\b(?:wearing|wears|wore|holding|holds|held|carrying|carries|carried|owns|owned|keeps|kept|wields|wielded|hides|hiding|hid|drew|draws)\s+(?:a|an|the|his|her|their|my|your)\s+[a-z]{3,}/;
const KIN = /\b(?:sister|brother|mother|father|daughter|son|husband|wife|lover|friend|enemy|master|servant|ally|mentor|captain|maker|sire|childe|partner|betrothed|widow|cousin|uncle|aunt)\b/i;
const CONDITION = /\b(?:wounded|bleeding|hurt|dying|dead|alive|asleep|awake|drunk|sober|afraid|frightened|angry|furious|calm|tired|exhausted|hungry|starving|cold|warm|sick|healed|cursed|blessed|mortal|immortal|human|turned|pregnant|married|engaged|free|captive|bound|silent|scared|ashamed)\b/i;
const PROMISE = /\b(?:promise|promised|swear|swore|vow|vowed|agree|agreed|refuse|refused|forbid|forbade|allow|allowed|owe|owes|owed)\b/i;
const QUOTED = /["“]([^"”]{2,400})["”]/g;

export function capitals(text) {
    const out = [];
    for (const m of text.matchAll(/\b[A-Z][a-z]{2,}\b/g)) {
        if (m.index === 0) continue;                       // sentence-initial
        if (/[.!?"“]\s$/.test(text.slice(0, m.index))) continue;
        out.push(m[0]);
    }
    return out;
}

export function rpIsFact(sentence, known) {
    if (PLACE.test(sentence) || HOLDS.test(sentence)) return true;
    if (KIN.test(sentence) || CONDITION.test(sentence) || PROMISE.test(sentence)) {
        return true;
    }
    if (/\d/.test(sentence)) return true;
    const caps = capitals(sentence);
    if (caps.length && known && caps.some(known)) return true;
    for (const m of sentence.toLowerCase().matchAll(WORD)) {
        if (MARKERS.has(stem(m[0]))) return true;
    }
    return false;
}

export function rpExtract(text, subject, known) {
    const out = [];
    for (const sentence of splitSentences(String(text ?? ''))) {
        const stripped = sentence.trim();
        if (!stripped) continue;
        // A sentence that is nothing but a quotation belongs to dialogue.
        const withoutQuotes = stripped.replace(QUOTED, ' ')
            .replace(/^[\s.,!?:;\-—*_]+|[\s.,!?:;\-—*_]+$/g, '');
        if (withoutQuotes.length < 12) continue;
        let cleaned = (withoutQuotes + ' ').replace(OPENERS, '')
            .replace(/^[\s.,!?:]+|[\s.,!?:]+$/g, '');
        cleaned = cleaned.replace(/[*_]/g, '').trim();
        if (cleaned.length < MIN_SENTENCE || !rpIsFact(cleaned, known)) continue;
        let knot = thirdPerson(trim(cleaned), subject);
        knot = attribute(fit(knot, MAX_ENTRY_CHARS), subject).trim();
        if (knot.length > MAX_ENTRY_CHARS) knot = fit(knot, MAX_ENTRY_CHARS);
        if (knot.length >= MIN_SENTENCE) out.push(knot);
    }
    return out;
}

// ------------------------------------------------------------------ frames

export function frame(text) {
    const out = new Set();
    const toks = text.match(WORD) || [];
    for (let i = 0; i < toks.length; i++) {
        const word = toks[i];
        const low = word.toLowerCase().replace(/^-+|-+$/g, '');
        if (!low || STOP.has(low)) continue;
        if (/\d/.test(word)) {
            const prev = i ? toks[i - 1].toLowerCase() : '';
            if (prev && !STOP.has(prev) && !DAYS.has(prev) && !MONTHS.has(prev)
                && !/\d/.test(prev)) {
                out.add(`ID:${stem(prev)}#${low}`);
            } else if (!i) {
                out.add(`ID:#${low}`);
            }
            continue;
        }
        if (DAYS.has(low) || MONTHS.has(low) || NUMBER_WORDS.has(low)) continue;
        if (i > 0 && word[0] === word[0].toUpperCase()
            && word[0] !== word[0].toLowerCase()) continue;
        const st = stem(low);
        if (FRAME_DROP.has(st)) continue;
        if (low.includes('-') && !MARKERS.has(st)) continue;
        out.add(TOPIC_VOCAB[st] ?? st);
    }
    return out;
}

export function identifiers(f) {
    const out = new Set();
    for (const t of f) if (t.startsWith('ID:')) out.add(t);
    return out;
}

export function importance(text, reinforcements = 0, hits = 0) {
    const stems = new Set();
    for (const m of text.toLowerCase().matchAll(WORD)) stems.add(stem(m[0]));
    let weight = 1.0;
    for (const [vocabulary, tier] of IMPORTANCE_TIERS) {
        if (vocabulary.some((w) => stems.has(w))) { weight = tier; break; }
    }
    if (!hasPayload(text)) weight -= 0.5;
    for (const w of stems) if (FLAGGED.has(w)) { weight += 1.0; break; }
    weight += Math.min(1.0, 0.4 * Math.log1p(reinforcements + hits));
    return Math.max(1.0, Math.min(5.0, weight));
}

function sameSet(a, b) {
    if (a.size !== b.size) return false;
    for (const x of a) if (!b.has(x)) return false;
    return true;
}

// ------------------------------------------------------------------ memory

export class RPMemory {
    constructor({ maxEntries = 800, reserve = SCENE_RESERVE,
                  useCords = USE_CORDS } = {}) {
        this.maxEntries = maxEntries;
        this.reserve = Math.max(0, Math.min(1, reserve));
        this.useCords = useCords;
        this.cords = new Map();      // seq -> Map(seq -> weight)
        this.revised = new Map();    // seq -> [text replaced]
        this.lastTurn = [];
        this.knots = [];
        this.seen = new Set();
        this.index = new Map();      // token -> Set(seq)
        this.byFrame = new Map();    // frame key -> [record]
        this.bySeq = new Map();
        this.names = new Map();      // capitalised token -> count
        this.cast = new Set();
        this.observed = 0;
        this.seq = 0;
    }

    known = (token) => this.cast.has(token)
        || (this.names.get(token) ?? 0) >= GAZETTEER_MIN;

    observe(speaker, text) {
        text = String(text ?? '');
        if (!text) return [];
        const name = String(speaker ?? '').trim() || 'the user';
        this.cast.add(name);
        for (const token of capitals(text)) {
            this.names.set(token, (this.names.get(token) ?? 0) + 1);
        }
        this.observed += text.length;
        const subject = ['user', 'you'].includes(name.toLowerCase())
            ? 'the user' : name;
        const before = this.seq;
        const added = [];
        for (const knot of rpExtract(text, subject, this.known)) {
            if (this._add(knot)) added.push(knot);
        }
        for (const knot of this._dialogueKnots(subject, text)) {
            if (this._add(knot)) added.push(knot);
        }
        this._tieTurn(before);
        return added;
    }

    _tie(left, right, weight) {
        if (left === right) return;
        for (const [a, b] of [[left, right], [right, left]]) {
            if (!this.cords.has(a)) this.cords.set(a, new Map());
            const row = this.cords.get(a);
            if ((row.get(b) ?? 0) < weight) row.set(b, weight);
        }
    }

    // Same-turn knots belong together; consecutive turns are how the scene
    // moved; a revision link keeps what a knot replaced instead of deleting
    // it, which is the one thing roleplay state churn genuinely needs.
    _tieTurn(before) {
        const fresh = [];
        for (let s = before + 1; s <= this.seq; s++) {
            if (this.bySeq.has(s)) fresh.push(s);
        }
        for (let i = 0; i < fresh.length; i++) {
            for (let j = i + 1; j < fresh.length; j++) {
                this._tie(fresh[i], fresh[j], SAME_TURN);
            }
        }
        for (const left of this.lastTurn) {
            for (const right of fresh) this._tie(left, right, ADJACENT);
        }
        for (const seq of fresh) {
            for (const text of this.revised.get(seq) ?? []) {
                const prior = this.knots.find((r) => r.text === text);
                if (prior) this._tie(seq, prior.seq, REVISION);
            }
        }
        if (fresh.length) this.lastTurn = fresh;
    }

    cordsOf(seq) {
        const row = this.cords.get(seq);
        if (!row) return [];
        return [...row].filter(([, w]) => w >= MIN_CORD)
            .sort((a, b) => b[1] - a[1]);
    }

    historyOf(text) {
        const record = this.knots.find((r) => r.text === text);
        return record ? (this.revised.get(record.seq) ?? []) : [];
    }

    _dialogueKnots(speaker, text) {
        const out = [];
        for (const m of text.matchAll(QUOTED)) {
            const said = m[1].trim();
            if (said.length < DIALOGUE_MIN || !rpIsFact(said, this.known)) continue;
            let body = trim(said.replace(/[.!?,]+$/, ''));
            if (body[0] && body[0] === body[0].toUpperCase()) {
                body = body[0].toLowerCase() + body.slice(1);
            }
            out.push(fit(`${speaker} says ${body}`, MAX_ENTRY_CHARS));
        }
        return out;
    }

    _add(knot) {
        if (this.seen.has(knot)) return false;
        const w = words(knot);
        const f = frame(knot);
        const [inherited, reinforced] = this._supersede(w, f);
        this.seq += 1;
        const record = {
            text: knot, seq: this.seq, hits: inherited, ts: Date.now(),
            w, f, key: [...f].sort().join(''), ids: identifiers(f),
            reinforced, imp: importance(knot, reinforced, inherited),
        };
        this.knots.push(record);
        this.seen.add(knot);
        this._indexAdd(record);
        this._evict();
        return true;
    }

    _indexAdd(record) {
        this.bySeq.set(record.seq, record);
        for (const token of record.w) {
            if (!this.index.has(token)) this.index.set(token, new Set());
            this.index.get(token).add(record.seq);
        }
        if (record.key) {
            if (!this.byFrame.has(record.key)) this.byFrame.set(record.key, []);
            this.byFrame.get(record.key).push(record);
        }
    }

    _drop(records) {
        const doomed = new Set(records.map((r) => r.seq));
        if (!doomed.size) return;
        for (const record of records) {
            this.bySeq.delete(record.seq);
            this.seen.delete(record.text);
            for (const token of record.w) {
                const bucket = this.index.get(token);
                if (bucket) {
                    bucket.delete(record.seq);
                    if (!bucket.size) this.index.delete(token);
                }
            }
            if (record.key && this.byFrame.has(record.key)) {
                const kept = this.byFrame.get(record.key)
                    .filter((k) => k.seq !== record.seq);
                if (kept.length) this.byFrame.set(record.key, kept);
                else this.byFrame.delete(record.key);
            }
        }
        this.knots = this.knots.filter((k) => !doomed.has(k.seq));
        // A cord to a knot that no longer exists costs budget on the walk and
        // buys nothing, and uncut they accumulate for the life of the memory.
        for (const seq of doomed) {
            for (const [other] of this.cords.get(seq) ?? []) {
                const row = this.cords.get(other);
                if (row) { row.delete(seq); if (!row.size) this.cords.delete(other); }
            }
            this.cords.delete(seq);
            this.revised.delete(seq);
        }
        this.lastTurn = this.lastTurn.filter((s) => !doomed.has(s));
    }

    _overlapping(w, minimum) {
        const probes = [...w].sort((a, b) =>
            (this.index.get(a)?.size ?? 0) - (this.index.get(b)?.size ?? 0))
            .slice(0, Math.max(1, w.size - minimum + 1));
        const seqs = new Set();
        for (const token of probes) {
            for (const s of this.index.get(token) ?? []) seqs.add(s);
        }
        const out = [];
        for (const seq of seqs) {
            const record = this.bySeq.get(seq);
            if (!record) continue;
            let shared = 0;
            for (const t of w) if (record.w.has(t)) shared += 1;
            if (shared >= minimum) out.push([record, shared]);
        }
        return out;
    }

    // Conservative on purpose: a wrong merge loses a fact silently, a missed
    // merge only wastes a knot.
    _supersede(w, f) {
        const ids = identifiers(f);
        const key = [...f].sort().join('');
        const doomed = [];
        const seen = new Set();
        if (key) {
            for (const k of this.byFrame.get(key) ?? []) {
                if (!seen.has(k.seq)) { seen.add(k.seq); doomed.push(k); }
            }
        }
        if (w.size) {
            const minimum = Math.max(1, Math.floor(SUPERSEDE_SIMILARITY * w.size));
            for (const [k, shared] of this._overlapping(w, minimum)) {
                if (!sameSet(k.ids, ids)) continue;
                const union = w.size + k.w.size - shared;
                if (union && shared / union >= SUPERSEDE_SIMILARITY
                    && !seen.has(k.seq)) {
                    seen.add(k.seq); doomed.push(k);
                }
            }
        }
        if (doomed.length) {
            this.revised.set(this.seq + 1, doomed.map((k) => k.text));
        }
        this._drop(doomed);
        return [
            doomed.reduce((best, k) => Math.max(best, k.hits), 0),
            doomed.reduce((sum, k) => sum + 1 + (k.reinforced ?? 0), 0),
        ];
    }

    _rarity(record) {
        if (!record.w.size) return 0;
        const total = this.knots.length || 1;
        let sum = 0;
        for (const t of record.w) {
            sum += Math.log(1 + total / (1 + (this.index.get(t)?.size ?? 0)));
        }
        return sum / record.w.size;
    }

    // Per-knot count of other knots within BURST_WINDOW_MS -- recomputed
    // from current timestamps every time, not cached on the knot, so it
    // stays correct as more knots land near an existing one later. A
    // sliding window over knots sorted by `ts`: O(n log n) for the sort,
    // O(n) for the sweep itself, not the O(n^2) a naive per-knot scan
    // would be. Ported from qontext_memory.py's `_burst_counts`.
    _burstCounts() {
        const knots = this.knots;
        const n = knots.length;
        const counts = new Map();
        if (n < 2) {
            for (const k of knots) counts.set(k.seq, 0);
            return counts;
        }
        const order = [...knots.keys()].sort((a, b) => knots[a].ts - knots[b].ts);
        const out = new Array(n).fill(0);
        let lo = 0;
        let hi = 0;
        for (let pos = 0; pos < n; pos += 1) {
            const t = knots[order[pos]].ts;
            while (knots[order[lo]].ts < t - BURST_WINDOW_MS) lo += 1;
            if (hi < pos) hi = pos;
            while (hi + 1 < n && knots[order[hi + 1]].ts <= t + BURST_WINDOW_MS) hi += 1;
            out[order[pos]] = hi - lo;   // neighbours, excluding self
        }
        for (let i = 0; i < n; i += 1) counts.set(knots[i].seq, out[i]);
        return counts;
    }

    // [text, count] per knot, oldest first. Read-only metadata, always
    // available regardless of BURST_WEIGHT -- independent of whether that
    // weight is currently making the count *act* on ranking or eviction.
    burstiness() {
        const counts = this._burstCounts();
        return this.knots.map((k) => [k.text, counts.get(k.seq) ?? 0]);
    }

    // 1.0 (a true no-op) when BURST_WEIGHT is 0 or the knot has no
    // temporal neighbours. Otherwise grows with the log of the neighbour
    // count, the same log(1+x) shape _rarity and _weights already use, so
    // one extremely frantic cluster cannot dominate the way a raw count
    // would.
    _burstFactor(record, counts) {
        if (!BURST_WEIGHT || !counts) return 1.0;
        const count = counts.get(record.seq) ?? 0;
        if (!count) return 1.0;
        return 1.0 + BURST_WEIGHT * Math.log(1 + count);
    }

    // Importance nudges distinctiveness, never outranks it. Two stronger
    // versions were measured in Python and both were worse. Burst density
    // (see BURST_WEIGHT) nudges the same way and is a no-op at the default
    // weight of 0 -- multiplying by 1.0 changes nothing.
    _evict() {
        const overflow = this.knots.length - this.maxEntries;
        if (overflow <= 0) return;
        const burstCounts = BURST_WEIGHT ? this._burstCounts() : null;
        const ranked = [...this.knots].sort((a, b) =>
            a.hits - b.hits
            || this._rarity(a) * (1 + a.imp / 5) * this._burstFactor(a, burstCounts)
               - this._rarity(b) * (1 + b.imp / 5) * this._burstFactor(b, burstCounts)
            || a.seq - b.seq);
        this._drop(ranked.slice(0, overflow));
    }

    _expand(query) {
        const expanded = new Set();
        for (const w of words(query)) {
            expanded.add(w);
            for (const syn of SYNONYMS[w] ?? []) expanded.add(syn);
        }
        return expanded;
    }

    _weights(expanded) {
        const total = this.knots.length || 1;
        const weights = new Map();
        for (const token of expanded) {
            const df = this.index.get(token)?.size ?? 0;
            weights.set(token, Math.log(1 + total / (1 + df)));
        }
        return weights;
    }

    _score(record, expanded, aboutUser, weights, burstCounts = null) {
        let overlap = 0;
        for (const t of expanded) if (record.w.has(t)) overlap += weights.get(t);
        if (overlap && aboutUser && SUBJECT_FOCUS) {
            const text = record.text.toLowerCase();
            const firstParty = (text.includes('the user') || text.includes('the team'))
                && !CHAINED_POSSESSIVE.test(text);
            if (!firstParty) overlap *= (1 - SUBJECT_FOCUS);
        }
        if (overlap && BURST_WEIGHT && burstCounts) {
            overlap *= this._burstFactor(record, burstCounts);
        }
        if (overlap && LENGTH_NORM) {
            overlap *= Math.pow(
                REFERENCE_LENGTH / Math.max(record.text.length, REFERENCE_LENGTH),
                LENGTH_NORM);
        }
        const subject = (aboutUser && record.text.toLowerCase().includes('user')) ? 1 : 0;
        return [Math.round(overlap * 1e6) / 1e6, subject, record.seq];
    }

    _ranked(query) {
        const expanded = this._expand(query);
        const aboutUser = String(query).toLowerCase().includes('user');
        const weights = this._weights(expanded);
        const seqs = new Set();
        for (const token of expanded) {
            for (const s of this.index.get(token) ?? []) seqs.add(s);
        }
        const pool = [...seqs].map((s) => this.bySeq.get(s)).filter(Boolean);
        const burstCounts = BURST_WEIGHT ? this._burstCounts() : null;
        const scored = pool.map((k) =>
            [this._score(k, expanded, aboutUser, weights, burstCounts), k]);
        scored.sort((a, b) => b[0][0] - a[0][0] || b[0][1] - a[0][1] || b[0][2] - a[0][2]);
        return scored;
    }

    // The pack for a roleplay turn. Reserved slice first (standing facts by
    // importance, ignoring the turn — a turn is a poor query), then whatever
    // the turn's own words reach, in conversation order.
    scene(turn, budget = 600) {
        budget = Math.max(0, budget | 0);
        if (!budget || !this.knots.length) return '';
        const chosen = [];
        const taken = new Set();
        let total = 0;
        const allowance = Math.floor(budget * this.reserve);
        const byImportance = [...this.knots].sort((a, b) =>
            b.imp - a.imp || b.seq - a.seq);
        for (const record of byImportance) {
            const cost = record.text.length + (chosen.length ? 1 : 0);
            if (total + cost > allowance) continue;
            chosen.push(record); taken.add(record.seq); total += cost;
            if (total >= allowance * 0.9) break;
        }
        // No relevance floor here, deliberately. The chat pack drops weak
        // matches because in a small memory they cost budget the strong ones
        // need. In roleplay the turn is a poor query and its best score is
        // low, so a floor proportional to it cuts the answer off along with
        // the noise — measured, and the reason scene() differs from pack().
        // Parity note: adding a floor here was the one divergence the JS port
        // introduced against the Python implementation, caught by parity.mjs.
        const ranked = this._ranked(turn).filter(([, r]) => !taken.has(r.seq));
        const share = this.useCords ? CORD_SHARE : 0;
        const seedRoom = total + Math.floor((budget - total) * (1 - share));
        const seeds = [];
        for (const [, record] of ranked) {
            const cost = record.text.length + (chosen.length ? 1 : 0);
            if (total + cost > seedRoom) continue;
            chosen.push(record); taken.add(record.seq); seeds.push(record.seq);
            total += cost;
        }

        // Walk outward. Two hops away costs the product of the threads, so a
        // weak cord twice is weaker than a strong one once — the weave sets
        // the reach rather than a fixed radius.
        const reached = new Map();
        let frontier = this.useCords ? seeds.map((s) => [s, 1.0]) : [];
        for (let hop = 0; hop < 2 && frontier.length; hop++) {
            const next = [];
            for (const [seq, carried] of frontier) {
                for (const [other, weight] of this.cordsOf(seq)) {
                    if (taken.has(other)) continue;
                    const strength = carried * weight;
                    if (strength < MIN_CORD) continue;
                    if (strength > (reached.get(other) ?? 0)) {
                        reached.set(other, strength);
                        next.push([other, strength]);
                    }
                }
            }
            frontier = next;
        }
        for (const [seq] of [...reached].sort((a, b) => b[1] - a[1])) {
            const record = this.bySeq.get(seq);
            if (!record) continue;
            const cost = record.text.length + (chosen.length ? 1 : 0);
            if (total + cost > budget) continue;
            chosen.push(record); taken.add(seq); total += cost;
        }
        for (const record of chosen) record.hits += 1;
        chosen.sort((a, b) => a.seq - b.seq);
        return chosen.map((r) => r.text).join('\n');
    }

    entries() { return this.knots.map((k) => k.text); }
    get size() { return this.knots.length; }

    stats() {
        const stored = this.knots.reduce((n, k) => n + k.text.length, 0);
        let entities = 0;
        for (const [n] of this.names) if (this.known(n)) entities += 1;
        return {
            entries: this.knots.length, stored_chars: stored,
            observed_chars: this.observed,
            density: this.observed ? stored / this.observed : 0,
            cast: this.cast.size, entities, reserve: this.reserve,
        };
    }

    serialize() {
        return {
            v: 1, reserve: this.reserve, maxEntries: this.maxEntries,
            cast: [...this.cast], names: [...this.names],
            knots: this.knots.map((k) => ({
                text: k.text, seq: k.seq, hits: k.hits, imp: k.imp,
                reinforced: k.reinforced,
            })),
        };
    }

    static deserialize(data) {
        const memory = new RPMemory({
            maxEntries: data.maxEntries ?? 800,
            reserve: data.reserve ?? SCENE_RESERVE,
        });
        for (const name of data.cast ?? []) memory.cast.add(name);
        for (const [n, c] of data.names ?? []) memory.names.set(n, c);
        for (const k of data.knots ?? []) {
            if (memory._add(k.text)) {
                const record = memory.knots[memory.knots.length - 1];
                record.hits = k.hits ?? 0;
                record.imp = k.imp ?? record.imp;
                // `reinforced` was already written by serialize() but never
                // read back here -- deserialize() rebuilds from just the
                // survivors, so _add()'s own _supersede() never sees the
                // corrections that produced this count and always
                // recomputes 0. Same class of bug as the Python
                // serialize()/deserialize() fix (FORMAT_VERSION 2 -> 3):
                // data written, never restored, silently resetting on
                // every save/load cycle.
                record.reinforced = k.reinforced ?? 0;
            }
        }
        return memory;
    }
}

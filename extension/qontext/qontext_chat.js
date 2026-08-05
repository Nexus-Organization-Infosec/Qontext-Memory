// Qontext for assistant chat — the portable core, ported from
// qontext_memory.py's QontextMemory (the "vanilla" configuration: no
// weave, no semantic bridge, no adaptive-K classifier — exactly what
// `QontextMemory()` with no arguments does, which is all app.py's
// "Assistant chat" tab ever called).
//
// Nearly every piece of language handling below is *not* duplicated here:
// stemming, sentence splitting, trimming, fitting, third-person rewriting,
// frame/importance scoring and the vocabulary tables are shared with the
// roleplay port in qontext.js, because qontext_rp.py imports them from
// qontext_memory.py in Python too. Only what is genuinely different about
// chat — the extraction admission rule (drop questions and second-person
// remarks; a marker-or-payload test instead of the structural roleplay
// one) and the QontextMemory class itself (index terms, no cords, no
// scene-reserve, budget-only pack()) lives in this file.

import {
    stem, words, splitSentences, hasPayload, isFact, trim, fit, thirdPerson,
    attribute, frame, identifiers, importance,
    MAX_ENTRY_CHARS, MIN_SENTENCE, SUPERSEDE_SIMILARITY, RELEVANCE_FLOOR,
    FLOOR_MAX_KNOTS, LENGTH_NORM, REFERENCE_LENGTH, SUBJECT_FOCUS,
} from './qontext.js';
import { OPENERS_SOURCE, OPENERS_FLAGS, SYNONYMS } from './tables.js';

const OPENERS = new RegExp(OPENERS_SOURCE, OPENERS_FLAGS.includes('i') ? 'gi' : 'g');
const CHAINED_POSSESSIVE = /\bthe (?:user|team)'s \w+'s\b/;

// Hidden index terms: words from the turn a knot came from, used only for
// matching, never shown to the model. See qontext_memory.py's INDEX_TERMS
// comment for the measurement behind this ("a knot has two representations
// with different objectives").
const INDEX_TERMS = 10;
const INDEX_WEIGHT = 0.35;
const INDEX_DF_CEILING = 0.02;

const QUESTION = /\?\s*$/;
const SECOND_PERSON = /\byou\b|\byour\b|\byours\b|\byou'(?:re|ll|ve|d)\b/i;
const FIRST_PERSON = /\bI\b|\bI'(?:m|ve|ll|d)\b|\bmy\b|\bmine\b|\bme\b|\bwe\b|\bwe'(?:re|ve|ll)\b|\bour\b|\bus\b/i;

function isQuestion(sentence) {
    return QUESTION.test(sentence);
}

// A claim about the person being spoken to rather than the speaker. Off for
// roleplay (a scene's other party has facts of its own); on for chat, where
// the other party is the assistant.
function addressesOther(sentence) {
    return SECOND_PERSON.test(sentence) && !FIRST_PERSON.test(sentence);
}

function sameSet(a, b) {
    if (a.size !== b.size) return false;
    for (const x of a) if (!b.has(x)) return false;
    return true;
}

// Pull the fact-bearing knots out of one piece of text. `subject` is who
// "I" refers to (always "the user" here — chat has one speaker who states
// facts). `dropAddress` discards sentences aimed at the assistant.
export function extract(text, subject = 'the user', dropAddress = true) {
    if (!text) return [];
    const out = [];
    for (const raw of splitSentences(String(text))) {
        if (isQuestion(raw) || (dropAddress && addressesOther(raw))) continue;
        let sent = (raw.trim() + ' ').replace(OPENERS, '');
        sent = sent.replace(/^[\s.,!?:]+|[\s.,!?:]+$/g, '');
        if (sent.length < MIN_SENTENCE || !isFact(sent)) continue;
        let knot = thirdPerson(trim(sent), subject);
        knot = attribute(fit(knot, MAX_ENTRY_CHARS), subject).trim();
        if (knot.length > MAX_ENTRY_CHARS) knot = fit(knot, MAX_ENTRY_CHARS);
        if (knot.length >= MIN_SENTENCE) out.push(knot);
    }
    return out;
}

export class QontextMemory {
    constructor({ maxEntries = 500, speakers = 'user' } = {}) {
        this.maxEntries = maxEntries;
        // "user": only the user states facts (assistant chatter is noise).
        // "all": every speaker is their own subject.
        this.speakers = speakers;
        this.knots = [];
        this.seen = new Set();
        this.index = new Map();      // token -> Set(seq)
        this.byFrame = new Map();    // frame key -> [record]
        this.bySeq = new Map();
        this.observed = 0;
        this.seq = 0;
    }

    // ------------------------------------------------------------- write

    observe(speaker, text) {
        text = String(text ?? '');
        if (!text) return [];
        const name = String(speaker ?? '').trim();
        const low = name.toLowerCase();
        this.observed += text.length;
        let subject;
        if (['user', 'you', ''].includes(low)) {
            subject = 'the user';
        } else if (this.speakers === 'user') {
            return [];    // assistant chatter never carries new facts
        } else {
            subject = name;
        }
        return extract(text, subject).filter((k) => this._add(k, text));
    }

    add(knot) {
        knot = String(knot ?? '').trim().slice(0, MAX_ENTRY_CHARS);
        if (knot.length < MIN_SENTENCE) return false;
        this.observed += knot.length;
        return this._add(knot);
    }

    _indexTerms(knotWords, context) {
        if (!INDEX_TERMS || !context) return new Set();
        const ceiling = Math.max(3, Math.floor(INDEX_DF_CEILING * this.knots.length));
        const spare = [...words(context)].filter((w) =>
            !knotWords.has(w) && w.length >= 4
            && (this.index.get(w)?.size ?? 0) <= ceiling);
        spare.sort((a, b) => (this.index.get(a)?.size ?? 0) - (this.index.get(b)?.size ?? 0));
        return new Set(spare.slice(0, INDEX_TERMS));
    }

    _add(knot, context = null) {
        if (this.seen.has(knot)) return false;
        const w = words(knot);
        const f = frame(knot);
        const [inherited, reinforced] = this._supersede(w, f);
        this.seq += 1;
        const record = {
            text: knot, seq: this.seq, hits: inherited, ts: Date.now(),
            w, f, key: [...f].sort().join(''), ids: identifiers(f),
            reinforced, imp: importance(knot, reinforced, inherited),
            idx: this._indexTerms(w, context),
        };
        this.knots.push(record);
        this.seen.add(knot);
        this._indexAdd(record);
        this._evict();
        return true;
    }

    _indexAdd(record) {
        this.bySeq.set(record.seq, record);
        for (const token of new Set([...record.w, ...record.idx])) {
            if (!this.index.has(token)) this.index.set(token, new Set());
            this.index.get(token).add(record.seq);
        }
        if (record.key) {
            if (!this.byFrame.has(record.key)) this.byFrame.set(record.key, []);
            this.byFrame.get(record.key).push(record);
        }
    }

    _indexRemove(record) {
        this.bySeq.delete(record.seq);
        for (const token of new Set([...record.w, ...record.idx])) {
            const bucket = this.index.get(token);
            if (bucket) { bucket.delete(record.seq); if (!bucket.size) this.index.delete(token); }
        }
        if (record.key && this.byFrame.has(record.key)) {
            const kept = this.byFrame.get(record.key).filter((k) => k.seq !== record.seq);
            if (kept.length) this.byFrame.set(record.key, kept);
            else this.byFrame.delete(record.key);
        }
    }

    _drop(records) {
        const doomed = new Set(records.map((r) => r.seq));
        if (!doomed.size) return;
        for (const record of records) this._indexRemove(record);
        this.knots = this.knots.filter((k) => !doomed.has(k.seq));
        this.seen = new Set(this.knots.map((k) => k.text));
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
        const key = [...f].sort().join('');
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

    _evict() {
        const overflow = this.knots.length - this.maxEntries;
        if (overflow <= 0) return;
        const ranked = [...this.knots].sort((a, b) =>
            a.hits - b.hits
            || this._rarity(a) * (1 + a.imp / 5) - this._rarity(b) * (1 + b.imp / 5)
            || a.seq - b.seq);
        this._drop(ranked.slice(0, overflow));
    }

    forget(pattern) {
        const needle = String(pattern ?? '').toLowerCase();
        if (!needle) return 0;
        const doomed = this.knots.filter((k) => k.text.toLowerCase().includes(needle));
        this._drop(doomed);
        return doomed.length;
    }

    clear() {
        this.knots = [];
        this.seen = new Set();
        this.index = new Map();
        this.byFrame = new Map();
        this.bySeq = new Map();
        this.observed = 0;
        this.seq = 0;
    }

    // -------------------------------------------------------------- read

    entries() { return this.knots.map((k) => k.text); }
    get size() { return this.knots.length; }

    // Knot texts ordered by (importance desc, recency desc), optionally
    // capped — see qontext_memory.py's candidates() docstring for why.
    candidates(limit = null) {
        const ranked = [...this.knots].sort((a, b) =>
            (a.imp ?? 1.0) - (b.imp ?? 1.0) || a.seq - b.seq).reverse();
        const capped = limit != null ? ranked.slice(0, Math.max(0, limit | 0)) : ranked;
        return capped.map((k) => k.text);
    }

    _expand(query) {
        const qwords = words(query);
        const expanded = new Set(qwords);
        for (const w of qwords) {
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

    _score(record, expanded, aboutUser, weights) {
        let overlap = 0;
        for (const t of expanded) if (record.w.has(t)) overlap += weights.get(t);
        // A word that was near the knot is weaker evidence than a word in it.
        let hidden = 0;
        for (const t of expanded) {
            if (record.idx.has(t) && !record.w.has(t)) hidden += weights.get(t);
        }
        if (hidden) overlap += INDEX_WEIGHT * hidden;
        if (overlap && aboutUser && SUBJECT_FOCUS) {
            const text = record.text.toLowerCase();
            const firstParty = (text.includes('the user') || text.includes('the team'))
                && !CHAINED_POSSESSIVE.test(text);
            if (!firstParty) overlap *= (1 - SUBJECT_FOCUS);
        }
        if (overlap && LENGTH_NORM) {
            overlap *= Math.pow(
                REFERENCE_LENGTH / Math.max(record.text.length, REFERENCE_LENGTH),
                LENGTH_NORM);
        }
        const subject = (aboutUser && record.text.toLowerCase().includes('user')) ? 1 : 0;
        return [Math.round(overlap * 1e6) / 1e6, subject, record.seq];
    }

    // Knots sharing at least one word with the query.
    _candidatesForQuery(expanded) {
        const seqs = new Set();
        for (const token of expanded) {
            for (const s of this.index.get(token) ?? []) seqs.add(s);
        }
        const out = [];
        for (const s of seqs) { const r = this.bySeq.get(s); if (r) out.push(r); }
        return out;
    }

    _ranked(query, allKnots = false) {
        const expanded = this._expand(query);
        const aboutUser = String(query ?? '').toLowerCase().includes('user');
        const pool = allKnots ? this.knots : this._candidatesForQuery(expanded);
        const weights = this._weights(expanded);
        const scored = pool.map((k) => [this._score(k, expanded, aboutUser, weights), k]);
        scored.sort((a, b) => b[0][0] - a[0][0] || b[0][1] - a[0][1] || b[0][2] - a[0][2]);
        return scored;
    }

    // The densest set of relevant knots that fits in `budget` characters.
    // Never exceeds the budget, never throws, returns '' when empty.
    pack(query, budget = 300) {
        budget = Math.max(0, budget | 0);
        if (!budget || !this.knots.length) return '';
        const ranked = this._ranked(query);
        if (!ranked.length) {
            // Nothing matched lexically: send the newest knot rather than
            // nothing, so the model still has some grounding.
            const newest = this.knots.reduce((a, b) => (b.seq > a.seq ? b : a));
            if (newest.text.length <= budget) {
                newest.hits += 1;
                return newest.text;
            }
            return '';
        }
        // A weak match is worse than no match in a small memory: it spends
        // budget the strong matches need. In a large one the top score is
        // inflated by whichever long knot matched best, so the floor is
        // dropped once the memory is big enough that being wrong gets
        // expensive rather than merely noisy.
        const floor = this.knots.length <= FLOOR_MAX_KNOTS
            ? ranked[0][0][0] * RELEVANCE_FLOOR : 0.0;
        const out = [];
        let total = 0;
        for (const [score, k] of ranked) {
            if (score[0] < floor) break;
            const cost = k.text.length + (out.length ? 1 : 0);
            if (total + cost > budget) continue;
            out.push(k);
            total += cost;
        }
        for (const k of out) k.hits += 1;
        return out.map((k) => k.text).join('\n');
    }

    explain(query, budget = 300) {
        const chosen = new Set(this.pack(query, budget).split('\n'));
        return this._ranked(query, true).map(([score, k]) =>
            [score[0], chosen.has(k.text), k.text]);
    }

    stats() {
        const stored = this.knots.reduce((n, k) => n + k.text.length, 0);
        return {
            meanImportance: this.knots.length
                ? this.knots.reduce((s, k) => s + (k.imp ?? 1.0), 0) / this.knots.length
                : 0.0,
            observedChars: this.observed,
            storedChars: stored,
            entries: this.knots.length,
            density: this.observed ? stored / this.observed : 0.0,
            maxEntries: this.maxEntries,
        };
    }
}

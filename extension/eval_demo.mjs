// Rigorous eval of the static demo's retrieval, per Hylke's test guide.
// Runs the exact same qontext_chat.js that powers the live Space.
import { QontextMemory, extract } from '/sessions/ecstatic-epic-cori/mnt/quipu-experiment/qontext-memory/extension/qontext/qontext_chat.js';

const TURNS = [
    // explicit fact
    ['user', "Alice works at Trello as a backend engineer."],
    ['assistant', "Nice, how's that going?"],
    // script fact (an event implying other unstated events)
    ['user', "She skipped the sprint standup this morning because she overslept."],
    ['assistant', "That happens to everyone eventually."],
    // a fact with a causal/consequence relation
    ['user', "I promised my manager I'd finish the report by Friday, but the deadline slipped because the API kept timing out."],
    ['assistant', "Oh no, that sounds stressful."],
    // correction / supersession
    ['user', "I live in Amsterdam, near the station."],
    ['assistant', "Nice area."],
    ['user', "Actually I moved to Utrecht last month, meant to mention that earlier."],
    ['assistant', "Utrecht is lovely, welcome to the area."],
    // distractor / filler
    ['user', "The weather was strange again this morning, nothing much happened."],
    ['assistant', "Noted."],
    ['user', "My coffee went cold as usual today, kind of a rough start."],
    ['assistant', "Understood."],
    ['user', "My dog Bikkel is doing fine, just a bit lazy lately."],
    ['assistant', "Good to hear."],
    // an implied fact stated as a question from the OTHER party (should be
    // dropped entirely -- chat's extractor discards questions on purpose)
    ['assistant', "Didn't you also have a dentist appointment this week?"],
    ['user', "Oh right, yes -- Thursday at 2pm, almost forgot."],
];

const QUERIES = [
    ['direct', "Where does Alice work?"],
    ['hypernym (job vs work)', "What is Alice's job?"],
    ['script (implies she was late/absent)', "Why was she late to the standup?"],
    ['consequence (why did X happen)', "Why did the report slip?"],
    ['inference (implied forgetting)', "Didn't the user forget to mention something?"],
    ['direct control', "When is the dentist appointment?"],
];

function transcript(turns) {
    return turns.map(([w, t]) => `${w}: ${t}`).join('\n');
}

const mem = new QontextMemory();
console.log('=== 1. Extraction, turn by turn ===\n');
for (const [who, text] of TURNS) {
    const added = mem.observe(who, text);
    const tag = added.length ? `-> ${added.length} knot(s)` : '-> (nothing stored)';
    console.log(`[${who}] "${text}"`);
    for (const k of added) console.log(`    kept: "${k}"`);
    if (!added.length) console.log(`    ${tag}`);
    console.log();
}

console.log('=== 2. Compression ===\n');
const full = transcript(TURNS);
const stats = mem.stats();
console.log(`raw transcript: ${full.length} chars`);
console.log(`stored knots:   ${stats.storedChars} chars across ${stats.entries} knots`);
console.log(`density:        ${(stats.storedChars / stats.observedChars).toFixed(3)} (stored/observed)`);
console.log(`compression:    ${(full.length / Math.max(1, stats.storedChars)).toFixed(1)}x smaller than the raw transcript\n`);

console.log('Every surviving knot:');
mem.entries().forEach((k, i) => console.log(`  ${i + 1}. ${k}`));
console.log();

console.log('Hidden index terms per knot (never shown to a model, matching-only):');
for (const k of mem.knots) {
    console.log(`  "${k.text}"`);
    console.log(`    visible words: ${[...k.w].join(', ') || '(none)'}`);
    console.log(`    hidden index:  ${[...k.idx].join(', ') || '(none)'}`);
}
console.log();

console.log('=== 3. Retrieval probes ===\n');
for (const [kind, query] of QUERIES) {
    const packed = mem.pack(query, 300);
    console.log(`[${kind}] "${query}"`);
    console.log(`  -> ${packed ? JSON.stringify(packed) : '(nothing retrieved)'}`);

    // 4. word-level attribution: which query tokens actually matched.
    const expanded = mem._expand(query);
    console.log(`  query tokens (incl. synonyms): ${[...expanded].join(', ')}`);
    if (packed) {
        for (const line of packed.split('\n')) {
            const rec = mem.knots.find((k) => k.text === line);
            if (!rec) continue;
            const directHits = [...expanded].filter((t) => rec.w.has(t));
            const hiddenHits = [...expanded].filter((t) => rec.idx.has(t) && !rec.w.has(t));
            console.log(`    "${line}"`);
            console.log(`      matched via visible words: ${directHits.join(', ') || '(none)'}`);
            console.log(`      matched via hidden index:  ${hiddenHits.join(', ') || '(none)'}`);
        }
    }
    console.log();
}

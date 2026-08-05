// Does the JS chat port behave like qontext_memory.py's QontextMemory?
//
// Reads chat_cases.json (written by build_chat_cases.py from the Python
// implementation), replays them through the JS core, and reports every
// divergence.
import { readFileSync } from 'fs';
import { QontextMemory } from './qontext/qontext_chat.js';

const cases = JSON.parse(readFileSync('chat_cases.json', 'utf8'));
let checked = 0, bad = 0;

for (const scenario of cases) {
    const memory = new QontextMemory({ maxEntries: scenario.maxEntries ?? 500 });
    scenario.turns.forEach(([who, text], i) => {
        const got = memory.observe(who, text);
        const want = scenario.extracted[i];
        checked += 1;
        if (JSON.stringify(got) !== JSON.stringify(want)) {
            bad += 1;
            console.log(`\n[${scenario.name}] turn ${i} (${who}) extraction differs`);
            console.log('  py:', JSON.stringify(want));
            console.log('  js:', JSON.stringify(got));
        }
    });

    checked += 1;
    const gotEntries = memory.entries();
    if (JSON.stringify(gotEntries) !== JSON.stringify(scenario.entries)) {
        bad += 1;
        console.log(`\n[${scenario.name}] entries() differs`);
        console.log('  py:', JSON.stringify(scenario.entries));
        console.log('  js:', JSON.stringify(gotEntries));
    }

    for (const [query, budget, want] of scenario.packs) {
        const got = memory.pack(query, budget);
        checked += 1;
        if (got !== want) {
            bad += 1;
            console.log(`\n[${scenario.name}] pack(${JSON.stringify(query)}, ${budget}) differs`);
            console.log('  py:', JSON.stringify(want));
            console.log('  js:', JSON.stringify(got));
        }
    }

    const stats = memory.stats();
    checked += 1;
    if (stats.entries !== scenario.stats.entries
        || stats.storedChars !== scenario.stats.stored_chars
        || stats.observedChars !== scenario.stats.observed_chars) {
        bad += 1;
        console.log(`\n[${scenario.name}] stats() differs`);
        console.log('  py:', JSON.stringify(scenario.stats));
        console.log('  js:', JSON.stringify(stats));
    }
}
console.log(`\n${checked - bad}/${checked} checks match`);
process.exit(bad ? 1 : 0);

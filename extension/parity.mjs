// Does the JS port behave like qontext_rp.py?
//
// Reads cases.json (written by build_cases.py from the Python implementation),
// replays them through the JS core, and reports every divergence. The port is
// only worth having if the Python measurements describe it.
import { readFileSync } from 'fs';
import { RPMemory } from './qontext/qontext.js';

const cases = JSON.parse(readFileSync('cases.json', 'utf8'));
let checked = 0, bad = 0;

for (const scenario of cases) {
    const memory = new RPMemory({ maxEntries: scenario.maxEntries ?? 800,
                                  reserve: scenario.reserve ?? 0.5 });
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
    for (const [query, budget, want] of scenario.scenes) {
        const got = memory.scene(query, budget);
        checked += 1;
        if (got !== want) {
            bad += 1;
            console.log(`\n[${scenario.name}] scene(${JSON.stringify(query)}, ${budget}) differs`);
            console.log('  py:', JSON.stringify(want));
            console.log('  js:', JSON.stringify(got));
        }
    }
}
console.log(`\n${checked - bad}/${checked} checks match`);
process.exit(bad ? 1 : 0);

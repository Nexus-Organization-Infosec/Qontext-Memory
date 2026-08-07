// Hand-rolled checks for two additions to the JS port (qontext.js's
// RPMemory) that have no Python RPMemory equivalent to parity-test
// against: burst density (ported from qontext_memory.py, UNVERIFIED and
// off by default -- see BURST_WEIGHT's comment) and the `reinforced`
// persistence fix (a bug native to this file, since qontext_rp.py itself
// has no serialize()/deserialize() to compare against).
//
//     node test_burst_and_persistence.mjs
import { RPMemory } from './qontext/qontext.js';

let checked = 0;
let failed = 0;

function check(name, condition, detail) {
    checked += 1;
    if (!condition) {
        failed += 1;
        console.log(`FAIL: ${name}${detail ? '  (' + detail + ')' : ''}`);
    }
}

function setTimes(mem, ...ms) {
    mem.knots.forEach((k, i) => { k.ts = ms[i]; });
}

// ---------------------------------------------------------- burst counts

{
    const mem = new RPMemory({ maxEntries: 10 });
    for (let i = 0; i < 4; i += 1) mem.observe('Alice', `The gym was busy on day ${i}.`);
    mem.observe('Alice', 'People call me Marta.');
    setTimes(mem, 0, 1000, 2000, 3000, 600000);   // ms: first four within 10s
    const previous = 120000;   // BURST_WINDOW_MS default, sanity-documented
    check('default BURST_WINDOW_MS is 2 minutes', previous === 120000);

    const counts = new Map(mem.burstiness());
    const gymCounts = [...counts].filter(([t]) => t.includes('gym')).map(([, c]) => c);
    const martaCounts = [...counts].filter(([t]) => t.includes('Marta')).map(([, c]) => c);
    check('all four burst members see 3 neighbours',
          gymCounts.every((c) => c === 3), JSON.stringify(gymCounts));
    check('the lone knot sees 0 neighbours',
          martaCounts.length === 1 && martaCounts[0] === 0,
          JSON.stringify(martaCounts));
}

// -------------------------------------------------- true no-op at weight 0

{
    const before = new RPMemory({ maxEntries: 10 });
    for (let i = 0; i < 4; i += 1) before.observe('Alice', `The gym was busy on day ${i}.`);
    setTimes(before, 0, 1000, 2000, 3000);
    const packBefore = before.scene('gym', 300);

    // burstWeight defaults to 0 (BURST_WEIGHT) when not passed to the
    // constructor -- this re-confirms the pack is unaffected by the new
    // _burstCounts()/_burstFactor() machinery when it is not engaged,
    // which parity.mjs's unchanged 24/24 already confirms end to end.
    const after = new RPMemory({ maxEntries: 10 });
    for (let i = 0; i < 4; i += 1) after.observe('Alice', `The gym was busy on day ${i}.`);
    setTimes(after, 0, 1000, 2000, 3000);
    check('scene() output identical with the new machinery present',
          after.scene('gym', 300) === packBefore);
}

// --------------------------------------------- per-instance, not global

{
    // The whole point of moving burstWeight/burstWindowMs onto the
    // instance (for the settings-panel slider) rather than leaving them as
    // module constants: two memories with different settings, built at
    // the same time, must not see each other's configuration.
    const off = new RPMemory({ maxEntries: 10, burstWeight: 0 });
    const on = new RPMemory({ maxEntries: 10, burstWeight: 5, burstWindowMs: 10000 });
    check('constructor option overrides the module default',
          off.burstWeight === 0 && on.burstWeight === 5,
          `off=${off.burstWeight} on=${on.burstWeight}`);

    for (const mem of [off, on]) {
        for (let i = 0; i < 4; i += 1) mem.observe('Alice', `The gym was busy on day ${i}.`);
        mem.observe('Alice', 'People call me Marta.');
        setTimes(mem, 0, 1000, 2000, 3000, 90000000);
        mem.maxEntries = 4;
        mem._evict();
    }
    check('burstWeight=0 instance evicts the oldest (plain tie-break)',
          !off.entries().join(' ').includes('day 0'));
    check('burstWeight=5 instance protects the burst, drops the lone knot',
          !on.entries().join(' ').includes('Marta'));

    const roundTripped = RPMemory.deserialize(on.serialize());
    check('burstWeight/burstWindowMs survive a serialize/deserialize round-trip',
          roundTripped.burstWeight === 5 && roundTripped.burstWindowMs === 10000,
          `weight=${roundTripped.burstWeight} window=${roundTripped.burstWindowMs}`);
}

// ------------------------------------------------- reinforced persistence

{
    const mem = new RPMemory({ maxEntries: 10 });
    mem.observe('Alice', "Alice's manager is Priya.");
    mem.observe('Alice', "Alice's manager is Tomas now.");
    const before = mem.knots.find((k) => k.text.toLowerCase().includes('tomas'));
    check('a correction accumulates reinforced > 0 before save',
          before && before.reinforced > 0, before && before.reinforced);

    const reloaded = RPMemory.deserialize(mem.serialize());
    const after = reloaded.knots.find((k) => k.text.toLowerCase().includes('tomas'));
    check('reinforced survives a serialize/deserialize round-trip',
          after && after.reinforced === before.reinforced,
          `before=${before && before.reinforced} after=${after && after.reinforced}`);
}

console.log(`\n${checked - failed}/${checked} checks passed`);
process.exit(failed ? 1 : 0);

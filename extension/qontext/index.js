// Qontext Memory — SillyTavern extension.
//
// Three modes, because which one is better is an open question and the point
// of shipping this is to find out on real sessions:
//
//   regular   do nothing. SillyTavern's own context, unchanged. The control.
//   augment   send the normal context AND a Qontext pack. Costs tokens, can
//             only add information.
//   replace   send a short recency window plus the pack instead of the full
//             history. This is the condition the benchmarks actually measure,
//             and the one that can lose.
//
// Measured expectations, so nobody is surprised: on 11 public logs the pack
// carried about 10% of the facts a reply needed that recency could not supply.
// That is double what the assistant-chat build managed and it is still one
// fact in ten. Replace mode is an experiment, not an upgrade.

import { extension_settings, getContext } from '../../../extensions.js';
import { saveSettingsDebounced, eventSource, event_types } from '../../../../script.js';
import { RPMemory } from './qontext.js';

const NAME = 'qontext';
const DEFAULTS = {
    mode: 'regular',        // regular | augment | replace
    budget: 600,            // characters of pack
    reserve: 0.5,           // share of budget for standing facts
    useCords: true,
    recency: 10,            // messages kept verbatim in replace mode
    maxEntries: 800,
    capture: false,         // write a transcript for later evaluation
    header: 'Things established earlier in this scene:',
    // Burst density: UNVERIFIED, off by default -- see qontext.js's
    // BURST_WEIGHT comment. 0 makes it a true no-op, same as upstream.
    burstWeight: 0,          // how strongly burst density sways ranking/eviction
    burstWindowSec: 120,     // seconds; knots this close in time share a burst
};

let memory = null;
let builtFor = null;        // chat id the memory was built from
let captureDir = null;      // FileSystemDirectoryHandle
let captureName = null;

function settings() {
    extension_settings[NAME] = Object.assign({}, DEFAULTS,
                                             extension_settings[NAME] || {});
    return extension_settings[NAME];
}

// --------------------------------------------------------------- the memory

function speakerOf(message) {
    if (message.is_user) return 'User';
    return message.name || 'Character';
}

// Rebuilt from the chat array rather than kept incrementally: swipes, edits
// and deletions all mutate history behind our back, and a memory that has
// silently diverged from the visible conversation is worse than none.
function rebuild(chat, chatId) {
    const config = settings();
    memory = new RPMemory({
        maxEntries: config.maxEntries,
        reserve: config.reserve,
        useCords: config.useCords,
        burstWeight: config.burstWeight,
        burstWindowMs: config.burstWindowSec * 1000,
    });
    for (const message of chat) {
        if (message.is_system) continue;
        memory.observe(speakerOf(message), message.mes || '');
    }
    builtFor = chatId;
    return memory;
}

function packFor(chat, chatId, turn) {
    const config = settings();
    if (!memory || builtFor !== chatId) rebuild(chat, chatId);
    return memory.scene(turn, config.budget);
}

// ---------------------------------------------------------- the interceptor

globalThis.qontextInterceptor = async function (chat, contextSize, abort, type) {
    const config = settings();
    if (config.mode === 'regular' || !Array.isArray(chat) || !chat.length) return;

    try {
        const context = getContext();
        const chatId = context.chatId ?? context.characterId ?? 'unknown';
        // The query is the latest user turn — which is a poor query, and the
        // reason half the budget is reserved for standing facts.
        const lastUser = [...chat].reverse().find((m) => m.is_user && m.mes);
        const pack = packFor(chat, chatId, lastUser ? lastUser.mes : '');
        if (!pack) return;

        if (config.mode === 'replace' && chat.length > config.recency) {
            // Drop everything but the recency window, in place — the array
            // identity matters to the caller.
            const keep = chat.slice(-config.recency);
            chat.length = 0;
            chat.push(...keep);
        }

        chat.unshift({
            is_user: false,
            is_system: true,
            name: 'Qontext',
            send_date: Date.now(),
            mes: `${config.header}\n${pack}`,
            extra: { qontext: true },
        });

        if (config.capture) {
            await capture(chatId, chat, pack, lastUser ? lastUser.mes : '');
        }
    } catch (error) {
        // A memory layer must never take the chat down with it.
        console.error('[Qontext] interceptor failed, sending normal context',
                      error);
    }
};

// ------------------------------------------------------------------ capture
//
// Writes a JSONL of what was sent, into a folder you pick once — separate from
// SillyTavern's own chat files, so the evaluation corpus is yours and not
// tangled with ST's data directory. Requires a Chromium browser: the File
// System Access API is what makes writing outside Downloads possible at all.

async function chooseFolder() {
    if (!window.showDirectoryPicker) {
        toastr.error('This browser has no File System Access API. Use Chrome or Edge.');
        return;
    }
    captureDir = await window.showDirectoryPicker({ mode: 'readwrite' });
    settings().capture = true;
    saveSettingsDebounced();
    toastr.success(`Qontext will log to "${captureDir.name}"`);
    $('#qontext_folder').text(captureDir.name);
}

async function capture(chatId, chat, pack, turn) {
    if (!captureDir) return;
    if (captureName !== chatId) {
        captureName = chatId;
    }
    const safe = String(chatId).replace(/[^\w.-]+/g, '_').slice(0, 80);
    const line = JSON.stringify({
        t: new Date().toISOString(),
        chat: chatId,
        mode: settings().mode,
        budget: settings().budget,
        turn,
        pack,
        knots: memory ? memory.size : 0,
        // The whole visible context, so an evaluation can compare what the
        // model saw against what a full transcript would have shown it.
        sent: chat.map((m) => ({ who: speakerOf(m), sys: !!m.is_system,
                                 mes: m.mes })),
    }) + '\n';
    try {
        const handle = await captureDir.getFileHandle(`qontext-${safe}.jsonl`,
                                                      { create: true });
        const file = await handle.getFile();
        const writable = await handle.createWritable();
        await writable.write(await file.text());   // append
        await writable.write(line);
        await writable.close();
    } catch (error) {
        console.error('[Qontext] capture failed', error);
    }
}

// ----------------------------------------------------------------------- UI

const HTML = `
<div class="qontext-settings">
  <div class="inline-drawer">
    <div class="inline-drawer-toggle inline-drawer-header">
      <b>Qontext Memory</b>
      <div class="inline-drawer-icon fa-solid fa-circle-chevron-down down"></div>
    </div>
    <div class="inline-drawer-content">
      <label for="qontext_mode">Context mode</label>
      <select id="qontext_mode" class="text_pole">
        <option value="regular">Regular context (off)</option>
        <option value="augment">Augment — normal context plus pack</option>
        <option value="replace">Replace — recency window plus pack</option>
      </select>
      <small>Replace is the experiment. It can lose information; that is what
      it is for.</small>

      <label for="qontext_budget">Pack budget (characters)</label>
      <input id="qontext_budget" class="text_pole" type="number" min="100"
             max="4000" step="50">

      <label for="qontext_reserve">Standing-fact reserve (0–1)</label>
      <input id="qontext_reserve" class="text_pole" type="number" min="0"
             max="1" step="0.05">
      <small>Share of the pack filled by importance, ignoring the turn.
      0.5 measured best on roleplay logs.</small>

      <label for="qontext_recency">Messages kept verbatim (replace mode)</label>
      <input id="qontext_recency" class="text_pole" type="number" min="2"
             max="60" step="1">

      <label class="checkbox_label">
        <input id="qontext_cords" type="checkbox">
        Follow cords (knots linked by turn and revision)
      </label>

      <hr>
      <label for="qontext_burst_weight">Burst weight:
        <span id="qontext_burst_weight_val"></span></label>
      <input id="qontext_burst_weight" type="range" min="0" max="5" step="0.1">
      <label for="qontext_burst_window">Burst window:
        <span id="qontext_burst_window_val"></span></label>
      <input id="qontext_burst_window" type="range" min="10" max="600" step="10">
      <small><b>UNVERIFIED, off by default (0).</b> How many other knots
      landed close together in time -- a flurry scores higher than a knot
      that arrived into a quiet stretch. No benchmark backs this yet, only
      correctness tests; treat higher values as an experiment to feel out,
      not a setting known to help. See HANDOFF.md for the full story.</small>

      <hr>
      <label class="checkbox_label">
        <input id="qontext_capture" type="checkbox">
        Log sessions for evaluation
      </label>
      <div>Folder: <span id="qontext_folder">none</span></div>
      <input id="qontext_pick" class="menu_button" type="button"
             value="Choose log folder">
      <input id="qontext_stats" class="menu_button" type="button"
             value="Show memory">
      <pre id="qontext_out" class="qontext-out"></pre>
    </div>
  </div>
</div>`;

function windowLabel(sec) {
    if (sec < 60) return `${sec}s`;
    const min = sec / 60;
    return `${sec}s (${min % 1 === 0 ? min : min.toFixed(1)} min)`;
}

function bind() {
    const config = settings();
    $('#qontext_mode').val(config.mode);
    $('#qontext_budget').val(config.budget);
    $('#qontext_reserve').val(config.reserve);
    $('#qontext_recency').val(config.recency);
    $('#qontext_cords').prop('checked', config.useCords);
    $('#qontext_capture').prop('checked', config.capture);
    $('#qontext_burst_weight').val(config.burstWeight);
    $('#qontext_burst_weight_val').text(
        config.burstWeight === 0 ? '0 (off)' : config.burstWeight.toFixed(1));
    $('#qontext_burst_window').val(config.burstWindowSec);
    $('#qontext_burst_window_val').text(windowLabel(config.burstWindowSec));

    const save = (key, cast) => (event) => {
        settings()[key] = cast(event.target);
        builtFor = null;                  // settings changed: rebuild
        saveSettingsDebounced();
    };
    $('#qontext_mode').on('change', save('mode', (t) => t.value));
    $('#qontext_budget').on('change', save('budget', (t) => Number(t.value)));
    $('#qontext_reserve').on('change', save('reserve', (t) => Number(t.value)));
    $('#qontext_recency').on('change', save('recency', (t) => Number(t.value)));
    $('#qontext_cords').on('change', save('useCords', (t) => t.checked));
    $('#qontext_capture').on('change', save('capture', (t) => t.checked));
    $('#qontext_pick').on('click', () => chooseFolder().catch(console.error));

    // Sliders: update the visible readout continuously (input), but only
    // persist + trigger a memory rebuild once the user lets go (change) --
    // rebuilding on every intermediate drag position would thrash the
    // memory for no benefit, the same reasoning the other fields already
    // follow by using `change` rather than `input`.
    $('#qontext_burst_weight').on('input', (event) => {
        const value = Number(event.target.value);
        $('#qontext_burst_weight_val').text(value === 0 ? '0 (off)' : value.toFixed(1));
    });
    $('#qontext_burst_weight').on('change', save('burstWeight', (t) => Number(t.value)));
    $('#qontext_burst_window').on('input', (event) => {
        $('#qontext_burst_window_val').text(windowLabel(Number(event.target.value)));
    });
    $('#qontext_burst_window').on('change', save('burstWindowSec', (t) => Number(t.value)));

    $('#qontext_stats').on('click', () => {
        const context = getContext();
        const chat = context.chat || [];
        rebuild(chat, context.chatId ?? 'preview');
        const lastUser = [...chat].reverse().find((m) => m.is_user && m.mes);
        const pack = memory.scene(lastUser ? lastUser.mes : '',
                                  settings().budget);
        const s = memory.stats();
        let out = `${s.entries} knots from ${s.observed_chars} chars ` +
            `(density ${s.density.toFixed(2)}), cast ${s.cast}\n\n` +
            `pack for the last turn:\n${pack || '(empty)'}`;
        // Only shown when the weight is on -- with it at 0 the numbers are
        // real (burstiness() always computes) but meaningless to look at,
        // since nothing in ranking or eviction is reading them yet.
        if (s.burst_weight) {
            const top = memory.burstiness()
                .filter(([, count]) => count > 0)
                .sort((a, b) => b[1] - a[1])
                .slice(0, 5);
            out += `\n\nburst weight ${s.burst_weight}, window ` +
                `${s.burst_window_ms / 1000}s -- most clustered knots:\n` +
                (top.length
                    ? top.map(([text, count]) => `  [${count}] ${text}`).join('\n')
                    : '  (none -- nothing landed close together in time)');
        }
        $('#qontext_out').text(out);
    });
}

jQuery(async () => {
    $('#extensions_settings').append(HTML);
    bind();
    // Any change to the visible history invalidates the memory built from it.
    for (const event of [event_types.CHAT_CHANGED, event_types.MESSAGE_DELETED,
                         event_types.MESSAGE_EDITED, event_types.MESSAGE_SWIPED]) {
        if (event) eventSource.on(event, () => { builtFor = null; });
    }
    console.log('[Qontext] loaded');
});

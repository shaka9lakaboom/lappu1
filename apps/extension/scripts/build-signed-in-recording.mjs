// Builds tests/fixtures/chatgpt/signed-in/recording.json (P9, ADR 0008 H10):
//
//   node scripts/build-signed-in-recording.mjs          write the file
//   node scripts/build-signed-in-recording.mjs --check  exit 1 if the committed file differs
//
// A signed-in session, frame by frame, RECORDED THROUGH the structure recorder
// (scripts/record-chatgpt-structure.js - the same code the owner pastes into DevTools on the live
// page) from the structure-from-live fixture thread-layout.html (the chatgpt-2 "thread" layout of
// the live signed-in page, census 2026-09-25). The session changes the page the way ChatGPT does:
//
//   user-turn, assistant-streaming, assistant-complete, regenerated, edited-user,
//   attachment-user-turn, math-streaming, math-complete, degraded
//
// Reproducible: a fixed salt and date, so the file is byte-identical on every build (the test
// rebuilds and compares). A live recording from the recorder (recording-live.json) has the same
// shape and replays through the same test.
import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
export const RECORDING_PATH = join(root, 'tests', 'fixtures', 'chatgpt', 'signed-in', 'recording.json');
export const PAGE_URL = 'https://chatgpt.com/c/68d5a7f0-0000-4000-8000-00000000c0de';
const SALT = 'p9-committed-recording-fixed-salt';
const REGENERATED_ID = '2a0c0a22-0000-4000-8000-0000000000ff';

/** A jsdom window with the recorder installed (test mode: no console banner). */
export function recorderWindow(html = readFileSync(join(root, 'tests', 'fixtures', 'chatgpt', 'thread-layout.html'), 'utf8'), url = PAGE_URL) {
  const dom = new JSDOM(html, { url, runScripts: 'outside-only' });
  dom.window.__SKILLMIRROR_RECORDER_TEST__ = true;
  dom.window.eval(readFileSync(join(root, 'scripts', 'record-chatgpt-structure.js'), 'utf8'));
  return dom.window;
}

export function buildRecording() {
  const window = recorderWindow();
  const doc = window.document;
  const recorder = window.skillmirrorRecorder;
  const state = recorder.createState({ salt: SALT });
  const main = doc.querySelector('main');
  const thread = doc.querySelector('[data-thread-find-target]');
  const [turn1, turn2] = Array.from(thread.querySelectorAll(':scope > [data-turn-key]'));
  const form = doc.querySelector('[data-chatgpt-composer] form');
  const assistantOf = (turn) => turn.querySelector('[data-chatgpt-search-unit-key$=":assistant"]');
  const frames = [];
  const shot = (name) => frames.push({ name, html: recorder.sanitize(main, state), summary: recorder.summary(doc) });
  const stop = doc.createElement('button');
  stop.setAttribute('aria-label', 'Stop streaming');
  const streaming = (on) => (on ? form.appendChild(stop) : stop.remove());
  const finalIds = new Map(); // kept off the DOM: the recording must hold only page structure
  const setFinal = (unit, on) => {
    const answer = unit.querySelector('[data-chatgpt-selection-conversation-id]');
    if (on) answer.setAttribute('data-chatgpt-selection-message-id', finalIds.get(answer));
    else {
      finalIds.set(answer, answer.getAttribute('data-chatgpt-selection-message-id'));
      answer.removeAttribute('data-chatgpt-selection-message-id');
    }
    const controls = unit.querySelector('.turn-action-controls');
    if (controls) controls.hidden = !on;
  };

  // 1. The learner sends the first question: only the user unit is rendered.
  turn2.remove();
  const answer1 = assistantOf(turn1);
  const start1 = turn1.querySelector('[data-chatgpt-agent-turn-start]');
  answer1.remove();
  start1.remove();
  shot('user-turn');

  // 2. The answer streams: the unit exists without its final id; the composer shows Stop.
  setFinal(answer1, false);
  const container1 = turn1.querySelector('[data-content-search-turn-key]');
  container1.append(start1, answer1);
  streaming(true);
  shot('assistant-streaming');

  // 3. The stream ends: ChatGPT adds the final id, Stop disappears.
  setFinal(answer1, true);
  streaming(false);
  shot('assistant-complete');

  // 4. "Regenerate": a new answer message id under the same question.
  answer1.setAttribute('data-chatgpt-search-message-ids', `${REGENERATED_ID} ${REGENERATED_ID}`);
  answer1.querySelector('[data-chatgpt-selection-message-id]').setAttribute('data-chatgpt-selection-message-id', REGENERATED_ID);
  answer1.querySelector('[data-markdown-text-style] p').textContent = 'It counts the items, so len of three items is 3.';
  shot('regenerated');

  // 5. The learner edits the first question.
  turn1.querySelector('[data-user-message-bubble] .whitespace-pre-wrap').textContent = 'What does len() return for a list of lists?';
  shot('edited-user');

  // 6. A second question with an attached screenshot (metadata only; content not captured).
  const answer2 = assistantOf(turn2);
  const start2 = turn2.querySelector('[data-chatgpt-agent-turn-start]');
  answer2.remove();
  start2.remove();
  const bubble2 = turn2.querySelector('[data-user-message-bubble]');
  const file = doc.createElement('div');
  file.setAttribute('data-testid', 'file-thumbnail');
  file.setAttribute('data-file-name', 'screenshot-of-my-notes.png');
  bubble2.parentElement.insertBefore(file, bubble2);
  bubble2.querySelector('.whitespace-pre-wrap').textContent = 'What is the area of the circle in my screenshot?';
  thread.append(turn2);
  shot('attachment-user-turn');

  // 7-8. The answer streams with rendered math (KaTeX), then completes.
  setFinal(answer2, false);
  answer2.querySelector('[data-markdown-text-style]').innerHTML =
    '<p>The area is <span class="katex"><span class="katex-mathml"><math xmlns="http://www.w3.org/1998/Math/MathML">' +
    '<semantics><mrow><mi>π</mi><msup><mi>r</mi><mn>2</mn></msup></mrow>' +
    '<annotation encoding="application/x-tex">\\pi r^2</annotation></semantics></math></span>' +
    '<span class="katex-html" aria-hidden="true"><span class="mord">πr</span><span class="msupsub">2</span></span></span> for radius r.</p>';
  turn2.querySelector('[data-content-search-turn-key]').append(start2, answer2);
  streaming(true);
  shot('math-streaming');
  setFinal(answer2, true);
  streaming(false);
  shot('math-complete');

  // 9. A future ChatGPT release renames the unit markers: the page is unreadable (degraded).
  for (const unit of Array.from(doc.querySelectorAll('[data-chatgpt-search-unit-key]'))) {
    unit.removeAttribute('data-chatgpt-search-unit-key');
    unit.removeAttribute('data-content-search-unit-key');
  }
  shot('degraded');

  return {
    recorder: recorder.VERSION,
    recorded_on: '2026-09-26',
    path: '/c/<conversation>',
    source:
      'structure-from-live: thread-layout.html (chatgpt-2 thread layout of the live signed-in page, census ' +
      '2026-09-25), re-recorded frame by frame through scripts/record-chatgpt-structure.js (fixed salt)',
    note: 'Structure only: text synthetic (salted, not stored), ids pseudonymous, no link/media/cookie/storage data.',
    frames,
  };
}

export function serialize(recording) {
  return `${JSON.stringify(recording, null, 1)}\n`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const text = serialize(buildRecording());
  if (process.argv.includes('--check')) {
    const same = readFileSync(RECORDING_PATH, 'utf8').replace(/\r\n/g, '\n') === text;
    console.log(same ? 'recording.json is up to date' : 'recording.json differs: run node scripts/build-signed-in-recording.mjs');
    process.exit(same ? 0 : 1);
  }
  mkdirSync(dirname(RECORDING_PATH), { recursive: true });
  writeFileSync(RECORDING_PATH, text);
  console.log(`wrote ${RECORDING_PATH}`);
}

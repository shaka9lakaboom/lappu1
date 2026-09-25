/*
 * SkillMirror - signed-in ChatGPT STRUCTURE recorder (P9, ADR 0008 H10). Browser console snippet.
 *
 * What it records: the element tree of the open conversation (<main>, plus the composer when it
 * sits outside), with data-* attributes, class tokens, roles and a small allowlist of button
 * labels - the things the chatgpt-2 adapter reads. What it never records:
 *   - text: every word becomes a synthetic word chosen by a keyed hash whose key is random per
 *     recording and never written out (the same text stays the same across frames, and cannot be
 *     reversed); math annotations become synthetic TeX
 *   - ids: every UUID becomes a sequential pseudonym (consistent across frames); file names
 *     become file-N.<ext>
 *   - links, images, media: href / src / srcset / alt and <script> / <style> / <iframe> / media
 *     are dropped; no cookie, storage, network or page JavaScript state is ever read
 *
 * Use (DevTools console on a chatgpt.com conversation):
 *   1. paste this file, press Enter
 *   2. skillmirrorRecorder.start()     then chat with SYNTHETIC content (e.g. "Explain a for
 *                                      loop"): send, wait for the answer, regenerate, edit, ...
 *   3. copy(skillmirrorRecorder.stop()) copies the recording JSON; save it as
 *      apps/extension/tests/fixtures/chatgpt/signed-in/recording-live.json
 *   One frame only: copy(skillmirrorRecorder.snapshotJson())
 * Read the JSON before sharing it: it must contain no word you typed.
 */
(function install(global) {
  'use strict';

  var VERSION = 'structure-recorder/1';
  var MAX_FRAMES = 80;
  var UUID = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;
  var PSEUDO = /^00000000-0000-4000-8000-\d{12}$/;
  var DROP = {
    SCRIPT: 1, STYLE: 1, NOSCRIPT: 1, IFRAME: 1, LINK: 1, META: 1, TEMPLATE: 1, VIDEO: 1,
    AUDIO: 1, CANVAS: 1, PICTURE: 1, SOURCE: 1, OBJECT: 1, EMBED: 1, NAV: 1, ASIDE: 1,
  };
  var KEEP_ATTRIBUTE = /^(data-[a-z0-9_-]+|class|role|aria-label|aria-hidden|aria-live|hidden|type|dir|encoding|xmlns|id)$/;
  // Control labels the adapter relies on (Stop ... / Send ...) and other fixed UI labels. Any
  // other label (e.g. a conversation title) is replaced like text.
  var SAFE_LABEL = /^(Copy|Edit|Stop|Send|More|Regenerate|Retry|Try again|Read aloud|Good|Bad|Share|Start|Dictate|Expand|Collapse|Open|Close|Attach|Add|Scroll|Switch|Branch|Previous|Next|Remove|Search|Voice|Model|Upload|Cancel|Delete|Rename|Archive)\b[\w\s]{0,30}$/;
  var FILE_NAME_ATTRIBUTES = { 'data-file-name': 1, 'data-filename': 1, 'data-name': 1 };
  var WORDS = [
    'loop', 'value', 'list', 'item', 'count', 'table', 'query', 'number', 'string', 'index',
    'range', 'print', 'total', 'group', 'order', 'field', 'model', 'graph', 'point', 'line',
    'shape', 'unit', 'test', 'case', 'step', 'rule', 'word', 'text', 'form', 'data', 'node',
    'edge', 'path', 'key', 'map', 'set', 'row', 'column', 'sum', 'mean', 'rate', 'time',
  ];

  function hash(text) {
    // FNV-1a (32 bit): a stable pick, not a security primitive; the salt makes it irreversible.
    var h = 0x811c9dc5;
    for (var i = 0; i < text.length; i++) {
      h ^= text.charCodeAt(i);
      h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h;
  }

  function randomSalt() {
    var bytes = new Uint8Array(16);
    global.crypto.getRandomValues(bytes);
    return Array.prototype.map.call(bytes, function (b) { return b.toString(16).padStart(2, '0'); }).join('');
  }

  /** Per-recording state: the salt (never output) and the pseudonym maps. */
  function createState(options) {
    return { salt: (options && options.salt) || randomSalt(), ids: new Map(), files: new Map() };
  }

  function pseudoId(state, value) {
    return value.replace(UUID, function (id) {
      var key = id.toLowerCase();
      if (!state.ids.has(key)) state.ids.set(key, '00000000-0000-4000-8000-' + String(state.ids.size + 1).padStart(12, '0'));
      return state.ids.get(key);
    });
  }

  function syntheticWords(state, text) {
    // Whitespace is kept exactly (pre-wrap user text stays multi-line); each word is replaced.
    return text.replace(/[^\s]+/g, function (word) {
      return WORDS[hash(state.salt + '|' + word) % WORDS.length];
    });
  }

  function fileName(state, value) {
    var ext = /\.([a-z0-9]{1,5})$/i.exec(value);
    var key = value;
    if (!state.files.has(key)) state.files.set(key, 'file-' + (state.files.size + 1) + (ext ? '.' + ext[1].toLowerCase() : ''));
    return state.files.get(key);
  }

  function dataValue(state, value) {
    var pseudo = pseudoId(state, value);
    if (/\s/.test(pseudo)) {
      // Only a list of ids may keep its spaces (e.g. data-chatgpt-search-message-ids).
      var tokens = pseudo.trim().split(/\s+/);
      return tokens.every(function (t) { return PSEUDO.test(t); }) ? pseudo : syntheticWords(state, pseudo);
    }
    if (pseudo.length > 120 || /@/.test(pseudo) || !/^[A-Za-z0-9_:.\/#=-]*$/.test(pseudo)) return 'x';
    return pseudo;
  }

  function cleanElement(state, el) {
    for (var i = el.attributes.length - 1; i >= 0; i--) {
      var attr = el.attributes[i];
      var name = attr.name.toLowerCase();
      if (!KEEP_ATTRIBUTE.test(name)) {
        el.removeAttribute(attr.name);
        continue;
      }
      var value = attr.value;
      if (FILE_NAME_ATTRIBUTES[name]) value = fileName(state, value);
      else if (name === 'aria-label') value = SAFE_LABEL.test(value) ? value : syntheticWords(state, value);
      else if (name === 'id') value = /^[A-Za-z0-9_:.-]{1,64}$/.test(pseudoId(state, value)) ? pseudoId(state, value) : 'x';
      else if (name.indexOf('data-') === 0) value = dataValue(state, value);
      if (value !== attr.value) el.setAttribute(attr.name, value);
    }
  }

  function scrub(state, node) {
    var children = Array.prototype.slice.call(node.childNodes);
    for (var i = 0; i < children.length; i++) {
      var child = children[i];
      if (child.nodeType === 8) {
        node.removeChild(child); // comments
      } else if (child.nodeType === 3) {
        var tex = node.nodeType === 1 && node.tagName.toLowerCase() === 'annotation';
        if (child.nodeValue.trim()) child.nodeValue = tex ? 'x^{2} + ' + syntheticWords(state, 'y') : syntheticWords(state, child.nodeValue);
      } else if (child.nodeType === 1) {
        if (DROP[child.tagName.toUpperCase()]) {
          node.removeChild(child);
          continue;
        }
        cleanElement(state, child);
        if (child.tagName.toLowerCase() === 'svg') {
          while (child.firstChild) child.removeChild(child.firstChild); // icons: shape only
          continue;
        }
        scrub(state, child);
      }
    }
  }

  /** A sanitized copy of `root` (default: the conversation) as HTML - structure only. */
  function sanitize(root, state) {
    var doc = root.ownerDocument;
    var clone = root.cloneNode(true);
    cleanElement(state, clone);
    scrub(state, clone);
    var html = clone.outerHTML;
    // The composer's Stop button can live outside <main> on the live page: keep its form.
    var composer = doc.querySelector('[data-chatgpt-composer], form[data-type="unified-composer"], #composer-background');
    if (composer && !root.contains(composer)) {
      var extra = composer.cloneNode(true);
      cleanElement(state, extra);
      scrub(state, extra);
      html += '\n<!-- outside main -->' + extra.outerHTML;
    }
    return html;
  }

  /** Counts the adapter's markers (for a quick look before saving). */
  function summary(doc) {
    var count = function (selector) { return doc.querySelectorAll(selector).length; };
    var stops = Array.prototype.filter.call(doc.querySelectorAll('button[aria-label]'), function (b) {
      return /^stop\b/i.test(b.getAttribute('aria-label')) && !/dictat|voice|record|listen|speak|aloud|read/i.test(b.getAttribute('aria-label'));
    }).length;
    return {
      turns: count('[data-turn-key]'),
      units: count('[data-chatgpt-search-unit-key]'),
      final_answers: count('[data-chatgpt-selection-message-id]'),
      user_bubbles: count('[data-user-message-bubble]'),
      stop_buttons: stops,
      math: count('.katex annotation[encoding="application/x-tex"]'),
      files: count('[data-file-name], [data-testid*="file" i], [data-attachment]'),
    };
  }

  function conversationRoot(doc) {
    return doc.querySelector('main') || doc.body;
  }

  var session = null;

  function snapshot() {
    if (!session) throw new Error('call skillmirrorRecorder.start() first');
    var html = sanitize(session.root, session.state);
    var last = session.frames[session.frames.length - 1];
    if ((!last || last.html !== html) && session.frames.length < MAX_FRAMES) {
      session.frames.push({ t_ms: Math.round(global.performance.now() - session.started), html: html, summary: summary(session.root.ownerDocument) });
    }
    return session.frames.length;
  }

  function pathShape(state, pathname) {
    return pseudoId(state, pathname).replace(/\/c\/[A-Za-z0-9-]+/, function (p) { return PSEUDO.test(p.slice(3)) ? p : '/c/<conversation>'; });
  }

  function recording(extra) {
    return JSON.stringify(
      Object.assign(
        {
          recorder: VERSION,
          recorded_on: new Date().toISOString().slice(0, 10),
          path: pathShape(session.state, global.location ? global.location.pathname : '/'),
          note: 'Structure only: text synthetic (salted, not stored), ids pseudonymous, no link/media/cookie/storage data.',
          frames: session.frames,
        },
        extra || {},
      ),
      null,
      1,
    );
  }

  var api = {
    VERSION: VERSION,
    createState: createState,
    sanitize: sanitize,
    summary: summary,
    /** Starts a recording session: a frame now and after each burst of DOM changes. */
    start: function (options) {
      var doc = global.document;
      session = {
        root: conversationRoot(doc),
        state: createState(options),
        frames: [],
        started: global.performance.now(),
        timer: null,
        observer: null,
      };
      snapshot();
      var View = global.MutationObserver;
      session.observer = new View(function () {
        if (session.timer) global.clearTimeout(session.timer);
        session.timer = global.setTimeout(snapshot, (options && options.debounceMs) || 400);
      });
      session.observer.observe(doc.body, { subtree: true, childList: true, characterData: true, attributes: true });
      return 'recording: chat with synthetic content, then copy(skillmirrorRecorder.stop())';
    },
    snapshot: snapshot,
    /** Stops, takes a last frame and returns the recording JSON. */
    stop: function () {
      if (!session) throw new Error('not recording');
      if (session.observer) session.observer.disconnect();
      if (session.timer) global.clearTimeout(session.timer);
      snapshot();
      var out = recording();
      return out;
    },
    /** One sanitized frame of the current page, as recording JSON. */
    snapshotJson: function (options) {
      api.start(options);
      return api.stop();
    },
  };

  global.skillmirrorRecorder = api;
  if (!global.__SKILLMIRROR_RECORDER_TEST__ && global.console) {
    global.console.log('SkillMirror structure recorder ' + VERSION + ' ready: skillmirrorRecorder.start()');
  }
})(typeof window !== 'undefined' ? window : globalThis);

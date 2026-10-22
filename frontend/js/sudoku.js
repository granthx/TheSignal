import { h, clear, fmtClock, store, apiUrl } from './dom.js';

const DIFFS = ['easy', 'medium', 'hard'];
const prefs = store();

const row = (i) => Math.floor(i / 9);
const col = (i) => i % 9;
const box = (i) => Math.floor(row(i) / 3) * 3 + Math.floor(col(i) / 3);
const isPeer = (a, b) => a !== b && (row(a) === row(b) || col(a) === col(b) || box(a) === box(b));

export function initSudoku(root, hooks = {}) {
  let diff = prefs.get('signal.sudoku.diff') || 'medium';
  if (!DIFFS.includes(diff)) diff = 'medium';
  let kind = 'daily'; // 'daily' | 'random'
  let G = null; // current game

  /* ------------------------------------------------------------ static UI */
  const diffBtns = DIFFS.map((d) => h('button', { type: 'button', 'data-d': d, onclick: () => { diff = d; prefs.set('signal.sudoku.diff', d); load(); } }, d.toUpperCase()));
  const kindBtns = ['daily', 'random'].map((k) => h('button', { type: 'button', 'data-k': k, onclick: () => { kind = k; load(); } }, k === 'daily' ? 'TODAY\'S' : 'RANDOM'));
  const boardEl = h('div', { class: 'board', role: 'grid', 'aria-label': 'Sudoku board' });
  const cellEls = Array.from({ length: 81 }, (_, i) => {
    const el = h('button', {
      type: 'button', role: 'gridcell', class: `cell${col(i) % 3 === 2 && col(i) < 8 ? ' bc' : ''}${row(i) % 3 === 2 && row(i) < 8 ? ' br' : ''}`,
      onclick: () => select(i), 'data-i': i,
    });
    boardEl.append(el);
    return el;
  });
  const timerEl = h('div', { class: 'timer', 'aria-live': 'off' });
  const subEl = h('small');
  const msgEl = h('div', { class: 'bubble flip', role: 'status', 'aria-live': 'polite' });
  const padBtns = Array.from({ length: 9 }, (_, k) => {
    const d = k + 1;
    return h('button', { type: 'button', 'aria-label': `Enter ${d}`, onclick: () => input(d) }, String(d), h('small', { 'aria-hidden': 'true' }));
  });
  const notesBtn = h('button', { class: 'btn ghost sm', type: 'button', 'aria-pressed': 'false', onclick: () => toggleNotes() }, 'NOTES: OFF');
  const tools = h('div', { class: 'tools' },
    notesBtn,
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => erase() }, 'ERASE'),
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => undo() }, 'UNDO'),
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => hint() }, 'HINT'),
    h('button', { class: 'btn ghost sm', type: 'button', onclick: () => check() }, 'CHECK'),
    h('button', { class: 'btn sm', type: 'button', onclick: () => restart() }, 'RESTART'),
  );
  const wrap = h('div', { class: 'board-wrap' }, boardEl);

  root.classList.add('sudoku');
  clear(root).append(
    wrap,
    h('div', { class: 'side' },
      h('div', { class: 'row' }, h('div', { class: 'seg', role: 'group', 'aria-label': 'Difficulty' }, diffBtns), h('div', { class: 'seg', role: 'group', 'aria-label': 'Puzzle type' }, kindBtns)),
      h('div', {}, timerEl, subEl),
      h('div', { class: 'pad', role: 'group', 'aria-label': 'Number pad' }, padBtns),
      tools,
      msgEl,
      h('p', { class: 'tip' }, 'Keys: ', h('kbd', { text: '1-9' }), ' fill · ', h('kbd', { text: '⌫' }), ' erase · ', h('kbd', { text: 'N' }), ' notes · ', h('kbd', { text: 'Ctrl+Z' }), ' undo · arrows move.'),
    ),
  );

  /* ------------------------------------------------------------ game state */
  const say = (text, bad = false) => { msgEl.textContent = text; msgEl.style.borderColor = bad ? 'var(--red)' : ''; };

  function snapshot() { return G.cells.map((c) => ({ v: c.v, n: [...c.notes], hint: c.hint })); }
  function pushUndo() { G.undo.push(snapshot()); if (G.undo.length > 300) G.undo.shift(); }
  function restore(snap) { snap.forEach((s, i) => { G.cells[i].v = s.v; G.cells[i].notes = new Set(s.n); G.cells[i].hint = s.hint; G.cells[i].err = false; }); }

  function storageKey() { return G.meta.daily ? `signal.sudoku.${G.meta.date}.${G.meta.difficulty}` : null; }
  function persist() {
    const key = storageKey();
    if (!key) return;
    prefs.set(key, { cells: G.cells.map((c) => ({ v: c.v, n: [...c.notes], hint: c.hint })), elapsed: G.elapsed, hints: G.hints, done: G.done });
  }

  function newGame(meta) {
    stopTimer();
    G = {
      meta, sol: [...meta.solution].map(Number),
      cells: [...meta.puzzle].map((ch) => ({ v: Number(ch), given: ch !== '0', notes: new Set(), err: false, hint: false })),
      sel: null, notes: false, undo: [], elapsed: 0, hints: 0, done: false, last: performance.now(),
    };
    const saved = G.meta.daily ? prefs.get(storageKey()) : null;
    if (saved && Array.isArray(saved.cells) && saved.cells.length === 81) {
      saved.cells.forEach((s, i) => { if (!G.cells[i].given) { G.cells[i].v = Number(s.v) || 0; G.cells[i].notes = new Set(s.n || []); G.cells[i].hint = !!s.hint; } });
      G.elapsed = Number(saved.elapsed) || 0; G.hints = Number(saved.hints) || 0; G.done = !!saved.done;
    }
    G.sel = G.cells.findIndex((c) => !c.given && c.v === 0);
    if (G.sel < 0) G.sel = 0;
    notesBtn.setAttribute('aria-pressed', 'false'); notesBtn.textContent = 'NOTES: OFF';
    wrap.querySelector('.win-banner')?.remove();
    boardEl.classList.toggle('done', G.done);
    render();
    if (G.done) showWin(true); else { say(G.meta.daily ? 'Today\'s puzzle is the same for everyone. Good luck.' : 'Fresh random puzzle. Good luck.'); startTimer(); }
  }

  /* ------------------------------------------------------------ rules */
  function conflicts() {
    const bad = new Set();
    for (let i = 0; i < 81; i++) {
      const v = G.cells[i].v;
      if (!v) continue;
      for (let j = i + 1; j < 81; j++) if (G.cells[j].v === v && isPeer(i, j)) { bad.add(i); bad.add(j); }
    }
    return bad;
  }
  const filled = () => G.cells.every((c) => c.v);
  const solved = () => G.cells.every((c, i) => c.v === G.sol[i]);

  /* ------------------------------------------------------------ actions */
  function select(i) { if (!G) return; G.sel = i; render(); cellEls[i].focus({ preventScroll: true }); }

  function input(d) {
    if (!G || G.done || G.sel == null) return;
    const c = G.cells[G.sel];
    if (c.given) { say('That one is part of the puzzle.'); return; }
    pushUndo();
    if (G.notes && !c.v) {
      c.notes.has(d) ? c.notes.delete(d) : c.notes.add(d);
    } else {
      c.v = c.v === d ? 0 : d;
      c.notes.clear(); c.err = false; c.hint = false;
      if (c.v) G.cells.forEach((o, j) => { if (isPeer(G.sel, j)) o.notes.delete(d); });
    }
    afterChange();
  }

  function erase() {
    if (!G || G.done || G.sel == null) return;
    const c = G.cells[G.sel];
    if (c.given || (!c.v && !c.notes.size)) return;
    pushUndo(); c.v = 0; c.notes.clear(); c.err = false; c.hint = false;
    afterChange();
  }

  function undo() {
    if (!G || G.done || !G.undo.length) return;
    restore(G.undo.pop());
    afterChange(true);
  }

  function toggleNotes() {
    if (!G) return;
    G.notes = !G.notes;
    notesBtn.setAttribute('aria-pressed', String(G.notes));
    notesBtn.textContent = `NOTES: ${G.notes ? 'ON' : 'OFF'}`;
  }

  function hint() {
    if (!G || G.done) return;
    let i = G.sel;
    const wrong = (k) => !G.cells[k].given && G.cells[k].v !== G.sol[k];
    if (i == null || !wrong(i)) {
      const candidates = G.cells.map((_, k) => k).filter(wrong);
      if (!candidates.length) return;
      i = candidates[Math.floor(Math.random() * candidates.length)];
    }
    pushUndo();
    const c = G.cells[i];
    c.v = G.sol[i]; c.notes.clear(); c.err = false; c.hint = true; G.hints++; G.sel = i;
    G.cells.forEach((o, j) => { if (isPeer(i, j)) o.notes.delete(c.v); });
    say(`Hint used (${G.hints} so far). Cell revealed.`);
    afterChange(false, true);
  }

  function check() {
    if (!G || G.done) return;
    let wrong = 0;
    G.cells.forEach((c) => { c.err = !c.given && c.v !== 0 && c.v !== G.sol[G.cells.indexOf(c)]; if (c.err) wrong++; });
    say(wrong ? `${wrong} cell${wrong > 1 ? 's are' : ' is'} off. They are marked in red.` : 'Everything filled in so far is correct.', wrong > 0);
    render(); persist();
  }

  function restart() {
    if (!G) return;
    if (!G.done && (G.cells.some((c) => !c.given && (c.v || c.notes.size))) && !confirm('Clear the board and start this puzzle over?')) return;
    const key = storageKey(); if (key) prefs.set(key, null);
    newGame(G.meta);
  }

  function afterChange(quiet = false, keepMsg = false) {
    const bad = conflicts();
    if (!quiet && !keepMsg) say(bad.size ? 'Two of the same number share a row, column or box.' : '', bad.size > 0);
    if (filled()) {
      if (solved()) { win(); return; }
      say('The board is full but something is wrong. Press CHECK to find it.', true);
    }
    render(); persist();
  }

  function win() {
    G.done = true; stopTimer(); G.cells.forEach((c) => { c.err = false; });
    boardEl.classList.add('done');
    render(); persist(); showWin(false);
    hooks.confetti?.({ particleCount: 160, spread: 80, origin: { y: 0.65 } });
    setTimeout(() => hooks.confetti?.({ particleCount: 90, angle: 60, spread: 60, origin: { x: 0, y: 0.7 } }), 250);
    setTimeout(() => hooks.confetti?.({ particleCount: 90, angle: 120, spread: 60, origin: { x: 1, y: 0.7 } }), 400);
  }

  function showWin(restored) {
    wrap.querySelector('.win-banner')?.remove();
    wrap.append(h('div', { class: 'win-banner', 'aria-hidden': 'true' }, 'SOLVED!', h('small', { text: `${fmtClock(G.elapsed)} · ${G.hints} hint${G.hints === 1 ? '' : 's'}` })));
    say(`${restored ? 'You already solved this one' : 'Solved'} in ${fmtClock(G.elapsed)}${G.hints ? ` with ${G.hints} hint${G.hints === 1 ? '' : 's'}` : ' with no hints'}. ${G.meta.daily ? 'Come back tomorrow for a new one, or try RANDOM.' : 'Nice.'}`);
  }

  /* ------------------------------------------------------------ timer */
  let tick = null;
  function startTimer() {
    stopTimer();
    if (G.done) return;
    G.last = performance.now();
    tick = setInterval(() => {
      const now = performance.now();
      if (!document.hidden) G.elapsed += (now - G.last) / 1000;
      G.last = now;
      drawTimer();
      if (Math.floor(G.elapsed) % 10 === 0) persist();
    }, 1000);
  }
  function stopTimer() { if (tick) clearInterval(tick); tick = null; }
  function drawTimer() {
    timerEl.textContent = fmtClock(G.elapsed);
    timerEl.append(subEl);
  }

  /* ------------------------------------------------------------ drawing */
  function render() {
    const bad = conflicts();
    const sel = G.sel != null ? G.cells[G.sel] : null;
    G.cells.forEach((c, i) => {
      const el = cellEls[i];
      const cls = ['cell'];
      if (col(i) % 3 === 2 && col(i) < 8) cls.push('bc');
      if (row(i) % 3 === 2 && row(i) < 8) cls.push('br');
      if (c.given) cls.push('given');
      if (G.sel === i) cls.push('sel');
      else if (G.sel != null && isPeer(G.sel, i)) cls.push('peer');
      if (sel && sel.v && c.v === sel.v && G.sel !== i) cls.push('same');
      if (bad.has(i) && !c.given) cls.push('dup');
      if (c.err) cls.push('err');
      if (c.hint) cls.push('hint');
      el.className = cls.join(' ');
      el.tabIndex = G.sel === i ? 0 : -1;
      el.setAttribute('aria-label', `Row ${row(i) + 1} column ${col(i) + 1}, ${c.v ? `value ${c.v}${c.given ? ', given' : ''}` : c.notes.size ? `notes ${[...c.notes].sort().join(' ')}` : 'empty'}`);
      el.setAttribute('aria-selected', G.sel === i ? 'true' : 'false');
      clear(el);
      if (c.v) el.textContent = String(c.v);
      else if (c.notes.size) el.append(h('span', { class: 'notes', 'aria-hidden': 'true' }, Array.from({ length: 9 }, (_, k) => h('span', { text: c.notes.has(k + 1) ? String(k + 1) : '' }))));
    });
    const counts = Array(10).fill(0);
    G.cells.forEach((c) => { if (c.v) counts[c.v]++; });
    padBtns.forEach((b, k) => {
      const left = 9 - counts[k + 1];
      b.disabled = G.done || left <= 0;
      b.querySelector('small').textContent = left > 0 ? String(left) : '';
    });
    diffBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.d === diff)));
    kindBtns.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.k === kind)));
    subEl.textContent = G.meta.daily ? `${G.meta.date} · ${G.meta.clues} clues` : `random · ${G.meta.clues} clues`;
    drawTimer();
  }

  /* ------------------------------------------------------------ keyboard */
  boardEl.addEventListener('keydown', (e) => {
    if (!G) return;
    const moves = { ArrowUp: -9, ArrowDown: 9, ArrowLeft: -1, ArrowRight: 1 };
    if (e.key in moves) {
      e.preventDefault();
      const i = G.sel ?? 0;
      if ((e.key === 'ArrowLeft' && col(i) === 0) || (e.key === 'ArrowRight' && col(i) === 8)) return;
      const next = i + moves[e.key];
      if (next >= 0 && next < 81) select(next);
    } else if (/^[1-9]$/.test(e.key) && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); input(Number(e.key)); }
    else if (['Backspace', 'Delete', '0'].includes(e.key)) { e.preventDefault(); erase(); }
    else if (e.key.toLowerCase() === 'n' && !e.ctrlKey && !e.metaKey) { e.preventDefault(); toggleNotes(); }
    else if (e.key.toLowerCase() === 'z' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); undo(); }
  });

  document.addEventListener('visibilitychange', () => { if (G && document.hidden) persist(); });
  addEventListener('pagehide', () => { if (G) persist(); });

  /* ------------------------------------------------------------ loading */
  async function load() {
    say('Setting up the grid…');
    diffBtns.concat(kindBtns).forEach((b) => { b.disabled = true; });
    try {
      const res = await fetch(apiUrl(`/api/sudoku?difficulty=${diff}${kind === 'random' ? '&mode=random' : ''}`));
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      newGame(await res.json());
    } catch {
      say('Could not load a puzzle. Is the server running?', true);
    } finally {
      diffBtns.concat(kindBtns).forEach((b) => { b.disabled = false; });
    }
  }

  load();
}

import { h, clear, store } from './dom.js';

const prefs = store();

const SPEEDS = {
  easy: 125,
  medium: 90,
  hard: 60,
};

// Minimalist Audio Synthesizer (Bamboo snap on bite, woodblock thud on collision)
class SnakeAudio {
  constructor() {
    this.ctx = null;
    this.enabled = true;
  }

  init() {
    if (!this.ctx && (window.AudioContext || window.webkitAudioContext)) {
      const AudioCtx = window.AudioContext || window.webkitAudioContext;
      this.ctx = new AudioCtx();
    }
  }

  playBite() {
    if (!this.enabled) return;
    this.init();
    if (!this.ctx) return;
    try {
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'triangle';
      osc.frequency.setValueAtTime(680, now);
      osc.frequency.exponentialRampToValueAtTime(140, now + 0.045);
      gain.gain.setValueAtTime(0.18, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.045);
      osc.connect(gain);
      gain.connect(this.ctx.destination);
      osc.start(now);
      osc.stop(now + 0.045);
    } catch { /* audio blocked */ }
  }

  playImpact() {
    if (!this.enabled) return;
    this.init();
    if (!this.ctx) return;
    try {
      const now = this.ctx.currentTime;
      const osc = this.ctx.createOscillator();
      const gain = this.ctx.createGain();
      osc.type = 'sine';
      osc.frequency.setValueAtTime(160, now);
      osc.frequency.exponentialRampToValueAtTime(45, now + 0.22);
      gain.gain.setValueAtTime(0.22, now);
      gain.gain.exponentialRampToValueAtTime(0.001, now + 0.22);
      osc.connect(gain);
      gain.connect(this.ctx.destination);
      osc.start(now);
      osc.stop(now + 0.22);
    } catch { /* audio blocked */ }
  }
}

export function initSnake(root, hooks = {}) {
  const sound = new SnakeAudio();
  const STORAGE_KEY = 'signal.snake.best';
  let bestScore = Number(prefs.get(STORAGE_KEY) || 0);

  let speedKey = prefs.get('signal.snake.speed') || 'medium';
  if (!SPEEDS[speedKey]) speedKey = 'medium';

  const GRID_SIZE = 20; // 20x20 cells
  const BASE_SIZE = 520; // 520x520 internal resolution
  const CELL_SIZE = BASE_SIZE / GRID_SIZE; // 26px

  let snake = [];
  let dir = { x: 1, y: 0 };
  let nextDir = { x: 1, y: 0 };
  let apple = { x: 14, y: 10 };
  let applesEaten = 0;
  let score = 0;
  let state = 'ready'; // 'ready' | 'slithering' | 'paused' | 'over'
  let gameLoopTimer = null;
  let animFrameId = null;

  // Visual FX
  let impactBursts = []; // Radial speed-line flashes and comic text on eat
  let motionMarks = [];  // Transient ink speed dashes behind slithering tail
  let shakeFrames = 0;   // Screen impact tremor

  /* ------------------------------------------------------------ DOM Elements */
  // Left: Board
  const canvas = h('canvas', { width: BASE_SIZE, height: BASE_SIZE, class: 'snake-canvas' });
  const ctx = canvas.getContext('2d');
  const overlayEl = h('div', { class: 'snake-board-overlay', id: 'snake-board-overlay' });
  const boardWrap = h('div', { class: 'board-wrap snake-board-wrap' }, canvas, overlayEl);

  // Right: Side Details (matching Sudoku 1-to-1)
  const speedBtns = ['easy', 'medium', 'hard'].map((s) =>
    h('button', {
      type: 'button',
      'aria-pressed': speedKey === s ? 'true' : 'false',
      'data-speed': s,
      onclick: () => {
        speedKey = s;
        prefs.set('signal.snake.speed', s);
        speedBtns.forEach((b) => b.setAttribute('aria-pressed', b.dataset.speed === s ? 'true' : 'false'));
        if (state === 'slithering') {
          clearInterval(gameLoopTimer);
          runLoop();
        }
      },
    }, s.toUpperCase())
  );

  const speedSeg = h('div', { class: 'seg', role: 'group', 'aria-label': 'Speed' }, speedBtns);

  const scoreDisplay = h('div', { class: 'timer snake-timer' },
    h('span', { id: 'snake-score-num', text: '000' }),
    h('small', { id: 'snake-best-num', text: `BEST: ${String(bestScore).padStart(3, '0')}` }),
  );

  // Stat boxes row (Apples, Length, Status)
  const applesCountEl = h('div', { class: 'snake-stat-card' },
    h('span', { class: 'stat-card-num', text: '0' }),
    h('span', { class: 'stat-card-label', text: 'APPLES' }),
  );
  const lengthCountEl = h('div', { class: 'snake-stat-card' },
    h('span', { class: 'stat-card-num', text: '3' }),
    h('span', { class: 'stat-card-label', text: 'LENGTH' }),
  );
  const statusCountEl = h('div', { class: 'snake-stat-card status' },
    h('span', { class: 'stat-card-num status-text', text: 'READY' }),
    h('span', { class: 'stat-card-label', text: 'STATUS' }),
  );
  const statsRow = h('div', { class: 'snake-stats-strip' }, applesCountEl, lengthCountEl, statusCountEl);

  // Action Buttons
  const playBtn = h('button', {
    class: 'btn sm',
    type: 'button',
    onclick: () => togglePlay(),
  }, 'START');

  const restartBtn = h('button', {
    class: 'btn ghost sm',
    type: 'button',
    onclick: () => restartGame(),
  }, 'RESTART');

  const soundBtn = h('button', {
    class: 'btn ghost sm',

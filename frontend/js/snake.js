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
    type: 'button',
    onclick: () => {
      sound.enabled = !sound.enabled;
      soundBtn.textContent = sound.enabled ? 'SOUND: ON' : 'SOUND: OFF';
    },
  }, 'SOUND: ON');

  const toolsRow = h('div', { class: 'tools' }, playBtn, restartBtn, soundBtn);

  // Comic Rule Speech Bubble
  const ruleBubble = h('div', { class: 'bubble flip', role: 'status' },
    h('span', { class: 'who', text: 'RULES' }),
    'EAT. GROW. SURVIVE. Hunt crimson apples to grow. Don’t hit the ink borders or bite your own tail!',
  );

  // Key hints
  const tipEl = h('p', { class: 'tip' },
    'Keys: ',
    h('kbd', { text: '↑' }), ' ',
    h('kbd', { text: '↓' }), ' ',
    h('kbd', { text: '←' }), ' ',
    h('kbd', { text: '→' }), ' or ',
    h('kbd', { text: 'W' }), ' ',
    h('kbd', { text: 'A' }), ' ',
    h('kbd', { text: 'S' }), ' ',
    h('kbd', { text: 'D' }), ' slither · ',
    h('kbd', { text: 'Space' }), ' pause · ',
    h('kbd', { text: 'R' }), ' restart.',
  );

  // Discreet D-Pad for touch screens
  const dpadUp = h('button', { class: 'dpad-btn up', type: 'button', 'aria-label': 'Up', onclick: () => handleDir(0, -1) }, '▲');
  const dpadLeft = h('button', { class: 'dpad-btn left', type: 'button', 'aria-label': 'Left', onclick: () => handleDir(-1, 0) }, '◀');
  const dpadRight = h('button', { class: 'dpad-btn right', type: 'button', 'aria-label': 'Right', onclick: () => handleDir(1, 0) }, '▶');
  const dpadDown = h('button', { class: 'dpad-btn down', type: 'button', 'aria-label': 'Down', onclick: () => handleDir(0, 1) }, '▼');

  const dpad = h('div', { class: 'snake-dpad' },
    h('div', { class: 'dpad-row center' }, dpadUp),
    h('div', { class: 'dpad-row' }, dpadLeft, dpadRight),
    h('div', { class: 'dpad-row center' }, dpadDown),
  );

  // Assemble Side Panel (Right side)
  const sidePanel = h('div', { class: 'side snake-side' },
    h('div', { class: 'row' }, speedSeg),
    scoreDisplay,
    statsRow,
    toolsRow,
    ruleBubble,
    tipEl,
    dpad,
  );

  // Main 2-column layout (matching Sudoku: left is board, right is details)
  const layout = h('div', { class: 'sudoku snake-two-col-layout' }, boardWrap, sidePanel);

  root.classList.add('snake-game-wrapper');
  clear(root).append(layout);

  /* ------------------------------------------------------------ Retina / HiDPI Scaling */
  function resizeCanvas() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = BASE_SIZE * dpr;
    canvas.height = BASE_SIZE * dpr;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.scale(dpr, dpr);
  }
  resizeCanvas();

  /* ------------------------------------------------------------ Overlays (100% English) */
  function showStartOverlay() {
    overlayEl.hidden = false;
    clear(overlayEl).append(
      h('div', { class: 'manga-card-panel' },
        h('div', { class: 'panel-stamp-box' },
          h('span', { class: 'stamp-main', text: 'CATCH THE APPLE' }),
          h('span', { class: 'stamp-sub', text: 'NO. 001 / ARCADE DEPT.' }),
        ),
        h('p', { class: 'panel-prose' },
          'An ink serpent slithers across printed newsprint.',
          h('br'),
          'Hunt the crimson apple. Never strike the paper borders.',
        ),
        h('div', { class: 'panel-action-block' },
          h('button', {
            class: 'btn',
            type: 'button',
            onclick: () => startGame(),
          }, 'START GAME [ SPACE ]'),
          h('span', { class: 'panel-shortcut-tip', text: 'Press Arrow Keys or WASD to Slither' }),
        ),
      )
    );
  }

  function showPauseOverlay() {
    overlayEl.hidden = false;
    clear(overlayEl).append(
      h('div', { class: 'manga-card-panel pause' },
        h('div', { class: 'panel-stamp-box' },
          h('span', { class: 'stamp-main', text: 'GAME PAUSED' }),
          h('span', { class: 'stamp-sub', text: 'TAKE A BREATH' }),
        ),
        h('p', { class: 'panel-prose', text: 'Press SPACE or click below to resume slithering.' }),
        h('div', { class: 'panel-action-block' },
          h('button', {
            class: 'btn',
            type: 'button',
            onclick: () => resumeGame(),
          }, 'RESUME [ SPACE ]'),
        ),
      )
    );
  }

  function showGameOverOverlay(isHigh) {
    overlayEl.hidden = false;
    clear(overlayEl).append(
      h('div', { class: 'manga-card-panel gameover' },
        h('div', { class: 'gameover-stamp' },
          h('span', { class: 'stamp-red-seal', text: 'END' }),
          h('div', { class: 'gameover-title-wrap' },
            h('span', { class: 'gameover-head', text: isHigh ? 'NEW RECORD!' : 'GAME OVER' }),
            h('span', { class: 'gameover-sub', text: 'The serpent struck the paper frame.' }),
          ),
        ),
        h('div', { class: 'editorial-breakdown' },
          h('div', { class: 'breakdown-row' },
            h('span', { class: 'row-label', text: 'FINAL SCORE' }),
            h('span', { class: 'row-num', text: String(score).padStart(3, '0') }),
          ),
          h('div', { class: 'breakdown-row' },
            h('span', { class: 'row-label', text: 'APPLES COLLECTED' }),
            h('span', { class: 'row-num', text: String(applesEaten) }),
          ),
          h('div', { class: 'breakdown-row' },
            h('span', { class: 'row-label', text: 'SERPENT LENGTH' }),
            h('span', { class: 'row-num', text: String(snake.length) }),
          ),
          h('div', { class: 'breakdown-row highlight' },
            h('span', { class: 'row-label', text: 'RECORD BEST' }),
            h('span', { class: 'row-num', text: String(bestScore).padStart(3, '0') }),
          ),
        ),
        h('div', { class: 'panel-action-block' },
          h('button', {
            class: 'btn',
            type: 'button',
            onclick: () => startGame(),
          }, 'PLAY AGAIN [ SPACE ]'),
          h('span', { class: 'panel-shortcut-tip', text: 'Press [ R ] or Spacebar to Slither Again' }),
        ),
      )
    );

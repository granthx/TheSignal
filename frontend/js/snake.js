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
  }

  /* ------------------------------------------------------------ Game State Logic */
  function resetSnake() {
    snake = [
      { x: 9, y: 10 },
      { x: 8, y: 10 },
      { x: 7, y: 10 },
    ];
    dir = { x: 1, y: 0 };
    nextDir = { x: 1, y: 0 };
    applesEaten = 0;
    score = 0;
    impactBursts = [];
    motionMarks = [];
    shakeFrames = 0;
    updateDisplay();
    spawnApple();
  }

  function spawnApple() {
    const occupied = new Set(snake.map((s) => `${s.x},${s.y}`));
    const freeCells = [];
    for (let x = 1; x < GRID_SIZE - 1; x++) {
      for (let y = 1; y < GRID_SIZE - 1; y++) {
        if (!occupied.has(`${x},${y}`)) freeCells.push({ x, y });
      }
    }
    if (freeCells.length) {
      apple = freeCells[Math.floor(Math.random() * freeCells.length)];
    }
  }

  function handleDir(dx, dy) {
    if (state === 'ready' || state === 'over') {
      startGame();
    }
    // Prevent immediate 180-degree self reversal
    if (dx !== 0 && dir.x !== 0) return;
    if (dy !== 0 && dir.y !== 0) return;
    nextDir = { x: dx, y: dy };
  }

  function togglePlay() {
    if (state === 'ready' || state === 'over') {
      startGame();
    } else if (state === 'slithering') {
      pauseGame();
    } else if (state === 'paused') {
      resumeGame();
    }
  }

  function startGame() {
    resetSnake();
    state = 'slithering';
    overlayEl.hidden = true;
    updateStatusText('SLITHERING');
    playBtn.textContent = 'PAUSE';
    runLoop();
  }

  function pauseGame() {
    state = 'paused';
    clearInterval(gameLoopTimer);
    updateStatusText('PAUSED');
    playBtn.textContent = 'RESUME';
    showPauseOverlay();
  }

  function resumeGame() {
    state = 'slithering';
    overlayEl.hidden = true;
    updateStatusText('SLITHERING');
    playBtn.textContent = 'PAUSE';
    runLoop();
  }

  function restartGame() {
    clearInterval(gameLoopTimer);
    startGame();
  }

  function gameOver() {
    state = 'over';
    clearInterval(gameLoopTimer);
    shakeFrames = 8;
    sound.playImpact();

    const isHigh = score > bestScore;
    if (isHigh) {
      bestScore = score;
      prefs.set(STORAGE_KEY, score);
      if (hooks.confetti) hooks.confetti({ particleCount: 50, spread: 70, origin: { y: 0.6 } });
    }

    updateDisplay();
    updateStatusText('GAME OVER');
    playBtn.textContent = 'START';
    showGameOverOverlay(isHigh);
  }

  function updateStatusText(txt) {
    const statusEl = statusCountEl.querySelector('.stat-card-num');
    if (statusEl) statusEl.textContent = txt;
  }

  function updateDisplay() {
    const scoreEl = scoreDisplay.querySelector('#snake-score-num');
    const bestEl = scoreDisplay.querySelector('#snake-best-num');
    const applesEl = applesCountEl.querySelector('.stat-card-num');
    const lengthEl = lengthCountEl.querySelector('.stat-card-num');

    if (scoreEl) scoreEl.textContent = String(score).padStart(3, '0');
    if (bestEl) bestEl.textContent = `BEST: ${String(bestScore).padStart(3, '0')}`;
    if (applesEl) applesEl.textContent = String(applesEaten);
    if (lengthEl) lengthEl.textContent = String(snake.length);
  }

  /* ------------------------------------------------------------ Visual Impact Effects (100% English) */
  function triggerAppleImpact(gridX, gridY) {
    const cx = gridX * CELL_SIZE + CELL_SIZE / 2;
    const cy = gridY * CELL_SIZE + CELL_SIZE / 2;

    impactBursts.push({
      x: cx,
      y: cy,
      life: 1.0,
      decay: 0.055,
      lineCount: 12,
      maxRadius: 28,
      text: '+10',
    });

    shakeFrames = 3;
  }

  function addMotionMark(gridX, gridY, moveDir) {
    const cx = gridX * CELL_SIZE + CELL_SIZE / 2;
    const cy = gridY * CELL_SIZE + CELL_SIZE / 2;

    motionMarks.push({
      x: cx,
      y: cy,
      dir: { ...moveDir },
      life: 1.0,
      decay: 0.065,
    });
  }

  /* ------------------------------------------------------------ Game Loop Tick */
  function tick() {
    dir = { ...nextDir };
    const head = { x: snake[0].x + dir.x, y: snake[0].y + dir.y };

    // Wall collision
    if (head.x < 0 || head.x >= GRID_SIZE || head.y < 0 || head.y >= GRID_SIZE) {
      gameOver();
      return;
    }

    // Self collision
    if (snake.some((s) => s.x === head.x && s.y === head.y)) {
      gameOver();
      return;
    }

    snake.unshift(head);

    // Apple collision
    let grew = false;
    if (head.x === apple.x && head.y === apple.y) {
      applesEaten++;
      score += 10;
      grew = true;
      sound.playBite();
      triggerAppleImpact(head.x, head.y);
      spawnApple();
      updateDisplay();
    }

    if (!grew) {
      const vacated = snake.pop();
      addMotionMark(vacated.x, vacated.y, dir);
    } else {
      const tail = snake[snake.length - 1];
      addMotionMark(tail.x, tail.y, dir);
    }
  }

  function runLoop() {
    clearInterval(gameLoopTimer);
    gameLoopTimer = setInterval(tick, SPEEDS[speedKey] || 90);
  }

  /* ------------------------------------------------------------ 60FPS Renderer */
  function draw() {
    const isDark = document.documentElement.dataset.theme === 'ink';

    // Palette Tokens
    const paperBg = isDark ? '#0f0f12' : '#f6f3ea';
    const inkSolid = isDark ? '#f1ede1' : '#0b0b0c';
    const inkFaint = isDark ? 'rgba(241, 237, 225, 0.06)' : 'rgba(11, 11, 12, 0.06)';
    const screentoneDot = isDark ? 'rgba(241, 237, 225, 0.09)' : 'rgba(11, 11, 12, 0.08)';
    const appleRed = isDark ? '#ff3b3b' : '#c8141b'; // Sole vivid crimson accent

    ctx.save();

    // 0. Screen shake on impact
    if (shakeFrames > 0) {
      const ox = (Math.random() - 0.5) * 3;
      const oy = (Math.random() - 0.5) * 3;
      ctx.translate(ox, oy);
      shakeFrames--;
    }

    // 1. Newsprint Ground
    ctx.fillStyle = paperBg;
    ctx.fillRect(0, 0, BASE_SIZE, BASE_SIZE);

    // 2. Screentone Dot Matrix
    ctx.fillStyle = screentoneDot;
    const dotSpacing = 13;
    for (let x = 6; x < BASE_SIZE; x += dotSpacing) {
      for (let y = 6; y < BASE_SIZE; y += dotSpacing) {
        ctx.beginPath();
        ctx.arc(x, y, 0.75, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    // 3. Editorial Grid Lines
    ctx.strokeStyle = inkFaint;
    ctx.lineWidth = 0.6;
    for (let i = 1; i < GRID_SIZE; i++) {
      const pos = i * CELL_SIZE;
      ctx.beginPath();
      ctx.moveTo(pos, 0);
      ctx.lineTo(pos, BASE_SIZE);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(0, pos);
      ctx.lineTo(BASE_SIZE, pos);
      ctx.stroke();
    }

    // 4. Registration Crosshairs (+) in corners
    ctx.strokeStyle = isDark ? 'rgba(241,237,225,0.25)' : 'rgba(11,11,12,0.25)';
    ctx.lineWidth = 1;
    const drawCross = (cx, cy) => {
      ctx.beginPath();
      ctx.moveTo(cx - 5, cy);
      ctx.lineTo(cx + 5, cy);
      ctx.moveTo(cx, cy - 5);
      ctx.lineTo(cx, cy + 5);
      ctx.stroke();
    };
    drawCross(12, 12);
    drawCross(BASE_SIZE - 12, 12);
    drawCross(12, BASE_SIZE - 12);
    drawCross(BASE_SIZE - 12, BASE_SIZE - 12);

    // 5. Double-Line Manga Panel Frame
    ctx.strokeStyle = inkSolid;
    ctx.lineWidth = 2.5;
    ctx.strokeRect(1.5, 1.5, BASE_SIZE - 3, BASE_SIZE - 3);

    ctx.strokeStyle = isDark ? 'rgba(241,237,225,0.45)' : 'rgba(11,11,12,0.45)';
    ctx.lineWidth = 0.8;
    ctx.strokeRect(5.5, 5.5, BASE_SIZE - 11, BASE_SIZE - 11);

    // 6. Motion Speed Marks behind tail
    for (let i = motionMarks.length - 1; i >= 0; i--) {
      const m = motionMarks[i];
      m.life -= m.decay;
      if (m.life <= 0) {
        motionMarks.splice(i, 1);
        continue;
      }

      ctx.save();
      ctx.strokeStyle = inkSolid;
      ctx.globalAlpha = Math.max(0, m.life * 0.45);
      ctx.lineWidth = 1.2;

      if (m.dir.x !== 0) {
        ctx.beginPath();
        ctx.moveTo(m.x - 7, m.y - 3);
        ctx.lineTo(m.x + 7, m.y - 3);
        ctx.moveTo(m.x - 4, m.y + 3);
        ctx.lineTo(m.x + 6, m.y + 3);
        ctx.stroke();
      } else {
        ctx.beginPath();
        ctx.moveTo(m.x - 3, m.y - 7);
        ctx.lineTo(m.x - 3, m.y + 7);
        ctx.moveTo(m.x + 3, m.y - 4);
        ctx.lineTo(m.x + 3, m.y + 6);
        ctx.stroke();
      }
      ctx.restore();
    }

    // 7. Render VIVID CRIMSON APPLE
    renderMangaApple(ctx, apple.x, apple.y, CELL_SIZE, appleRed, inkSolid, paperBg);

    // 8. Render THE INK SNAKE
    renderMangaSnake(ctx, snake, dir, CELL_SIZE, inkSolid, paperBg);

    // 9. Render Impact Bursts
    for (let i = impactBursts.length - 1; i >= 0; i--) {
      const b = impactBursts[i];
      b.life -= b.decay;
      if (b.life <= 0) {
        impactBursts.splice(i, 1);
        continue;
      }

      ctx.save();
      ctx.globalAlpha = Math.max(0, b.life);

      const radius = b.maxRadius * (1 - b.life * 0.2);
      ctx.strokeStyle = inkSolid;
      ctx.lineWidth = 1.5;
      for (let j = 0; j < b.lineCount; j++) {
        const angle = (j / b.lineCount) * Math.PI * 2;
        const r1 = radius * 0.35;
        const r2 = radius;
        ctx.beginPath();

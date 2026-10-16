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

/**
 * End-of-time sound cues of the stimulus card.
 *
 * The built-in cues are synthesized with WebAudio, so the card ships no audio
 * files. A custom cue is fetched and decoded while the card prepares, so it
 * plays without delay when the time is up. Browsers - iPad Safari above all -
 * only allow audio after a user gesture; `installSoundCueUnlock()` therefore
 * resumes the audio context on the first press anywhere on the page.
 *
 * Nothing here holds participant data. The audio context is the device, and
 * the decoded custom files are dropped at every session boundary like all
 * other card state.
 */
import { onSessionReset } from '/static/scripts/cards/session-state.js';

export const BUILT_IN_CUES = Object.freeze(['gong', 'bell', 'beep']);

// Partials as [frequency ratio, relative gain, decay seconds]. The gong uses
// inharmonic ratios, the bell near-harmonic ones; both stay short and soft.
const CUE_SHAPES = Object.freeze({
  gong: { base: 196, attack: 0.02, partials: [[1, 1, 2.8], [2.0, 0.5, 2.0], [2.76, 0.35, 1.4], [5.4, 0.2, 0.8]] },
  bell: { base: 880, attack: 0.005, partials: [[1, 1, 1.6], [2.0, 0.45, 1.1], [3.0, 0.25, 0.7], [4.2, 0.15, 0.5]] },
  beep: { base: 1000, attack: 0.01, partials: [[1, 1, 0.25]], hold: 0.2 },
});
const SILENT_GAIN = 0.0001;

export function createSoundCuePlayer({
  AudioContextClass = globalThis.AudioContext || globalThis.webkitAudioContext,
  fetchImpl = globalThis.fetch?.bind(globalThis),
} = {}) {
  let context = null;
  const customCues = new Map();

  function audioContext() {
    if (context || typeof AudioContextClass !== 'function') return context;
    try {
      context = new AudioContextClass();
    } catch (error) {
      console.warn('[stimulus] Audio is unavailable:', error);
      context = null;
    }
    return context;
  }

  async function resume() {
    const ctx = audioContext();
    if (ctx?.state === 'suspended' && typeof ctx.resume === 'function') {
      try { await ctx.resume(); } catch { /* a later gesture can still unlock */ }
    }
    return ctx;
  }

  function unlock() {
    const ctx = audioContext();
    if (!ctx) return;
    void resume();
    // One silent sample marks the context as user-activated on iOS.
    try {
      const buffer = ctx.createBuffer(1, 1, ctx.sampleRate || 44100);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start(0);
    } catch { /* unlocking is best effort */ }
  }

  function preload(question) {
    const url = customUrl(question);
    if (!url) return Promise.resolve(null);
    if (!customCues.has(url)) {
      customCues.set(url, decodeCustomCue(url).catch((error) => {
        console.warn('[stimulus] Custom end sound could not be loaded:', error);
        customCues.delete(url);
        return null;
      }));
    }
    return customCues.get(url);
  }

  async function decodeCustomCue(url) {
    const ctx = audioContext();
    if (!ctx || typeof fetchImpl !== 'function') return null;
    const response = await fetchImpl(url);
    if (!response?.ok) throw new Error(`HTTP ${response?.status ?? 'error'} for ${url}`);
    const bytes = await response.arrayBuffer();
    return ctx.decodeAudioData(bytes);
  }

  /**
   * Play the cue the card configures. Resolves to the cue that actually
   * sounded: `none`, `unavailable`, a built-in name, or `custom`. A custom
   * file that cannot be loaded falls back to the gong and reports why.
   */
  async function play(question, { onFallback } = {}) {
    const cue = String(question?.end_sound || 'none');
    if (cue === 'none') return 'none';
    const ctx = await resume();
    if (!ctx) return 'unavailable';
    const volume = clampVolume(question?.end_sound_volume);
    if (cue === 'custom') {
      const buffer = await preload(question);
      if (buffer) {
        playBuffer(ctx, buffer, volume);
        return 'custom';
      }
      onFallback?.(customUrl(question) || '');
      synthesize(ctx, 'gong', volume);
      return 'gong';
    }
    synthesize(ctx, BUILT_IN_CUES.includes(cue) ? cue : 'gong', volume);
    return BUILT_IN_CUES.includes(cue) ? cue : 'gong';
  }

  function forgetCustomCues() {
    customCues.clear();
  }

  return { unlock, preload, play, forgetCustomCues };
}

function customUrl(question) {
  if (String(question?.end_sound || '') !== 'custom') return '';
  return String(question?.end_sound_url || '').trim();
}

function clampVolume(value) {
  const number = Number(value ?? 80);
  if (!Number.isFinite(number)) return 0.8;
  return Math.min(100, Math.max(0, number)) / 100;
}

function playBuffer(ctx, buffer, volume) {
  const source = ctx.createBufferSource();
  const gain = ctx.createGain();
  source.buffer = buffer;
  gain.gain.value = volume;
  source.connect(gain);
  gain.connect(ctx.destination);
  source.start(ctx.currentTime);
}

function synthesize(ctx, cue, volume) {
  const shape = CUE_SHAPES[cue] || CUE_SHAPES.gong;
  const start = ctx.currentTime;
  const hold = shape.hold || 0;
  shape.partials.forEach(([ratio, relativeGain, decay]) => {
    const oscillator = ctx.createOscillator();
    const gain = ctx.createGain();
    const peak = Math.max(SILENT_GAIN, volume * relativeGain * 0.5);
    oscillator.type = 'sine';
    oscillator.frequency.setValueAtTime(shape.base * ratio, start);
    gain.gain.setValueAtTime(SILENT_GAIN, start);
    gain.gain.linearRampToValueAtTime(peak, start + shape.attack);
    if (hold) gain.gain.setValueAtTime(peak, start + shape.attack + hold);
    gain.gain.exponentialRampToValueAtTime(SILENT_GAIN, start + shape.attack + hold + decay);
    oscillator.connect(gain);
    gain.connect(ctx.destination);
    oscillator.start(start);
    oscillator.stop(start + shape.attack + hold + decay + 0.05);
  });
}

const sharedPlayer = createSoundCuePlayer();
onSessionReset(() => sharedPlayer.forgetCustomCues());

// Marks a page whose first press already unlocks audio.
const UNLOCK_INSTALLED = Symbol('stimulusSoundCueUnlock');

/** Resume audio on the first press anywhere; safe to call repeatedly. */
export function installSoundCueUnlock(target = globalThis.document) {
  if (!target?.addEventListener || target[UNLOCK_INSTALLED]) return;
  target[UNLOCK_INSTALLED] = true;
  const unlockOnce = () => {
    sharedPlayer.unlock();
    target.removeEventListener('pointerdown', unlockOnce, true);
    target.removeEventListener('keydown', unlockOnce, true);
  };
  target.addEventListener('pointerdown', unlockOnce, true);
  target.addEventListener('keydown', unlockOnce, true);
}

export const preloadSoundCue = (question) => sharedPlayer.preload(question);
export const playSoundCue = (question, options) => sharedPlayer.play(question, options);

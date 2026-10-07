// The stimulus card's end sound (its own module) and the manifest rule that
// decides which plugins a card can start and stop.
import assert from 'node:assert/strict';
import test from 'node:test';

import { importCardModule } from './card-test-support.mjs';
import {
  configurePluginCatalog,
  isStimulusActuator,
  stimulusActuatorPlugins,
} from '../../study_runner/apps/ui/scripts/shared/plugin-catalog.js';

const { createSoundCuePlayer, installSoundCueUnlock } = await importCardModule('stimulus', 'sound-cues.js');

class FakeParam {
  constructor() { this.events = []; }
  setValueAtTime(value, time) { this.events.push(['set', value, time]); }
  linearRampToValueAtTime(value, time) { this.events.push(['linear', value, time]); }
  exponentialRampToValueAtTime(value, time) { this.events.push(['exponential', value, time]); }
}

class FakeAudioContext {
  constructor() {
    this.state = 'suspended';
    this.currentTime = 0;
    this.sampleRate = 44100;
    this.destination = {};
    this.oscillators = [];
    this.gains = [];
    this.sources = [];
    this.resumed = 0;
    FakeAudioContext.instances.push(this);
  }
  resume() { this.resumed += 1; this.state = 'running'; return Promise.resolve(); }
  createOscillator() {
    const oscillator = { type: '', frequency: new FakeParam(), connect() {}, start() { this.started = true; }, stop() {} };
    this.oscillators.push(oscillator);
    return oscillator;
  }
  createGain() {
    const gain = { gain: new FakeParam(), connect() {} };
    this.gains.push(gain);
    return gain;
  }
  createBuffer() { return {}; }
  createBufferSource() {
    const source = { connect() {}, start() { this.started = true; } };
    this.sources.push(source);
    return source;
  }
  decodeAudioData(bytes) { return Promise.resolve({ decoded: bytes }); }
}
FakeAudioContext.instances = [];

const peak = (context) => Math.max(...context.gains.flatMap((gain) => gain.gain.events
  .filter(([kind]) => kind === 'linear')
  .map(([, value]) => value)));

test('no sound means no audio at all', async () => {
  FakeAudioContext.instances = [];
  const player = createSoundCuePlayer({ AudioContextClass: FakeAudioContext });
  assert.equal(await player.play({ end_sound: 'none' }), 'none');
  assert.equal(FakeAudioContext.instances.length, 0);
});

test('a built-in cue is synthesized after resuming the audio context', async () => {
  FakeAudioContext.instances = [];
  const player = createSoundCuePlayer({ AudioContextClass: FakeAudioContext });
  assert.equal(await player.play({ end_sound: 'bell', end_sound_volume: 80 }), 'bell');
  const [context] = FakeAudioContext.instances;
  assert.equal(context.resumed, 1);
  assert.equal(context.oscillators.length, 4);
  assert.equal(context.oscillators[0].frequency.events[0][1], 880);
});

test('the volume scales the cue', async () => {
  const loud = createSoundCuePlayer({ AudioContextClass: FakeAudioContext });
  const quiet = createSoundCuePlayer({ AudioContextClass: FakeAudioContext });
  FakeAudioContext.instances = [];
  await loud.play({ end_sound: 'beep', end_sound_volume: 100 });
  await quiet.play({ end_sound: 'beep', end_sound_volume: 50 });
  const [loudContext, quietContext] = FakeAudioContext.instances;
  assert.ok(Math.abs(peak(loudContext) / peak(quietContext) - 2) < 1e-9);
});

test('a custom file is fetched once, decoded ahead and played', async () => {
  FakeAudioContext.instances = [];
  const fetched = [];
  const player = createSoundCuePlayer({
    AudioContextClass: FakeAudioContext,
    fetchImpl: async (url) => {
      fetched.push(url);
      return { ok: true, arrayBuffer: async () => new ArrayBuffer(4) };
    },
  });
  const question = { end_sound: 'custom', end_sound_url: 'https://lab/gong.mp3' };
  await player.preload(question);
  assert.equal(await player.play(question), 'custom');
  assert.equal(await player.play(question), 'custom');
  assert.deepEqual(fetched, ['https://lab/gong.mp3']);
  assert.equal(FakeAudioContext.instances[0].sources.filter((source) => source.started).length, 2);

  player.forgetCustomCues();
  await player.play(question);
  assert.equal(fetched.length, 2, 'a new session fetches the file again');
});

test('a custom file that cannot be loaded falls back to the gong and says why', async () => {
  const fallbacks = [];
  const player = createSoundCuePlayer({
    AudioContextClass: FakeAudioContext,
    fetchImpl: async () => ({ ok: false, status: 404 }),
  });
  const result = await player.play(
    { end_sound: 'custom', end_sound_url: 'https://lab/missing.mp3' },
    { onFallback: (url) => fallbacks.push(url) },
  );
  assert.equal(result, 'gong');
  assert.deepEqual(fallbacks, ['https://lab/missing.mp3']);
});

test('the audio unlock listens to real gestures and stays installed', () => {
  // On iPad Safari a finger's pointerdown is no user gesture for audio;
  // touchend/pointerup/click are. The listeners stay so iOS can be unlocked
  // again after it suspends audio (screen lock).
  const listeners = new Map();
  const target = {
    addEventListener: (type, handler, options) => listeners.set(type, { handler, options }),
    removeEventListener: (type) => listeners.delete(type),
  };
  installSoundCueUnlock(target);
  installSoundCueUnlock(target);
  assert.deepEqual([...listeners.keys()].sort(), ['click', 'keydown', 'pointerup', 'touchend']);
  listeners.get('touchend').handler();
  listeners.get('click').handler();
  assert.equal(listeners.size, 4);
  assert.equal(listeners.get('touchend').options.capture, true);
});

test('a cue the browser never allows reports blocked instead of hanging', async () => {
  class NeverResumingContext extends FakeAudioContext {
    resume() { this.resumed += 1; return new Promise(() => {}); }
  }
  const blocked = [];
  const player = createSoundCuePlayer({ AudioContextClass: NeverResumingContext });
  const started = Date.now();
  const result = await player.play({ end_sound: 'gong' }, { onBlocked: () => blocked.push('blocked') });
  assert.equal(result, 'blocked');
  assert.deepEqual(blocked, ['blocked']);
  assert.ok(Date.now() - started < 2000);
  assert.equal(FakeAudioContext.instances.at(-1).oscillators.length, 0);
});

test('stimulus cards list exactly the plugins whose manifest declares start and stop', () => {
  configurePluginCatalog({
    plugins: [
      { plugin_key: 'lamp', status: 'valid', capabilities: [], runtime: { trial_events: ['start', 'stop'] } },
      { plugin_key: 'eeg', status: 'valid', capabilities: ['recording_source'], runtime: { trial_events: ['session_end'] } },
      { plugin_key: 'half', status: 'valid', capabilities: [], runtime: { trial_events: ['start'] } },
      { plugin_key: 'card', status: 'valid', capabilities: ['card_contract'] },
    ],
  });
  assert.deepEqual(stimulusActuatorPlugins().map((plugin) => plugin.plugin_key), ['lamp']);
  assert.equal(isStimulusActuator(null), false);
});

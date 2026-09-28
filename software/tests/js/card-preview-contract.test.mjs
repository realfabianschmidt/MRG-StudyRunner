// Every card runs in two places: on the participant page and as a live
// preview next to the study editor. Both mount it the same way
// (cards/card-mount.js); these tests hold every shipped card -- and any card
// added later -- to that, and check the shared animation loop.
import assert from 'node:assert/strict';
import test from 'node:test';

import { loadShippedCards, snapshot } from './card-test-support.mjs';
import { mountCard, dispatchCardHook } from '../../study_runner/apps/ui/scripts/cards/card-mount.js';
import { runAnimation, stopAllAnimations } from '../../study_runner/apps/ui/scripts/cards/card-motion.js';
import { resetAllCardState } from '../../study_runner/apps/ui/scripts/cards/session-state.js';

const CARDS = await loadShippedCards();

// Just enough of an element for render + bind: no card may assume more of
// the page than its own element when it is mounted.
function stubElement() {
  const listeners = [];
  return {
    innerHTML: '',
    dataset: {},
    style: { setProperty() {} },
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (...args) => listeners.push(args),
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0 }),
    isConnected: false,
    listeners,
  };
}

test('every shipped card mounts in preview mode without touching the page', () => {
  const types = Object.keys(CARDS);
  assert.ok(types.length > 5);
  for (const type of types) {
    const defaults = Object.values(snapshot.defaults).find((entry) => entry?.[type])?.[type];
    assert.ok(defaults, `defaults for ${type}`);
    const element = stubElement();
    const cardModule = mountCard(element, { ...defaults, type }, 0, { mode: 'preview' });
    assert.equal(cardModule, CARDS[type], type);
    assert.ok(element.innerHTML.length > 0, `${type} rendered`);
  }
  resetAllCardState();
});

test('mountCard hands the mode to bindInteractions', () => {
  const seen = [];
  const original = CARDS['mood-meter'];
  const fake = { ...original, bindInteractions: (element, index, options) => seen.push(options.mode) };
  CARDS['mood-meter'] = fake;
  try {
    mountCard(stubElement(), { ...snapshot.defaults.mood_meter['mood-meter'] }, 0, { mode: 'preview' });
    mountCard(stubElement(), { ...snapshot.defaults.mood_meter['mood-meter'] }, 0);
  } finally {
    CARDS['mood-meter'] = original;
  }
  assert.deepEqual(seen, ['preview', 'study']);
});

test('dispatchCardHook reaches every card module except skipped ones', () => {
  const reached = [];
  const modules = [...new Set(Object.values(CARDS))];
  const fakes = modules.map((cardModule) => ({ ...cardModule, onClick: () => reached.push(cardModule) }));
  const skip = fakes.slice(0, 1);
  for (const [type, cardModule] of Object.entries(CARDS)) {
    CARDS[type] = fakes[modules.indexOf(cardModule)];
  }
  try {
    dispatchCardHook('onClick', { target: null }, { skip });
  } finally {
    for (const [type, cardModule] of Object.entries(CARDS)) {
      CARDS[type] = modules[fakes.indexOf(cardModule)];
    }
  }
  assert.equal(reached.length, modules.length - 1);
  assert.ok(!reached.includes(modules[0]));
});

test('an animation ends once its element is gone and on stopAllAnimations', async () => {
  const frames = [];
  globalThis.requestAnimationFrame = (callback) => { frames.push(callback); return frames.length; };
  globalThis.cancelAnimationFrame = () => {};
  const element = { isConnected: true, dataset: {} };
  let ticks = 0;
  runAnimation(element, () => { ticks += 1; return true; });
  frames.shift()?.(16);
  frames.shift()?.(32);
  assert.equal(ticks, 2);
  element.isConnected = false;
  frames.shift()?.(48);
  assert.equal(ticks, 2, 'no tick for a removed element');
  assert.equal(frames.length, 0, 'and no further frame');

  const other = { isConnected: true, dataset: {} };
  runAnimation(other, () => { ticks += 1; return true; });
  const root = { querySelectorAll: () => [other] };
  stopAllAnimations(root);
  assert.equal(other.dataset.cardAnimated, undefined);
});

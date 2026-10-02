// The Mood Meter's views place every word on the energy x pleasantness plane
// and record either the words or words + position. Pure logic only: the
// drawing and gestures need a browser and are checked by hand.
import assert from 'node:assert/strict';
import test from 'node:test';

import { loadShippedCards, snapshot } from './card-test-support.mjs';
import { cardState, resetAllCardState } from '../../study_runner/apps/ui/scripts/cards/session-state.js';
import {
  blobPath,
  nearestWords,
  orbitPlace,
  quadrantAt,
  shapeAt,
  wordCoordinates,
  wordLists,
} from '../../study_runner/plugins/cards/mood_meter/mood-core.js';

const CARDS = await loadShippedCards();
const moodMeter = CARDS['mood-meter'];
const defaults = snapshot.defaults.mood_meter['mood-meter'];
const places = new Map(wordCoordinates(wordLists(null, defaults)).map((entry) => [entry.word, entry]));

test('default words sit where the original 10 x 10 Mood Meter grid puts them', () => {
  const enraged = places.get('Enraged');
  assert.equal(enraged.quadrant, 'red');
  assert.ok(enraged.energy > 0.9 && enraged.pleasantness < 0.1, 'Enraged is the top-left corner');
  const surprised = places.get('Surprised');
  assert.ok(surprised.energy > 0.9 && surprised.pleasantness > 0.5 && surprised.pleasantness < 0.6);
  const lastGreen = defaults.word_lists.green.at(-1);
  assert.ok(places.get(lastGreen).energy < 0.1 && places.get(lastGreen).pleasantness > 0.9, 'green ends bottom right');
  assert.equal(places.size, 100);
});

test('quadrants, nearest words and shapes follow the plane', () => {
  assert.equal(quadrantAt(0.2, 0.8), 'red');
  assert.equal(quadrantAt(0.8, 0.8), 'yellow');
  assert.equal(quadrantAt(0.8, 0.2), 'green');
  assert.equal(quadrantAt(0.2, 0.2), 'blue');
  assert.equal(nearestWords([...places.values()], 0, 1, 1)[0].word, 'Enraged');
  assert.ok(shapeAt(0.05, 0.95).spikes > 0.9, 'tense corner is jagged');
  assert.ok(shapeAt(0.95, 0.95).bloom > 0.9, 'elated corner flowers');
  assert.ok(shapeAt(0.05, 0.05).droop > 0.9, 'low corner hangs');
  const calm = shapeAt(0.9, 0.1);
  assert.equal(calm.spikes + calm.bloom + calm.droop, 0, 'calm is round');
  assert.match(blobPath(50, 50, 20, calm, 0, 12), /^M[\d. L-]+Z$/);
});

test('the orbit puts intense words outside and neutral ones inside', () => {
  const outer = orbitPlace(places.get('Enraged'));
  const inner = orbitPlace({ pleasantness: 0.45, energy: 0.55 });
  assert.ok(outer.radius > inner.radius);
  assert.ok(outer.x < 0 && outer.y < 0, 'red is top left on the wheel');
});

function questionWith(variant, index) {
  const state = cardState('mood-meter', index, () => ({ selected: new Set(), position: null, question: null }));
  state.question = { ...defaults, variant };
  return state;
}

test('classic and blobs answer with the words', () => {
  for (const variant of ['classic', 'blobs']) {
    const state = questionWith(variant, 7);
    state.selected.add('Calm');
    assert.deepEqual(moodMeter.collectAnswer(7), ['Calm']);
    resetAllCardState();
  }
});

test('field and orbit need the position and answer with it', () => {
  const field = questionWith('field', 8);
  field.selected.add('Calm');
  assert.equal(moodMeter.collectAnswer(8), null, 'no position yet');
  assert.equal(moodMeter.isAnswered({}, 8), false);
  field.position = { pleasantness: 0.8, energy: 0.2 };
  assert.deepEqual(moodMeter.collectAnswer(8), { words: ['Calm'], pleasantness: 0.8, energy: 0.2 });

  const orbitState = questionWith('orbit', 9);
  orbitState.selected.add('Tense');
  orbitState.position = { pleasantness: 0.1, energy: 0.9, intensity: 0.9 };
  assert.deepEqual(moodMeter.collectAnswer(9), { words: ['Tense'], pleasantness: 0.1, energy: 0.9, intensity: 0.9 });
});

test('a session reset forgets the words and the position', () => {
  const state = questionWith('field', 10);
  state.selected.add('Calm');
  state.position = { pleasantness: 0.8, energy: 0.2 };
  resetAllCardState();
  assert.equal(moodMeter.collectAnswer(10), null);
});

test('the editor offers four views and keeps the chosen one', () => {
  const html = moodMeter.renderEditor({ ...defaults, variant: 'orbit' });
  assert.equal((html.match(/class="mm-ed-variant-input"/g) || []).length, 4);
  assert.match(html, /value="orbit" checked/);
  assert.match(html, /mm-ed-help/);
  // The word lists sit where their quadrant sits on the card.
  assert.deepEqual([...html.matchAll(/data-quadrant="(\w+)"/g)].map((match) => match[1]), ['red', 'yellow', 'blue', 'green']);
});

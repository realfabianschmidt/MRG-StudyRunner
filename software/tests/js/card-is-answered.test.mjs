// Package 5g.B2: isAnswered() moved from 13 branches in study-controller.js
// into each card module's own optional export. This is the safety net that
// move needed and did not have: nothing previously tested this logic at
// all, and it gates whether a real participant can advance past a question.
//
// No jsdom dependency exists in this project (tests/js has none), so DOM
// access here is a minimal hand-rolled stub - just enough querySelector/
// querySelectorAll surface for each card's isAnswered to exercise, not a
// real DOM. That is deliberately in keeping with this test directory's
// existing style (pure-logic imports, no browser environment).
import assert from 'node:assert/strict';
import test from 'node:test';

import * as slider from '../../study_runner/apps/ui/scripts/cards/card-slider.js';
import * as multiSlider from '../../study_runner/apps/ui/scripts/cards/card-multi-slider.js';
import * as ranking from '../../study_runner/apps/ui/scripts/cards/card-ranking.js';
import * as choice from '../../study_runner/apps/ui/scripts/cards/card-choice.js';
import * as likert from '../../study_runner/apps/ui/scripts/cards/card-likert.js';
import * as semantic from '../../study_runner/apps/ui/scripts/cards/card-semantic.js';
import * as participantId from '../../study_runner/apps/ui/scripts/cards/card-participant-id.js';
import * as stimulus from '../../study_runner/apps/ui/scripts/cards/card-stimulus.js';
import * as finish from '../../study_runner/apps/ui/scripts/cards/card-finish.js';
import { CARDS } from '../../study_runner/apps/ui/scripts/cards/index.js';

// A cardElement stub that answers exactly one querySelector/querySelectorAll
// call the way a real DOM element would for a fixed "checked" count.
function elementWithChecked(count) {
  return {
    querySelector: () => (count > 0 ? {} : null),
    querySelectorAll: () => Array.from({ length: count }),
  };
}

test('slider: touching one field is enough, the default 50 is not answered on its own', () => {
  const question = { type: 'slider' };
  assert.equal(slider.isAnswered(question, 0, { touchedFieldCount: 0 }), false);
  assert.equal(slider.isAnswered(question, 0, { touchedFieldCount: 1 }), true);
});

test('multi-slider: needs every dimension touched, not just one', () => {
  const question = { type: 'multi-slider', dimensions: [{ label: 'a' }, { label: 'b' }] };
  assert.equal(multiSlider.isAnswered(question, 0, { touchedFieldCount: 1 }), false);
  assert.equal(multiSlider.isAnswered(question, 0, { touchedFieldCount: 2 }), true);
});

test('multi-slider: zero configured dimensions counts as answered', () => {
  assert.equal(multiSlider.isAnswered({ type: 'multi-slider' }, 0, { touchedFieldCount: 0 }), true);
});

test('ranking: the initial order is not itself an answer', () => {
  const question = { type: 'ranking' };
  assert.equal(ranking.isAnswered(question, 0, { touchedFieldCount: 0 }), false);
  assert.equal(ranking.isAnswered(question, 0, { touchedFieldCount: 1 }), true);
});

test('choice: checked means a checkbox, not a radio', () => {
  const question = { type: 'choice' };
  assert.equal(choice.isAnswered(question, 0, { cardElement: elementWithChecked(0) }), false);
  assert.equal(choice.isAnswered(question, 0, { cardElement: elementWithChecked(1) }), true);
});

test('single (choice module, aliased): checked means a radio', () => {
  const question = { type: 'single' };
  assert.equal(choice.isAnswered(question, 0, { cardElement: elementWithChecked(0) }), false);
  assert.equal(choice.isAnswered(question, 0, { cardElement: elementWithChecked(1) }), true);
});

test('likert: a separate module from choice, same radio check', () => {
  const question = { type: 'likert' };
  assert.equal(likert.isAnswered(question, 0, { cardElement: elementWithChecked(0) }), false);
  assert.equal(likert.isAnswered(question, 0, { cardElement: elementWithChecked(1) }), true);
});

test('semantic: needs one checked radio per configured pair', () => {
  const question = { type: 'semantic', pairs: [['a', 'b'], ['c', 'd']] };
  assert.equal(semantic.isAnswered(question, 0, { cardElement: elementWithChecked(1) }), false);
  assert.equal(semantic.isAnswered(question, 0, { cardElement: elementWithChecked(2) }), true);
});

test('participant-id: unanswered until a computed id exists', () => {
  // collectAnswer() reads module-private state set by onInput; with nothing
  // ever typed, the module has no id yet.
  assert.equal(participantId.isAnswered(), false);
});

test('stimulus and finish export no isAnswered hook: the controller default applies', () => {
  assert.equal(typeof stimulus.isAnswered, 'undefined');
  assert.equal(typeof finish.isAnswered, 'undefined');
});

test('every answerable registered type has an isAnswered hook; stimulus/finish deliberately do not', () => {
  const withoutHook = Object.entries(CARDS)
    .filter(([, cardModule]) => typeof cardModule.isAnswered !== 'function')
    .map(([type]) => type);
  assert.deepEqual(new Set(withoutHook), new Set(['stimulus', 'finish']));
});

test('ranking and word-cloud are the two types with a bindInteractions hook', () => {
  const withHook = Object.entries(CARDS)
    .filter(([, cardModule]) => typeof cardModule.bindInteractions === 'function')
    .map(([type]) => type);
  assert.deepEqual(new Set(withHook), new Set(['ranking', 'word-cloud']));
});

// No answer may survive from one participant session into the next. Cards
// keep non-DOM state only in cards/session-state.js, which the participant
// page clears at every session boundary; these tests prove the shipped
// stateful cards really read from that store, so a reset empties them.
import assert from 'node:assert/strict';
import test from 'node:test';

import { loadShippedCards } from './card-test-support.mjs';
import { cardState, onSessionReset, resetAllCardState } from '../../study_runner/apps/ui/scripts/cards/session-state.js';

const CARDS = await loadShippedCards();

test('cardState is per card and per question, and a reset empties it', () => {
  cardState('example', 0).value = 'first participant';
  assert.equal(cardState('example', 0).value, 'first participant');
  assert.equal(cardState('example', 1).value, undefined);
  resetAllCardState();
  assert.equal(cardState('example', 0).value, undefined);
});

test('reset handlers run on every reset and a failing one does not stop the rest', () => {
  let calls = 0;
  onSessionReset(() => { throw new Error('boom'); });
  onSessionReset(() => { calls += 1; });
  resetAllCardState();
  resetAllCardState();
  assert.equal(calls, 2);
});

test('mood meter: a selection is gone after the session reset', () => {
  const moodMeter = CARDS['mood-meter'];
  cardState('mood-meter', 3, () => ({ selected: new Set(), question: null })).selected.add('Calm');
  assert.deepEqual(moodMeter.collectAnswer(3), ['Calm']);
  assert.equal(moodMeter.isAnswered({}, 3), true);
  resetAllCardState();
  assert.equal(moodMeter.collectAnswer(3), null);
  assert.equal(moodMeter.isAnswered({}, 3), false);
});

test('word cloud: a selection is gone after the session reset', () => {
  const wordCloud = CARDS['word-cloud'];
  cardState('word-cloud', 2, () => ({ selected: new Set() })).selected.add('Joy');
  assert.deepEqual(wordCloud.collectAnswer(2), ['Joy']);
  resetAllCardState();
  assert.equal(wordCloud.isAnswered({}, 2), false);
});

test('participant ID: the previous code and metadata are gone after the session reset', () => {
  const participantId = CARDS['participant-id'];
  const state = cardState('participant-id', 'current');
  state.id = 'abc123';
  state.metadata = { childhood_area: 'city' };
  assert.equal(participantId.collectAnswer(), 'abc123');
  assert.deepEqual(participantId.collectMetadata(), { childhood_area: 'city' });
  resetAllCardState();
  assert.equal(participantId.collectAnswer(), null);
  assert.equal(participantId.isAnswered(), false);
  assert.deepEqual(participantId.collectMetadata(), {});
});

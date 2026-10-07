import assert from 'node:assert/strict';
import test from 'node:test';
import { createParticipantNavigation } from '../../study_runner/apps/ui/scripts/participant/participant-navigation.js';

test('finish navigation stays local when the server is unavailable', async () => {
  const state = {
    config: { questions: [{ type: 'likert' }, { type: 'finish' }] },
    currentIndex: 0,
    activeStimulus: { signalStarted: true },
    submitInFlight: true,
    navigationBusy: false,
  };
  const calls = [];
  const cards = new Map([0, 1].map((index) => [
    `card-q-${index}`,
    { classList: { remove: () => calls.push('remove') } },
  ]));
  const goTo = createParticipantNavigation({
    state,
    getElement: (key) => cards.get(key),
    updateNavigation: () => {},
    stopActiveStimulus: async ({ shouldSendStop }) => {
      assert.equal(shouldSendStop, false);
      throw new Error('server offline');
    },
    recordQuestionCompletion: () => { throw new Error('unexpected completion'); },
    commitCheckpoint: () => { throw new Error('unexpected checkpoint'); },
    clearCardAnimationClasses: () => {},
    playCardEntrance: () => calls.push('finish shown'),
    markQuestionShown: () => {},
    saveSessionSnapshot: () => { throw new Error('unexpected snapshot'); },
    startStimulusCard: () => {},
  });

  await goTo(1, { force: true, localOnly: true, lockNavigation: false });
  assert.equal(state.currentIndex, 1);
  assert.ok(calls.includes('finish shown'));
});

export function createParticipantNavigation({
  state, getElement, updateNavigation, stopActiveStimulus,
  recordQuestionCompletion, commitCheckpoint, clearCardAnimationClasses,
  playCardEntrance, markQuestionShown, saveSessionSnapshot, startStimulusCard,
}) {
  return async function goTo(targetIndex, options = {}) {
    const total = (state.config.questions || []).length;
    if (targetIndex < 0 || targetIndex >= total) return;
    const lockNavigation = options.lockNavigation !== false;
    const force = options.force === true;
    const localOnly = options.localOnly === true;
    if ((state.navigationBusy || state.submitInFlight) && !force) return;

    if (lockNavigation) {
      state.navigationBusy = true;
      updateNavigation();
    }

    try {
      const shouldSendStop = !localOnly && Boolean(state.activeStimulus?.signalStarted);
      try {
        await stopActiveStimulus({ shouldSendStop });
      } catch (error) {
        if (!localOnly) throw error;
        console.error('[study] Stimulus stop could not be confirmed:', error);
      }

      const currentCard = getElement(`card-q-${state.currentIndex}`);
      const targetCard = getElement(`card-q-${targetIndex}`);
      if (!currentCard || !targetCard) return;

      if (!localOnly) {
        await recordQuestionCompletion(state.currentIndex);
        await commitCheckpoint({ nextIndex: targetIndex, completedIndex: state.currentIndex });
      }

      const goingForward = targetIndex > state.currentIndex;
      currentCard.classList.remove('active');
      clearCardAnimationClasses(currentCard);
      playCardEntrance(targetCard, goingForward ? 'enter-right' : 'enter-left');

      state.currentIndex = targetIndex;
      markQuestionShown(targetIndex);
      updateNavigation();
      if (!localOnly) saveSessionSnapshot();

      const targetQuestion = (state.config.questions || [])[targetIndex];
      if (targetQuestion?.type === 'stimulus') {
        void startStimulusCard(targetIndex, targetQuestion);
      }
    } finally {
      if (lockNavigation) {
        state.navigationBusy = false;
        updateNavigation();
      }
    }
  };
}

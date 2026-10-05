import { CLOCK_SYNC_INTERVAL_MS, createTabletClock } from './tablet-clock.js';

/**
 * Own participant clock sync, lifecycle snapshots, partial saves, and reload
 * recovery.  The controller supplies state and navigation callbacks explicitly
 * so this module has no hidden dependency on page orchestration.
 */
export function createParticipantSessionRecovery(context) {
  const {
    state,
    getElement,
    getJson,
    postJson,
    sendStudyBeacon,
    flushReliableStudyEvents,
    getStudyClientId,
    resolveParticipantId,
    collectParticipantMetadata,
    collectAnswers,
    collectAnswerEvents,
    collectCardEvents,
    getCardModule,
    isAnswerless,
    updateSensorRuntime,
    handleStudyRunState,
    queueParticipantExtensionSync,
    isStudyRunRunning,
    activateStudyUiAfterAdminStart,
    showScreen,
    updateProgressBar,
    buildQuestions,
    markQuestionShown,
    updateNavigation,
    playCardEntrance,
    prepareStimulusCard,
    startStimulusCard,
    discardInterruptedTrialEvents,
    startParticipantExtensionMonitors,
    renderMediaLayout,
    escapeHtml,
    t,
    isPreview = false,
    constants,
  } = context;

  let studyNoticeTimer = null;

  const tabletClock = createTabletClock({
    exchange: (clientSendMs) => postJson('/api/sync-clock', {
      client_id: getStudyClientId(),
      client_send_ms: clientSendMs,
    }, { timeoutMs: constants.clockSyncTimeoutMs }),
    onSelected: (sample) => {
      state.clockOffsetMs = sample.offset_ms;
      state.clockRttMs = sample.network_delay_ms;
      state.clockSyncSamples = [...(state.clockSyncSamples || []), sample].slice(-1440);
    },
  });

  async function syncClock() { return tabletClock.sync(); }
  function getClockEvidence(clientPerfMs = performance.now()) { return tabletClock.evidence(clientPerfMs); }
  function estimateServerEpochMs(clientPerfMs = performance.now()) {
    return getClockEvidence(clientPerfMs).source_epoch_ms;
  }
  function getClientClockOffsetMs() {
    const estimate = estimateServerEpochMs();
    return Number.isFinite(estimate) ? Math.round(estimate - Date.now()) : null;
  }

  function getSessionPayload() {
    return {
      session_id: state.sessionId,
      client_id: getStudyClientId(),
      study_id: state.config.study_id || '',
      participant_id: resolveParticipantId(),
    };
  }

  function reportNoticeToAdmin(message, type) {
    if (type !== 'error' && type !== 'warning') return;
    void postJson('/api/study/session/client-event', {
      event: 'participant_notice',
      severity: type,
      message: String(message || ''),
      ...getSessionPayload(),
    }, { timeoutMs: 1500 }).catch(() => {});
  }

  function showStudyNotice(message, type = 'error', durationMs = 6000, options = {}) {
    if (options.reportToAdmin !== false) reportNoticeToAdmin(message, type);
    let toast = document.getElementById('study-toast');
    if (!toast) {
      toast = document.createElement('div');
      toast.id = 'study-toast';
      document.body.appendChild(toast);
    }
    toast.textContent = message;
    toast.className = `toast toast--${type} show`;
    clearTimeout(studyNoticeTimer);
    studyNoticeTimer = setTimeout(() => toast.classList.remove('show'), durationMs);
  }

  function handleHeartbeatResponse(response) {
    if (response?.sensor_runtime) updateSensorRuntime(response.sensor_runtime);
    if (response?.study_run_state) handleStudyRunState(response.study_run_state);
  }

  function startRuntimePolling() {
    if (state.runtimePollTimer !== null) window.clearInterval(state.runtimePollTimer);
    const poll = async () => {
      try {
        const runtime = await getJson(
          `/api/study/runtime?client_id=${encodeURIComponent(getStudyClientId())}`,
          { timeoutMs: constants.runtimePollTimeoutMs },
        );
        updateSensorRuntime(runtime?.sensor_runtime || {});
        handleStudyRunState(runtime?.study_run_state);
      } catch (error) {
        console.debug('[study] Runtime poll failed:', error);
      }
    };
    void poll();
    state.runtimePollTimer = window.setInterval(poll, constants.runtimePollIntervalMs);
  }

  function closeVisibilityInterruption(stimulusRun, observedAtMs = performance.now()) {
    if (!stimulusRun?.hiddenStartedAtMs) return;
    const metrics = state.questionMetrics[stimulusRun.index] || {};
    state.questionMetrics[stimulusRun.index] = {
      ...metrics,
      visibility_interrupted: true,
      visibility_hidden_duration_ms: Math.round(
        Number(metrics.visibility_hidden_duration_ms || 0)
          + Math.max(0, observedAtMs - stimulusRun.hiddenStartedAtMs),
      ),
    };
    stimulusRun.hiddenStartedAtMs = null;
  }

  function handleStudyVisibilityChange() {
    const stimulusRun = state.activeStimulus;
    if (!stimulusRun) return;
    const observedAtMs = performance.now();
    const metrics = state.questionMetrics[stimulusRun.index] || {};
    if (document.hidden) {
      if (!stimulusRun.hiddenStartedAtMs) {
        stimulusRun.hiddenStartedAtMs = observedAtMs;
        state.questionMetrics[stimulusRun.index] = {
          ...metrics,
          visibility_interrupted: true,
          visibility_interruption_count: Number(metrics.visibility_interruption_count || 0) + 1,
        };
      }
      return;
    }
    closeVisibilityInterruption(stimulusRun, observedAtMs);
    stimulusRun.timer?.tick?.();
  }

  function saveSessionSnapshot() {
    if (!state.startTime || !state.sessionId) return;
    try {
      window.sessionStorage.setItem(constants.sessionStateKey, JSON.stringify({
        session_id: state.sessionId,
        client_id: getStudyClientId(),
        study_id: state.config.study_id || '',
        study_revision: state.studyRevision || '',
        participant_id: resolveParticipantId(),
        participant_metadata: collectParticipantMetadata(),
        current_index: state.currentIndex,
        current_type: (state.config.questions || [])[state.currentIndex]?.type || '',
        study_started_at: new Date(state.startTime).toISOString(),
        sensor_session_started: state.sensorSessionStarted,
        checkpoint_sequence: state.checkpointSequence,
      }));
    } catch {
      // Session recovery is best-effort.
    }
  }

  function buildPartialResultsPayload({ nextIndex = state.currentIndex, completedIndex = null } = {}) {
    const completed = new Set(state.completedCards || []);
    if (Number.isInteger(completedIndex)) completed.add(completedIndex);
    const answers = collectAnswers({ includeIndex: completedIndex });
    const touchedFields = Object.fromEntries(Object.entries(state.touchedFields || {})
      .map(([index, fields]) => [index, [...fields]]));
    const cardStates = Object.fromEntries([...completed].map((index) => [
      `q${index}`, { answer: answers[`q${index}`] ?? null, touched_fields: touchedFields[index] || [] },
    ]));
    const activeStimulus = state.activeStimulus;
    return {
      ...getSessionPayload(),
      study_revision: state.studyRevision || '',
      checkpoint_version: 2,
      checkpoint_sequence: ++state.checkpointSequence,
      client_clock_offset_ms: getClientClockOffsetMs(),
      clock_sync_samples: state.clockSyncSamples || [],
      timestamp_start: state.startTime ? new Date(state.startTime).toISOString() : null,
      snapshot_at: new Date().toISOString(),
      current_index: nextIndex,
      completed_indices: [...completed].sort((a, b) => a - b),
      answers,
      card_states: cardStates,
      touched_fields: touchedFields,
      question_metrics: structuredClone(state.questionMetrics || {}),
      active_stimulus: activeStimulus ? {
        index: activeStimulus.index,
        stimulus_id: activeStimulus.stimulusId,
        start_event_id: activeStimulus.startEventId,
        stop_event_id: activeStimulus.stopEventId,
        signal_started: Boolean(activeStimulus.signalStarted),
      } : null,
      participant_metadata: collectParticipantMetadata(),
      answer_events: collectAnswerEvents(),
      card_events: collectCardEvents(),
    };
  }

  async function commitCheckpoint({ nextIndex = state.currentIndex, completedIndex = null } = {}) {
    if (!state.startTime || (!state.sessionId && !isPreview)) throw new Error('No active session to checkpoint.');
    const payload = buildPartialResultsPayload({ nextIndex, completedIndex });
    if (!isPreview) {
      const response = await postJson('/api/results/partial', payload, { timeoutMs: 5000 });
      if (response?.checkpoint_sequence !== payload.checkpoint_sequence) {
        throw new Error('The server did not acknowledge the answer checkpoint.');
      }
    }
    state.completedCards = new Set(payload.completed_indices);
    state.acknowledgedAnswers = structuredClone(payload.answers);
    state.lastAcknowledgedCheckpoint = payload;
    saveSessionSnapshot();
    return payload;
  }

  function sendPartialResults({ useBeacon = false } = {}) {
    if (!state.startTime || !state.sessionId) return;
    try {
      const payload = buildPartialResultsPayload();
      if (useBeacon && navigator.sendBeacon) {
        sendStudyBeacon('/api/results/partial', new Blob([JSON.stringify(payload)], { type: 'application/json' }));
        return;
      }
      void postJson('/api/results/partial', payload, { timeoutMs: 1500 }).catch(() => {});
    } catch {
      // Partial saves are best-effort; the final submit is authoritative.
    }
  }

  function bindPageLifecycleEvents() {
    const sendLeaveEvent = () => {
      saveSessionSnapshot();
      sendPartialResults({ useBeacon: true });
      const payload = {
        event: 'client_reload_or_leave',
        ...getSessionPayload(),
        current_index: state.currentIndex,
        current_type: (state.config.questions || [])[state.currentIndex]?.type || null,
        is_stimulus_active: Boolean(state.activeStimulus),
        stimulus_id: state.activeStimulus?.stimulusId || null,
        start_event_id: state.activeStimulus?.startEventId || null,
        stop_event_id: state.activeStimulus?.stopEventId || null,
      };
      try {
        const body = JSON.stringify(payload);
        if (navigator.sendBeacon) {
          sendStudyBeacon('/api/study/session/client-event', new Blob([body], { type: 'application/json' }));
          return;
        }
      } catch {
        // Fall through to fetch.
      }
      void postJson('/api/study/session/client-event', payload).catch(() => {});
    };
    window.addEventListener('pagehide', sendLeaveEvent);
    window.addEventListener('beforeunload', sendLeaveEvent);
    const flushIfSafe = () => {
      if (!loadSessionSnapshot() || state.startTime) void flushReliableStudyEvents();
    };
    window.addEventListener('online', () => {
      void syncClock();
      flushIfSafe();
    });
    document.addEventListener('visibilitychange', () => {
      handleStudyVisibilityChange();
      if (!document.hidden) void syncClock();
    });
    window.setInterval(() => void syncClock(), CLOCK_SYNC_INTERVAL_MS);
    flushIfSafe();
  }

  function loadSessionSnapshot() {
    try {
      const raw = window.sessionStorage.getItem(constants.sessionStateKey);
      return raw ? JSON.parse(raw) : null;
    } catch {
      return null;
    }
  }

  function clearSessionSnapshot() {
    try {
      window.sessionStorage.removeItem(constants.sessionStateKey);
    } catch {
      // Ignore storage failures.
    }
  }

  function matchingSessionSnapshot() {
    const snapshot = loadSessionSnapshot();
    if (!snapshot
      || snapshot.study_id !== (state.config.study_id || '')
      || snapshot.client_id !== getStudyClientId()) return null;
    return snapshot;
  }

  function currentRunKey() {
    return state.studyRunState?.run_id || 'local';
  }

  function coverPageSettings() {
    const cover = state.config?.study_settings?.cover_page;
    return cover?.enabled ? cover : null;
  }

  function showCoverPage(cover) {
    const content = getElement('cover-content');
    if (content) content.innerHTML = renderMediaLayout(cover);
    const label = getElement('btn-cover-start-label');
    if (label) label.textContent = cover.button_label || t('study.cover.start', 'Start');
    state.coverVisibleRunId = currentRunKey();
    showScreen('cover');
    updateProgressBar(0, 0);
  }

  function dismissCoverPage() {
    if (!state.coverVisibleRunId) return;
    state.coverDismissedRunId = state.coverVisibleRunId;
    state.coverVisibleRunId = '';
    void activateStudyUiAfterAdminStart();
  }

  function renderRecoveryBlockIfNeeded() {
    const snapshot = matchingSessionSnapshot();
    const container = getElement('q-container');
    if (!snapshot || !container) return false;
    state.recoveryPending = true;
    container.innerHTML = `
      <div class="q-card-study active">
        <div class="q-type-tag"><i class="iconoir-refresh"></i> ${escapeHtml(t('study.recoveryTag', 'Session recovery'))}</div>
        <p class="q-prompt">${escapeHtml(t('study.recoveryTitle', 'Study page was reloaded'))}</p>
        <p class="screen-sub">${escapeHtml(t('study.recoveryBody', 'A running study session was found for this tablet. Continue only if this was an accidental reload. Active stimulus timing is marked as interrupted.'))}</p>
        <div class="dashboard-actions">
          <button class="btn-secondary" type="button" id="btn-recover-session">${escapeHtml(t('study.recoveryContinue', 'Continue study'))}</button>
        </div>
      </div>`;
    getElement('btn-prev').disabled = true;
    getElement('btn-next').disabled = true;
    getElement('btn-next-label').textContent = t('study.next', 'Next');
    getElement('btn-next-icon').className = 'iconoir-lock';
    getElement('btn-recover-session')?.addEventListener('click', () => void resumeAfterReload(snapshot));
    return true;
  }

  async function resumeAfterReload(snapshot) {
    try {
      const response = await postJson('/api/study/session/resume', {
        event: 'study_resume_after_reload',
        session_id: snapshot.session_id,
        client_id: getStudyClientId(),
        study_id: snapshot.study_id,
        study_revision: snapshot.study_revision,
        participant_id: snapshot.participant_id,
        current_index: snapshot.current_index,
        current_type: snapshot.current_type,
      });
      state.sessionId = response.session?.session_id || snapshot.session_id || '';
      state.participantIdOverride = snapshot.participant_id || '';
      state.participantMetadataOverride = snapshot.participant_metadata || {};
      state.startTime = Date.parse(snapshot.study_started_at) || Date.now();
      state.sensorSessionStarted = Boolean(snapshot.sensor_session_started);
      updateSensorRuntime(response.sensor_runtime || state.sensorRuntime);
      const checkpoint = response.checkpoint;
      if (!checkpoint || checkpoint.checkpoint_version !== 2) {
        throw new Error('The server returned no verified answer checkpoint.');
      }
      state.participantIdOverride = checkpoint.participant_id;
      state.participantMetadataOverride = checkpoint.participant_metadata || {};
      buildQuestions({ markInitialShown: false, startFirstStimulus: false });
      state.checkpointSequence = checkpoint.checkpoint_sequence;
      state.completedCards = new Set(checkpoint.completed_indices || []);
      state.acknowledgedAnswers = structuredClone(checkpoint.answers || {});
      state.lastAcknowledgedCheckpoint = checkpoint;
      state.questionMetrics = structuredClone(checkpoint.question_metrics || {});
      state.touchedFields = Object.fromEntries(Object.entries(checkpoint.touched_fields || {})
        .map(([index, fields]) => [index, new Set(fields)]));
      state.clockSyncSamples = [...(checkpoint.clock_sync_samples || [])];
      const participantCardIndex = state.config.questions?.findIndex((question) => question.type === 'participant-id');
      if (participantCardIndex >= 0) {
        getCardModule('participant-id')?.restoreAnswer?.(
          participantCardIndex,
          state.config.questions[participantCardIndex],
          checkpoint.participant_id,
          getElement(`card-q-${participantCardIndex}`),
          checkpoint.participant_metadata || {},
        );
      }
      for (const index of state.completedCards) {
        const question = state.config.questions?.[index];
        if (!question || isAnswerless(question.type)) continue;
        const answer = checkpoint.answers?.[`q${index}`];
        if (answer === undefined) continue;
        const cardModule = getCardModule(question.type);
        const cardElement = getElement(`card-q-${index}`);
        if (!cardModule?.restoreAnswer || !cardElement) {
          throw new Error(`Card ${question.type} does not support verified answer restoration.`);
        }
        cardModule.restoreAnswer(index, question, answer, cardElement);
        if (JSON.stringify(cardModule.collectAnswer(index, question)) !== JSON.stringify(answer)) {
          throw new Error(`Card ${question.type} failed answer restoration.`);
        }
      }
      const targetIndex = Number.isInteger(checkpoint.current_index) ? checkpoint.current_index : 0;
      const safeIndex = Math.max(0, Math.min(targetIndex, (state.config.questions || []).length - 1));
      if (safeIndex === 0) {
        markQuestionShown(0);
        updateNavigation();
      } else {
        showRecoveredCard(safeIndex);
      }
      saveSessionSnapshot();
      if (response.interrupted_stimulus?.stimulus_id) {
        discardInterruptedTrialEvents(state.sessionId, response.interrupted_stimulus.stimulus_id);
        state.repeatInterruptedStimulus = response.interrupted_stimulus;
        const interruptedIndex = Number(response.interrupted_stimulus.index);
        if (Number.isInteger(interruptedIndex) && interruptedIndex >= 0) {
          showRecoveredCard(interruptedIndex);
          const card = getElement(`card-q-${interruptedIndex}`);
          const button = document.createElement('button');
          button.type = 'button';
          button.className = 'btn-primary';
          button.textContent = t('study.repeatStimulus', 'Repeat stimulus');
          button.addEventListener('click', () => {
            button.remove();
            const old = state.questionMetrics[interruptedIndex] || {};
            const attempt = {
              stimulus_id: response.interrupted_stimulus.stimulus_id,
              start_event_id: old.start_event_id || checkpoint.active_stimulus?.start_event_id || '',
              stop_event_id: old.stop_event_id || checkpoint.active_stimulus?.stop_event_id || '',
              active_started_at: old.active_started_at || null,
              active_ended_at: old.active_ended_at || null,
              server_start_received_epoch_ms: old.server_start_received_epoch_ms || null,
              server_stop_received_epoch_ms: response.interrupted_stimulus.server_stop_received_epoch_ms || null,
              interrupted_by_reload: true,
              inferred_from_checkpoint: response.interrupted_stimulus.inferred_from_checkpoint === true,
              reconciliation_outcome: response.interrupted_stimulus.outcome,
            };
            state.questionMetrics[interruptedIndex] = {
              shown_at: old.shown_at,
              shown_at_server_epoch_ms: old.shown_at_server_epoch_ms,
              shown_marker_sent: old.shown_marker_sent,
              shown_event_id: old.shown_event_id,
              attempt_history: [...(old.attempt_history || []), attempt].slice(-10),
            };
            state.repeatInterruptedStimulus = null;
            updateNavigation();
            void startStimulusCard(interruptedIndex, state.config.questions[interruptedIndex]);
          });
          card?.appendChild(button);
        }
      }
      state.recoveryPending = false;
      updateNavigation();
      void syncClock();
      void flushReliableStudyEvents();
      startParticipantExtensionMonitors('session_recovered');
    } catch (error) {
      console.error('[study] Could not resume study session:', error);
      showStudyNotice(t('study.recoveryFailed', 'Could not resume this session. Please contact the study supervisor.'));
    }
  }

  function showRecoveredCard(targetIndex) {
    const currentCard = getElement(`card-q-${state.currentIndex}`);
    const targetCard = getElement(`card-q-${targetIndex}`);
    if (!targetCard) return;
    if (currentCard && currentCard !== targetCard) currentCard.classList.remove('active');
    playCardEntrance(targetCard, 'card-enter-initial');
    state.currentIndex = targetIndex;
    markQuestionShown(targetIndex);
    const targetQuestion = (state.config.questions || [])[targetIndex];
    if (targetQuestion?.type === 'stimulus') prepareStimulusCard(targetIndex, targetQuestion);
    updateNavigation();
  }

  return {
    bindPageLifecycleEvents,
    clearSessionSnapshot,
    closeVisibilityInterruption,
    coverPageSettings,
    currentRunKey,
    dismissCoverPage,
    estimateServerEpochMs,
    getClockEvidence,
    getClientClockOffsetMs,
    getSessionPayload,
    handleHeartbeatResponse,
    matchingSessionSnapshot,
    renderRecoveryBlockIfNeeded,
    reportNoticeToAdmin,
    saveSessionSnapshot,
    sendPartialResults,
    commitCheckpoint,
    showCoverPage,
    showStudyNotice,
    startRuntimePolling,
    syncClock,
  };
}

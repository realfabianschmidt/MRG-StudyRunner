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
    startParticipantExtensionMonitors,
    renderMediaLayout,
    escapeHtml,
    t,
    constants,
  } = context;

  let studyNoticeTimer = null;

  async function syncClock() {
    const offsets = [];
    const rtts = [];
    for (let i = 0; i < 3; i += 1) {
      const clientSendMs = performance.now();
      try {
        const response = await postJson('/api/sync-clock', {
          client_id: getStudyClientId(),
          client_send_ms: clientSendMs,
        }, { timeoutMs: constants.clockSyncTimeoutMs });
        const clientRecvMs = performance.now();
        offsets.push(((response.server_receive_ms - clientSendMs)
          + (response.server_send_ms - clientRecvMs)) / 2);
        rtts.push(Math.max(0, clientRecvMs - clientSendMs));
      } catch {
        // Server unreachable; skip this round.
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    if (offsets.length > 0) {
      offsets.sort((a, b) => a - b);
      rtts.sort((a, b) => a - b);
      state.clockOffsetMs = offsets[Math.floor(offsets.length / 2)];
      const medianRtt = rtts[Math.floor(rtts.length / 2)];
      state.clockRttMs = Number.isFinite(medianRtt) ? medianRtt : null;
      console.debug('[study] Clock offset estimated:', state.clockOffsetMs.toFixed(2), 'ms');
    }
  }

  function estimateServerEpochMs(clientPerfMs = performance.now()) {
    return Number.isFinite(state.clockOffsetMs) ? clientPerfMs + state.clockOffsetMs : Date.now();
  }

  function getClientClockOffsetMs() {
    return Number.isFinite(state.clockOffsetMs)
      ? Math.round(estimateServerEpochMs() - Date.now())
      : null;
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
        participant_id: resolveParticipantId(),
        participant_metadata: collectParticipantMetadata(),
        current_index: state.currentIndex,
        current_type: (state.config.questions || [])[state.currentIndex]?.type || '',
        study_started_at: new Date(state.startTime).toISOString(),
        sensor_session_started: state.sensorSessionStarted,
      }));
    } catch {
      // Session recovery is best-effort.
    }
  }

  function buildPartialResultsPayload() {
    return {
      ...getSessionPayload(),
      client_clock_offset_ms: getClientClockOffsetMs(),
      timestamp_start: state.startTime ? new Date(state.startTime).toISOString() : null,
      snapshot_at: new Date().toISOString(),
      current_index: state.currentIndex,
      answers: collectAnswers(),
      participant_metadata: collectParticipantMetadata(),
      answer_events: collectAnswerEvents(),
      card_events: collectCardEvents(),
    };
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
    window.addEventListener('online', () => void flushReliableStudyEvents());
    document.addEventListener('visibilitychange', handleStudyVisibilityChange);
    void flushReliableStudyEvents();
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
    container.innerHTML = `
      <div class="q-card-study active">
        <div class="q-type-tag"><i class="iconoir-refresh"></i> ${escapeHtml(t('study.recoveryTag', 'Session recovery'))}</div>
        <p class="q-prompt">${escapeHtml(t('study.recoveryTitle', 'Study page was reloaded'))}</p>
        <p class="screen-sub">${escapeHtml(t('study.recoveryBody', 'A running study session was found for this tablet. Continue only if this was an accidental reload. Active stimulus timing is marked as interrupted.'))}</p>
        <div class="dashboard-actions">
          <button class="btn-secondary" type="button" id="btn-recover-session">${escapeHtml(t('study.recoveryContinue', 'Continue study'))}</button>
          <button class="btn-secondary" type="button" id="btn-recover-discard">${escapeHtml(t('study.recoveryRestart', 'Start over'))}</button>
        </div>
      </div>`;
    getElement('btn-prev').disabled = true;
    getElement('btn-next').disabled = true;
    getElement('btn-next-label').textContent = t('study.next', 'Next');
    getElement('btn-next-icon').className = 'iconoir-lock';
    getElement('btn-recover-session')?.addEventListener('click', () => void resumeAfterReload(snapshot));
    getElement('btn-recover-discard')?.addEventListener('click', () => {
      clearSessionSnapshot();
      buildQuestions({ markInitialShown: false, startFirstStimulus: false });
    });
    return true;
  }

  async function resumeAfterReload(snapshot) {
    try {
      const response = await postJson('/api/study/session/resume', {
        event: 'study_resume_after_reload',
        session_id: snapshot.session_id,
        client_id: getStudyClientId(),
        study_id: snapshot.study_id,
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
      buildQuestions({ markInitialShown: false, startFirstStimulus: false });
      const targetIndex = Number.isInteger(Number(snapshot.current_index)) ? Number(snapshot.current_index) : 0;
      const safeIndex = Math.max(0, Math.min(targetIndex, (state.config.questions || []).length - 1));
      if (safeIndex === 0) {
        markQuestionShown(0);
        updateNavigation();
      } else {
        showRecoveredCard(safeIndex);
      }
      saveSessionSnapshot();
      startParticipantExtensionMonitors('session_recovered');
    } catch (error) {
      console.error('[study] Could not resume study session:', error);
      showStudyNotice(t('study.recoveryFailed', 'Could not resume the study session.'));
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
    getClientClockOffsetMs,
    getSessionPayload,
    handleHeartbeatResponse,
    matchingSessionSnapshot,
    renderRecoveryBlockIfNeeded,
    reportNoticeToAdmin,
    saveSessionSnapshot,
    sendPartialResults,
    showCoverPage,
    showStudyNotice,
    startRuntimePolling,
    syncClock,
  };
}

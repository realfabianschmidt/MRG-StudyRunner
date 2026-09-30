/**
 * Own participant answer metrics, plugin/sensor session state, navigation
 * readiness, and final result submission.
 */
export function createParticipantResultSubmission(context) {
  const {
    state,
    getElement,
    t,
    resolveParticipantId,
    collectParticipantMetadata,
    participantExtensions,
    getClientClockOffsetMs,
    estimateServerEpochMs,
    createEventId,
    sendMarker,
    pluginsWithCapability,
    postJson,
    getStudyClientId,
    clearSessionSnapshot,
    CARDS,
    isAnswerless,
    startTrial,
    goTo,
    showScreen,
    showStudyNotice,
    resetParticipantSessionState,
    loadPendingSubmission,
    persistPendingSubmission,
    clearPendingSubmission,
    constants,
  } = context;
  const { studySessionStopTimeoutMs: STUDY_SESSION_STOP_TIMEOUT_MS } = constants;

  let participantExtensionSync = Promise.resolve();

  function getStudyClientHeartbeatPayload() {
    const questions = state.config.questions || [];
    const currentQuestion = questions[state.currentIndex] || null;
  
    return {
      participant_id: resolveParticipantId(),
      study_id: state.config.study_id || '',
      session_id: state.sessionId,
      current_index: Number.isInteger(state.currentIndex) ? state.currentIndex : null,
      current_type: currentQuestion?.type || null,
      is_stimulus_active: Boolean(state.activeStimulus),
      signal_started: Boolean(state.activeStimulus?.signalStarted),
      plugin_status: participantExtensions.heartbeatStatus(),
      study_started: Boolean(state.startTime),
      study_run_status: state.studyRunState?.status || 'loaded',
      waiting_for_admin_start: Boolean(state.waitingForAdminStart),
      clock_offset_ms: getClientClockOffsetMs(),
      clock_sync_rtt_ms: Number.isFinite(state.clockRttMs) ? Math.round(state.clockRttMs) : null,
    };
  }
  
  function getQuestionIndexFromElement(element) {
    const cardElement = element?.closest?.('.q-card-study');
    if (!cardElement?.id?.startsWith('card-q-')) {
      return null;
    }
  
    const index = Number.parseInt(cardElement.id.replace('card-q-', ''), 10);
    return Number.isInteger(index) ? index : null;
  }
  
  function markQuestionField(questionIndex, fieldKey) {
    const normalizedKey = fieldKey || '__question__';
    if (!state.touchedFields[questionIndex]) {
      state.touchedFields[questionIndex] = new Set();
    }
    state.touchedFields[questionIndex].add(normalizedKey);
  }
  
  function markQuestionShown(questionIndex) {
    const questions = state.config.questions || [];
    const question = questions[questionIndex];
    const nowIso = new Date().toISOString();
    const current = state.questionMetrics[questionIndex] || {};
    state.questionMetrics[questionIndex] = {
      ...current,
      shown_at: current.shown_at || nowIso,
      // Server-clock estimate so biosignal slicing is immune to tablet clock skew.
      shown_at_server_epoch_ms: current.shown_at_server_epoch_ms || estimateServerEpochMs(),
    };
    if (!state.startTime || question?.type === 'participant-id') {
      return;
    }
    if (question?.type !== 'finish' && !current.shown_marker_sent) {
      const shownEventId = createEventId('marker-question-shown');
      state.questionMetrics[questionIndex].shown_marker_sent = true;
      state.questionMetrics[questionIndex].shown_event_id = shownEventId;
      void sendMarker(
        question?.type === 'stimulus' ? 'stimulus_shown' : 'question_shown',
        questionIndex,
        question,
        'shown',
        { eventId: shownEventId },
      );
    }
  }
  
  function recordQuestionCompletion(questionIndex) {
    const questions = state.config.questions || [];
    const question = questions[questionIndex];
    if (!question || question.type === 'stimulus' || question.type === 'finish') {
      return Promise.resolve();
    }
  
    const current = state.questionMetrics[questionIndex] || {};
    if (current.answered_at) {
      return Promise.resolve();
    }
    state.questionMetrics[questionIndex] = {
      ...current,
      answered_at: new Date().toISOString(),
      answered_at_server_epoch_ms: estimateServerEpochMs(),
      answered_event_id: createEventId('marker-question-answered'),
    };
    if (!state.startTime || question.type === 'participant-id') {
      return Promise.resolve();
    }
    return sendMarker(
      'question_answered',
      questionIndex,
      question,
      'answered',
      { eventId: state.questionMetrics[questionIndex].answered_event_id },
    );
  }
  
  function getTouchedFieldCount(questionIndex) {
    return state.touchedFields[questionIndex]?.size || 0;
  }
  
  function shouldActivateHardware(question) {
    const sensorsEnabled = state.config.study_settings?.sensors_enabled !== false
      && hasAnyStudySensorEnabled();
    // Card actions belong to their plugin, not to biodata recording. OSC or any
    // future output plugin must still receive a prepared trial when the study
    // deliberately records no sensor streams.
    const hasPluginAction = Object.values(getActivePluginActions(question)).some((actions) => (
      Object.values(actions).some((value) => value !== false && value !== null && value !== '')
    ));
    return sensorsEnabled || hasPluginAction;
  }
  
  function getStudySensorSettings() {
    const effective = state.sensorRuntime?.effective;
    if (effective && typeof effective === 'object') {
      return Object.fromEntries(Object.entries(effective).map(([key, enabled]) => [key, enabled === true]));
    }
    const settings = state.config.study_settings || {};
    if (settings.sensors_enabled === false) {
      return Object.fromEntries(pluginsWithCapability('study_sensor').map((plugin) => [plugin.plugin_key, false]));
    }
    if (settings.plugins && typeof settings.plugins === 'object') {
      return Object.fromEntries(pluginsWithCapability('study_sensor').map((plugin) => [
        plugin.plugin_key,
        settings.plugins[plugin.plugin_key]?.enabled === true,
      ]));
    }
    const sensors = settings.sensors && typeof settings.sensors === 'object' ? settings.sensors : {};
    return Object.fromEntries(Object.entries(sensors).map(([key, enabled]) => [key, enabled === true]));
  }
  
  function isStudySensorEnabled(sensorKey) {
    return Boolean(getStudySensorSettings()[sensorKey]);
  }
  
  function hasAnyStudySensorEnabled() {
    return Object.values(getStudySensorSettings()).some(Boolean);
  }
  
  function getActivePluginActions(question) {
    const configured = question?.plugin_actions && typeof question.plugin_actions === 'object'
      ? question.plugin_actions
      : {};
    return Object.fromEntries(
      Object.entries(configured)
        .filter(([, actions]) => actions && typeof actions === 'object' && !Array.isArray(actions))
        .map(([pluginKey, actions]) => [pluginKey, { ...actions }]),
    );
  }
  
  function isParticipantPluginEnabled(plugin) {
    const pluginKey = String(plugin?.plugin_key || '').trim();
    if (!pluginKey) return false;
    if ((plugin.capabilities || []).includes('study_sensor')) {
      return isStudySensorEnabled(pluginKey);
    }
    const configured = state.config.study_settings?.plugins?.[pluginKey];
    return configured?.enabled === true;
  }
  
  function getParticipantSessionContext() {
    return {
      participantId: resolveParticipantId(),
      studyId: state.config.study_id || '',
      sessionId: state.sessionId,
      currentIndex: state.currentIndex,
      studyStarted: Boolean(state.startTime),
      questionsBuilt: Boolean(state.questionsBuilt),
    };
  }
  
  function createParticipantExtensionContext(plugin) {
    const pluginKey = String(plugin?.plugin_key || '');
    const encodedPluginKey = encodeURIComponent(pluginKey);
    return {
      isEnabled: () => isParticipantPluginEnabled(plugin),
      getConfig: () => state.config,
      getActiveStimulus: () => state.activeStimulus,
      getSession: getParticipantSessionContext,
      getPluginActions: (question) => getActivePluginActions(question)[pluginKey] || {},
      postJson,
      runParticipantAction: (actionKey, payload = {}, options = {}) => postJson(
        `/api/plugins/${encodedPluginKey}/participant/actions/${encodeURIComponent(String(actionKey || ''))}`,
        payload,
        options,
      ),
      ingestParticipant: (ingestKey, payload, options = {}) => postJson(
        `/api/plugins/${encodedPluginKey}/participant/ingest/${encodeURIComponent(String(ingestKey || ''))}`,
        payload,
        options,
      ),
      estimateServerEpochMs,
      getClientClockOffsetMs,
    };
  }
  
  function participantExtensionsMayMonitor() {
    return Boolean(state.startTime) || (isStudyRunRunning() && state.questionsBuilt);
  }
  
  function startParticipantExtensionMonitors(reason) {
    if (!participantExtensionsMayMonitor()) return;
    participantExtensions.startPrestudyMonitors({
      reason,
      session: getParticipantSessionContext(),
    });
  }
  
  function queueParticipantExtensionSync(reason) {
    participantExtensionSync = participantExtensionSync
      .catch(() => {})
      .then(async () => {
        await participantExtensions.sync({
          reason,
          sensorRuntime: state.sensorRuntime,
          session: getParticipantSessionContext(),
        });
        startParticipantExtensionMonitors(reason);
      })
      .catch((error) => {
        participantExtensions.reportStatus('participant_extensions', {
          state: 'warning',
          last_error: error?.message || String(error),
          failed_hook: 'sync',
        });
        console.warn('[study] Optional participant extensions could not be synchronized:', error);
      });
    return participantExtensionSync;
  }
  
  async function startStudySensorSession() {
    try {
      const response = await postJson('/api/study/session/start', {
        session_id: state.sessionId,
        client_id: getStudyClientId(),
        study_id: state.config.study_id || '',
        participant_id: resolveParticipantId(),
        current_index: state.currentIndex,
        current_type: (state.config.questions || [])[state.currentIndex]?.type || null,
        require_admin_start: true,
        study_run_id: state.studyRunState?.run_id || '',
      });
      state.sessionId = response.session?.session_id || state.sessionId;
      state.sensorSessionStarted = true;
      return true;
    } catch (error) {
      state.sensorSessionStarted = false;
      console.error('[study] Could not start study sensor session:', error);
      // A refusal the server answered was already reported to the admin with
      // its reason; only an unanswered request must be reported from here.
      showStudyNotice(t('study.startFailed', 'Could not start the study session.'), 'error', 6000, {
        reportToAdmin: !error?.status,
      });
      return false;
    }
  }
  
  async function stopStudySensorSession(options = {}) {
    const sessionId = options.sessionId || state.sessionId;
    if (!state.sensorSessionStarted && !sessionId) {
      return;
    }
    const clearSnapshot = options.clearSnapshot !== false;
    try {
      await postJson('/api/study/session/stop', {
        session_id: sessionId,
        client_id: getStudyClientId(),
        study_id: options.studyId || state.config.study_id || '',
        participant_id: options.participantId || resolveParticipantId(),
      }, { timeoutMs: STUDY_SESSION_STOP_TIMEOUT_MS });
    } catch (error) {
      console.error('[study] Could not stop study sensor session:', error);
    } finally {
      state.sensorSessionStarted = false;
      if (clearSnapshot) {
        clearSessionSnapshot();
      }
    }
  }
  
  // Package 5g.B2: every branch here used to be a type check against one
  // card's own answer logic. Each card module now exports its own optional
  // isAnswered(question, questionIndex, context) - a type with nothing to
  // check (stimulus, finish) simply has no such export, and "no hook" means
  // "always answered" below. Adding a new interactive card type no longer
  // means editing this function at all.
  function isAnswered(questionIndex) {
    const question = (state.config.questions || [])[questionIndex];
    if (!question) {
      return true;
    }
    const cardModule = CARDS[question.type];
    if (!cardModule?.isAnswered) {
      return true;
    }
    const cardElement = getElement(`card-q-${questionIndex}`);
    if (!cardElement) {
      return true;
    }
    return cardModule.isAnswered(question, questionIndex, {
      cardElement,
      touchedFieldCount: getTouchedFieldCount(questionIndex),
    });
  }
  
  function updateNavigation() {
    const questions = state.config.questions || [];
    const total = questions.length;
    if (!total) {
      getElement('btn-prev').disabled = true;
      getElement('btn-next').disabled = true;
      renderCounter(0, 0);
      updateProgressBar(0, 0);
      getElement('btn-next-label').textContent = t('study.finish', 'Finish');
      getElement('btn-next-icon').className = 'iconoir-check';
      return;
    }
  
    const currentIndex = state.currentIndex;
    const currentQuestion = questions[currentIndex];
    const totalNormal = questions.filter(q => q.type !== 'finish').length;
  
    const nav = document.querySelector('.q-nav');
    if (currentQuestion && currentQuestion.type === 'finish') {
      if (nav) nav.hidden = true;
      renderCounter(totalNormal, totalNormal);
      updateProgressBar(totalNormal, totalNormal);
      return;
    } else {
      if (nav) nav.hidden = false;
    }
  
    const isFirst = currentIndex === 0;
    const isStimulusBusy = Boolean(state.activeStimulus);
    const isUiBusy = state.navigationBusy || state.submitInFlight;
    const isOptional = currentQuestion?.required === false;
    const answered = (isOptional || isAnswered(currentIndex)) && !isStimulusBusy;
  
    const isLastNormalCard = (currentIndex === total - 1) || (questions[currentIndex + 1]?.type === 'finish');
    const isPreStudyStart = !state.startTime && currentQuestion?.type === 'participant-id';
  
    getElement('btn-prev').disabled = isFirst || isStimulusBusy || isUiBusy;
    getElement('btn-next').disabled = !answered || isUiBusy;
  
    renderCounter(Math.min(currentIndex + 1, totalNormal), totalNormal);
    updateProgressBar(Math.min(currentIndex + 1, totalNormal), totalNormal);
    getElement('btn-next-label').textContent = state.submitInFlight
      ? t('study.saving', 'Saving...')
      : (state.navigationBusy
        ? (isPreStudyStart ? t('study.starting', 'Starting...') : t('study.pleaseWait', 'Please wait...'))
        : (isPreStudyStart ? t('study.start', 'Start') : (isLastNormalCard ? t('study.submit', 'Submit') : t('study.next', 'Next'))));
    getElement('btn-next-icon').className = isLastNormalCard ? 'iconoir-check' : 'iconoir-nav-arrow-right';
  }
  
  function renderCounter(current, total) {
    const pad = (value) => String(value).padStart(2, '0');
    getElement('q-counter').innerHTML = `
      <span class="q-counter-current">${pad(current)}</span>
      <span class="q-counter-divider">/</span>
      <span class="q-counter-total">${pad(total)}</span>`;
  }
  
  function updateProgressBar(current, total) {
    const progressBar = getElement('study-progress-bar');
    const progressFill = getElement('study-progress-fill');
    if (!progressBar || !progressFill) {
      return;
    }
  
    const enabled = state.config.study_settings?.progress_bar_enabled === true;
    if (!enabled || total <= 0) {
      progressBar.hidden = true;
      progressFill.style.width = '0%';
      return;
    }
  
    const percent = Math.max(0, Math.min(100, (current / total) * 100));
    progressBar.hidden = false;
    progressFill.style.width = `${percent}%`;
  }
  
  async function handleNext() {
    if (state.navigationBusy || state.submitInFlight) {
      return;
    }
    const total = (state.config.questions || []).length;
    const nextQuestion = state.config.questions[state.currentIndex + 1];
  
    const willSubmit = (nextQuestion && nextQuestion.type === 'finish') || state.currentIndex === total - 1;
    if (state.startTime && willSubmit) {
      await submitResults();
      return;
    }
  
    state.navigationBusy = true;
    updateNavigation();
    if (!state.startTime) {
      try {
        await startTrial({ rebuild: false });
        if (!state.startTime) {
          return;
        }
        if (willSubmit) {
          state.navigationBusy = false;
          updateNavigation();
          await submitResults();
          return;
        }
        await goTo(state.currentIndex + 1, { lockNavigation: false, force: true });
      } finally {
        if (!state.submitInFlight) {
          state.navigationBusy = false;
          updateNavigation();
        }
      }
      return;
    }
  
    try {
      await goTo(state.currentIndex + 1, { lockNavigation: false, force: true });
    } finally {
      state.navigationBusy = false;
      updateNavigation();
    }
  }
  
  function collectAnswers() {
    const answers = {};
  
    (state.config.questions || []).forEach((question, questionIndex) => {
      if (isAnswerless(question.type)) {
        return;
      }
      // An optional, untouched question is omitted entirely so the server can
      // tell "shown but skipped" apart from "answered" - never send a default.
      if (question.required === false && !isAnswered(questionIndex)) {
        return;
      }
  
      const cardModule = CARDS[question.type];
      if (cardModule) {
        answers[`q${questionIndex}`] = cardModule.collectAnswer(questionIndex, question);
      }
    });
  
    return answers;
  }
  
  function collectAnswerEvents() {
    const events = [];
    const questions = state.config.questions || [];
  
    questions.forEach((question, questionIndex) => {
      if (!question || question.type === 'stimulus' || question.type === 'finish') {
        return;
      }
  
      const metrics = state.questionMetrics[questionIndex] || {};
      if (!metrics.answered_at) {
        return;
      }
      const answerKey = question.type === 'participant-id' ? null : `q${questionIndex}`;
      events.push({
        question_index: questionIndex,
        question_type: question.type,
        answer_key: answerKey,
        shown_at: metrics.shown_at || metrics.answered_at,
        answered_at: metrics.answered_at,
      });
    });
  
    return events;
  }
  
  function collectCardEvents() {
    const events = [];
    const questions = state.config.questions || [];
  
    questions.forEach((question, questionIndex) => {
      if (!question || question.type === 'finish') {
        return;
      }
  
      const metrics = state.questionMetrics[questionIndex] || {};
      if (!metrics.shown_at) {
        return;
      }
      const event = {
        question_index: questionIndex,
        question_type: question.type,
        shown_at: metrics.shown_at,
        shown_at_server_epoch_ms: metrics.shown_at_server_epoch_ms || null,
      };
  
      if (question.type === 'stimulus') {
        event.active_started_at = metrics.active_started_at || null;
        event.active_ended_at = metrics.active_ended_at || metrics.completed_at || null;
        event.completed_at = metrics.completed_at || metrics.active_ended_at || null;
        event.server_start_received_at = metrics.server_start_received_at || null;
        event.server_stop_received_at = metrics.server_stop_received_at || null;
        event.server_start_received_epoch_ms = metrics.server_start_received_epoch_ms || null;
        event.server_stop_received_epoch_ms = metrics.server_stop_received_epoch_ms || null;
        event.client_start_trigger_epoch_ms = metrics.client_start_trigger_epoch_ms || null;
        event.client_stop_trigger_epoch_ms = metrics.client_stop_trigger_epoch_ms || null;
        event.planned_start_epoch_ms = metrics.planned_start_epoch_ms || null;
        event.planned_deadline_epoch_ms = metrics.planned_deadline_epoch_ms || null;
        event.stimulus_id = metrics.stimulus_id || '';
        event.start_event_id = metrics.start_event_id || '';
        event.stop_event_id = metrics.stop_event_id || '';
        event.prepare_failed = metrics.prepare_failed === true;
        event.visibility_interrupted = metrics.visibility_interrupted === true;
        event.visibility_interruption_count = Number(metrics.visibility_interruption_count || 0);
        event.visibility_hidden_duration_ms = Number(metrics.visibility_hidden_duration_ms || 0);
        event.warmup_callback_delay_ms = Number(metrics.warmup_callback_delay_ms || 0);
        event.onset_callback_delay_ms = Number(metrics.onset_callback_delay_ms || 0);
        event.deadline_callback_delay_ms = Number(metrics.deadline_callback_delay_ms || 0);
        event.start_marker = metrics.start_marker || '';
        event.stop_marker = metrics.stop_marker || '';
      } else {
        event.answered_at = metrics.answered_at || null;
        event.answered_at_server_epoch_ms = metrics.answered_at_server_epoch_ms || null;
        event.completed_at = metrics.answered_at || null;
        event.shown_event_id = metrics.shown_event_id || '';
        event.answered_event_id = metrics.answered_event_id || '';
      }
  
      events.push(event);
    });
  
    return events;
  }
  
  async function submitResults() {
    if (state.submitInFlight) {
      return;
    }
    state.submitInFlight = true;
    state.navigationBusy = false;
    const btn = getElement('btn-next');
    if (btn) {
      btn.disabled = true;
      getElement('btn-next-label').textContent = t('study.saving', 'Saving...');
    }
    updateNavigation();
  
    try {
      await recordQuestionCompletion(state.currentIndex);
      const sessionId = state.sessionId;
      const participantId = resolveParticipantId();
      const studyId = state.config.study_id;
      let submission = loadPendingSubmission(sessionId);
      if (!submission) {
        const endMonotonicMs = performance.now();
        submission = {
          submission_id: `submission-${sessionId}`,
          session_id: sessionId,
          participant_id: participantId,
          study_id: studyId,
          client_clock_offset_ms: getClientClockOffsetMs(),
          timestamp_start: new Date(state.startTime).toISOString(),
          timestamp_end: new Date().toISOString(),
          study_end_event: {
            event_id: createEventId('study-end'),
            source_monotonic_ms: endMonotonicMs,
            source_epoch_ms: estimateServerEpochMs(endMonotonicMs),
            sequence_number: null,
          },
          answers: collectAnswers(),
          participant_metadata: collectParticipantMetadata(),
          answer_events: collectAnswerEvents(),
          card_events: collectCardEvents(),
        };
        persistPendingSubmission(submission);
      }
      participantExtensions.beforeSubmit({
        submission,
        session: getParticipantSessionContext(),
      });
      const response = await postJson('/api/results', submission);
      state.studyRunState = response?.study_run_state || state.studyRunState;
      state.completedLocally = true;
      state.completedRunId = state.studyRunState?.run_id || '';
      clearPendingSubmission();
      clearSessionSnapshot();
      state.startTime = null;
      state.sessionId = '';
      // Finalization now owns end-marker, producer stop, worker drain, and XDF
      // footer ordering. Calling /session/stop here would recreate the old race.
      state.sensorSessionStarted = false;
      // The submission is on the server; nothing of it stays in this page.
      resetParticipantSessionState();
      void participantExtensions.dispose('submission_committed');
  
      const finishIndex = (state.config.questions || []).findIndex(q => q.type === 'finish');
      if (finishIndex !== -1) {
        await goTo(finishIndex, { force: true, lockNavigation: false });
      } else {
        showScreen('done'); // Fallback when the finish card is missing
      }
      state.submitInFlight = false;
      updateNavigation();
    } catch (error) {
      console.error('[study] Could not save results:', error);
      showStudyNotice(t('study.saveFailedBody', 'Your answers could not be saved. Please tell the study supervisor - your answers are still on this screen.'), 'error', 10000);
      state.submitInFlight = false;
      state.navigationBusy = false;
      participantExtensions.onSubmitFailed({
        error,
        submission: state.pendingSubmission,
        session: getParticipantSessionContext(),
      });
      if (btn) {
        btn.disabled = false;
        getElement('btn-next-label').textContent = t('study.submit', 'Submit');
      }
      updateNavigation();
    }
  }
  

  return {
    collectAnswerEvents,
    collectAnswers,
    collectCardEvents,
    createParticipantExtensionContext,
    getActivePluginActions,
    getParticipantSessionContext,
    getQuestionIndexFromElement,
    getStudyClientHeartbeatPayload,
    handleNext,
    isAnswered,
    isParticipantPluginEnabled,
    markQuestionField,
    markQuestionShown,
    queueParticipantExtensionSync,
    recordQuestionCompletion,
    renderCounter,
    shouldActivateHardware,
    startParticipantExtensionMonitors,
    startStudySensorSession,
    stopStudySensorSession,
    submitResults,
    updateNavigation,
    updateProgressBar,
  };
}

/**
 * Own the prepared-trial lifecycle and visual stimulus execution.
 * All navigation and session effects enter through explicit callbacks.
 */
export function createParticipantStimulusExecution(context) {
  const {
    state,
    getElement,
    createEventId,
    shouldActivateHardware,
    postJson,
    buildEventPayload,
    estimateServerEpochMs,
    syncClock = async () => {},
    sendPartialResults = () => {},
    updateNavigation,
    handleNext,
    stopStudySensorSession,
    resetParticipantSessionState,
    showWaitingForAdminStart,
    t,
    showStudyNotice,
    reportNoticeToAdmin,
    createModal,
    escapeHtml,
    startDeadlineTimer,
    remainingWholeSeconds,
    participantExtensions,
    getParticipantSessionContext,
    sendReliableStudyEvent,
    closeVisibilityInterruption,
    // A card may react to its own stimulus (onStimulusPrepared, onTimeUp).
    getCardModule = () => null,
    constants,
  } = context;
  const {
    trialPrepareTimeoutMs: TRIAL_PREPARE_TIMEOUT_MS,
    trialStopTimeoutMs: TRIAL_STOP_TIMEOUT_MS,
    trialStartTimeoutMs: TRIAL_START_TIMEOUT_MS,
  } = constants;

  async function startStimulusCard(questionIndex, question) {
    if (estimateServerEpochMs() === null) await syncClock();
    if (estimateServerEpochMs() === null) {
      state.stimulusClockBlocked = true;
      updateNavigation();
      showStudyNotice(t('study.clockRequired', 'Tablet time calibration is unavailable. Retry the stimulus when connected.'), 'error', 10000);
      const card = getElement(`card-q-${questionIndex}`);
      if (card && !card.querySelector('.stimulus-clock-retry')) {
        const retry = document.createElement('button');
        retry.type = 'button';
        retry.className = 'btn-secondary stimulus-clock-retry';
        retry.textContent = t('study.retryStimulus', 'Retry stimulus');
        retry.addEventListener('click', () => {
          retry.remove();
          void startStimulusCard(questionIndex, question);
        });
        card.appendChild(retry);
      }
      return;
    }
    state.stimulusClockBlocked = false;
    updateNavigation();
    const stimulusRun = {
      index: questionIndex,
      question,
      timer: null,
      signalStarted: false,
      activeStarted: false,
      cleanup: null,
      extensionCleanup: null,
      hiddenStartedAtMs: null,
      stimulusId: createEventId(`stimulus-${questionIndex}`),
      startEventId: createEventId(`stimulus-${questionIndex}-start`),
      stopEventId: createEventId(`stimulus-${questionIndex}-stop`),
      timeUpEventId: overtimeEventId(questionIndex, question),
      plannedStartPerfMs: null,
      plannedDeadlinePerfMs: null,
      plannedStartEpochMs: null,
      plannedDeadlineEpochMs: null,
      // Overtime: the duration was reached and the participant may stay.
      overtime: false,
      overtimeStartedPerfMs: null,
      actuatorsStopped: false,
      contentCleanup: null,
    };

    state.activeStimulus = stimulusRun;
    sendPartialResults();
    prepareStimulusCard(questionIndex, question);
    runCardHook(question, 'onStimulusPrepared', questionIndex);
  
    assignStimulusSchedule(stimulusRun);
    if (shouldActivateHardware(question)) {
      const prepared = await prepareStimulusRouting(stimulusRun);
      if (!prepared || state.activeStimulus !== stimulusRun) return;
    }
  
    if (state.activeStimulus !== stimulusRun) return;
  
    if (getWarmupSeconds(question) > 0) {
      startWarmupPhase(stimulusRun);
      return;
    }
  
    await startActiveStimulusPhase(stimulusRun);
  }
  
  function assignStimulusSchedule(stimulusRun, { replaceIds = false } = {}) {
    if (replaceIds) {
      stimulusRun.stimulusId = createEventId(`stimulus-${stimulusRun.index}`);
      stimulusRun.startEventId = createEventId(`stimulus-${stimulusRun.index}-start`);
      stimulusRun.stopEventId = createEventId(`stimulus-${stimulusRun.index}-stop`);
      stimulusRun.timeUpEventId = overtimeEventId(stimulusRun.index, stimulusRun.question);
    }
    // Fix the monotonic schedule before network I/O. The acknowledged values are
    // later compared with the actual browser onset instead of silently moving it.
    const scheduleStartMs = performance.now();
    stimulusRun.scheduleStartPerfMs = scheduleStartMs;
    stimulusRun.plannedStartPerfMs = scheduleStartMs + getWarmupSeconds(stimulusRun.question) * 1000;
    stimulusRun.plannedDeadlinePerfMs = stimulusRun.plannedStartPerfMs
      + getActiveSeconds(stimulusRun.question) * 1000;
    // Convert once per attempt: prepare, start and stop must carry identical
    // times, even if the tablet re-syncs its clock while the card is active.
    const scheduleEpochMs = estimateServerEpochMs(scheduleStartMs);
    const toEpoch = (perfMs) => (Number.isFinite(scheduleEpochMs) ? scheduleEpochMs + (perfMs - scheduleStartMs) : null);
    stimulusRun.plannedStartEpochMs = toEpoch(stimulusRun.plannedStartPerfMs);
    stimulusRun.plannedDeadlineEpochMs = toEpoch(stimulusRun.plannedDeadlinePerfMs);
  }
  
  function stimulusPreparePayload(stimulusRun) {
    return {
      ...buildEventPayload(
        stimulusRun.index,
        stimulusRun.question,
        'stimulus_prepare',
        stimulusRun.scheduleStartPerfMs,
      ),
      event_id: stimulusRun.startEventId,
      stop_event_id: stimulusRun.stopEventId,
      stimulus_id: stimulusRun.stimulusId,
      planned_start_epoch_ms: stimulusRun.plannedStartEpochMs,
      planned_deadline_epoch_ms: stimulusRun.plannedDeadlineEpochMs,
      ...endOfTimeTrialFields(stimulusRun, stimulusRun.plannedDeadlineEpochMs),
    };
  }

  /**
   * How the card ends, read from its own fields. Without auto-advance the
   * duration is a minimum: the card stays until Next, at most `maxOvertimeMs`.
   */
  function endOfTimeSettings(question) {
    const autoAdvance = question?.auto_advance !== false;
    const maxOvertimeMs = Number(question?.overtime_max_ms);
    return {
      autoAdvance,
      keepStimulus: !autoAdvance && question?.overtime_keep_stimulus !== false,
      keepActuators: !autoAdvance && question?.overtime_keep_actuators === true,
      maxOvertimeMs: Number.isFinite(maxOvertimeMs) && maxOvertimeMs > 0 ? maxOvertimeMs : 300000,
    };
  }

  function overtimeEventId(questionIndex, question) {
    return endOfTimeSettings(question).autoAdvance
      ? ''
      : createEventId(`stimulus-${questionIndex}-time-up`);
  }

  // The actuators stop with the time-up event unless they keep running
  // through the overtime; then they stop when the card is left.
  function actuatorStopAt(question) {
    const settings = endOfTimeSettings(question);
    return !settings.autoAdvance && !settings.keepActuators ? 'time_up' : 'stop';
  }

  /**
   * Fields the server stores with the prepared trial, so its safety deadline
   * stops the actuators with the right event at the right time even when the
   * tablet goes silent. Derived from the planned deadline so prepare and
   * start always agree.
   */
  function endOfTimeTrialFields(stimulusRun, plannedDeadlineEpochMs) {
    const settings = endOfTimeSettings(stimulusRun.question);
    const stopAt = actuatorStopAt(stimulusRun.question);
    const extraMs = stopAt === 'stop' && !settings.autoAdvance ? settings.maxOvertimeMs : 0;
    return {
      time_up_event_id: stimulusRun.timeUpEventId || '',
      actuator_stop_at: stopAt,
      stop_deadline_epoch_ms: Number.isFinite(plannedDeadlineEpochMs)
        ? plannedDeadlineEpochMs + extraMs
        : null,
    };
  }

  function runCardHook(question, hookName, ...args) {
    const hook = getCardModule(question?.type)?.[hookName];
    if (typeof hook !== 'function') return;
    try {
      const result = hook(question, ...args);
      if (typeof result?.catch === 'function') {
        result.catch((error) => console.warn(`[stimulus] Card hook ${hookName} failed:`, error));
      }
    } catch (error) {
      console.warn(`[stimulus] Card hook ${hookName} failed:`, error);
    }
  }
  
  const PREPARE_CANCEL_REASONS = Object.freeze({
    retry: 'tablet_retry',
    skip: 'tablet_skip',
    abort: 'tablet_abort',
  });

  async function prepareStimulusRouting(stimulusRun) {
    while (state.activeStimulus === stimulusRun) {
      let failure = null;
      try {
        const response = await postJson(
          '/api/trial/prepare',
          stimulusPreparePayload(stimulusRun),
          { timeoutMs: TRIAL_PREPARE_TIMEOUT_MS },
        );
        const metrics = state.questionMetrics[stimulusRun.index] || {};
        state.questionMetrics[stimulusRun.index] = {
          ...metrics,
          prepare_failed: false,
          prepare_overridden: response?.overridden === true,
          prepare_server_epoch_ms: response?.server_epoch_ms || null,
        };
        return true;
      } catch (error) {
        console.error('[study] Could not prepare stimulus routing:', error);
        failure = error;
        const metrics = state.questionMetrics[stimulusRun.index] || {};
        state.questionMetrics[stimulusRun.index] = {
          ...metrics,
          prepare_failed: true,
          prepare_error: error?.message || String(error),
        };
      }

      // The failed attempt may still exist on the server (a lost response):
      // cancel it before anything else, and never resend the same attempt.
      let choice;
      let cancelled = false;
      do {
        choice = await showPrepareFailureDialog(failure);
        if (state.activeStimulus !== stimulusRun) return false;
        cancelled = await cancelStimulusPreparation(stimulusRun, PREPARE_CANCEL_REASONS[choice]);
      } while (!cancelled);

      if (choice === 'retry') {
        if (estimateServerEpochMs() === null) await syncClock();
        assignStimulusSchedule(stimulusRun, { replaceIds: true });
        sendPartialResults();
        continue;
      }

      state.activeStimulus = null;
      state.questionMetrics[stimulusRun.index] = {
        ...state.questionMetrics[stimulusRun.index],
        prepare_resolution: choice,
        completed_at: new Date().toISOString(),
      };
      updateNavigation();
      if (choice === 'skip') {
        await handleNext();
      } else {
        await abortStudyAfterPrepareFailure();
      }
      return false;
    }
    return false;
  }
  
  async function cancelStimulusPreparation(stimulusRun, reason) {
    try {
      await postJson('/api/trial/prepare/cancel', {
        event_id: stimulusRun.startEventId,
        stimulus_id: stimulusRun.stimulusId,
        reason,
      }, { timeoutMs: TRIAL_STOP_TIMEOUT_MS });
      return true;
    } catch (error) {
      showStudyNotice(
        t('study.prepareCancelFailed', 'The prepared card could not be cancelled safely. Please retry or tell the study supervisor.'),
        'error',
        10000,
      );
      return false;
    }
  }
  
  async function abortStudyAfterPrepareFailure() {
    try {
      await postJson('/api/admin/study-run/stop', {});
    } catch (error) {
      console.error('[study] Could not stop study run after prepare failure:', error);
    }
    await stopStudySensorSession();
    state.startTime = null;
    resetParticipantSessionState();
    showWaitingForAdminStart({
      title: t('study.prepareAbortedTitle', 'Study stopped'),
      body: t('study.prepareAbortedBody', 'The card was not shown. Please tell the study supervisor.'),
    });
  }
  
  function showPrepareFailureDialog(error) {
    reportNoticeToAdmin(`${t('study.prepareFailedTitle', 'Card could not be prepared')}: ${error?.message || String(error)}`, 'error');
    return new Promise((resolve) => {
      let settled = false;
      const finish = (choice) => {
        if (settled) return;
        settled = true;
        modal.destroy();
        resolve(choice);
      };
      const modal = createModal({
        title: t('study.prepareFailedTitle', 'Card could not be prepared'),
        closeLabel: t('study.prepareAbort', 'Stop study'),
        // A stray tap on the backdrop or Escape must not stop the study by
        // accident; only the three buttons below decide.
        onClose: () => { if (!settled) modal.open(); },
      });
      modal.element.querySelector('.overlay-close')?.remove();
      modal.body.innerHTML = `
        <p class="settings-hint">${escapeHtml(t('study.prepareFailedBody', 'The stimulus remains hidden so no unrecorded onset can occur.'))}</p>
        <p class="status-warning">${escapeHtml(error?.message || String(error))}</p>
        <div class="dashboard-actions">
          <button type="button" class="btn-secondary" data-prepare-abort>${escapeHtml(t('study.prepareAbort', 'Stop study'))}</button>
          <button type="button" class="btn-secondary" data-prepare-skip>${escapeHtml(t('study.prepareSkip', 'Skip card'))}</button>
          <button type="button" class="btn-primary" data-prepare-retry>${escapeHtml(t('study.prepareRetry', 'Retry'))}</button>
        </div>`;
      modal.body.querySelector('[data-prepare-abort]')?.addEventListener('click', () => finish('abort'));
      modal.body.querySelector('[data-prepare-skip]')?.addEventListener('click', () => finish('skip'));
      modal.body.querySelector('[data-prepare-retry]')?.addEventListener('click', () => finish('retry'));
      modal.open();
    });
  }
  
  function startWarmupPhase(stimulusRun) {
    const { index, question } = stimulusRun;
    const numberLabel = getElement(`warmup-num-${index}`);
  
    setStimulusPhase(index, 'warmup');
    updateNavigation();
  
    if (numberLabel) {
      numberLabel.textContent = String(getWarmupSeconds(question));
    }
  
    stimulusRun.timer = startDeadlineTimer({
      startedAtMs: performance.now(),
      deadlineMs: stimulusRun.plannedStartPerfMs,
      onTick: ({ remainingMs }) => {
        if (state.activeStimulus === stimulusRun && numberLabel) {
          numberLabel.textContent = String(remainingWholeSeconds(remainingMs));
        }
      },
      onDeadline: ({ callbackDelayMs }) => {
        if (state.activeStimulus !== stimulusRun) return;
        stimulusRun.timer = null;
        const metrics = state.questionMetrics[index] || {};
        state.questionMetrics[index] = {
          ...metrics,
          warmup_callback_delay_ms: Math.round(callbackDelayMs),
        };
        void startActiveStimulusPhase(stimulusRun);
      },
    });
  }
  
  async function startActiveStimulusPhase(stimulusRun) {
    if (state.activeStimulus !== stimulusRun || stimulusRun.activeStarted) {
      return;
    }
    stimulusRun.activeStarted = true;
  
    const { index, question } = stimulusRun;
    const ring = getElement(`ring-prog-${index}`);
    const numberLabel = getElement(`cd-num-${index}`);
    const renderRequestedMs = performance.now();
    const plannedStartPerfMs = Number.isFinite(stimulusRun.plannedStartPerfMs)
      ? stimulusRun.plannedStartPerfMs
      : renderRequestedMs;
    const plannedDeadlinePerfMs = Number.isFinite(stimulusRun.plannedDeadlinePerfMs)
      ? stimulusRun.plannedDeadlinePerfMs
      : renderRequestedMs + getActiveSeconds(question) * 1000;
    // Reuse the schedule confirmed at prepare time; only fall back to a fresh
    // conversion when this attempt was never prepared (no sensors required it).
    const plannedStartEpochMs = Number.isFinite(stimulusRun.plannedStartEpochMs)
      ? stimulusRun.plannedStartEpochMs
      : estimateServerEpochMs(plannedStartPerfMs);
    const plannedDeadlineEpochMs = Number.isFinite(stimulusRun.plannedDeadlineEpochMs)
      ? stimulusRun.plannedDeadlineEpochMs
      : estimateServerEpochMs(plannedDeadlinePerfMs);
  
    setStimulusPhase(index, 'active');
  
    const currentMetrics = state.questionMetrics[index] || {};
    state.questionMetrics[index] = {
      ...currentMetrics,
      active_started_at: new Date().toISOString(),
      render_requested_epoch_ms: estimateServerEpochMs(renderRequestedMs),
      planned_start_epoch_ms: plannedStartEpochMs,
      planned_deadline_epoch_ms: plannedDeadlineEpochMs,
      stimulus_id: stimulusRun.stimulusId,
      start_event_id: stimulusRun.startEventId,
      stop_event_id: stimulusRun.stopEventId,
    };
  
    // The visual onset is tied to the monotonic timestamp above. It does not wait
    // for a network round-trip; the command was durably prepared beforehand and is
    // queued with its original source time if the network is temporarily down.
    const contentCleanup = applyStimulusContent(index, question);
    stimulusRun.extensionCleanup = participantExtensions.startStimulus({
      stimulus: stimulusRun,
      session: getParticipantSessionContext(),
    });
    stimulusRun.contentCleanup = () => {
      stimulusRun.contentCleanup = null;
      if (typeof contentCleanup === 'function') contentCleanup();
    };
    stimulusRun.cleanup = () => {
      stimulusRun.extensionCleanup?.();
      stimulusRun.contentCleanup?.();
    };
    updateNavigation();
  
    // requestAnimationFrame's timestamp identifies the browser frame in which
    // the already-mutated stimulus DOM becomes visible. This is the scientific
    // onset sent to the server/LSL mapping; the earlier callback time remains a
    // separate scheduling diagnostic.
    const visualOnsetPerfMs = await nextVisualFrameTimestamp();
    if (state.activeStimulus !== stimulusRun) return;
    const visualOnsetEpochMs = estimateServerEpochMs(visualOnsetPerfMs);
    state.questionMetrics[index] = {
      ...state.questionMetrics[index],
      visual_onset_epoch_ms: visualOnsetEpochMs,
      visual_onset_perf_ms: visualOnsetPerfMs,
      onset_callback_delay_ms: Math.round(Math.max(0, visualOnsetPerfMs - plannedStartPerfMs)),
      onset_uncertainty_ms: Number.isFinite(state.clockRttMs) ? state.clockRttMs / 2 : null,
    };
  
    if (shouldActivateHardware(question)) {
      stimulusRun.signalStarted = true;
      void sendReliableStudyEvent('/api/start', {
          ...buildEventPayload(index, question, 'stimulus_active_start', visualOnsetPerfMs),
          event_id: stimulusRun.startEventId,
          stop_event_id: stimulusRun.stopEventId,
          stimulus_id: stimulusRun.stimulusId,
          planned_start_epoch_ms: plannedStartEpochMs,
          planned_deadline_epoch_ms: plannedDeadlineEpochMs,
          ...endOfTimeTrialFields(stimulusRun, plannedDeadlineEpochMs),
          visual_onset_epoch_ms: visualOnsetEpochMs,
          onset_uncertainty_ms: Number.isFinite(state.clockRttMs) ? state.clockRttMs / 2 : null,
          marker_event: 'stimulus_active_start',
        }, { timeoutMs: TRIAL_START_TIMEOUT_MS })
        .then((response) => {
        state.questionMetrics[index] = {
          ...state.questionMetrics[index],
          server_start_received_at: response.server_received_at || null,
          server_start_received_epoch_ms: response.server_received_epoch_ms || null,
          start_marker: response.marker_value || null,
        };
        })
        .catch((error) => console.error('[study] Could not send /api/start; event remains queued:', error));
    }
  
    if (numberLabel) {
      numberLabel.textContent = String(remainingWholeSeconds(plannedDeadlinePerfMs - performance.now()));
    }
    if (ring) {
      ring.style.strokeDashoffset = '0';
    }
  
    stimulusRun.timer = startDeadlineTimer({
      startedAtMs: plannedStartPerfMs,
      deadlineMs: plannedDeadlinePerfMs,
      onTick: ({ remainingMs, progress }) => {
        if (state.activeStimulus !== stimulusRun) return;
        if (numberLabel) numberLabel.textContent = String(remainingWholeSeconds(remainingMs));
        if (ring) ring.style.strokeDashoffset = String(314 * progress);
      },
      onDeadline: ({ callbackDelayMs }) => handleTimeUp(stimulusRun, callbackDelayMs),
    });
  }

  function handleTimeUp(stimulusRun, callbackDelayMs) {
    if (state.activeStimulus !== stimulusRun) return;
    stimulusRun.timer = null;
    const { index, question } = stimulusRun;
    state.questionMetrics[index] = {
      ...(state.questionMetrics[index] || {}),
      deadline_callback_delay_ms: Math.round(callbackDelayMs),
    };
    runCardHook(question, 'onTimeUp', index, {
      overtime: !endOfTimeSettings(question).autoAdvance,
      notify: (message, level = 'warning') => reportNoticeToAdmin(message, level),
    });
    if (endOfTimeSettings(question).autoAdvance) {
      void finishStimulusCard(stimulusRun);
      return;
    }
    enterOvertime(stimulusRun);
  }

  /**
   * The duration is reached but the participant may stay. Sensors keep
   * recording; only a marker notes the moment. Next becomes available, and
   * the card continues by itself after the maximum overtime.
   */
  function enterOvertime(stimulusRun) {
    const { index, question } = stimulusRun;
    const settings = endOfTimeSettings(question);
    const timeUpPerfMs = performance.now();
    stimulusRun.overtime = true;
    stimulusRun.overtimeStartedPerfMs = timeUpPerfMs;
    state.questionMetrics[index] = {
      ...(state.questionMetrics[index] || {}),
      time_up_at: new Date().toISOString(),
      time_up_epoch_ms: estimateServerEpochMs(timeUpPerfMs),
      time_up_event_id: stimulusRun.timeUpEventId,
    };

    if (!settings.keepStimulus) {
      stimulusRun.contentCleanup?.();
      clearStimulusContent(index);
    }

    if (stimulusRun.signalStarted && shouldActivateHardware(question)) {
      const stopsActuators = actuatorStopAt(question) === 'time_up';
      if (stopsActuators) stimulusRun.actuatorsStopped = true;
      sendStimulusEvent(stimulusRun, {
        path: stopsActuators ? '/api/stop' : '/api/marker',
        eventId: stimulusRun.timeUpEventId,
        markerEvent: 'stimulus_time_up',
        clientTriggerMs: timeUpPerfMs,
        metricsPrefix: 'time_up',
      });
    }

    stimulusRun.timer = startDeadlineTimer({
      startedAtMs: timeUpPerfMs,
      deadlineMs: timeUpPerfMs + settings.maxOvertimeMs,
      onDeadline: () => {
        if (state.activeStimulus !== stimulusRun) return;
        stimulusRun.timer = null;
        state.questionMetrics[index] = { ...state.questionMetrics[index], overtime_capped: true };
        void finishStimulusCard(stimulusRun);
      },
    });
    updateNavigation();
  }

  /**
   * Queue one trial event of this stimulus. A stop reaches the selected
   * actuators; a marker reaches the recording only. Queueing is durable, so
   * navigation never waits for the network.
   */
  function sendStimulusEvent(stimulusRun, { path, eventId, markerEvent, clientTriggerMs, metricsPrefix }) {
    const { index, question } = stimulusRun;
    void sendReliableStudyEvent(path, {
      ...buildEventPayload(index, question, markerEvent, clientTriggerMs),
      event_id: eventId,
      stimulus_id: stimulusRun.stimulusId,
      planned_deadline_epoch_ms: state.questionMetrics[index]?.planned_deadline_epoch_ms,
      marker_event: markerEvent,
    }, { timeoutMs: TRIAL_STOP_TIMEOUT_MS })
      .then((response) => {
        state.questionMetrics[index] = {
          ...state.questionMetrics[index],
          [`server_${metricsPrefix}_received_at`]: response.server_received_at || null,
          [`server_${metricsPrefix}_received_epoch_ms`]: response.server_received_epoch_ms || null,
          [`${metricsPrefix}_marker`]: response.marker_value || null,
        };
      })
      .catch((error) => console.error(`[study] Could not send ${path}; event remains queued:`, error));
  }
  
  function nextVisualFrameTimestamp() {
    return new Promise((resolve) => {
      if (typeof window.requestAnimationFrame === 'function') {
        window.requestAnimationFrame((timestamp) => resolve(timestamp));
        return;
      }
      resolve(performance.now());
    });
  }
  
  async function finishStimulusCard(stimulusRun) {
    if (state.activeStimulus !== stimulusRun) {
      return;
    }
  
    await stopActiveStimulus({ shouldSendStop: stimulusRun.signalStarted });
    await handleNext();
  }
  
  async function stopActiveStimulus({ shouldSendStop }) {
    const stimulusRun = state.activeStimulus;
    if (!stimulusRun) {
      return;
    }
  
    stimulusRun.timer?.cancel?.();
    stimulusRun.timer = null;
    closeVisibilityInterruption(stimulusRun);
    if (stimulusRun.overtime && Number.isFinite(stimulusRun.overtimeStartedPerfMs)) {
      state.questionMetrics[stimulusRun.index] = {
        ...(state.questionMetrics[stimulusRun.index] || {}),
        overtime_ms: Math.round(Math.max(0, performance.now() - stimulusRun.overtimeStartedPerfMs)),
      };
    }

    if (typeof stimulusRun.cleanup === 'function') {
      try {
        stimulusRun.cleanup();
      } catch (error) {
        console.error('[stimulus] Cleanup callback failed:', error);
      }
    }
  
    clearStimulusContent(stimulusRun.index);
  
    if (shouldSendStop && stimulusRun.signalStarted && shouldActivateHardware(stimulusRun.question)) {
      const clientTriggerMs = performance.now();
      const currentMetrics = state.questionMetrics[stimulusRun.index] || {};
      state.questionMetrics[stimulusRun.index] = {
        ...currentMetrics,
        active_ended_at: new Date().toISOString(),
        completed_at: new Date().toISOString(),
        client_stop_trigger_epoch_ms: estimateServerEpochMs(clientTriggerMs),
      };
      // Queueing is synchronous and durable. Navigation does not wait for the
      // network; the serialized event queue preserves start-before-stop order.
      // Actuators that already stopped with the time-up event get no second
      // stop: leaving the card is then a marker only.
      sendStimulusEvent(stimulusRun, {
        path: stimulusRun.actuatorsStopped ? '/api/marker' : '/api/stop',
        eventId: stimulusRun.stopEventId,
        markerEvent: 'stimulus_active_stop',
        clientTriggerMs,
        metricsPrefix: 'stop',
      });
    } else if (stimulusRun.question?.type === 'stimulus') {
      const currentMetrics = state.questionMetrics[stimulusRun.index] || {};
      state.questionMetrics[stimulusRun.index] = {
        ...currentMetrics,
        completed_at: currentMetrics.completed_at || new Date().toISOString(),
      };
    }
  
    prepareStimulusCard(stimulusRun.index, stimulusRun.question);
    state.activeStimulus = null;
    updateNavigation();
  }
  
  function prepareStimulusCard(questionIndex, question) {
    const shell = getElement(`stimulus-shell-${questionIndex}`);
    const warmupLabel = getElement(`warmup-num-${questionIndex}`);
    const activeLabel = getElement(`cd-num-${questionIndex}`);
    const ring = getElement(`ring-prog-${questionIndex}`);
  
    if (shell) {
      shell.classList.remove('stimulus-body--warmup', 'stimulus-body--active');
      shell.classList.add(getWarmupSeconds(question) > 0 ? 'stimulus-body--warmup' : 'stimulus-body--active');
    }
  
    if (warmupLabel) {
      warmupLabel.textContent = String(getWarmupSeconds(question));
    }
    if (activeLabel) {
      activeLabel.textContent = String(getActiveSeconds(question));
    }
    if (ring) {
      ring.style.strokeDashoffset = '0';
    }
  
    clearStimulusContent(questionIndex);
    setStimulusPhase(questionIndex, getWarmupSeconds(question) > 0 ? 'warmup' : 'active');
  }
  
  function setStimulusPhase(questionIndex, phase) {
    const shell = getElement(`stimulus-shell-${questionIndex}`);
    const warmupStage = getElement(`stimulus-warmup-${questionIndex}`);
    const activeStage = getElement(`stimulus-active-${questionIndex}`);
  
    if (shell) {
      shell.dataset.phase = phase;
      shell.classList.toggle('stimulus-body--warmup', phase === 'warmup');
      shell.classList.toggle('stimulus-body--active', phase === 'active');
    }
    if (warmupStage) {
      warmupStage.hidden = phase !== 'warmup';
    }
    if (activeStage) {
      activeStage.hidden = phase !== 'active';
    }
  }
  
  function clearStimulusContent(questionIndex) {
    const contentElement = getElement(`stimulus-content-${questionIndex}`);
    if (!contentElement) {
      return;
    }
  
    contentElement.querySelectorAll('video, audio').forEach((mediaElement) => {
      try {
        mediaElement.pause();
        mediaElement.removeAttribute('src');
        if (typeof mediaElement.load === 'function') {
          mediaElement.load();
        }
      } catch (error) {
        console.error('[stimulus] Could not stop media element:', error);
      }
    });
  
    contentElement.replaceChildren();
    contentElement.hidden = true;
  }
  
  function isUnsafeStimulusCodeAllowed() {
    return state.config?._capabilities?.unsafe_stimulus_code === true;
  }
  
  function showUnsafeStimulusWarning(contentElement, triggerType) {
    const warningBox = document.createElement('div');
    warningBox.className = 'stimulus-unsafe-warning';
  
    const title = document.createElement('strong');
    title.textContent = t('stimulus.unsafeBlockedTitle', '{type} stimulus blocked').replace('{type}', String(triggerType).toUpperCase());
  
    const message = document.createElement('p');
    message.textContent = t('stimulus.unsafeBlockedBody', 'This study uses executable stimulus content, but the server has not enabled unsafe stimulus code. Set STUDY_RUNNER_ALLOW_UNSAFE_STIMULUS_CODE=1 on the server to allow it intentionally.');
  
    warningBox.appendChild(title);
    warningBox.appendChild(message);
    contentElement.appendChild(warningBox);
    contentElement.hidden = false;
  }
  
  function applyStimulusContent(questionIndex, question) {
    const contentElement = getElement(`stimulus-content-${questionIndex}`);
    if (!contentElement) {
      return null;
    }
  
    clearStimulusContent(questionIndex);
  
    const cleanupCallbacks = [];
    const triggerType = question.trigger_type || 'timer';
    const triggerContent = question.trigger_content || '';
  
    if (triggerType === 'image' && triggerContent) {
      const image = document.createElement('img');
      image.src = triggerContent;
      image.className = 'stimulus-image';
      image.alt = '';
      contentElement.appendChild(image);
      contentElement.hidden = false;
    } else if (triggerType === 'video' && triggerContent) {
      const video = document.createElement('video');
      video.src = triggerContent;
      video.className = 'stimulus-video';
      video.autoplay = true;
      video.loop = true;
      video.muted = true;
      video.playsInline = true;
      contentElement.appendChild(video);
      contentElement.hidden = false;
    } else if (triggerType === 'audio' && triggerContent) {
      const audio = document.createElement('audio');
      audio.src = triggerContent;
      audio.autoplay = true;
      audio.loop = true;
      contentElement.appendChild(audio);
    } else if (triggerType === 'html' && triggerContent) {
      if (!isUnsafeStimulusCodeAllowed()) {
        showUnsafeStimulusWarning(contentElement, triggerType);
      } else {
        contentElement.innerHTML = triggerContent;
        contentElement.hidden = false;
      }
    } else if (triggerType === 'js' && triggerContent) {
      if (!isUnsafeStimulusCodeAllowed()) {
        showUnsafeStimulusWarning(contentElement, triggerType);
        return null;
      }
  
      const studyHelper = {
        call: (path, data = {}) => postJson(path, data),
        onCleanup: (callback) => {
          if (typeof callback === 'function') {
            cleanupCallbacks.push(callback);
          }
        },
      };
  
      try {
        const returnedCleanup = (new Function('study', triggerContent))(studyHelper);
        if (typeof returnedCleanup === 'function') {
          cleanupCallbacks.push(returnedCleanup);
        }
      } catch (error) {
        console.error('[stimulus] Custom JavaScript error:', error);
      }
    }
  
    return () => {
      cleanupCallbacks.forEach((callback) => {
        try {
          callback();
        } catch (error) {
          console.error('[stimulus] Custom cleanup failed:', error);
        }
      });
    };
  }
  
  function getWarmupSeconds(question) {
    return Math.max(0, Math.round((question.warmup_duration_ms || 0) / 1000));
  }
  
  function getActiveSeconds(question) {
    return Math.max(1, Math.round((question.duration_ms || 30000) / 1000));
  }
  

  return {
    getActiveSeconds,
    getWarmupSeconds,
    prepareStimulusCard,
    startStimulusCard,
    stopActiveStimulus,
  };
}

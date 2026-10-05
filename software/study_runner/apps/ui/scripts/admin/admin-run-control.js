/** Run gating, readiness, and active-run controls for the admin UI. */
export function createAdminRunControl(context) {
  const {
    state,
    getJson,
    postJson,
    $,
    t,
    setText,
    setHidden,
    escapeHtml,
    pluginByKey,
    openStudySettingsPanel,
    byId,
    createModal,
    applyFonts,
    renderGroupLogo,
    loadBranding,
    getCurrentStudyName,
    confirmWithModal,
    saveConfig,
    showToast,
    switchView,
    constants,
  } = context;
  const { studyRunPollIntervalMs: STUDY_RUN_POLL_INTERVAL_MS } = constants;

  async function loadStudyRunState(options = {}) {
    try {
      const response = await getJson('/api/admin/study-run');
      state.studyRunState = response?.run_state || null;
      state.tabletGate = response?.tablet_gate || null;
      renderStudyRunState();
      return state.studyRunState;
    } catch (error) {
      if (!options.silent) {
        console.error('[admin] Could not load study run state:', error);
      }
      renderStudyRunState();
      return null;
    }
  }
  
  function startStudyRunPolling() {
    if (state.studyRunPollTimer !== null) {
      window.clearInterval(state.studyRunPollTimer);
    }
    state.studyRunPollTimer = window.setInterval(() => {
      if (!document.hidden) {
        void loadStudyRunState({ silent: true });
      }
    }, STUDY_RUN_POLL_INTERVAL_MS);
  }
  
  /**
   * Load the pre-run check for the loaded study.
   *
   * Without it a study imported from another computer runs happily and only
   * fails to upload afterwards, into the retry queue, long after the participant
   * has left. Cheap and purely config-based, so it can run after every save.
   */
  async function loadStudyReadiness() {
    try {
      state.readiness = await getJson('/api/admin/study-readiness', { timeoutMs: 2000 });
    } catch (error) {
      // Never let a failed check block the operator; just show no warning.
      console.debug('[admin] Could not load study readiness:', error);
      state.readiness = null;
    }
    renderStudyRunState();
  }
  
  /**
   * Keep pre-run failures visible until the operator fixes them. The marker is
   * still useful as a compact cue, while the panel carries the full explanation.
   */
  function currentReadinessBlockers() {
    return state.readiness?.ready === false ? (state.readiness.blockers || []) : [];
  }
  
  function renderReadinessCta() {
    const blockers = currentReadinessBlockers();
    const blocking = blockers.some((blocker) => blocker.blocking === true);
    const marker = $('hub-readiness-marker');
    if (marker) {
      marker.hidden = blockers.length === 0;
      marker.title = blockers.length ? readinessSummary(blockers, { includeDetails: true }) : '';
    }
  
    const panel = $('hub-study-readiness');
    if (panel) {
      panel.hidden = blockers.length === 0;
      panel.classList.toggle('is-blocking', blocking);
    }
    setText(
      'hub-study-readiness-title',
      blocking
        ? t('readiness.blockedTitle', 'Study cannot start')
        : t('readiness.warningTitle', 'Study needs attention'),
    );
    const list = $('hub-study-readiness-list');
    if (list) list.innerHTML = readinessListMarkup(blockers);
  
    // Edit is the way to every fix, so it carries the call to action on the hub.
    $('btn-hub-editor')?.classList.toggle('is-cta', blockers.length > 0);
    // Inside the editor, the study settings button is the next step.
    $('btn-study-settings')?.classList.toggle('is-cta', blockers.length > 0);
  }
  
  function readinessDetails(blocker) {
    const message = readinessMessage(blocker);
    return (Array.isArray(blocker?.details) ? blocker.details : [])
      .map((detail) => String(detail || '').trim())
      .filter((detail) => detail && detail !== message);
  }
  
  function readinessListMarkup(blockers, className = 'study-readiness-list-item') {
    return blockers.map((blocker) => {
      const details = readinessDetails(blocker);
      const detailMarkup = details.length
        ? `<ul class="study-readiness-details">${details.map((detail) => `<li>${escapeHtml(detail)}</li>`).join('')}</ul>`
        : '';
      return `<li class="${className}"><strong>${escapeHtml(readinessMessage(blocker))}</strong>${detailMarkup}</li>`;
    }).join('');
  }
  
  function readinessSummary(blockers, { includeDetails = false } = {}) {
    return blockers.map((blocker) => {
      const lines = [readinessMessage(blocker)];
      if (includeDetails) lines.push(...readinessDetails(blocker));
      return lines.join('\n');
    }).join('\n\n');
  }
  
  function readinessMessage(blocker) {
    const pluginKey = blocker.plugin || blocker.sensor || blocker.destination || '';
    const pluginLabel = pluginByKey(pluginKey)?.ui?.label || pluginKey;
    const sensorLabel = blocker.sensor ? pluginLabel : '';
    const supportedModes = Array.isArray(blocker.supported_modes) ? blocker.supported_modes.join(', ') : '';
    const messages = {
      plugin_unavailable: t('readiness.pluginUnavailable', '{plugin} is referenced by this study but is not installed.')
        .replace('{plugin}', pluginLabel),
      sensor_machine_disabled: t('readiness.sensorMachineDisabled', '{sensor} is used by this study but switched off on this computer.').replace('{sensor}', sensorLabel),
      browser_source_requires_https: t('readiness.browserSourceRequiresHttps', 'A selected browser sensor needs a secure HTTPS connection, which is currently off.'),
      plugin_mode_unsupported: t(
        'readiness.pluginModeUnsupported',
        '{sensor} mode {mode} is unavailable on {platform}. Supported: {supported}.',
      )
        .replace('{sensor}', sensorLabel || blocker.plugin || '')
        .replace('{mode}', blocker.mode || '')
        .replace('{platform}', blocker.platform || '')
        .replace('{supported}', supportedModes),
      recording_capacity_insufficient: t(
        'readiness.recordingCapacityInsufficient',
        'Recording storage cannot be confirmed. Set a planned session duration and check the target disk.',
      ),
      recording_clock_implausible: t(
        'readiness.recordingClockImplausible',
        'The system clock or local time service is not ready for recording.',
      ),
      recording_worker_unavailable: t(
        'readiness.recordingWorkerUnavailable',
        'The recording module is not installed or unavailable.',
      ),
    };
    if (String(blocker.code || '').endsWith('.credential_missing')) {
      return t('readiness.pluginCredentialMissing', '{plugin} is enabled, but no credential is available for this study.')
        .replace('{plugin}', pluginLabel);
    }
    if (String(blocker.code || '').endsWith('.setting_missing')) {
      return t('readiness.pluginSettingMissing', '{plugin} is enabled, but a required destination setting is missing.')
        .replace('{plugin}', pluginLabel);
    }
    if (String(blocker.code || '').endsWith('.machine_disabled')) {
      return t('readiness.pluginMachineDisabled', '{plugin} is enabled for this study, but switched off on this computer.')
        .replace('{plugin}', pluginLabel);
    }
    return messages[blocker.code] || blocker.code;
  }
  
  function readinessSettingsPanel(blockers) {
    const blocker = blockers.find((entry) => entry.blocking === true) || blockers[0] || {};
    if (blocker.destination) return 'destinations';
    return ['sensors', 'participant', 'destinations', 'export'].includes(blocker.panel)
      ? blocker.panel
      : 'sensors';
  }
  
  async function openReadinessSettings(blockers = currentReadinessBlockers()) {
    if (!blockers.length) return;
    await openStudySettingsPanel(readinessSettingsPanel(blockers));
    if (blockers.some((blocker) => blocker.code === 'recording_capacity_insufficient')) {
      byId('study-planned-duration')?.focus();
    }
  }
  
  function showBlockingReadinessDialog(blockers) {
    return new Promise((resolve) => {
      let settled = false;
      let modal;
      const finish = (action) => {
        if (settled) return;
        settled = true;
        modal.destroy();
        resolve(action);
      };
      modal = createModal({
        title: t('readiness.blockedTitle', 'Study cannot start'),
        closeLabel: t('readiness.close', 'Close'),
        onClose: () => finish('close'),
      });
      modal.body.innerHTML = `
        <p class="settings-hint">${escapeHtml(t('readiness.blockedBody', 'Start is blocked until every required plugin and the recording infrastructure are ready.'))}</p>
        <ul class="readiness-dialog-list">${readinessListMarkup(blockers, 'readiness-dialog-list-item')}</ul>
        <div class="dashboard-actions confirm-modal-actions">
          <button type="button" class="btn-secondary" data-readiness-close>${escapeHtml(t('readiness.close', 'Close'))}</button>
          <button type="button" class="btn-primary" data-readiness-settings>${escapeHtml(t('readiness.openSettings', 'Open study settings'))}</button>
        </div>`;
      modal.body.querySelector('[data-readiness-close]')?.addEventListener('click', () => finish('close'));
      modal.body.querySelector('[data-readiness-settings]')?.addEventListener('click', () => finish('settings'));
      modal.open();
      modal.body.querySelector('[data-readiness-settings]')?.focus();
    }).then(async (action) => {
      if (action === 'settings') await openReadinessSettings(blockers);
    });
  }
  
  async function applyHubBranding() {
    void applyFonts();
    renderGroupLogo($('hub-brand-logo'), await loadBranding());
  }
  
  function renderStudyRunState() {
    const runState = state.studyRunState || {};
    const tabletGate = state.tabletGate || {};
    const status = runState.status || 'loaded';
    const label = $('hub-active-label');
    const hint = $('hub-active-run-hint');
    const startButton = $('btn-hub-start-study');
    const startLabel = $('btn-hub-start-study-label');
    const abortButton = $('btn-hub-abort-study');
    const dashboardButton = $('btn-admin-dashboard');
  
    if (label) {
      label.removeAttribute('data-i18n');
      label.textContent = runStatusLabel(status);
    }
    if (hint) {
      hint.removeAttribute('data-i18n');
      hint.textContent = runStatusHint(status, runState);
    }
    if (startButton) {
      const running = status === 'running' || status === 'aborting';
      const gateBlocksStart = status !== 'running' && tabletGate.can_start !== true;
      // Keep Play visible so a click can explain the blocker. The backend is the
      // authoritative gate for required plugins and recording infrastructure.
      const notReady = status !== 'running' && state.readiness?.ready === false;
      startButton.disabled = running || !getCurrentStudyName() || gateBlocksStart;
      startButton.classList.toggle('is-running', running);
      startButton.classList.toggle('is-blocked', gateBlocksStart || notReady);
    }
    // Only while a study is actually running is there a live recording to end
    // -- offering it any earlier would have nothing to target.
    if (abortButton) {
      abortButton.hidden = status !== 'running' && status !== 'aborting';
    }
    renderReadinessCta();
    if (startLabel) {
      startLabel.textContent = status === 'running'
        ? t('hub.runRunning', 'Running')
        : t('hub.startStudy', 'Start study');
    }
    // The dashboard is reachable at any time on purpose: sensors can be started
    // and tested from it before pressing Play, which is where setup problems are
    // actually fixed. Only the run-specific readouts idle until a study runs.
    if (dashboardButton) {
      dashboardButton.hidden = false;
    }
  }
  
  function runStatusLabel(status) {
    if (status === 'running') return t('hub.runStatus.running', 'RUNNING');
    if (status === 'completed') return t('hub.runStatus.completed', 'COMPLETED');
    if (status === 'aborted') return t('hub.runStatus.aborted', 'ABORTED');
    if (status === 'aborting') return t('hub.runStatus.aborting', 'STOPPING');
    if (status === 'stopped') return t('hub.runStatus.stopped', 'STOPPED');
    return t('hub.runStatus.loaded', 'LOADED');
  }
  
  function runStatusHint(status, runState) {
    const gateHint = tabletGateHint(state.tabletGate);
    if (status === 'running') {
      const startError = runState?.last_start_error?.message || '';
      if (startError) {
        return t('hub.runHint.startFailed', 'The tablet could not start the study: {reason}').replace('{reason}', startError);
      }
      return gateHint || t('hub.runHint.running', 'The participant tablet can enter the study now.');
    }
    if (status === 'completed') {
      return gateHint || t('hub.runHint.completed', 'The last run was saved. Start again when the tablet should continue.');
    }
    if (status === 'aborted') {
      const reason = runState?.aborted_reason || '';
      return reason
        ? t('hub.runHint.aborted', 'Aborted: {reason}').replace('{reason}', reason)
        : t('hub.runHint.abortedNoReason', 'Aborted. The tablet waits for the next start.');
    }
    if (status === 'aborting') {
      const error = runState?.abort_error || '';
      return error
        ? t('hub.runHint.abortFailed', 'Stop not confirmed: {reason}. Retry abort.').replace('{reason}', error)
        : t('hub.runHint.aborting', 'Stopping stimulus and recording. Do not start another run yet.');
    }
    if (status === 'stopped') {
      return gateHint || t('hub.runHint.stopped', 'The run was stopped. The tablet waits for the next start.');
    }
    if (runState?.study_id) {
      return gateHint || t('hub.runHint.loaded', 'Loaded on the tablet as a waiting room until you press Play.');
    }
    return t('hub.runHint.empty', 'Load a study, then press Play when the tablet is ready.');
  }
  
  function tabletGateHint(tabletGate) {
    const status = tabletGate?.status || '';
    if (status === 'ready') {
      return t('hub.tabletGate.ready', 'One tablet is waiting. Press Play to start it.');
    }
    if (status === 'waiting_for_tablet') {
      return t('hub.tabletGate.waiting', 'Open the participant page on one tablet before pressing Play.');
    }
    if (status === 'conflict') {
      return t('hub.tabletGate.conflict', 'More than one tablet is connected. Keep only the tablet that should run this study.');
    }
    if (status === 'assigned_missing') {
      return t('hub.tabletGate.assignedMissing', 'The assigned tablet is no longer visible. Stop or reload before starting again.');
    }
    return '';
  }
  
  /**
   * Play, from the editor.
   *
   * Starting a run from the editor is a different act than starting it from the
   * hub: the operator is mid-edit and the tablet is about to be handed over, so
   * it asks first, saves whatever is unsaved, and then leaves the editor for the
   * dashboard - which is where a running study is actually watched.
   */
  async function confirmAndStartFromEditor() {
    const proceed = await confirmWithModal({
      title: getCurrentStudyName(),
      message: t('workspace.startConfirm', 'The tablet can join as soon as the study is running. Unsaved changes are saved first.'),
      confirmLabel: t('workspace.startConfirmAction', 'Start study'),
      cancelLabel: t('common.cancel', 'Cancel'),
    });
    if (!proceed) return;
    await startLoadedStudyRun({ buttonId: 'btn-workspace-start', goToDashboard: true });
  }
  
  /**
   * Start the loaded study.
   *
   * `buttonId` because two controls do this now - the hub's start button and the
   * editor's play button - and the spinner belongs on whichever one was pressed.
   * `then` decides where the operator lands afterwards.
   */
  async function startLoadedStudyRun({ buttonId = 'btn-hub-start-study', goToDashboard = false } = {}) {
    if (state.readiness?.start_blocked === true) {
      const blockers = state.readiness.blockers || [];
      await showBlockingReadinessDialog(blockers);
      return;
    }
  
    // Destination warnings are overridable because the local scientific commit
    // remains possible and publishing can be retried after the session.
    if (state.readiness?.ready === false) {
      const blockers = state.readiness.blockers || [];
      const message = `${t('readiness.confirmTitle', 'This study is not fully set up:')}\n\n`
        + `${readinessSummary(blockers)}\n\n`
        + t('readiness.confirmBody', 'Measurements are saved locally either way, but the uploads listed above will fail. Start anyway?');
      const proceed = await confirmWithModal({
        title: t('readiness.confirmTitle', 'This study is not fully set up:'),
        message,
        confirmLabel: t('readiness.confirmStart', 'Start anyway'),
        cancelLabel: t('common.cancel', 'Cancel'),
      });
      if (!proceed) return;
    }
  
    const button = $(buttonId);
    const previousHtml = button?.innerHTML || '';
    if (button) {
      button.disabled = true;
      button.innerHTML = `<i class="iconoir-refresh"></i><span>${escapeHtml(t('hub.startingStudy', 'Starting...'))}</span>`;
    }
  
    try {
      if ($('btn-save-config')?.classList.contains('btn-primary--dirty')) {
        const saved = await saveConfig({ skipToast: true });
        if (saved === false) return;
      }
      const response = await postStartStudyRun();
      if (!response) return;
      state.studyRunState = response?.run_state || null;
      state.tabletGate = response?.tablet_gate || state.tabletGate;
      renderStudyRunState();
      showToast(t('toast.studyStarted', 'Study started'), 'success');
      if (goToDashboard) await switchView('view-dashboard');
    } catch (error) {
      console.error('[admin] Could not start study run:', error);
      if (error.status === 409 && error.payload?.readiness) {
        state.readiness = error.payload.readiness;
        renderStudyRunState();
        if (state.readiness.start_blocked === true) {
          await showBlockingReadinessDialog(state.readiness.blockers || []);
          return;
        }
      }
      showToast(error.message || t('toast.studyStartFailed', 'Could not start the study'), 'error');
    } finally {
      if (button) {
        button.innerHTML = previousHtml;
        renderStudyRunState();
      }
    }
  }
  
  // Play asks the server first; when a sensor the study needs is not live the
  // server answers 409 with the list, and the admin decides whether to start
  // anyway (runtime_core/studies/live_sensor_readiness.py). Returns null when
  // the admin cancels.
  async function postStartStudyRun() {
    try {
      return await postJson('/api/admin/study-run/start', {}, { timeoutMs: 4000 });
    } catch (error) {
      const issues = error.status === 409 ? error.payload?.live_issues : null;
      if (!Array.isArray(issues) || !issues.length) throw error;
      const lines = issues.map((issue) => {
        // Sensors with the shared connection pattern say what to do next.
        if (issue.next_step) {
          const phase = t(`sensorConnection.phase.${issue.status}`, String(issue.status || '').replace(/_/g, ' '));
          return `• ${issue.label}: ${phase} – ${t(`sensorConnection.nextStep.${issue.next_step}`, issue.problem || issue.next_step)}`;
        }
        const status = t(`dashboard.status.${issue.status}`, String(issue.status || '').replace(/_/g, ' '));
        return `• ${issue.label}: ${status}${issue.problem ? ` – ${issue.problem}` : ''}`;
      });
      const proceed = await confirmWithModal({
        title: t('liveCheck.titleNotReady', 'Sensors are not ready'),
        message: [
          ...lines,
          '',
          t('liveCheck.body', 'Start and check these sensors on the dashboard first. Without them the session cannot record their data.'),
        ].join('\n'),
        confirmLabel: t('liveCheck.startAnyway', 'Start anyway'),
        cancelLabel: t('common.cancel', 'Cancel'),
      });
      if (!proceed) return null;
      return postJson('/api/admin/study-run/start', { override_live_check: true }, { timeoutMs: 4000 });
    }
  }
  
  // Ends the currently recording session on the admin's word: freezes the
  // recording (nothing captured is deleted, unlike Withdraw consent on a
  // completed session), then marks the run aborted with the operator's
  // reason. Built with createModal() directly, like sessions-browser.js's
  // withdrawal modal, because a plain yes/no confirm cannot gate a button on
  // typed input -- an abort with no stated reason is exactly the kind of
  // silent "something happened" this project avoids elsewhere.
  async function openAbortStudyModal() {
    const modal = createModal({
      title: t('hub.abort.title', 'Abort study'),
      closeLabel: t('common.cancel', 'Cancel'),
    });
  
    modal.body.innerHTML = `
      <p class="settings-hint">${escapeHtml(
        t(
          'hub.abort.warning',
          'This ends the current recording right away. Everything captured so far is kept, but the session stops here.',
        ),
      )}</p>
      <div class="field">
        <label for="abort-reason-input">${escapeHtml(t('hub.abort.reasonLabel', 'Reason'))}</label>
        <textarea id="abort-reason-input" rows="3"></textarea>
      </div>
      <p class="settings-hint" id="abort-error" hidden></p>
      <div class="dashboard-actions confirm-modal-actions">
        <button type="button" class="btn-secondary" data-abort-cancel>${escapeHtml(t('common.cancel', 'Cancel'))}</button>
        <button type="button" class="btn-primary btn-primary--danger" data-abort-confirm disabled>
          ${escapeHtml(t('hub.abort.confirmButton', 'Abort study'))}
        </button>
      </div>
    `;
  
    const input = modal.body.querySelector('#abort-reason-input');
    const confirmButton = modal.body.querySelector('[data-abort-confirm]');
    const errorText = modal.body.querySelector('#abort-error');
  
    input.addEventListener('input', () => {
      confirmButton.disabled = input.value.trim() === '';
    });
    modal.body.querySelector('[data-abort-cancel]').addEventListener('click', () => modal.destroy());
  
    confirmButton.addEventListener('click', async () => {
      confirmButton.disabled = true;
      setHidden('abort-error', true);
      try {
        const response = await postJson('/api/admin/study-run/abort', { reason: input.value.trim() });
        modal.destroy();
        state.studyRunState = response?.run_state || null;
        renderStudyRunState();
        showToast(t('hub.abort.done', 'Study aborted.'), 'success');
      } catch (error) {
        console.error('[admin] Abort failed:', error);
        errorText.textContent = error.message || t('hub.abort.failed', 'Could not abort the study.');
        setHidden('abort-error', false);
        confirmButton.disabled = input.value.trim() === '';
      }
    });
  
    modal.open();
    input.focus();
  }
  
  /**
   * The single funnel for every admin view change.
   *
   * Everything that opens a view goes through here - hub buttons, the settings
   * controllers, the session browser - so wrapping it in the sweep is what gives
   * every one of them the same transition. `onCovered` runs while the screen is
   * opaque, which is where a view's first data load belongs.
   *
   * `animate: false` is for programmatic switches that are not a user navigation
   * (a poll-driven correction, for example) - sweeping those would flash the
   * screen white for no reason.
   */

  return {
    applyHubBranding,
    confirmAndStartFromEditor,
    currentReadinessBlockers,
    loadStudyReadiness,
    loadStudyRunState,
    openAbortStudyModal,
    openReadinessSettings,
    renderStudyRunState,
    startLoadedStudyRun,
    startStudyRunPolling,
  };
}

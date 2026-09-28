/**
 * The one connection panel every sensor tile shows.
 *
 * The server standardizes each sensor's status into a `connection` block
 * (phase, device, candidates, signal, setup, streaming) and decides `ready`
 * and `next_step` in the core (plugin_framework/sensor_connection.py). This
 * module only draws it, the same way for every sensor:
 *
 *   [status pill] Connected – electrode contact good · Calibration done [Ready]   [switch] [restart]
 *   [device select ...................] [Search] [Measure contact] [Initialize]
 *
 * The button for `next_step` is the call to action; buttons that cannot help
 * right now are disabled with the reason as tooltip. Choosing a device in the
 * list connects immediately, so there is no separate Connect button.
 */
import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';

export const STEP_ROLES = ['scan', 'measure_signal', 'initialize'];
const ROLE_ICONS = {
  scan: 'iconoir-search',
  measure_signal: 'iconoir-activity',
  initialize: 'iconoir-play',
};
const PHASE_FALLBACKS = {
  off: 'Switched off',
  no_device: 'No device known yet',
  searching: 'Searching …',
  selection_required: 'Several found – choose one',
  connecting: 'Connecting …',
  connected: 'Connected',
  reconnecting: 'Connection lost – reconnecting …',
  failed: 'Connection failed',
};
const SIGNAL_FALLBACKS = {
  good: 'good',
  fair: 'fair',
  poor: 'poor',
  measuring: 'being measured …',
  stale: 'measure again for this participant',
  unknown: 'not measured yet',
};
const SETUP_FALLBACKS = {
  needed: 'needed',
  running: 'running',
  done: 'done',
  stalled: 'stalled – please repeat',
};
// Pill colours reuse the existing status palette.
const PHASE_PILL = {
  off: 'stopped',
  no_device: 'waiting',
  searching: 'starting',
  selection_required: 'waiting',
  connecting: 'starting',
  connected: 'connected',
  reconnecting: 'stale',
  failed: 'failed',
};

/** `{role: action}` for the actions a manifest gives a connection role. */
export function roleActions(manifest) {
  const actions = manifest?.capability_config?.admin_actions?.actions || [];
  const roles = {};
  actions.forEach((action) => {
    if (action?.role && !roles[action.role]) roles[action.role] = action;
  });
  return roles;
}

export function connectionConfig(manifest) {
  return manifest?.capability_config?.connection || {};
}

/** "sensorConnection.signal.electrodeContact" -> "Electrode contact" when no translation is loaded. */
function keyFallback(key, generic) {
  const last = String(key || '').split('.').pop() || '';
  const words = last.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/[_-]+/g, ' ').trim().toLowerCase();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : generic;
}

function nounText(config, suffix, fallback) {
  const key = config.device_noun_key;
  return key ? t(suffix ? `${key}.${suffix}` : key, fallback) : fallback;
}

/** The status sentence: "Connected – electrode contact good · calibration done". */
export function connectionStatusText(connection, manifest) {
  const config = connectionConfig(manifest);
  const roles = roleActions(manifest);
  const phase = connection?.phase || 'connecting';
  const parts = [];
  if (phase === 'no_device') {
    parts.push(nounText(config, 'noneKnown', t('sensorConnection.phase.no_device', PHASE_FALLBACKS.no_device)));
  } else {
    parts.push(t(`sensorConnection.phase.${phase}`, PHASE_FALLBACKS[phase] || phase));
  }
  if (phase === 'connected') {
    const signal = connection.signal || {};
    const state = signal.state || 'unknown';
    const signalKey = config.signal_label_key || 'sensorConnection.signal.signal';
    const signalLabel = t(signalKey, keyFallback(signalKey, 'Signal'));
    if (signal.detail) {
      parts.push(t(`sensorConnection.signalDetail.${signal.detail}`, String(signal.detail).replace(/_/g, ' ')));
    } else if (state !== 'unknown' || roles.measure_signal) {
      const channels = Array.isArray(signal.channels) && signal.channels.length ? ` (${signal.channels.join(', ')})` : '';
      parts.push(`${signalLabel} ${t(`sensorConnection.signalState.${state}`, SIGNAL_FALLBACKS[state] || state)}${channels}`);
    }
    const setup = connection.setup || {};
    if (setup.state && setup.state !== 'not_needed') {
      const setupKey = config.setup_label_key || 'sensorConnection.setup.setup';
      const setupLabel = t(setupKey, keyFallback(setupKey, 'Setup'));
      const progress = setup.state === 'running' && Number.isFinite(setup.progress_percent)
        ? ` ${Math.round(setup.progress_percent)} %`
        : '';
      parts.push(`${setupLabel} ${t(`sensorConnection.setupState.${setup.state}`, SETUP_FALLBACKS[setup.state] || setup.state)}${progress}`);
    }
  }
  return parts;
}

/** Why a step button is disabled right now, or '' when it can be used. */
export function stepBlockReason(role, connection, { locked = false, pending = false } = {}) {
  if (locked) return t('sensorConnection.blocked.locked', 'Locked while a participant session is recording.');
  if (pending) return t('sensorConnection.blocked.pending', 'An action is still running.');
  const phase = connection?.phase || 'connecting';
  if (phase === 'off') return t('sensorConnection.blocked.off', 'Switch the sensor on first.');
  if (role === 'scan') {
    return ['searching', 'connecting'].includes(phase)
      ? t('sensorConnection.blocked.busy', 'Wait until the current search or connection finishes.')
      : '';
  }
  if (phase !== 'connected' || !connection.streaming) {
    return t('sensorConnection.blocked.notConnected', 'Connect the device first.');
  }
  const signal = connection.signal?.state || 'unknown';
  if (role === 'measure_signal') {
    return signal === 'measuring' ? t('sensorConnection.blocked.measuring', 'The measurement is running.') : '';
  }
  if (role === 'initialize') {
    if (signal === 'measuring') return t('sensorConnection.blocked.measuring', 'The measurement is running.');
    if (['poor', 'stale', 'unknown'].includes(signal) && connection.next_step === 'measure_signal') {
      return t('sensorConnection.blocked.signalFirst', 'Measure the signal first and make sure it is good.');
    }
    if (connection.setup?.state === 'running') return t('sensorConnection.blocked.setupRunning', 'The initialization is running.');
  }
  return '';
}

function renderDeviceSelect(plugin, manifest, connection, selectAction, disabled) {
  const config = connectionConfig(manifest);
  const candidates = Array.isArray(connection.candidates) ? connection.candidates : [];
  const device = connection.device;
  const known = new Set(candidates.map((candidate) => candidate.id));
  const connectedSuffix = connection.phase === 'connected' ? t('sensorConnection.deviceConnected', 'connected') : '';
  const options = [];
  if (device?.id && !known.has(device.id)) {
    options.push(`<option value="" data-option-key="${escapeHtml(device.id)}" selected>${escapeHtml(device.label)}${connectedSuffix ? ` (${escapeHtml(connectedSuffix)})` : ''}</option>`);
  }
  candidates.forEach((candidate) => {
    const selected = device?.id && candidate.id === device.id ? 'selected' : '';
    options.push(`<option value="${escapeHtml(JSON.stringify(candidate.payload || {}))}" data-option-key="${escapeHtml(candidate.id)}" ${selected}>${escapeHtml(candidate.label)}</option>`);
  });
  const placeholder = candidates.length
    ? t('sensorConnection.choose', 'Choose a device')
    : nounText(config, 'searchPrompt', t('sensorConnection.searchPrompt', 'Please search for devices'));
  const cta = connection.next_step === 'select' ? ' is-cta' : '';
  return `<select class="dashboard-select sensor-connection-select${cta}" id="sensor-connection-${escapeHtml(plugin.key)}-select"
      data-connection-select data-plugin-key="${escapeHtml(plugin.key)}" data-plugin-admin-action="${escapeHtml(selectAction.key)}"
      aria-label="${escapeHtml(t(`plugins.${plugin.key}.actions.${selectAction.key}`, selectAction.label))}" ${disabled ? 'disabled data-blocked' : ''}>
      ${device?.id ? '' : `<option value="" selected disabled>${escapeHtml(placeholder)}</option>`}${options.join('')}
    </select>`;
}

function renderStepButton(plugin, role, action, connection, state) {
  const reason = stepBlockReason(role, connection, state);
  const label = t(`plugins.${plugin.key}.actions.${action.key}`, action.label || role);
  const isCta = connection.next_step === role && !reason;
  const title = reason || action.description || label;
  return `<button type="button" class="${isCta ? 'btn-primary' : 'btn-secondary'} sensor-connection-step"
      data-connection-action="${escapeHtml(role)}" data-plugin-key="${escapeHtml(plugin.key)}"
      data-plugin-admin-action="${escapeHtml(action.key)}" ${reason ? 'disabled data-blocked' : ''}
      title="${escapeHtml(title)}" aria-label="${escapeHtml(label)}">
      <i class="${ROLE_ICONS[role] || 'iconoir-flash'}"></i><span class="sensor-connection-step-label">${escapeHtml(label)}</span>
    </button>`;
}

function renderSwitch(plugin, { pendingRunning } = {}) {
  const running = pendingRunning ?? Boolean(plugin.running);
  const busy = pendingRunning !== undefined;
  const canSwitch = Boolean(plugin.can_start || plugin.can_stop);
  const label = running
    ? t('sensorConnection.switchOff', 'Switch off')
    : t('sensorConnection.switchOn', 'Switch on');
  const restartLabel = t('dashboard.action.restart', 'Restart');
  return `<div class="dashboard-status-row-actions">
      <label class="dashboard-toggle" title="${escapeHtml(label)}">
        <span class="switch"><input type="checkbox" data-runtime-toggle="${escapeHtml(plugin.key)}" ${running ? 'checked' : ''} ${canSwitch && !busy ? '' : 'disabled'} aria-label="${escapeHtml(label)}"><span class="switch-slider"></span></span>
      </label>
      ${plugin.can_restart ? `<button type="button" class="btn-icon-only is-danger" data-dashboard-action="runtime_${escapeHtml(plugin.key)}_restart"
          ${running && !busy ? '' : 'disabled'} title="${escapeHtml(restartLabel)}" aria-label="${escapeHtml(restartLabel)}"><i class="iconoir-refresh"></i></button>` : ''}
    </div>`;
}

/**
 * @param plugin   the plugin status (standardized, with `connection`, `running`)
 * @param manifest the plugin manifest
 * @param state    {locked, pending, pendingRunning, deviatesFromStudy}
 */
export function renderSensorConnectionPanel(plugin, manifest, state = {}) {
  const connection = plugin?.connection;
  if (!connection) return '';
  const roles = roleActions(manifest);
  const phase = connection.phase || 'connecting';
  const pill = PHASE_PILL[phase] || 'unknown';
  const statusParts = connectionStatusText(connection, manifest);
  const readyPill = connection.ready
    ? `<span class="status-pill status-pill--ready"><i class="iconoir-check"></i> ${escapeHtml(t('sensorConnection.ready', 'Ready'))}</span>`
    : '';
  const stepRoles = STEP_ROLES.filter((role) => roles[role]);
  const hasSteps = Boolean(roles.select || stepRoles.length);
  const disabledSelect = state.locked || state.pending || phase === 'off' || ['searching', 'connecting'].includes(phase);
  const steps = hasSteps ? `
    <div class="sensor-connection-steps">
      ${roles.select ? renderDeviceSelect(plugin, manifest, connection, roles.select, disabledSelect) : '<span class="sensor-connection-device">'
        + escapeHtml(connection.device?.label || '') + '</span>'}
      ${stepRoles.map((role) => renderStepButton(plugin, role, roles[role], connection, state)).join('')}
    </div>` : '';
  const deviation = state.deviatesFromStudy
    ? `<p class="sensor-connection-note">${escapeHtml(t('sensorConnection.deviation', 'Differs from the loaded study until the study is loaded again.'))}
        <button type="button" class="btn-secondary btn-xs" data-dashboard-action="reset_sensor_overrides">${escapeHtml(t('dashboard.overrideReset', 'Reset to study settings'))}</button></p>`
    : '';
  return `<div class="sensor-connection" data-plugin-dashboard-controls data-sensor-connection="${escapeHtml(plugin.key)}" data-phase="${escapeHtml(phase)}">
      <div class="sensor-connection-status">
        <span class="status-pill status-pill--${escapeHtml(pill)}">${escapeHtml(statusParts[0] || '')}</span>
        <span class="sensor-connection-text" role="status">${escapeHtml(statusParts.slice(1).join(' · '))}</span>
        ${readyPill}
        ${renderSwitch(plugin, state)}
      </div>
      ${steps}
      ${deviation}
    </div>`;
}

/**
 * What the dashboard's study bar says: which sensors the loaded study needs,
 * which are ready, and whether the tablet is connected. Start becomes the
 * call to action once nothing is missing.
 */
export function studyBarState(status) {
  const runState = status?.study_run_state || {};
  const plugins = status?.plugins || {};
  const effective = status?.sensor_runtime?.effective || {};
  const needed = Object.keys(effective).filter((key) => effective[key] && plugins[key]?.connection);
  const ready = needed.filter((key) => plugins[key].connection.ready);
  const missing = needed.filter((key) => !plugins[key].connection.ready);
  const tabletOk = status?.study_clients?.single_tablet?.can_start === true;
  const running = runState.status === 'running';
  return { runState, needed, ready, missing, tabletOk, running, plugins };
}

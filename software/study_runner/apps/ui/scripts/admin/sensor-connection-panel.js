/**
 * The one connection panel every sensor tile shows.
 *
 * The server standardizes each sensor's status into a `connection` block
 * (phase, device, candidates, signal, setup, streaming, auto_reconnect) and
 * decides `ready` and `next_step` in the core
 * (plugin_framework/sensor_connection.py). This module only draws it, the
 * same way for every sensor:
 *
 *   [● Connected / Ready ✓]                                   [ Off | On | Restart ]
 *   [device list (shrinks) ...........] (search) (contact) (initialize) (auto-reconnect)
 *   electrode contact good · calibration done          (small info line)
 *
 * The round buttons always stay beside the list and are always visible. The
 * button for `next_step` is the call to action; buttons that cannot help
 * right now are greyed out with the reason as tooltip. Choosing a device in
 * the list connects immediately, so there is no separate Connect button. The
 * last button switches auto-reconnect; it stays usable during a recording.
 */
import { t } from '../shared/i18n.js';
import { escapeHtml } from '../shared/dom-utils.js';

export const STEP_ROLES = ['scan', 'measure_signal', 'initialize'];
const ROLE_ICONS = {
  scan: 'iconoir-search',
  measure_signal: 'iconoir-activity',
  initialize: 'iconoir-play',
  auto_reconnect: 'iconoir-refresh-double',
};
const PHASE_FALLBACKS = {
  off: 'Switched off',
  starting: 'Starting …',
  idle: 'Ready to connect',
  searching: 'Searching …',
  selection_required: 'Several found – choose one',
  connecting: 'Connecting …',
  connected: 'Connected',
  reconnecting: 'Connection lost – reconnecting …',
  failed: 'Connection failed',
};
// Why a sensor waits for the operator: a pill title and a hint.
const DETAIL_FALLBACKS = {
  not_found: ['', 'Nothing found – is the device switched on, charged and close by?'],
  target_missing: ['Not reachable', 'The chosen device did not answer. Switch it on, or search.'],
  connect_failed: ['Connection failed', 'Choose the device again, or search.'],
  connection_lost: ['Connection lost', 'Choose the device again to reconnect, or search.'],
  no_data: ['No data', 'The device sends no data. Choose it again to reconnect.'],
  bluetooth_unavailable: ['Bluetooth unavailable', 'Switch Bluetooth on, then search again.'],
  missing_dependency: ['Software missing', 'The device software is not installed. See the diagnostics.'],
  crashed: ['Stopped unexpectedly', 'Choose the device again, or search.'],
  publication_failed: ['Data not published', 'The data could not be passed on for recording. Restart the sensor.'],
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
  starting: 'starting',
  idle: 'waiting',
  searching: 'starting',
  selection_required: 'waiting',
  connecting: 'starting',
  connected: 'connected',
  reconnecting: 'stale',
  failed: 'failed',
};
// Phases where the list shows the device in use rather than a choice.
const DEVICE_IN_USE_PHASES = new Set(['connecting', 'connected', 'reconnecting']);
// Short phases that end by themselves; searching or choosing waits for them.
const BUSY_PHASES = new Set(['starting', 'searching', 'connecting']);

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

function phaseTitle(connection) {
  const phase = connection?.phase || 'connecting';
  const detail = connection?.detail;
  const detailTitle = detail && DETAIL_FALLBACKS[detail]?.[0];
  if (detailTitle) return t(`sensorConnection.detailTitle.${detail}`, detailTitle);
  return t(`sensorConnection.phase.${phase}`, PHASE_FALLBACKS[phase] || phase);
}

/** The status sentence: "Connected – electrode contact good · calibration done". */
export function connectionStatusText(connection, manifest) {
  const config = connectionConfig(manifest);
  const roles = roleActions(manifest);
  const phase = connection?.phase || 'connecting';
  const parts = [phaseTitle(connection)];
  const detail = connection?.detail;
  if (detail && DETAIL_FALLBACKS[detail] && ['idle', 'failed'].includes(phase)) {
    parts.push(t(`sensorConnection.detail.${detail}`, DETAIL_FALLBACKS[detail][1]));
  }
  if (['connecting', 'reconnecting'].includes(phase) && connection.device?.label) {
    parts.push(connection.device.label);
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

/**
 * Why a button or the device list is disabled right now, or '' when usable.
 * `role` is a step role, 'select' for the list or 'auto_reconnect'.
 */
export function stepBlockReason(role, connection, { locked = false, pending = false } = {}) {
  const phase = connection?.phase || 'connecting';
  // Auto-reconnect changes no data, only how a lost connection is handled,
  // so it stays usable while a participant session records.
  if (locked && role !== 'auto_reconnect') {
    return t('sensorConnection.blocked.locked', 'Locked while a participant session is recording.');
  }
  if (pending) return t('sensorConnection.blocked.pending', 'An action is still running.');
  if (phase === 'off') return t('sensorConnection.blocked.off', 'Switch the sensor on first.');
  if (phase === 'starting') return t('sensorConnection.blocked.starting', 'The sensor is starting.');
  if (role === 'auto_reconnect') return '';
  if (role === 'scan' || role === 'select') {
    return BUSY_PHASES.has(phase)
      ? t('sensorConnection.blocked.busy', 'Wait until the current search or connection finishes.')
      : '';
  }
  if (phase !== 'connected' || !connection.streaming) {
    return t('sensorConnection.blocked.notConnected', 'Connect the device first.');
  }
  const signal = connection.signal?.state || 'unknown';
  if (signal === 'measuring') return t('sensorConnection.blocked.measuring', 'The measurement is running.');
  if (role === 'initialize' && connection.setup?.state === 'running') {
    return t('sensorConnection.blocked.setupRunning', 'The initialization is running.');
  }
  return '';
}

function renderDeviceSelect(plugin, manifest, connection, selectAction, state) {
  const config = connectionConfig(manifest);
  const candidates = Array.isArray(connection.candidates) ? connection.candidates : [];
  const phase = connection.phase || 'connecting';
  // Only a device in use is pre-selected. Waiting for the operator, the list
  // asks for a choice, so picking the remembered device fires `change`.
  const device = DEVICE_IN_USE_PHASES.has(phase) ? connection.device : null;
  const known = new Set(candidates.map((candidate) => candidate.id));
  const connectedSuffix = phase === 'connected' ? t('sensorConnection.deviceConnected', 'connected') : '';
  const lastUsed = t('sensorConnection.lastUsed', 'last used');
  const options = [];
  if (device?.id && !known.has(device.id)) {
    options.push(`<option value="" data-option-key="${escapeHtml(device.id)}" selected>${escapeHtml(device.label)}${connectedSuffix ? ` (${escapeHtml(connectedSuffix)})` : ''}</option>`);
  }
  candidates.forEach((candidate) => {
    const selected = device?.id && candidate.id === device.id ? 'selected' : '';
    const note = candidate.note === 'last_used' && !selected ? ` (${lastUsed})` : '';
    const suffix = selected && connectedSuffix ? ` (${connectedSuffix})` : note;
    options.push(`<option value="${escapeHtml(JSON.stringify(candidate.payload || {}))}" data-option-key="${escapeHtml(candidate.id)}" ${selected}>${escapeHtml(candidate.label)}${escapeHtml(suffix)}</option>`);
  });
  const placeholder = candidates.length
    ? nounText(config, 'choose', t('sensorConnection.choose', 'Choose a device'))
    : nounText(config, 'searchPrompt', t('sensorConnection.searchPrompt', 'Please search for devices'));
  const reason = stepBlockReason('select', connection, state);
  const cta = connection.next_step === 'select' && !reason ? ' is-cta' : '';
  const label = t(`plugins.${plugin.key}.actions.${selectAction.key}`, selectAction.label);
  return `<select class="dashboard-select sensor-connection-select${cta}" id="sensor-connection-${escapeHtml(plugin.key)}-select"
      data-connection-select data-plugin-key="${escapeHtml(plugin.key)}" data-plugin-admin-action="${escapeHtml(selectAction.key)}"
      aria-label="${escapeHtml(label)}" title="${escapeHtml(reason || label)}" ${reason ? 'disabled data-blocked' : ''}>
      ${device?.id ? '' : `<option value="" selected disabled>${escapeHtml(placeholder)}</option>`}${options.join('')}
    </select>`;
}

function nounText(config, suffix, fallback) {
  const key = config.device_noun_key;
  return key ? t(suffix ? `${key}.${suffix}` : key, fallback) : fallback;
}

function renderStepButton(plugin, role, action, connection, state) {
  const reason = stepBlockReason(role, connection, state);
  const label = t(`plugins.${plugin.key}.actions.${action.key}`, action.label || role);
  const isCta = connection.next_step === role && !reason;
  const title = reason ? `${label} – ${reason}` : (action.description ? `${label} – ${action.description}` : label);
  return `<button type="button" class="sensor-connection-step${isCta ? ' is-cta' : ''}"
      data-connection-action="${escapeHtml(role)}" data-plugin-key="${escapeHtml(plugin.key)}"
      data-plugin-admin-action="${escapeHtml(action.key)}" ${reason ? 'disabled data-blocked' : ''}
      title="${escapeHtml(title)}" aria-label="${escapeHtml(label)}">
      <i class="${ROLE_ICONS[role] || 'iconoir-flash'}" aria-hidden="true"></i>
    </button>`;
}

/** Auto-reconnect: a pressed/unpressed button; a dot when it is armed right now. */
function renderAutoReconnectToggle(plugin, action, connection, state) {
  const auto = connection.auto_reconnect || {};
  const enabled = auto.enabled !== false;
  const active = enabled && Boolean(auto.active);
  const reason = stepBlockReason('auto_reconnect', connection, state);
  const label = t(`plugins.${plugin.key}.actions.${action.key}`, action.label || 'Auto-reconnect');
  let meaning;
  if (!enabled) {
    meaning = t('sensorConnection.auto.off', 'Off – a lost connection waits for you.');
  } else if (active) {
    meaning = t('sensorConnection.auto.active', 'On and active – a lost connection is restored by itself.');
  } else {
    meaning = t('sensorConnection.auto.armed', 'On – takes effect once a device is connected and the study runs.');
  }
  const title = reason ? `${label} – ${reason}` : `${label}: ${meaning}`;
  return `<button type="button" class="sensor-connection-step sensor-connection-toggle${enabled ? ' is-on' : ''}${active ? ' is-active' : ''}"
      data-connection-action="auto_reconnect" data-lock-exempt data-plugin-key="${escapeHtml(plugin.key)}"
      data-plugin-admin-action="${escapeHtml(action.key)}" aria-pressed="${enabled ? 'true' : 'false'}"
      ${reason ? 'disabled data-blocked' : ''} title="${escapeHtml(title)}" aria-label="${escapeHtml(label)}">
      <i class="${ROLE_ICONS.auto_reconnect}" aria-hidden="true"></i>
    </button>`;
}

/**
 * Where a push moves the runtime switch: right from Off switches on (however
 * far), right from On restarts, left from On switches off. null = no change.
 */
export function runtimeSwitchStep(position, direction) {
  if (direction > 0) return position === 'off' ? 'on' : (position === 'on' ? 'restart' : null);
  if (direction < 0) return position === 'off' ? null : 'off';
  return null;
}

/** A click on a field: Restart while off only switches on, like a push right. */
export function runtimeSwitchClick(position, target) {
  if (target === position) return null;
  if (position === 'off' && target === 'restart') return 'on';
  return target;
}

/** The connected device for sensors without a device list: name, ID, transport. */
function renderDeviceBar(plugin, connection) {
  const label = connection.device?.label || plugin.device_label || '';
  const text = label || t('sensorConnection.noDevice', 'No device connected');
  // The inner span scrolls through when the text does not fit (sensor-columns.js).
  return `<span class="sensor-connection-device${label ? '' : ' is-empty'}" title="${escapeHtml(text)}" data-marquee><span class="marquee-text">${escapeHtml(text)}</span></span>`;
}

/** Where the runtime switch stands: off, on, or restarting (right). */
export function runtimeSwitchPosition(plugin, { pendingRunning, pendingRestart } = {}) {
  if (pendingRestart) return 'restart';
  if (pendingRunning !== undefined) return pendingRunning ? 'on' : 'off';
  return plugin?.running ? 'on' : 'off';
}

/**
 * Off | On | Restart in one track. Pushing right from Off switches on;
 * pushing right from On restarts and the knob comes back to On once the
 * restart is done; pushing left switches off. Handled in the dashboard
 * controller (click, drag, arrow keys).
 */
function renderRuntimeSwitch(plugin, state = {}) {
  const position = runtimeSwitchPosition(plugin, state);
  const busy = state.pendingRunning !== undefined || Boolean(state.pendingRestart);
  const canSwitch = Boolean(plugin.can_start || plugin.can_stop);
  const segments = [
    ['off', t('sensorConnection.runtime.off', 'Off'), '', !canSwitch],
    ['on', t('sensorConnection.runtime.on', 'On'), '', !canSwitch],
    ['restart', t('dashboard.action.restart', 'Restart'), 'iconoir-refresh', !plugin.can_restart || position === 'off'],
  ];
  const label = t('sensorConnection.runtime.label', 'Sensor runtime');
  return `<div class="runtime-switch is-${position}${busy ? ' is-busy' : ''}" role="radiogroup"
      aria-label="${escapeHtml(label)}" data-runtime-switch="${escapeHtml(plugin.key)}" data-position="${position}"
      ${busy || !canSwitch ? 'aria-disabled="true"' : ''}>
      ${segments.map(([target, text, icon, disabled]) => `<button type="button" role="radio" class="runtime-switch-option"
        data-target="${target}" aria-checked="${position === target ? 'true' : 'false'}"
        title="${escapeHtml(text)}" aria-label="${escapeHtml(text)}" tabindex="${position === target ? '0' : '-1'}"
        ${disabled || busy ? 'disabled' : ''}>${icon ? `<i class="${icon}" aria-hidden="true"></i>` : `<span>${escapeHtml(text)}</span>`}</button>`).join('')}
      <span class="runtime-switch-knob" aria-hidden="true"></span>
    </div>`;
}

/**
 * @param plugin   the plugin status (standardized, with `connection`, `running`)
 * @param manifest the plugin manifest
 * @param state    {locked, pending, pendingRunning, pendingRestart, deviatesFromStudy}
 *
 *   [● Connected           ]                 [ Off | On | ↻ ]
 *   [device list ▾        ] (search)(contact)(init)(auto)
 *   Electrode contact good · Calibration done
 */
export function renderSensorConnectionPanel(plugin, manifest, state = {}) {
  const connection = plugin?.connection;
  if (!connection) return '';
  const roles = roleActions(manifest);
  const phase = connection.phase || 'connecting';
  const statusParts = connectionStatusText(connection, manifest);
  const tone = connection.ready ? 'ready' : (PHASE_PILL[phase] || 'unknown');
  const indicatorText = connection.ready ? t('sensorConnection.ready', 'Ready') : (statusParts[0] || '');
  const stepRoles = STEP_ROLES.filter((role) => roles[role]);
  const buttons = [
    ...stepRoles.map((role) => renderStepButton(plugin, role, roles[role], connection, state)),
    roles.auto_reconnect ? renderAutoReconnectToggle(plugin, roles.auto_reconnect, connection, state) : '',
  ].join('');
  // Every sensor has this bar: the device list, or what it is connected to.
  const steps = `
    <div class="sensor-connection-steps">
      ${roles.select ? renderDeviceSelect(plugin, manifest, connection, roles.select, state) : renderDeviceBar(plugin, connection)}
      ${buttons ? `<div class="sensor-connection-buttons" role="group" aria-label="${escapeHtml(t('sensorConnection.steps', 'Connection steps'))}">${buttons}</div>` : ''}
    </div>`;
  // When ready, the indicator says so; the phase then joins the info line.
  const info = (connection.ready ? statusParts : statusParts.slice(1)).join(' · ');
  const deviation = state.deviatesFromStudy
    ? `<p class="sensor-connection-note">${escapeHtml(t('sensorConnection.deviation', 'Differs from the loaded study until the study is loaded again.'))}
        <button type="button" class="btn-secondary btn-xs" data-dashboard-action="reset_sensor_overrides">${escapeHtml(t('dashboard.overrideReset', 'Reset to study settings'))}</button></p>`
    : '';
  return `<div class="sensor-connection" data-plugin-dashboard-controls data-sensor-connection="${escapeHtml(plugin.key)}" data-phase="${escapeHtml(phase)}">
      <div class="sensor-connection-head">
        <span class="sensor-indicator sensor-indicator--${escapeHtml(tone)}" role="status">${connection.ready ? '<i class="iconoir-check" aria-hidden="true"></i>' : '<span class="sensor-indicator-dot" aria-hidden="true"></span>'}${escapeHtml(indicatorText)}</span>
        ${renderRuntimeSwitch(plugin, state)}
      </div>
      ${steps}
      ${info ? `<p class="sensor-connection-info">${escapeHtml(info)}</p>` : ''}
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

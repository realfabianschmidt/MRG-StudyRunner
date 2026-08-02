/**
 * Completed-session browser: the hub list plus the full-detail panel.
 *
 * The detail panel reuses the session-timeline renderer for the sensor
 * lanes and answer markers; this module owns the data fetching, the answer
 * and file lists, and the marker popover.
 */
import { getJson } from '../api-client.js';
import { t } from '../i18n.js';
import { byId, escapeHtml, formatDateTime, formatFileSize, setHidden, setText } from '../lib/dom-utils.js';
import { bindTimelineMarkers, renderSessionTimeline } from './session-timeline.js';

const MAX_HUB_ITEMS = 25;
const HUB_REFRESH_INTERVAL_MS = 10000;

let callbacks = {};
let initialized = false;
let currentMarkers = [];
let refreshTimer = null;

export function initializeSessionsBrowser(options = {}) {
  callbacks = options;
  if (initialized) return;
  initialized = true;

  byId('btn-session-back')?.addEventListener('click', () => callbacks.switchView?.('view-hub'));
  refreshTimer = window.setInterval(() => {
    if (!document.hidden && byId('view-hub')?.classList.contains('active')) {
      void loadCompletedSessions();
    }
  }, HUB_REFRESH_INTERVAL_MS);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) {
      void loadCompletedSessions();
    }
  });
}

export async function loadCompletedSessions() {
  const listEl = byId('hub-sessions-list');
  if (!listEl) return;
  try {
    const sessions = await getJson('/api/admin/sessions');
    renderHubList(listEl, Array.isArray(sessions) ? sessions : []);
  } catch (error) {
    console.error('[sessions] Could not load completed sessions:', error);
    listEl.innerHTML = `
      <div class="hub-recent-item empty">
        <i class="iconoir-warning-triangle"></i>
        <div>${escapeHtml(t('sessions.loadFailed', 'Could not load completed studies'))}</div>
      </div>`;
  }
}

function renderHubList(listEl, sessions) {
  if (!sessions.length) {
    listEl.innerHTML = `
      <div class="hub-recent-item empty">
        <i class="iconoir-clock"></i>
        <div>${escapeHtml(t('sessions.empty', 'No completed studies yet.'))}</div>
      </div>`;
    return;
  }

  const shown = sessions.slice(0, MAX_HUB_ITEMS);
  listEl.innerHTML = shown.map((session) => `
    <div class="hub-recent-item" data-study-id="${escapeHtml(session.study_id)}" data-participant-id="${escapeHtml(session.participant_id)}" data-session-id="${escapeHtml(session.session_id)}" data-session-folder="${escapeHtml(session.session_folder)}" style="justify-content: flex-start; padding: 12px 16px; cursor: pointer;">
      <i class="iconoir-graph-up" style="font-size: 20px; color: var(--accent);"></i>
      <div style="text-align: left; flex: 1;">
        <div style="font-weight: 600; color: var(--ink);">${escapeHtml(session.study_id)} <span style="font-weight: 400; color: var(--ink-40);">/ ${escapeHtml(session.participant_id)}</span></div>
        <div style="font-size: 0.75rem; color: var(--ink-40);">${escapeHtml(formatDateTime(session.saved_at))} &middot; ${escapeHtml(String(session.answers_count))} ${escapeHtml(t('sessions.answersLabel', 'Answers'))}${sessionTags(session).map((tag) => ` &middot; ${escapeHtml(tag)}`).join('')}</div>
      </div>
    </div>
  `).join('') + (sessions.length > MAX_HUB_ITEMS
    ? `<div class="hub-recent-item empty">${escapeHtml(t('sessions.moreHint', '{count} more not shown').replace('{count}', String(sessions.length - MAX_HUB_ITEMS)))}</div>`
    : '');

  listEl.querySelectorAll('.hub-recent-item[data-study-id]').forEach((item) => {
    item.addEventListener('click', () => void openSessionDetail(
      item.dataset.studyId,
      item.dataset.participantId,
      item.dataset.sessionId,
      item.dataset.sessionFolder,
    ));
  });
}

async function openSessionDetail(studyId, participantId, sessionId, sessionFolder) {
  callbacks.switchView?.('view-session-detail');
  setText('session-detail-title', t('sessions.detailLoading', 'Loading ...'));
  setText('session-detail-subtitle', '');
  hidePopover();

  try {
    const params = new URLSearchParams();
    if (sessionId) params.set('session_id', sessionId);
    if (sessionFolder) params.set('session_folder', sessionFolder);
    const query = params.size ? `?${params.toString()}` : '';
    const session = await getJson(`/api/admin/sessions/${encodeURIComponent(studyId)}/${encodeURIComponent(participantId)}${query}`);
    renderSessionSummary(session);
    renderAnswerList(session);
    renderFileList(session);
    await renderTimeline(session);
  } catch (error) {
    console.error('[sessions] Could not load session detail:', error);
    setText('session-detail-title', t('sessions.loadFailed', 'Could not load completed studies'));
    callbacks.showToast?.(t('sessions.loadFailed', 'Could not load completed studies'), 'error');
  }
}

function renderSessionSummary(session) {
  setText('session-detail-title', `${session.study_id} / ${session.participant_id}`);
  setText('session-detail-subtitle', formatDateTime(session.saved_at));

  setText('session-answers-value', String(session.answers_count ?? 0));
  setText('session-answers-hint', sessionTags(session).join(' · '));

  const result = session.result || {};
  const durationSeconds = _durationSeconds(result.timestamp_start, result.timestamp_end);
  setText('session-duration-value', durationSeconds != null ? formatDuration(durationSeconds) : '-');
  setText('session-duration-hint', '');

  const streams = Array.isArray(session.streams) ? session.streams : (Array.isArray(session.sidecars) ? session.sidecars : []);
  setText('session-signals-value', streams.length ? String(streams.length) : '-');
  setText(
    'session-signals-hint',
    streams.length
      ? streams.map((stream) => stream.stream_name || stream.sensor).join(', ')
      : t('sessions.noSignals', 'No sensor recordings for this session.'),
  );

  const files = Array.isArray(session.files) ? session.files : [];
  setText('session-files-value', String(files.length));
  setText('session-files-hint', '');
}

function renderAnswerList(session) {
  const container = byId('session-answer-list');
  if (!container) return;
  const entries = Array.isArray(session.result?.answer_details) ? session.result.answer_details : [];
  if (!entries.length) {
    container.innerHTML = `<p class="settings-hint">${escapeHtml(t('sessions.noAnswers', 'No answers were recorded.'))}</p>`;
    return;
  }

  container.innerHTML = entries.map((entry) => `
    <div class="session-answer-row">
      <div class="session-answer-num">${escapeHtml(String(entry.question_number ?? ''))}</div>
      <div class="session-answer-body">
        <div class="session-answer-prompt">${escapeHtml(entry.question_prompt || entry.question_type || '')}</div>
        <div class="session-answer-value">${entry.skipped
          ? `<em>${escapeHtml(t('sessions.skipped', 'skipped'))}</em>`
          : escapeHtml(formatAnswerValue(entry.answer))}</div>
      </div>
    </div>
  `).join('');
}

function renderFileList(session) {
  const container = byId('session-file-list');
  if (!container) return;
  const files = Array.isArray(session.files) ? session.files : [];
  if (!files.length) {
    container.innerHTML = `<p class="settings-hint">${escapeHtml(t('sessions.noFiles', 'No files were saved.'))}</p>`;
    return;
  }

  container.innerHTML = files.map((file) => `
    <div class="session-file-row">
      <i class="iconoir-page"></i>
      <span class="session-file-name">${escapeHtml(file.name)}</span>
      <span class="session-file-meta">${escapeHtml(formatFileSize(file.size))} &middot; ${escapeHtml(formatDateTime(file.modified_at))}</span>
    </div>
  `).join('');
}

async function renderTimeline(session) {
  const container = byId('session-timeline');
  if (!container) return;
  hidePopover();

  const streams = (Array.isArray(session.streams) ? session.streams : (Array.isArray(session.sidecars) ? session.sidecars : []))
    .filter((stream) => stream.sensor && stream.sample_count > 0);

  const lanes = (await Promise.all(streams.map(async (stream) => {
    try {
      const signals = await getJson(
        `/api/admin/sessions/${encodeURIComponent(session.study_id)}/${encodeURIComponent(session.participant_id)}/signals`
        + `?sensor=${encodeURIComponent(stream.sensor)}&session_folder=${encodeURIComponent(session.session_folder)}`,
      );
      return { sensor: stream.sensor, points: signals.points, mode: signals.mode };
    } catch (error) {
      console.error(`[sessions] Could not load ${stream.sensor} signals:`, error);
      return null;
    }
  }))).filter(Boolean);

  currentMarkers = buildMarkers(session.result?.answer_details);

  const labels = {
    nothingRecorded: t('sessions.timelineEmpty', 'Nothing was recorded for this session.'),
    chartLabel: t('sessions.timeline', 'Timeline'),
    markersLabel: t('sessions.answersTitle', 'Questions and answers'),
    channels: {},
  };

  renderSessionTimeline(container, { lanes, markers: currentMarkers, labels });
  bindTimelineMarkers(container, currentMarkers, showPopover);
}

function buildMarkers(entries) {
  if (!Array.isArray(entries)) return [];
  return entries
    .map((entry) => {
      const start = _entryEpoch(entry, 'start');
      const end = _entryEpoch(entry, 'end');
      if (start == null) return null;
      return {
        start,
        end: end ?? start,
        number: entry.question_number,
        title: `#${entry.question_number} ${entry.question_prompt || entry.question_type || ''}`.trim(),
        entry,
      };
    })
    .filter(Boolean);
}

function showPopover(marker, node) {
  const popover = byId('session-timeline-popover');
  if (!popover) return;
  const entry = marker.entry || {};
  const answerLine = entry.skipped
    ? `<em>${escapeHtml(t('sessions.skipped', 'skipped'))}</em>`
    : escapeHtml(formatAnswerValue(entry.answer));

  popover.innerHTML = `
    <div class="timeline-popover-title">#${escapeHtml(String(entry.question_number ?? ''))} ${escapeHtml(entry.question_prompt || entry.question_type || '')}</div>
    <div class="timeline-popover-answer">${answerLine}</div>
    ${renderBiosignalSummary(entry.biosignal_interval)}
  `;
  popover.hidden = false;

  const containerRect = popover.parentElement?.getBoundingClientRect();
  const nodeRect = node.getBoundingClientRect();
  if (containerRect) {
    popover.style.left = `${Math.max(0, nodeRect.left - containerRect.left - 80)}px`;
    popover.style.top = `${nodeRect.bottom - containerRect.top + 8}px`;
  }
}

function hidePopover() {
  setHidden('session-timeline-popover', true);
}

function renderBiosignalSummary(intervalSummary) {
  if (!intervalSummary || typeof intervalSummary !== 'object') return '';
  const rows = [];
  Object.entries(intervalSummary).forEach(([sensor, summary]) => {
    if (!summary || typeof summary !== 'object' || !summary.available) return;
    const numericFields = Object.entries(summary).filter(
      ([key, value]) => typeof value === 'number' && !['dropped_in_interval', 'max_gap_seconds'].includes(key),
    );
    if (!numericFields.length) return;
    const fieldsText = numericFields.map(([key, value]) => `${key}: ${Number(value.toFixed?.(2) ?? value)}`).join(', ');
    rows.push(`<div class="timeline-popover-signal"><strong>${escapeHtml(sensor)}</strong>: ${escapeHtml(fieldsText)}</div>`);
  });
  return rows.join('');
}

function formatAnswerValue(value) {
  if (value === null || value === undefined || value === '') return '-';
  if (Array.isArray(value)) return value.join(', ');
  if (typeof value === 'object') return Object.entries(value).map(([key, entryValue]) => `${key}: ${entryValue}`).join(', ');
  return String(value);
}

function sessionTags(session) {
  const tags = [];
  if (session.status === 'attention_required') {
    tags.push(t('sessions.attentionTag', 'attention required'));
  } else if (session.status === 'completed_degraded' || session.quality_status === 'degraded') {
    tags.push(t('sessions.degradedTag', 'quality warning'));
  }
  if (session.recovered) tags.push(t('sessions.recoveredTag', 'recovered'));
  return tags;
}

function formatDuration(totalSeconds) {
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = Math.round(totalSeconds % 60);
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

function _durationSeconds(startIso, endIso) {
  if (!startIso || !endIso) return null;
  const start = Date.parse(startIso);
  const end = Date.parse(endIso);
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return (end - start) / 1000;
}

function _entryEpoch(entry, edge) {
  const msKey = edge === 'start' ? 'server_start_received_epoch_ms' : 'server_stop_received_epoch_ms';
  const isoKey = edge === 'start' ? 'biosignal_interval_start' : 'biosignal_interval_end';
  const ms = Number(entry[msKey]);
  if (Number.isFinite(ms)) return ms / 1000;
  const parsed = Date.parse(entry[isoKey] || '');
  return Number.isNaN(parsed) ? null : parsed / 1000;
}

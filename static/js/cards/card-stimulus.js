function escapeHtml(v) {
  return String(v ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

export const meta = {
  type:  'stimulus',
  icon:  'timer',
  label: 'Stimulus / Countdown',
};

export const defaultQuestion = {
  type:            'stimulus',
  title:           'Observe the material',
  subtitle:        'Pay attention to all sensory impressions. The questionnaire will appear automatically.',
  duration_ms:     30000,
  trigger_type:    'timer',
  trigger_content: '',
  send_signal:     true,
};

// IDs are scoped by index so multiple stimulus cards can coexist in the same study.
// .stimulus-body provides centering inside the card tile.
export function renderStudy(q, i) {
  const durationSeconds = Math.round((q.duration_ms || 30000) / 1000);
  return `
    <div class="stimulus-body">
      <div class="q-type-tag"><i class="iconoir-timer"></i> Stimulus active</div>
      <h1 class="screen-title">${escapeHtml(q.title || 'Observe')}</h1>
      <p class="screen-sub">${escapeHtml(q.subtitle || '')}</p>
      <div class="stimulus-content" id="stimulus-content-${i}" hidden></div>
      <svg class="cd-ring" viewBox="0 0 120 120">
        <circle cx="60" cy="60" r="50" fill="none" stroke="var(--ink-06)" stroke-width="5"/>
        <circle id="ring-prog-${i}" cx="60" cy="60" r="50" fill="none" stroke="var(--accent)" stroke-width="5"
          stroke-linecap="round" stroke-dasharray="314" stroke-dashoffset="0"
          transform="rotate(-90 60 60)" style="transition:stroke-dashoffset 1s linear"/>
      </svg>
      <div class="cd-num" id="cd-num-${i}">${durationSeconds}</div>
      <div class="cd-lbl">seconds remaining</div>
    </div>`;
}

export function renderEditor(q) {
  const durationSeconds = Math.round((q.duration_ms || 30000) / 1000);
  const triggerType     = q.trigger_type || 'timer';
  const TRIGGER_TYPES   = ['timer', 'image', 'video', 'audio', 'html', 'js'];
  const isContentHidden = triggerType === 'timer';
  const isCode          = triggerType === 'html' || triggerType === 'js';

  return `
    <div class="field">
      <label>Title</label>
      <input type="text" class="se-title" value="${escapeHtml(q.title || '')}">
    </div>
    <div class="field">
      <label>Subtitle / instruction</label>
      <textarea class="se-subtitle" rows="3">${escapeHtml(q.subtitle || '')}</textarea>
    </div>
    <div class="field">
      <label>Duration (seconds)</label>
      <input type="number" class="se-duration" min="1" max="600" value="${durationSeconds}">
    </div>
    <div class="field">
      <label>Trigger type</label>
      <div class="trigger-type-pills">
        ${TRIGGER_TYPES.map(type => `
          <button type="button" class="trigger-pill${triggerType === type ? ' active' : ''}" data-trigger-type="${escapeHtml(type)}">
            ${escapeHtml(type)}
          </button>`).join('')}
      </div>
      <input type="hidden" class="se-trigger-type" value="${escapeHtml(triggerType)}">
    </div>
    <div class="field se-trigger-content-field"${isContentHidden ? ' hidden' : ''}>
      <label>${isCode ? 'Code' : 'URL'}</label>
      ${isCode
        ? `<textarea class="se-trigger-content se-trigger-content--code" rows="6" placeholder="Paste ${escapeHtml(triggerType)} code here...">${escapeHtml(q.trigger_content || '')}</textarea>`
        : `<input type="url" class="se-trigger-content" placeholder="https://..." value="${escapeHtml(q.trigger_content || '')}">`
      }
    </div>
    <div class="field">
      <label class="checkbox-row">
        <input type="checkbox" class="se-send-signal"${q.send_signal !== false ? ' checked' : ''}>
        <span>Send /api/start and /api/stop signals</span>
      </label>
    </div>`;
}

export function collectConfig(el) {
  return {
    type:            'stimulus',
    title:           el.querySelector('.se-title')?.value.trim()            || '',
    subtitle:        el.querySelector('.se-subtitle')?.value.trim()         || '',
    duration_ms:     Number.parseInt(el.querySelector('.se-duration')?.value || '30', 10) * 1000,
    trigger_type:    el.querySelector('.se-trigger-type')?.value            || 'timer',
    trigger_content: el.querySelector('.se-trigger-content')?.value.trim()  || '',
    send_signal:     el.querySelector('.se-send-signal')?.checked ?? true,
  };
}

// Stimulus cards have no participant answer.
export function collectAnswer() { return null; }

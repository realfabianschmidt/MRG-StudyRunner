import { t } from '../i18n.js';
import { renderCardInstruction } from './card-info.js';

// Field metadata: render kind, label, and (for choice fields) default options.
// `configurable: true` fields expose an editable option list in the settings modal.
const FIELD_META = {
  first_name: { kind: 'text', label: ['cards.participant.firstName', 'First name'], placeholder: ['cards.participant.firstNamePlaceholder', 'e.g. Anna'] },
  last_name: { kind: 'text', label: ['cards.participant.lastName', 'Last name'], placeholder: ['cards.participant.lastNamePlaceholder', 'e.g. Miller'] },
  age_group: { kind: 'select', configurable: true, label: ['cards.participant.ageGroup', 'Age group'], defaultOptions: ['18-25', '26-35', '36-45', '46-60', '60+'] },
  gender: { kind: 'select', configurable: true, label: ['cards.participant.gender', 'Gender'], defaultOptions: ['Female', 'Male', 'Non-binary', 'Prefer not to say'] },
  childhood_area: { kind: 'area', label: ['cards.participant.childhoodArea', 'Childhood area'] },
  childhood_nearest_city: { kind: 'text', label: ['cards.participant.childhoodNearestCity', 'Nearest larger city in childhood'], placeholder: ['cards.participant.childhoodNearestCityPlaceholder', 'e.g. Munich'] },
  birth_place: { kind: 'text', label: ['cards.participant.birthPlace', 'Place of birth'], placeholder: ['cards.participant.birthPlacePlaceholder', 'e.g. Munich'] },
  birth_date: { kind: 'date', label: ['cards.participant.birthDate', 'Date of birth'] },
};

const FIELD_ORDER = [
  'first_name',
  'last_name',
  'age_group',
  'gender',
  'childhood_area',
  'childhood_nearest_city',
  'birth_place',
  'birth_date',
];

const DEFAULT_FIELDS = {
  first_name: { enabled: true, use_for_key: true, store: false },
  last_name: { enabled: true, use_for_key: true, store: false },
  age_group: { enabled: true, use_for_key: true, store: true },
  gender: { enabled: false, use_for_key: false, store: true },
  childhood_area: { enabled: true, use_for_key: true, store: true },
  childhood_nearest_city: { enabled: true, use_for_key: true, store: true },
  birth_place: { enabled: false, use_for_key: false, store: true },
  birth_date: { enabled: false, use_for_key: false, store: true },
};

function isConfigurable(fieldKey) {
  return Boolean(FIELD_META[fieldKey]?.configurable);
}

function defaultOptions(fieldKey) {
  return [...(FIELD_META[fieldKey]?.defaultOptions || [])];
}

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function cloneDefaultFields() {
  const fields = JSON.parse(JSON.stringify(DEFAULT_FIELDS));
  FIELD_ORDER.forEach((fieldKey) => {
    if (isConfigurable(fieldKey)) fields[fieldKey].options = defaultOptions(fieldKey);
  });
  return fields;
}

function fieldLabel(fieldKey) {
  const [key, fallback] = FIELD_META[fieldKey]?.label || [fieldKey, fieldKey];
  return t(key, fallback);
}

function normalizeOptions(rawOptions, fieldKey) {
  if (!Array.isArray(rawOptions)) return defaultOptions(fieldKey);
  const cleaned = [];
  rawOptions.forEach((item) => {
    const text = String(item ?? '').trim();
    if (text && !cleaned.includes(text)) cleaned.push(text);
  });
  return cleaned.length ? cleaned : defaultOptions(fieldKey);
}

function normalizeFields(rawFields) {
  const defaults = cloneDefaultFields();
  const source = rawFields && typeof rawFields === 'object' ? rawFields : {};
  const fields = {};

  FIELD_ORDER.forEach((fieldKey) => {
    const raw = source[fieldKey] && typeof source[fieldKey] === 'object' ? source[fieldKey] : {};
    const base = defaults[fieldKey];
    const enabled = raw.enabled ?? base.enabled;
    fields[fieldKey] = {
      enabled: Boolean(enabled),
      use_for_key: Boolean(enabled) && Boolean(raw.use_for_key ?? base.use_for_key),
      store: Boolean(enabled) && Boolean(raw.store ?? base.store),
    };
    if (isConfigurable(fieldKey)) {
      fields[fieldKey].options = normalizeOptions(raw.options ?? base.options, fieldKey);
    }
  });

  if (!FIELD_ORDER.some((fieldKey) => fields[fieldKey].enabled && fields[fieldKey].use_for_key)) {
    const fallbackKey = FIELD_ORDER.find((fieldKey) => fields[fieldKey].enabled) || FIELD_ORDER[0];
    fields[fallbackKey].enabled = true;
    fields[fallbackKey].use_for_key = true;
  }

  return fields;
}

export const meta = {
  type: 'participant-id',
  icon: 'user-badge-check',
  label: 'Participant ID',
  pill: 'pill-participant-id',
};

export const defaultQuestion = {
  type: 'participant-id',
  prompt: 'Please enter your data for anonymous identification.',
  code_label: 'Your anonymous code',
  info_top: 'Your input is transformed locally into a one-way SHA-256 hash. Only fields marked for storage are saved.',
  fields: cloneDefaultFields(),
};

let _computedId = null;
let _computedMetadata = null;

export function renderStudy(q, _i) {
  const prompt = q.prompt || defaultQuestion.prompt;
  const codeLabel = q.code_label ?? defaultQuestion.code_label;
  const fields = normalizeFields(q.fields);
  const activeFieldMarkup = FIELD_ORDER
    .filter((fieldKey) => fields[fieldKey].enabled)
    .map((fieldKey) => renderStudyField(fieldKey, fields[fieldKey]))
    .join('');

  return `
    <div class="q-type-tag"><i class="iconoir-user-badge-check"></i> ${escapeHtml(t('cards.participant.tag', 'Participant ID'))}</div>
    <p class="q-prompt">${escapeHtml(prompt)}</p>
    ${renderCardInstruction(q)}
    <div class="pid-card-body">
      <div class="pid-fields">
        ${activeFieldMarkup}
      </div>
      <div class="pid-code-box" hidden>
        <div class="pid-code-label">${escapeHtml(codeLabel)}</div>
        <div class="pid-code-display"></div>
      </div>
    </div>`;
}

function renderStudyField(fieldKey, fieldConfig) {
  const label = escapeHtml(fieldLabel(fieldKey));
  const kind = FIELD_META[fieldKey]?.kind || 'text';
  const commonAttrs = [
    'class="fi-input pid-field"',
    `name="pid-${escapeHtml(fieldKey)}"`,
    `data-pid-field="${escapeHtml(fieldKey)}"`,
    `data-pid-use-for-key="${fieldConfig.use_for_key ? '1' : '0'}"`,
    `data-pid-store="${fieldConfig.store ? '1' : '0'}"`,
  ].join(' ');

  let control = '';
  if (kind === 'select') {
    const options = normalizeOptions(fieldConfig.options, fieldKey);
    control = `
      <select ${commonAttrs}>
        <option value="">${escapeHtml(t('cards.participant.selectPlaceholder', 'Select...'))}</option>
        ${options.map((opt) => `<option value="${escapeHtml(opt)}">${escapeHtml(opt)}</option>`).join('')}
      </select>`;
  } else if (kind === 'area') {
    control = `
      <select ${commonAttrs}>
        <option value="">${escapeHtml(t('cards.participant.selectPlaceholder', 'Select...'))}</option>
        <option value="urban">${escapeHtml(t('cards.participant.childhoodAreaUrban', 'Urban'))}</option>
        <option value="rural">${escapeHtml(t('cards.participant.childhoodAreaRural', 'Rural'))}</option>
      </select>`;
  } else if (kind === 'date') {
    control = `<input ${commonAttrs} type="date">`;
  } else {
    const [pKey, pFallback] = FIELD_META[fieldKey]?.placeholder || ['', ''];
    const placeholder = pKey ? t(pKey, pFallback) : '';
    control = `<input ${commonAttrs} type="text" autocomplete="off" autocorrect="off" spellcheck="false" placeholder="${escapeHtml(placeholder)}">`;
  }

  return `
    <div class="pid-field-row">
      <label class="pid-label">${label}</label>
      ${control}
    </div>`;
}

// ── Editor ─────────────────────────────────────────────────────────────────

export function renderEditor(q) {
  const fields = normalizeFields(q.fields);

  return `
    <div class="field">
      <label>${escapeHtml(t('cards.editor.prompt', 'Prompt'))}</label>
      <textarea class="fi-textarea q-prompt-input" rows="3">${escapeHtml(q.prompt || defaultQuestion.prompt)}</textarea>
    </div>
    <div class="field">
      <label>${escapeHtml(t('cards.participant.codeTitleLabel', 'Generated code title'))}</label>
      <input type="text" class="fi-input q-code-label-input" value="${escapeHtml(q.code_label ?? defaultQuestion.code_label)}">
    </div>
    <div class="field">
      <label>${escapeHtml(t('cards.participant.fieldsLabel', 'Participant fields'))}</label>
      <div class="pid-field-list">
        ${FIELD_ORDER.map((fieldKey) => renderEditorFieldRow(fieldKey, fields[fieldKey])).join('')}
      </div>
    </div>
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      ${escapeHtml(t('cards.participant.editorHint', 'Turn fields on or off. Use the gear to choose whether a field feeds the anonymous code, is stored in results, and (for choice fields) which answers are allowed. At least one field must feed the code.'))}
    </p>`;
}

function renderEditorFieldRow(fieldKey, fieldConfig) {
  const settingsAria = escapeHtml(`${fieldLabel(fieldKey)} ${t('cards.participant.fieldSettings', 'settings')}`);
  const stateValue = escapeHtml(JSON.stringify({
    use_for_key: fieldConfig.use_for_key,
    store: fieldConfig.store,
    options: isConfigurable(fieldKey) ? normalizeOptions(fieldConfig.options, fieldKey) : undefined,
  }));

  return `
    <div class="pid-field-row-ed" data-pid-editor-row="${escapeHtml(fieldKey)}">
      <span class="pid-field-name">${escapeHtml(fieldLabel(fieldKey))}</span>
      <div class="pid-field-controls">
        <label class="switch" aria-label="${escapeHtml(fieldLabel(fieldKey))}">
          <input type="checkbox" class="pid-enabled" data-pid-field="${escapeHtml(fieldKey)}" ${fieldConfig.enabled ? 'checked' : ''}>
          <span class="switch-slider"></span>
        </label>
        <button type="button" class="pid-gear" data-pid-gear="${escapeHtml(fieldKey)}"
                title="${settingsAria}" aria-label="${settingsAria}" ${fieldConfig.enabled ? '' : 'hidden'}>
          <i class="iconoir-settings"></i>
        </button>
      </div>
      <input type="hidden" class="pid-field-state" data-pid-field="${escapeHtml(fieldKey)}" value="${stateValue}">
    </div>`;
}

function readFieldState(editorEl, fieldKey) {
  const stateEl = editorEl.querySelector(`.pid-field-state[data-pid-field="${fieldKey}"]`);
  let parsed = {};
  try { parsed = JSON.parse(stateEl?.value || '{}'); } catch { parsed = {}; }
  return {
    use_for_key: Boolean(parsed.use_for_key),
    store: Boolean(parsed.store),
    options: isConfigurable(fieldKey) ? normalizeOptions(parsed.options, fieldKey) : undefined,
  };
}

function writeFieldState(editorEl, fieldKey, next) {
  const stateEl = editorEl.querySelector(`.pid-field-state[data-pid-field="${fieldKey}"]`);
  if (!stateEl) return;
  stateEl.value = JSON.stringify({
    use_for_key: Boolean(next.use_for_key),
    store: Boolean(next.store),
    options: isConfigurable(fieldKey) ? normalizeOptions(next.options, fieldKey) : undefined,
  });
  // Trigger the admin overlay's input delegation so the preview + unsaved state update.
  stateEl.dispatchEvent(new Event('input', { bubbles: true }));
}

export function bindEditorEvents(editorEl) {
  // #editor-fields is reused across overlay opens; bind the delegated listeners once.
  if (editorEl.dataset.pidBound === '1') return;
  editorEl.dataset.pidBound = '1';

  editorEl.addEventListener('change', (event) => {
    const toggle = event.target.closest?.('.pid-enabled');
    if (toggle) syncFieldRow(editorEl, toggle.dataset.pidField);
  });

  editorEl.addEventListener('click', (event) => {
    const gear = event.target.closest?.('.pid-gear');
    if (gear) {
      event.preventDefault();
      openFieldModal(editorEl, gear.dataset.pidGear);
    }
  });
}

function syncFieldRow(editorEl, fieldKey) {
  const row = editorEl.querySelector(`[data-pid-editor-row="${fieldKey}"]`);
  if (!row) return;
  const enabled = Boolean(row.querySelector('.pid-enabled')?.checked);
  const gear = row.querySelector('.pid-gear');
  if (gear) gear.hidden = !enabled;
}

// ── Per-field settings modal ─────────────────────────────────────────────────

let _activeFieldModal = null;

function closeFieldModal() {
  if (_activeFieldModal) {
    if (_activeFieldModal._escHandler) document.removeEventListener('keydown', _activeFieldModal._escHandler);
    _activeFieldModal.remove();
    _activeFieldModal = null;
  }
}

function openFieldModal(editorEl, fieldKey) {
  closeFieldModal();

  const state = readFieldState(editorEl, fieldKey);
  const configurable = isConfigurable(fieldKey);

  const backdrop = document.createElement('div');
  backdrop.className = 'modal-backdrop';

  const optionsSection = configurable ? `
    <div class="field">
      <label>${escapeHtml(t('cards.participant.optionsLabel', 'Answer options'))}</label>
      <div class="pid-options-editor">
        ${state.options.map((opt) => renderOptionRow(opt)).join('')}
      </div>
      <button type="button" class="btn-secondary pid-add-option" style="margin-top:8px;">
        <i class="iconoir-plus"></i> ${escapeHtml(t('cards.participant.addOption', 'Add option'))}
      </button>
    </div>` : '';

  backdrop.innerHTML = `
    <div class="settings-modal" role="dialog" aria-modal="true" style="max-width: 460px;">
      <div class="settings-modal-header">
        <h2>${escapeHtml(fieldLabel(fieldKey))}</h2>
        <button class="overlay-close pid-modal-close" type="button" aria-label="${escapeHtml(t('cards.participant.modalClose', 'Close'))}">
          <i class="iconoir-xmark"></i>
        </button>
      </div>
      <div class="settings-modal-body">
        <label class="checkbox-row" style="margin-bottom:12px;">
          <input type="checkbox" class="pid-modal-key" ${state.use_for_key ? 'checked' : ''}>
          <span>${escapeHtml(t('cards.participant.useForKeyLabel', 'Use for anonymous code (hash)'))}</span>
        </label>
        <label class="checkbox-row" style="margin-bottom:12px;">
          <input type="checkbox" class="pid-modal-store" ${state.store ? 'checked' : ''}>
          <span>${escapeHtml(t('cards.participant.storeLabel', 'Store in results (DB)'))}</span>
        </label>
        ${optionsSection}
        <button class="btn-primary pid-modal-apply" type="button" style="width:100%; justify-content:center; margin-top:16px;">
          ${escapeHtml(t('cards.participant.modalApply', 'Apply'))}
        </button>
      </div>
    </div>`;

  document.body.appendChild(backdrop);
  _activeFieldModal = backdrop;

  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) closeFieldModal();
  });
  backdrop.querySelector('.pid-modal-close')?.addEventListener('click', closeFieldModal);

  const optionsEditor = backdrop.querySelector('.pid-options-editor');
  backdrop.querySelector('.pid-add-option')?.addEventListener('click', () => {
    optionsEditor.insertAdjacentHTML('beforeend', renderOptionRow(''));
    optionsEditor.lastElementChild?.querySelector('input')?.focus();
  });
  optionsEditor?.addEventListener('click', (event) => {
    const remove = event.target.closest('.pid-option-remove');
    if (remove && optionsEditor.querySelectorAll('.pid-option-row').length > 1) {
      remove.closest('.pid-option-row')?.remove();
    }
  });

  backdrop.querySelector('.pid-modal-apply')?.addEventListener('click', () => {
    const next = {
      use_for_key: Boolean(backdrop.querySelector('.pid-modal-key')?.checked),
      store: Boolean(backdrop.querySelector('.pid-modal-store')?.checked),
    };
    if (configurable) {
      next.options = [...backdrop.querySelectorAll('.pid-option-input')]
        .map((input) => input.value.trim())
        .filter(Boolean);
    }
    writeFieldState(editorEl, fieldKey, next);
    closeFieldModal();
  });

  backdrop._escHandler = (event) => { if (event.key === 'Escape') closeFieldModal(); };
  document.addEventListener('keydown', backdrop._escHandler);
}

function renderOptionRow(value) {
  return `
    <div class="pid-option-row">
      <input type="text" class="fi-input pid-option-input" value="${escapeHtml(value)}">
      <button type="button" class="pid-option-remove" aria-label="${escapeHtml(t('cards.participant.removeOption', 'Remove option'))}">
        <i class="iconoir-trash"></i>
      </button>
    </div>`;
}

export function collectConfig(el) {
  const fields = {};

  FIELD_ORDER.forEach((fieldKey) => {
    const enabled = Boolean(el.querySelector(`.pid-enabled[data-pid-field="${fieldKey}"]`)?.checked);
    const state = readFieldState(el, fieldKey);
    fields[fieldKey] = {
      enabled,
      use_for_key: enabled && state.use_for_key,
      store: enabled && state.store,
    };
    if (isConfigurable(fieldKey)) fields[fieldKey].options = state.options;
  });

  if (!FIELD_ORDER.some((fieldKey) => fields[fieldKey].enabled && fields[fieldKey].use_for_key)) {
    const fallbackKey = FIELD_ORDER.find((fieldKey) => fields[fieldKey].enabled) || FIELD_ORDER[0];
    fields[fallbackKey].enabled = true;
    fields[fallbackKey].use_for_key = true;
  }

  return {
    type: 'participant-id',
    prompt: el.querySelector('.q-prompt-input')?.value ?? defaultQuestion.prompt,
    code_label: el.querySelector('.q-code-label-input')?.value ?? defaultQuestion.code_label,
    fields,
  };
}

export function collectAnswer() {
  return _computedId;
}

export function collectMetadata() {
  return _computedMetadata ? { ..._computedMetadata } : {};
}

export function onInput(event) {
  const cardBody = event.target.closest('.pid-card-body');
  if (!cardBody) return false;
  void _updateHash(cardBody);
  return true;
}

async function _updateHash(cardBody) {
  const entries = collectRenderedFieldEntries(cardBody);
  const hasKeyField = entries.some((entry) => entry.useForKey);

  if (!entries.length || !hasKeyField || entries.some((entry) => !entry.value)) {
    _computedId = null;
    _computedMetadata = null;
    const box = cardBody.querySelector('.pid-code-box');
    if (box) box.hidden = true;
    cardBody.dispatchEvent(new Event('participantid:changed', { bubbles: true }));
    return;
  }

  try {
    const raw = entries
      .filter((entry) => entry.useForKey)
      .map((entry) => `${entry.key}:${entry.value.trim().toLowerCase()}`)
      .join('|');

    if (window.crypto && window.crypto.subtle) {
      const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
      const hex = Array.from(new Uint8Array(buffer)).map((b) => b.toString(16).padStart(2, '0')).join('');
      _computedId = hex.slice(0, 16);
    } else {
      let h1 = 0xdeadbeef ^ raw.length, h2 = 0x41c6ce57 ^ raw.length;
      for (let i = 0; i < raw.length; i++) {
        const ch = raw.charCodeAt(i);
        h1 = Math.imul(h1 ^ ch, 2654435761);
        h2 = Math.imul(h2 ^ ch, 1597334677);
      }
      h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
      h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
      _computedId = (Math.abs(h1).toString(16) + Math.abs(h2).toString(16)).padStart(16, '0').slice(0, 16);
    }

    _computedMetadata = {};
    entries
      .filter((entry) => entry.store)
      .forEach((entry) => {
        _computedMetadata[entry.key] = entry.value;
      });

    const display = cardBody.querySelector('.pid-code-display');
    if (display) display.textContent = _computedId.slice(0, 8);
    const box = cardBody.querySelector('.pid-code-box');
    if (box) box.hidden = false;
  } catch (error) {
    console.error('Hash generation failed:', error);
    _computedId = null;
    _computedMetadata = null;
  }

  cardBody.dispatchEvent(new Event('participantid:changed', { bubbles: true }));
}

function collectRenderedFieldEntries(cardBody) {
  return [...cardBody.querySelectorAll('[data-pid-field]')].map((control) => ({
    key: control.dataset.pidField,
    useForKey: control.dataset.pidUseForKey === '1',
    store: control.dataset.pidStore === '1',
    value: String(control.value || '').trim(),
  }));
}

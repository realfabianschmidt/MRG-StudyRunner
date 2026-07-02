import { t } from '../i18n.js';

const FIELD_ORDER = [
  'first_name',
  'last_name',
  'age_group',
  'childhood_area',
  'childhood_nearest_city',
];

const AGE_GROUPS = ['18-25', '26-35', '36-45', '46-60', '60+'];

const DEFAULT_FIELDS = {
  first_name: { enabled: true, use_for_key: true, store: false },
  last_name: { enabled: true, use_for_key: true, store: false },
  age_group: { enabled: true, use_for_key: true, store: true },
  childhood_area: { enabled: true, use_for_key: true, store: true },
  childhood_nearest_city: { enabled: true, use_for_key: true, store: true },
};

const FIELD_LABELS = {
  first_name: ['cards.participant.firstName', 'First name'],
  last_name: ['cards.participant.lastName', 'Last name'],
  age_group: ['cards.participant.ageGroup', 'Age group'],
  childhood_area: ['cards.participant.childhoodArea', 'Childhood area'],
  childhood_nearest_city: ['cards.participant.childhoodNearestCity', 'Nearest larger city in childhood'],
};

function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function cloneDefaultFields() {
  return JSON.parse(JSON.stringify(DEFAULT_FIELDS));
}

function fieldLabel(fieldKey) {
  const [key, fallback] = FIELD_LABELS[fieldKey] || [fieldKey, fieldKey];
  return t(key, fallback);
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
  const commonAttrs = [
    'class="fi-input pid-field"',
    `name="pid-${escapeHtml(fieldKey)}"`,
    `data-pid-field="${escapeHtml(fieldKey)}"`,
    `data-pid-use-for-key="${fieldConfig.use_for_key ? '1' : '0'}"`,
    `data-pid-store="${fieldConfig.store ? '1' : '0'}"`,
  ].join(' ');

  let control = '';
  if (fieldKey === 'age_group') {
    control = `
      <select ${commonAttrs}>
        <option value="">${escapeHtml(t('cards.participant.selectPlaceholder', 'Select...'))}</option>
        ${AGE_GROUPS.map((ageGroup) => `<option value="${escapeHtml(ageGroup)}">${escapeHtml(ageGroup)}</option>`).join('')}
      </select>`;
  } else if (fieldKey === 'childhood_area') {
    control = `
      <select ${commonAttrs}>
        <option value="">${escapeHtml(t('cards.participant.selectPlaceholder', 'Select...'))}</option>
        <option value="urban">${escapeHtml(t('cards.participant.childhoodAreaUrban', 'Urban'))}</option>
        <option value="rural">${escapeHtml(t('cards.participant.childhoodAreaRural', 'Rural'))}</option>
      </select>`;
  } else {
    const placeholder = fieldKey === 'childhood_nearest_city'
      ? t('cards.participant.childhoodNearestCityPlaceholder', 'e.g. Munich')
      : (fieldKey === 'first_name'
        ? t('cards.participant.firstNamePlaceholder', 'e.g. Anna')
        : t('cards.participant.lastNamePlaceholder', 'e.g. Miller'));
    control = `<input ${commonAttrs} type="text" autocomplete="off" autocorrect="off" spellcheck="false" placeholder="${escapeHtml(placeholder)}">`;
  }

  return `
    <div class="pid-field-row">
      <label class="pid-label">${label}</label>
      ${control}
    </div>`;
}

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
      <div class="pid-editor-matrix">
        <div class="pid-editor-row pid-editor-row--head">
          <span>${escapeHtml(t('cards.participant.fieldColumn', 'Field'))}</span>
          <span>${escapeHtml(t('cards.participant.askColumn', 'Ask'))}</span>
          <span>${escapeHtml(t('cards.participant.keyColumn', 'Key'))}</span>
          <span>${escapeHtml(t('cards.participant.storeColumn', 'Store'))}</span>
        </div>
        ${FIELD_ORDER.map((fieldKey) => renderEditorFieldRow(fieldKey, fields[fieldKey])).join('')}
      </div>
    </div>
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      ${escapeHtml(t('cards.participant.editorHint', 'Choose which fields are asked, used for the generated key, and stored in local results or Notion. At least one field must be used for the key.'))}
    </p>`;
}

function renderEditorFieldRow(fieldKey, fieldConfig) {
  return `
    <div class="pid-editor-row" data-pid-editor-row="${escapeHtml(fieldKey)}">
      <span class="pid-editor-field-name">${escapeHtml(fieldLabel(fieldKey))}</span>
      ${renderEditorCheckbox(fieldKey, 'enabled', fieldConfig.enabled, false)}
      ${renderEditorCheckbox(fieldKey, 'use_for_key', fieldConfig.use_for_key, !fieldConfig.enabled)}
      ${renderEditorCheckbox(fieldKey, 'store', fieldConfig.store, !fieldConfig.enabled)}
    </div>`;
}

function renderEditorCheckbox(fieldKey, setting, checked, disabled) {
  const label = `${fieldLabel(fieldKey)} ${setting}`;
  return `
    <label class="pid-editor-check" aria-label="${escapeHtml(label)}">
      <input type="checkbox"
        class="pid-editor-toggle"
        data-pid-field="${escapeHtml(fieldKey)}"
        data-pid-setting="${escapeHtml(setting)}"
        ${checked ? 'checked' : ''}
        ${disabled ? 'disabled' : ''}>
      <span></span>
    </label>`;
}

export function bindEditorEvents(editorEl) {
  syncParticipantFieldMatrix(editorEl);
  editorEl.addEventListener('input', (event) => {
    if (event.target?.matches?.('.pid-editor-toggle')) {
      syncParticipantFieldMatrix(editorEl);
    }
  });
}

function syncParticipantFieldMatrix(editorEl) {
  const rows = FIELD_ORDER
    .map((fieldKey) => editorEl.querySelector(`[data-pid-editor-row="${fieldKey}"]`))
    .filter(Boolean);

  rows.forEach((row) => {
    const enabledInput = row.querySelector('[data-pid-setting="enabled"]');
    const keyInput = row.querySelector('[data-pid-setting="use_for_key"]');
    const storeInput = row.querySelector('[data-pid-setting="store"]');
    const enabled = Boolean(enabledInput?.checked);

    [keyInput, storeInput].forEach((input) => {
      if (!input) return;
      input.disabled = !enabled;
      if (!enabled) input.checked = false;
    });
  });

  const keyInputs = rows.map((row) => row.querySelector('[data-pid-setting="use_for_key"]')).filter(Boolean);
  if (keyInputs.some((input) => input.checked && !input.disabled)) {
    return;
  }

  const fallbackRow = rows.find((row) => row.querySelector('[data-pid-setting="enabled"]')?.checked) || rows[0];
  const enabledInput = fallbackRow?.querySelector('[data-pid-setting="enabled"]');
  const keyInput = fallbackRow?.querySelector('[data-pid-setting="use_for_key"]');

  if (enabledInput && !enabledInput.checked) {
    enabledInput.checked = true;
  }
  if (keyInput) {
    keyInput.disabled = false;
    keyInput.checked = true;
  }
}

export function collectConfig(el) {
  const fields = {};

  FIELD_ORDER.forEach((fieldKey) => {
    const enabled = Boolean(el.querySelector(`[data-pid-field="${fieldKey}"][data-pid-setting="enabled"]`)?.checked);
    fields[fieldKey] = {
      enabled,
      use_for_key: enabled && Boolean(el.querySelector(`[data-pid-field="${fieldKey}"][data-pid-setting="use_for_key"]`)?.checked),
      store: enabled && Boolean(el.querySelector(`[data-pid-field="${fieldKey}"][data-pid-setting="store"]`)?.checked),
    };
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

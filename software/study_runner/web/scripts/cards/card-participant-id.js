function escapeHtml(v) {
  return String(v ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

export const meta = {
  type: 'participant-id',
  icon: 'user-badge-check',
  label: 'Participant ID',
  pill: 'pill-participant-id',
};

export const defaultQuestion = {
  type: 'participant-id',
  prompt: 'Bitte gib deine Daten zur anonymen Identifikation ein.',
  code_label: 'Dein anonymer Code',
  code_hint: 'Deine Eingaben werden lokal auf dem Gerät in eine unumkehrbare Zeichenfolge (SHA-256 Hash) umgewandelt und danach sofort verworfen. Deine echten Daten werden zu keinem Zeitpunkt gespeichert oder übertragen.',
};

// Module-level: the currently computed hash. Set async on input.
let _computedId = null;

export function renderStudy(q, _i) {
  const prompt = q.prompt || defaultQuestion.prompt;
  const codeLabel = q.code_label ?? defaultQuestion.code_label;
  const codeHint = q.code_hint ?? defaultQuestion.code_hint;

  return `
    <div class="q-type-tag"><i class="iconoir-user-badge-check"></i> Participant ID</div>
    <p class="q-prompt">${escapeHtml(prompt)}</p>
    <div class="pid-card-body">
      <div class="pid-fields">
        <div class="pid-field-row">
          <label class="pid-label">Vorname</label>
          <input class="fi-input pid-field" type="text" name="pid-firstname"
            autocomplete="off" autocorrect="off" spellcheck="false" placeholder="z.B. Anna">
        </div>
        <div class="pid-field-row">
          <label class="pid-label">Nachname</label>
          <input class="fi-input pid-field" type="text" name="pid-lastname"
            autocomplete="off" autocorrect="off" spellcheck="false" placeholder="z.B. Müller">
        </div>
        <div class="pid-field-row">
          <label class="pid-label">Geburtsdatum</label>
          <input class="fi-input pid-field" type="date" name="pid-birthdate">
        </div>
        <div class="pid-field-row">
          <label class="pid-label">Geburtsort</label>
          <input class="fi-input pid-field" type="text" name="pid-birthplace"
            autocomplete="off" autocorrect="off" spellcheck="false" placeholder="z.B. München">
        </div>
      </div>
      <div class="pid-code-box" hidden>
        <div class="pid-code-label">${escapeHtml(codeLabel)}</div>
        <div class="pid-code-display"></div>
        <div class="pid-code-hint">${escapeHtml(codeHint)}</div>
      </div>
    </div>`;
}

export function renderEditor(q) {
  return `
    <div class="field">
      <label>Prompt</label>
      <textarea class="fi-textarea q-prompt-input" rows="3">${escapeHtml(q.prompt || defaultQuestion.prompt)}</textarea>
    </div>
    <div class="field">
      <label>Titel für generierten Code</label>
      <input type="text" class="fi-input q-code-label-input" value="${escapeHtml(q.code_label ?? defaultQuestion.code_label)}">
    </div>
    <div class="field">
      <label>Hinweistext unter dem Code (Datenschutz)</label>
      <textarea class="fi-textarea q-code-hint-input" rows="3">${escapeHtml(q.code_hint ?? defaultQuestion.code_hint)}</textarea>
    </div>
    <p class="editor-hint" style="margin-top:0.75rem;font-size:0.8rem;opacity:0.6;">
      Dieses Feld erfasst Vorname, Nachname, Geburtsdatum und Geburtsort des Teilnehmers
      und berechnet daraus einen anonymen SHA-256-Hash als Participant-ID.
      Die Rohdaten werden niemals gespeichert.
    </p>`;
}

export function collectConfig(el) {
  return {
    type: 'participant-id',
    prompt: el.querySelector('.q-prompt-input')?.value ?? defaultQuestion.prompt,
    code_label: el.querySelector('.q-code-label-input')?.value ?? defaultQuestion.code_label,
    code_hint: el.querySelector('.q-code-hint-input')?.value ?? defaultQuestion.code_hint,
  };
}

export function collectAnswer() {
  return _computedId;
}

export function onInput(event) {
  const cardBody = event.target.closest('.pid-card-body');
  if (!cardBody) return false;
  void _updateHash(cardBody);
  return true;
}

async function _updateHash(cardBody) {
  const fields = ['firstname', 'lastname', 'birthdate', 'birthplace'];
  const vals = fields.map(n => (cardBody.querySelector(`[name="pid-${n}"]`)?.value ?? '').trim().toLowerCase());

  if (vals.some(v => !v)) {
    _computedId = null;
    const box = cardBody.querySelector('.pid-code-box');
    if (box) box.hidden = true;
    cardBody.dispatchEvent(new Event('participantid:changed', { bubbles: true }));
    return;
  }

  try {
    const raw = vals.join('|');
    
    // crypto.subtle erfordert HTTPS! Fallback für lokales HTTP-Testing:
    if (window.crypto && window.crypto.subtle) {
      const buffer = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(raw));
      const hex = Array.from(new Uint8Array(buffer)).map(b => b.toString(16).padStart(2, '0')).join('');
      _computedId = hex.slice(0, 16);
    } else {
      // Fallback-Hash ohne HTTPS
      let h1 = 0xdeadbeef ^ raw.length, h2 = 0x41c6ce57 ^ raw.length;
      for (let i = 0; i < raw.length; i++) {
        let ch = raw.charCodeAt(i);
        h1 = Math.imul(h1 ^ ch, 2654435761);
        h2 = Math.imul(h2 ^ ch, 1597334677);
      }
      h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
      h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
      _computedId = (Math.abs(h1).toString(16) + Math.abs(h2).toString(16)).padStart(16, '0').slice(0, 16);
    }

    const display = cardBody.querySelector('.pid-code-display');
    if (display) display.textContent = _computedId.slice(0, 8);
    const box = cardBody.querySelector('.pid-code-box');
    if (box) box.hidden = false;
  } catch (_e) {
    console.error("Hash generation failed:", _e);
    _computedId = null;
  }

  cardBody.dispatchEvent(new Event('participantid:changed', { bubbles: true }));
}

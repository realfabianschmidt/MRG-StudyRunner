// Lightweight UI localization for Study Runner.
//
// English is the default language; German is available. No framework is used.
// Mark elements in HTML with one of these attributes and call applyTranslations():
//   data-i18n              -> sets textContent
//   data-i18n-placeholder  -> sets the placeholder attribute
//   data-i18n-title        -> sets the title attribute
//   data-i18n-aria-label   -> sets the aria-label attribute
// For strings built in JavaScript, use t('some.key').
//
// To add a string: add the same key to locales/en.json and locales/de.json.

const SUPPORTED_LANGUAGES = ['en', 'de'];
const DEFAULT_LANGUAGE = 'en';
const STORAGE_KEY = 'studyRunnerLanguage';

let activeLanguage = DEFAULT_LANGUAGE;
let messages = {};

function pickInitialLanguage() {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && SUPPORTED_LANGUAGES.includes(saved)) {
      return saved;
    }
  } catch (error) {
    // localStorage can be blocked; fall back to the default language.
  }
  return DEFAULT_LANGUAGE;
}

export function getLanguage() {
  return activeLanguage;
}

export function getSupportedLanguages() {
  return [...SUPPORTED_LANGUAGES];
}

// Return the translated string for a key, or the fallback (or the key) if missing.
export function t(key, fallback) {
  if (Object.prototype.hasOwnProperty.call(messages, key)) {
    return messages[key];
  }
  return fallback !== undefined ? fallback : key;
}

async function loadMessages(language) {
  const response = await fetch(`/static/locales/${language}.json`, { cache: 'no-cache' });
  if (!response.ok) {
    throw new Error(`Could not load locale "${language}" (HTTP ${response.status})`);
  }
  return response.json();
}

function has(key) {
  return Object.prototype.hasOwnProperty.call(messages, key);
}

// Only overwrite an element when the key exists, so a failed locale load leaves the
// original (English) HTML in place instead of showing raw keys.
export function applyTranslations(root = document) {
  root.querySelectorAll('[data-i18n]').forEach((element) => {
    if (has(element.dataset.i18n)) element.textContent = messages[element.dataset.i18n];
  });
  root.querySelectorAll('[data-i18n-placeholder]').forEach((element) => {
    if (has(element.dataset.i18nPlaceholder)) {
      element.setAttribute('placeholder', messages[element.dataset.i18nPlaceholder]);
    }
  });
  root.querySelectorAll('[data-i18n-title]').forEach((element) => {
    if (has(element.dataset.i18nTitle)) {
      element.setAttribute('title', messages[element.dataset.i18nTitle]);
    }
  });
  root.querySelectorAll('[data-i18n-aria-label]').forEach((element) => {
    if (has(element.dataset.i18nAriaLabel)) {
      element.setAttribute('aria-label', messages[element.dataset.i18nAriaLabel]);
    }
  });
}

export async function setLanguage(language) {
  const next = SUPPORTED_LANGUAGES.includes(language) ? language : DEFAULT_LANGUAGE;
  messages = await loadMessages(next);
  activeLanguage = next;
  try {
    window.localStorage.setItem(STORAGE_KEY, next);
  } catch (error) {
    // Ignore storage errors; the language still applies for this session.
  }
  document.documentElement.lang = next;
  applyTranslations(document);
  document.dispatchEvent(new CustomEvent('languagechange', { detail: { language: next } }));
}

// Load the saved or default language and translate the current page once.
export async function initI18n() {
  await setLanguage(pickInitialLanguage());
}

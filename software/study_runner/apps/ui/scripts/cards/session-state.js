// Per-session card state: the one place a card may keep anything the DOM does
// not already hold (a selection Set, a computed hash, an open overlay).
//
// Rule for every card: no mutable module-level variables. Either read the
// answer back from the rendered DOM, or keep it here via cardState(). The card
// loader calls resetAllCardState() whenever questions are (re)built and when a
// session ends, so nothing one participant entered can reach the next one.
// tests/test_card_session_isolation.py enforces the rule for every card.js.

const stores = new Map();
const resetHandlers = new Set();

// State for card `owner` at question index `index` in the running session.
// `init` builds the fresh value the first time it is asked for per session.
export function cardState(owner, index, init = () => ({})) {
  let store = stores.get(owner);
  if (!store) {
    store = new Map();
    stores.set(owner, store);
  }
  const key = String(index);
  if (!store.has(key)) store.set(key, init());
  return store.get(key);
}

// Register cleanup that is not plain data, e.g. closing an open overlay.
// Handlers stay registered across sessions; they run on every reset.
export function onSessionReset(handler) {
  if (typeof handler === 'function') resetHandlers.add(handler);
}

export function resetAllCardState() {
  for (const handler of resetHandlers) {
    try {
      handler();
    } catch (error) {
      console.warn('[cards] session reset handler failed:', error);
    }
  }
  stores.clear();
}

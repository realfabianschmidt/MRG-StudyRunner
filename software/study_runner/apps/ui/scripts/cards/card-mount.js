// The one way to bring a card to life, used by the participant page and by
// the live preview next to the study editor alike:
//
//   mountCard(element, question, index, { mode })   render + bindInteractions
//   dispatchCardHook('onClick', event)               delegated clicks/inputs
//
// `mode` is 'study' (a participant answers) or 'preview' (the editor shows
// the card; nothing is recorded). A card's bindInteractions(element, index,
// { mode }) must work in both; most cards never need to look at it.
import { CARDS } from './index.js';
import { renderInfoBottom, renderOptionalTag } from './card-info.js';

export function mountCard(element, question, index, { mode = 'study' } = {}) {
  const cardModule = CARDS[question?.type];
  if (!element || !cardModule) return null;
  element.innerHTML = renderOptionalTag(question) + cardModule.renderStudy(question, index) + renderInfoBottom(question);
  cardModule.bindInteractions?.(element, index, { mode });
  return cardModule;
}

// Card hooks are dispatched to every loaded card module; each one checks its
// own selectors. `skip` leaves out modules the caller handles itself.
export function dispatchCardHook(hookName, event, { skip = [] } = {}) {
  for (const cardModule of new Set(Object.values(CARDS))) {
    if (skip.includes(cardModule)) continue;
    cardModule[hookName]?.(event);
  }
}

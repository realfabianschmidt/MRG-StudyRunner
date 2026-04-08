import * as likert    from './card-likert.js';
import * as semantic   from './card-semantic.js';
import * as choice     from './card-choice.js';
import * as slider     from './card-slider.js';
import * as ranking    from './card-ranking.js';
import * as text       from './card-text.js';
import * as stimulus   from './card-stimulus.js';

// Registry: type string → card module
export const CARDS = {
  likert,
  semantic,
  choice,
  single:   choice,    // single choice reuses the choice module
  slider,
  ranking,
  text,
  stimulus,
};

// Ordered list for the "Add question" type picker
// stimulus appears first so it is easy to find when building a new study
export const CARD_TYPES = [
  { type: 'stimulus', module: stimulus },
  { type: 'likert',   module: likert   },
  { type: 'semantic', module: semantic },
  { type: 'choice',   module: choice,  overrideMeta: choice.meta        },
  { type: 'single',   module: choice,  overrideMeta: choice.metaSingle  },
  { type: 'slider',   module: slider   },
  { type: 'ranking',  module: ranking  },
  { type: 'text',     module: text     },
];

export function defaultFor(type) {
  if (type === 'single') return JSON.parse(JSON.stringify(choice.defaultQuestionSingle));
  const mod = CARDS[type];
  return mod ? JSON.parse(JSON.stringify(mod.defaultQuestion)) : { type, prompt: '' };
}

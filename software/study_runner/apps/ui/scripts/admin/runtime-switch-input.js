/**
 * Input for the Off | On | Restart runtime switch on the sensor tiles
 * (drawn by sensor-connection-panel.js). A click picks a field, a push
 * counts by its direction, not its distance, and the arrow keys push.
 * `onMove(pluginKey, target)` gets 'off' | 'on' | 'restart'.
 */
import { runtimeSwitchClick, runtimeSwitchStep } from './sensor-connection-panel.js';

const PUSH_THRESHOLD_PX = 8;
const DRAG_LIMIT_PX = 40;

export function bindRuntimeSwitch(root, onMove) {
  let push = null;
  // A push already acted; the click the browser fires after it must not.
  let ignoreClick = false;
  const usable = (control) => control && control.getAttribute('aria-disabled') !== 'true';

  root.addEventListener('click', (event) => {
    const control = event.target.closest('[data-runtime-switch]');
    if (!control) return;
    const option = event.target.closest('.runtime-switch-option');
    if (!ignoreClick && option && !option.disabled) {
      const target = runtimeSwitchClick(control.dataset.position, option.dataset.target);
      if (target) onMove(control.dataset.runtimeSwitch, target);
    }
    ignoreClick = false;
  });
  root.addEventListener('pointerdown', (event) => {
    const control = event.target.closest('[data-runtime-switch]');
    if (!usable(control)) return;
    push = { key: control.dataset.runtimeSwitch, position: control.dataset.position, x: event.clientX, control };
  });
  root.addEventListener('pointermove', (event) => {
    if (!push) return;
    const dx = Math.max(-DRAG_LIMIT_PX, Math.min(DRAG_LIMIT_PX, event.clientX - push.x));
    push.control.style.setProperty('--runtime-drag', `${dx}px`);
    push.control.classList.toggle('is-dragging', Math.abs(dx) > 3);
  });
  window.addEventListener('pointerup', (event) => {
    if (!push) return;
    const done = push;
    push = null;
    done.control.classList.remove('is-dragging');
    done.control.style.removeProperty('--runtime-drag');
    const dx = event.clientX - done.x;
    if (Math.abs(dx) < PUSH_THRESHOLD_PX) return; // a click: handled above
    ignoreClick = true;
    window.setTimeout(() => { ignoreClick = false; }, 400);
    const target = runtimeSwitchStep(done.position, Math.sign(dx));
    if (target) onMove(done.key, target);
  });
  root.addEventListener('keydown', (event) => {
    const control = event.target.closest('[data-runtime-switch]');
    if (!control || !['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    if (!usable(control)) return;
    const target = runtimeSwitchStep(control.dataset.position, event.key === 'ArrowRight' ? 1 : -1);
    if (target) onMove(control.dataset.runtimeSwitch, target);
  });
}

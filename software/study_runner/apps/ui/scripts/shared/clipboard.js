/**
 * Copy text to the clipboard. The Clipboard API needs a secure context, which
 * an admin page opened over plain http on the lab network is not, so a hidden
 * textarea is the fallback.
 */
export async function copyText(value) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch (error) {
    console.warn('[clipboard] Clipboard API failed, using the fallback:', error);
  }
  const input = document.createElement('textarea');
  input.value = value;
  input.setAttribute('readonly', '');
  input.style.position = 'fixed';
  input.style.opacity = '0';
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  input.remove();
}

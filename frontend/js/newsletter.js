import { h, apiUrl } from './dom.js';

export function initNewsletter({ meta } = {}) {
  const form = document.getElementById('sub-form');
  const btn = document.getElementById('sub-btn');
  const msg = document.getElementById('sub-msg');
  const lede = document.getElementById('sub-lede');

  if (meta && meta.send_time) {
    lede.textContent = `One email a day at ${meta.send_time} (${meta.timezone}) with the three stories that matter, quick hits, and brain fuel. About five minutes. No ads, no filler, one-click unsubscribe.`;
  }

  const show = (text, bad = false) => {
    msg.hidden = false;
    msg.classList.toggle('err', bad);
    msg.replaceChildren(h('span', { class: 'who', text: bad ? 'OOPS' : 'DONE!' }), text);
  };

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = new FormData(form);
    const email = String(data.get('email') || '').trim();
    if (!email || !form.elements.email.checkValidity()) {
      show('That doesn\'t look like a valid email address.', true);
      form.elements.email.focus();
      return;
    }
    btn.disabled = true;
    const label = btn.textContent;
    btn.textContent = 'SENDING…';
    try {
      const res = await fetch(apiUrl('/api/subscribe'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, name: data.get('name') || '', website: data.get('website') || '' }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.error || `Something went wrong (${res.status}).`);
      show(body.message || 'Check your inbox.');
      form.reset();
    } catch (err) {
      show(err.message || 'Network problem. Please try again.', true);
    } finally {
      btn.disabled = false;
      btn.textContent = label;
    }
  });

  // Live preview of the email in a modal (served by the backend from the latest edition).
  const dialog = document.getElementById('preview');
  const frame = document.getElementById('preview-frame');
  document.getElementById('preview-btn').addEventListener('click', () => {
    frame.src = apiUrl('/api/email-preview');
    if (typeof dialog.showModal === 'function') dialog.showModal();
    else window.open(apiUrl('/api/email-preview'), '_blank', 'noopener');
  });
  document.getElementById('preview-close').addEventListener('click', () => dialog.close());
  dialog.addEventListener('click', (e) => { if (e.target === dialog) dialog.close(); });
  dialog.addEventListener('close', () => { frame.removeAttribute('src'); });
}

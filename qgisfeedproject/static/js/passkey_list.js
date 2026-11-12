// The server renders the list, so every string stays in the translation catalogue.
(function () {
  const slot = document.getElementById('passkey-list');
  if (!slot) {
    return;
  }
  fetch(slot.dataset.passkeyUrl, {
    credentials: 'same-origin',
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
  })
    .then((response) => {
      if (!response.ok) {
        throw new Error('passkeys');
      }
      return response.text();
    })
    .then((html) => {
      slot.innerHTML = html;
    })
    .catch(() => {
      const warning = document.createElement('div');
      warning.className = 'notification is-warning';
      warning.textContent = slot.dataset.errorMessage;
      slot.replaceChildren(warning);
    })
    .finally(() => {
      slot.setAttribute('aria-busy', 'false');
    });
})();

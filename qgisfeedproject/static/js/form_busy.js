/**
 * Show that a form is working, and stop it being sent twice.
 *
 * Creating QGIS accounts asks auth.qgis.org about every person in the wave,
 * and people press again while they wait. One handler covers every form.
 * Opt a form out with `data-no-busy`, and name an element in
 * `data-busy-overlay` to reveal it while the form is in flight.
 */

const BUSY = 'is-loading';
// A dataset key with a dash and a lowercase letter throws, so this stays camelCase.
const FLAG = 'ssoBusySubmitting';
const LOCKED = 'sso-busy-locked';

function overlayFor(form) {
  const id = form.dataset.busyOverlay;
  return id ? document.getElementById(id) : null;
}

function markBusy(form, submitter) {
  form.dataset[FLAG] = 'yes';

  if (submitter) {
    submitter.classList.add(BUSY);
    submitter.setAttribute('aria-busy', 'true');
    // On the next tick, so the browser has already sent the button's own value.
    window.setTimeout(() => {
      submitter.disabled = true;
    }, 0);
  }

  const overlay = overlayFor(form);
  if (overlay) {
    overlay.hidden = false;
    document.documentElement.classList.add(LOCKED);
  }
}

function clearBusy(form) {
  delete form.dataset[FLAG];
  form.querySelectorAll(`.${BUSY}`).forEach((el) => {
    el.classList.remove(BUSY);
    el.removeAttribute('aria-busy');
    el.disabled = false;
  });
  const overlay = overlayFor(form);
  if (overlay) {
    overlay.hidden = true;
    document.documentElement.classList.remove(LOCKED);
  }
}

function onSubmit(event) {
  const form = event.target;
  if (!(form instanceof HTMLFormElement) || 'noBusy' in form.dataset) {
    return;
  }

  if (form.dataset[FLAG]) {
    event.preventDefault();
    return;
  }

  // The browser will refuse an invalid form, so locking it would strand the reader.
  if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
    return;
  }

  markBusy(form, event.submitter);
}

document.addEventListener('submit', onSubmit);

// The back button restores the page as it was left, still busy.
window.addEventListener('pageshow', (event) => {
  if (event.persisted) {
    document.querySelectorAll('form').forEach(clearBusy);
  }
});

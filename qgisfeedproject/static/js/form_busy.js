/**
 * Show that a form is working, and stop it being sent twice.
 *
 * Some of what this site does takes a while. Creating QGIS accounts talks to
 * auth.qgis.org once or twice for every person in the wave, and until that
 * comes back the page looks exactly as it did before the button was pressed.
 * People press again. That sends the run a second time.
 *
 * One handler on the document covers every form on the site rather than each
 * template growing its own. Opt a form out with `data-no-busy`, and name an
 * element in `data-busy-overlay` to reveal it while the form is in flight.
 */

const BUSY = 'is-loading';
// camelCase, and it has to be. Assigning to a dataset key that contains a dash
// followed by a lowercase letter throws a SyntaxError, so a hyphenated name
// here would abort the handler before anything was marked busy.
const FLAG = 'ssoBusySubmitting';
const LOCKED = 'sso-busy-locked';

/**
 * Keep the pressed button's name and value in the submission.
 *
 * A disabled control is not successful, so its name and value never reach the
 * server. Several forms here read the button to decide what to do, so the pair
 * is copied into a hidden input before the button is disabled.
 */
function preserveSubmitter(form, submitter) {
  if (!submitter || !submitter.name) {
    return;
  }
  const carried = document.createElement('input');
  carried.type = 'hidden';
  carried.name = submitter.name;
  carried.value = submitter.value;
  // Marked so it can be taken out again when the page comes back from the
  // cache, or a later submit would carry it twice.
  carried.dataset.busyCarried = 'yes';
  form.appendChild(carried);
}

function overlayFor(form) {
  const id = form.dataset.busyOverlay;
  return id ? document.getElementById(id) : null;
}

function markBusy(form, submitter) {
  form.dataset[FLAG] = 'yes';

  if (submitter) {
    preserveSubmitter(form, submitter);
    submitter.classList.add(BUSY);
    submitter.setAttribute('aria-busy', 'true');
    // Disabling after the value is carried, and on the next tick so the
    // browser has already collected the form data for this submission.
    window.setTimeout(() => {
      submitter.disabled = true;
    }, 0);
  }

  const overlay = overlayFor(form);
  if (overlay) {
    overlay.hidden = false;
    // The overlay covers the viewport, so scrolling the page behind it only
    // moves something nobody can reach.
    document.documentElement.classList.add(LOCKED);
  }
}

function clearBusy(form) {
  delete form.dataset[FLAG];
  form.querySelectorAll('input[data-busy-carried]').forEach((el) => el.remove());
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

  // Already in flight. Refuse the second press rather than sending the run
  // again.
  if (form.dataset[FLAG]) {
    event.preventDefault();
    return;
  }

  // A form the browser is about to refuse never leaves, so locking its button
  // would strand the reader on a page they cannot submit.
  if (typeof form.checkValidity === 'function' && !form.checkValidity()) {
    return;
  }

  markBusy(form, event.submitter);
}

document.addEventListener('submit', onSubmit);

// Coming back with the back button restores the page as it was, button still
// disabled and overlay still up. Put it back the way it started.
window.addEventListener('pageshow', (event) => {
  if (event.persisted) {
    document.querySelectorAll('form').forEach(clearBusy);
  }
});

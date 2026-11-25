// The people pages: select every candidate at once, and copy a setup link.
document.addEventListener('change', (event) => {
  if (event.target.id !== 'select-all') {
    return;
  }
  document.querySelectorAll('.sso-row-select').forEach((box) => {
    box.checked = event.target.checked;
  });
});

document.addEventListener('click', (event) => {
  const button = event.target.closest('#copy-link');
  if (!button) {
    return;
  }
  const field = document.getElementById('issued-link');
  const label = button.querySelector('span:last-child');
  field.select();
  navigator.clipboard?.writeText(field.value).then(() => {
    label.textContent = button.dataset.copied;
  });
});

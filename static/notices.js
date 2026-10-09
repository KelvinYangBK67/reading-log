// Keep success feedback unobtrusive; errors stay visible until dismissed.
document.querySelectorAll('[data-notice]').forEach((notice) => {
  const dismiss = () => notice.remove();
  notice.querySelector('[data-dismiss-notice]')?.addEventListener('click', dismiss);

  const delay = Number(notice.dataset.dismissAfter);
  if (Number.isFinite(delay) && delay > 0) {
    window.setTimeout(dismiss, delay);
  }
});

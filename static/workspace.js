(() => {
  const workspace = document.querySelector('[data-workspace]');
  if (!workspace) return;
  const detailPane = workspace.querySelector('[data-detail-pane]');
  const bookSearch = workspace.querySelector('[data-search-input]');
  const plannedList = workspace.querySelector('[data-planned-list]');
  const resizer = workspace.querySelector('[data-resizer]');
  const csrf = document.querySelector('input[name="csrf_token"]')?.value || '';
  const storageKey = 'reading-log-list-width';

  const bookPane = workspace.querySelector('.book-pane');
  const scrollKey = 'reading-log-index-scroll';
  try {
    const previous = Number(sessionStorage.getItem(scrollKey));
    if (previous > 0) bookPane.scrollTop = previous;
  } catch (_) {}
  bookPane.addEventListener('scroll', () => {
    try { sessionStorage.setItem(scrollKey, String(bookPane.scrollTop)); } catch (_) {}
  });


  function setWidth(ratio) {
    const width = workspace.getBoundingClientRect().width;
    const minimum = Math.min(320, width * 0.45);
    const left = Math.min(width * 0.60, Math.max(minimum, width * ratio));
    const actual = left / width;
    workspace.style.setProperty('--list-width', left + 'px');
    resizer.setAttribute('aria-valuenow', String(Math.round(actual * 100)));
    return actual;
  }

  try {
    const saved = Number(localStorage.getItem(storageKey));
    if (saved > 0 && saved < 1) setWidth(saved);
  } catch (_) { /* Local storage can be unavailable. */ }

  resizer.addEventListener('pointerdown', (event) => {
    if (window.matchMedia('(max-width: 760px)').matches) return;
    resizer.setPointerCapture(event.pointerId);
    const move = (pointer) => {
      const bounds = workspace.getBoundingClientRect();
      setWidth((pointer.clientX - bounds.left) / bounds.width);
    };
    const done = () => {
      resizer.removeEventListener('pointermove', move);
      resizer.removeEventListener('pointerup', done);
      resizer.removeEventListener('pointercancel', done);
      try {
        localStorage.setItem(storageKey, resizer.getAttribute('aria-valuenow') / 100);
      } catch (_) {}
    };
    resizer.addEventListener('pointermove', move);
    resizer.addEventListener('pointerup', done);
    resizer.addEventListener('pointercancel', done);
    move(event);
  });
  resizer.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight'].includes(event.key)) return;
    event.preventDefault();
    const current = Number(resizer.getAttribute('aria-valuenow')) / 100;
    const adjusted = setWidth(current + (event.key === 'ArrowRight' ? 0.02 : -0.02));
    try { localStorage.setItem(storageKey, adjusted); } catch (_) {}
  });

  function confirmDiscard() {
    const form = detailPane.querySelector('[data-record-form]:not([hidden])');
    const field = form?.querySelector('textarea');
    return !field || field.value === field.defaultValue ||
      window.confirm('尚有未儲存的記錄，確定切換書籍嗎？');
  }

  document.addEventListener('click', async (event) => {
    const toggle = event.target.closest('[data-toggle-record]');
    if (toggle && detailPane.contains(toggle)) {
      const form = detailPane.querySelector('[data-record-form]');
      form.hidden = false;
      detailPane.querySelector('[data-record-display]').hidden = true;
      form.querySelector('textarea').focus();
      return;
    }
    const cancel = event.target.closest('[data-cancel-record]');
    if (cancel && detailPane.contains(cancel)) {
      const form = detailPane.querySelector('[data-record-form]');
      form.querySelector('textarea').value = form.querySelector('textarea').defaultValue;
      form.hidden = true;
      detailPane.querySelector('[data-record-display]').hidden = false;
      return;
    }
    const link = event.target.closest('a[data-detail-url]');
    if (!link || !workspace.contains(link) || event.ctrlKey || event.metaKey ||
        event.shiftKey || event.altKey) return;
    event.preventDefault();
    if (!confirmDiscard()) return;
    try {
      const response = await fetch(link.dataset.detailUrl);
      if (!response.ok) throw new Error('Unable to load book');
      detailPane.innerHTML = await response.text();
      detailPane.scrollTop = 0;
      workspace.querySelectorAll('.book-row').forEach((item) => {
        item.classList.toggle('is-selected', item.contains(link));
      });
      workspace.querySelectorAll('a[data-detail-url]').forEach((anchor) => {
        if (anchor === link) anchor.setAttribute('aria-current', 'true');
        else anchor.removeAttribute('aria-current');
      });
      history.replaceState(null, '', link.href);
    } catch (_) {
      window.location.href = link.href;
    }
  });

  bookSearch.addEventListener('input', () => {
    const value = bookSearch.value.trim().toLocaleLowerCase();
    let visible = 0;
    workspace.querySelectorAll('[data-book-row]').forEach((row) => {
      row.hidden = !row.dataset.search.includes(value);
      if (!row.hidden) visible++;
    });
    workspace.querySelectorAll('[data-year-group]').forEach((group) => {
      group.hidden = !group.querySelector('[data-book-row]:not([hidden])');
    });
    workspace.querySelectorAll('[data-group]').forEach((group) => {
      group.hidden = Boolean(value) && !group.querySelector('[data-book-row]:not([hidden])');
    });
    workspace.querySelector('[data-no-matches]').hidden = visible > 0 || !value;
  });

  if (!plannedList) return;
  let dragged = null;
  plannedList.addEventListener('dragstart', (event) => {
    const row = event.target.closest('[data-book-row]');
    if (!row || bookSearch.value.trim()) {
      event.preventDefault();
      return;
    }
    dragged = row;
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', row.dataset.bookId);
    row.classList.add('is-dragging');
  });
  plannedList.addEventListener('dragover', (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest('[data-book-row]');
    if (!target || target === dragged) return;
    const midpoint = target.getBoundingClientRect().top + target.offsetHeight / 2;
    plannedList.insertBefore(dragged, event.clientY < midpoint ? target : target.nextSibling);
  });
  plannedList.addEventListener('dragend', async () => {
    if (!dragged) return;
    dragged.classList.remove('is-dragging');
    dragged = null;
    const ids = [...plannedList.querySelectorAll('[data-book-row]')].map(
      (row) => Number(row.dataset.bookId)
    );
    try {
      const response = await fetch(plannedList.dataset.reorderUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Reading-Token': csrf },
        body: JSON.stringify({ ids }),
      });
      if (!response.ok) throw new Error('Failed to save order');
    } catch (_) {
      alert('儲存順序失敗，頁面將重新載入。');
      location.reload();
    }
  });
})();

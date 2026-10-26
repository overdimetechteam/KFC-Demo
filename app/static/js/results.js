/* Status filter checkboxes for a results card (used on the match page and the saved report). */
(function () {
  function bindResultFilters(root) {
    root = root || document;
    const boxes = root.querySelectorAll('.flt');
    const apply = () => {
      const on = new Set([...boxes].filter(b => b.checked).map(b => b.value));
      root.querySelectorAll('tr[data-status]').forEach(tr => {
        tr.hidden = !on.has(tr.dataset.status);
      });
    };
    boxes.forEach(b => b.addEventListener('change', apply));
    apply();

    // "Header & control checks" are collapsed until the button is clicked
    root.querySelectorAll('.checks-toggle').forEach(btn => {
      const body = root.querySelector('#' + btn.getAttribute('aria-controls'));
      if (!body) return;
      btn.addEventListener('click', () => {
        const open = body.hidden;
        body.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        btn.querySelector('.checks-toggle-label').textContent = open ? 'Hide checks' : 'Show checks';
      });
    });
  }
  window.bindResultFilters = bindResultFilters;
  document.addEventListener('DOMContentLoaded', () => bindResultFilters(document));
})();

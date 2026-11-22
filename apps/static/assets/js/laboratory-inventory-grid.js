(function () {
  "use strict";
  let draftNumber = 0;
  const table = document.querySelector('[data-inventory-grid]');
  if (!table) return;

  function refreshTotals() {
    const totals = new Map();
    table.querySelectorAll('tr[data-inventory-row]').forEach(function (row) {
      const inputs = row.querySelectorAll('input[type="number"]');
      if (!inputs.length) return;
      let total = 0;
      inputs.forEach(function (input) {
        const value = Number(input.value || 0);
        if (Number.isFinite(value)) total += value;
      });
      const cell = row.querySelector('[data-inventory-row-total]');
      if (cell) cell.textContent = String(total);
      totals.set(row.dataset.accession, (totals.get(row.dataset.accession) || 0) + total);
    });
    table.querySelectorAll('[data-accession-count-total]').forEach(function (cell) {
      if (totals.has(cell.dataset.accessionCountTotal)) {
        cell.textContent = String(totals.get(cell.dataset.accessionCountTotal));
      }
    });
  }

  table.addEventListener('click', function (event) {
    const add = event.target.closest('[data-add-inventory-row]');
    if (add) {
      const template = document.getElementById(add.dataset.template);
      const footer = add.closest('tr');
      if (!template || !footer) return;
      const fragment = template.content.cloneNode(true);
      const draftId = 'draft-' + (++draftNumber);
      fragment.querySelectorAll('[id], [form]').forEach(function (element) {
        ['id', 'form'].forEach(function (attribute) {
          if (element.hasAttribute(attribute)) {
            element.setAttribute(attribute, element.getAttribute(attribute).replace('__ROW__', draftId));
          }
        });
      });
      const firstInput = fragment.querySelector('input[type="number"]');
      footer.before(fragment);
      if (firstInput) firstInput.focus();
      refreshTotals();
    }
    const remove = event.target.closest('[data-discard-inventory-row]');
    if (remove) {
      remove.closest('tr[data-unsaved-inventory-row]')?.remove();
      refreshTotals();
    }
  });
  table.addEventListener('input', refreshTotals);
})();

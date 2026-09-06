/* Progressive enhancement: all routes and saves remain server-rendered. */
(() => {
  const toggle = document.querySelector('.menu-toggle');
  const shade = document.querySelector('.sidebar-shade');
  function closeMenu() {
    document.body.classList.remove('sidebar-open');
    toggle?.setAttribute('aria-expanded', 'false');
    if (shade) shade.hidden = true;
  }
  toggle?.addEventListener('click', () => {
    const open = !document.body.classList.contains('sidebar-open');
    document.body.classList.toggle('sidebar-open', open);
    toggle.setAttribute('aria-expanded', String(open));
    shade.hidden = !open;
  });
  shade?.addEventListener('click', closeMenu);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
  document.querySelectorAll('[data-dismiss]').forEach(button => button.addEventListener('click', () => button.closest('[role=status]').remove()));
  const dialog = document.querySelector('.search-dialog');
  const openSearch = () => { if (dialog && !dialog.open) { dialog.showModal(); dialog.querySelector('input').focus(); } };
  document.querySelector('[data-open-search]')?.addEventListener('click', openSearch);
  document.querySelector('[data-close-search]')?.addEventListener('click', () => dialog.close());
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && dialog) { e.preventDefault(); openSearch(); } });
  const form = document.querySelector('[data-wizard]');
  if (!form) return;
  const panels = [...form.querySelectorAll('[data-panel]')];
  const steps = [...form.querySelectorAll('[data-step]')];
  const prev = form.querySelector('[data-prev]');
  const next = form.querySelector('[data-next]');
  const save = form.querySelector('[data-save]');
  let current = 0;
  form.classList.add('is-enhanced');
  form.noValidate = true;
  function show(index, focus = true) {
    current = index;
    panels.forEach((panel, i) => { panel.hidden = i !== index; });
    steps.forEach((step, i) => { if (i === index) step.setAttribute('aria-current', 'step'); else step.removeAttribute('aria-current'); });
    prev.hidden = index === 0;
    next.hidden = index === panels.length - 1;
    save.hidden = index !== panels.length - 1;
    if (focus) { const heading = panels[index].querySelector('h2'); heading.tabIndex = -1; heading.focus(); }
  }
  function valid(index) {
    const invalid = [...panels[index].querySelectorAll('input,select,textarea')].find(field => !field.checkValidity());
    if (invalid) { show(index, false); invalid.reportValidity(); return false; }
    return true;
  }
  steps.forEach((step, index) => step.addEventListener('click', () => {
    if (index > current) { for (let i = 0; i < index; i++) if (!valid(i)) return; }
    show(index);
  }));
  prev.addEventListener('click', () => show(current - 1));
  next.addEventListener('click', () => { if (valid(current)) show(current + 1); });
  const guardian = document.getElementById('id_responsavel');
  const fresh = document.querySelector('.new-guardian');
  function guardianMode() {
    const existing = Boolean(guardian.value);
    fresh.hidden = existing;
    fresh.querySelectorAll('input').forEach(input => {
      input.disabled = existing;
      input.required = !existing && ['id_responsavel_nome', 'id_responsavel_whatsapp'].includes(input.id);
    });
  }
  guardian.addEventListener('change', guardianMode);
  guardianMode();
  form.addEventListener('submit', e => {
    for (let i = 0; i < panels.length; i++) { if (!valid(i)) { e.preventDefault(); return; } }
  });
  const errorIndex = panels.findIndex(panel => panel.querySelector('.errorlist'));
  show(errorIndex < 0 ? 0 : errorIndex, false);
})();

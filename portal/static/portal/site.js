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
  // Delegado em document: cobre também o botão "Copiar código" dentro do
  // modal de detalhe, cujo conteúdo é injetado depois via fetch.
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy]');
    if (!button) return;
    try {
      await navigator.clipboard.writeText(button.dataset.copy);
      const original = button.textContent;
      button.textContent = 'Copiado!';
      setTimeout(() => { button.textContent = original; }, 1800);
    } catch (_) {
      button.closest('.pix-copia')?.querySelector('textarea')?.select();
    }
  });
  const dialog = document.querySelector('.search-dialog');
  const openSearch = () => { if (dialog && !dialog.open) { dialog.showModal(); dialog.querySelector('input').focus(); } };
  document.querySelector('[data-open-search]')?.addEventListener('click', openSearch);
  document.querySelector('[data-close-search]')?.addEventListener('click', () => dialog.close());
  document.addEventListener('keydown', e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k' && dialog) { e.preventDefault(); openSearch(); } });
  const formset = document.querySelector('[data-formset]');
  if (formset) {
    const rows = formset.querySelector('[data-formset-rows]');
    const template = formset.querySelector('[data-formset-empty]');
    const total = document.getElementById('id_faixa-TOTAL_FORMS');
    formset.querySelector('[data-formset-add]')?.addEventListener('click', () => {
      const index = Number(total.value);
      const holder = document.createElement('div');
      holder.innerHTML = template.innerHTML.replace(/__prefix__/g, index).trim();
      const row = holder.firstElementChild;
      rows.appendChild(row);
      total.value = index + 1;
      row.querySelector('input, select')?.focus();
    });
    rows.addEventListener('click', event => {
      const button = event.target.closest('[data-faixa-remove]');
      if (!button) return;
      const row = button.closest('.faixa-row');
      const id = row.querySelector('input[name$="-id"]');
      const del = row.querySelector('input[name$="-DELETE"]');
      if (id && id.value) {
        if (del) del.checked = true;               // faixa salva: marca para excluir ao salvar
      } else {
        row.querySelectorAll('input:not([type=hidden]), select').forEach(el => {
          if (el.type === 'checkbox') el.checked = false; else el.value = '';
        });                                          // faixa nova: esvazia para ser ignorada
      }
      row.hidden = true;
    });
  }

  const detalheDialog = document.querySelector('[data-detalhe-dialog]');
  if (detalheDialog) {
    const body = detalheDialog.querySelector('[data-detalhe-body]');
    document.querySelectorAll('a[data-detalhe]').forEach(link => link.addEventListener('click', async event => {
      event.preventDefault();
      body.innerHTML = '<p class="text-secondary">Carregando…</p>';
      detalheDialog.showModal();
      try {
        const response = await fetch(link.href, { headers: { 'X-Requested-With': 'fetch' } });
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const content = doc.getElementById('detalhe-conteudo');
        if (content) body.replaceChildren(content); else window.location = link.href;
      } catch (_) {
        window.location = link.href;
      }
    }));
  }

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
  const self = document.getElementById('id_proprio_responsavel');
  const selfFields = document.querySelector('.self-guardian');
  function guardianMode() {
    const isSelf = Boolean(self?.checked);
    const existing = Boolean(guardian.value) && !isSelf;
    guardian.disabled = isSelf;
    guardian.closest('.field-group').hidden = isSelf;
    if (selfFields) {
      selfFields.hidden = !isSelf;
      selfFields.querySelectorAll('input').forEach(input => {
        input.disabled = !isSelf;
        input.required = isSelf && input.id === 'id_aluno_whatsapp';
      });
    }
    const cpf = document.getElementById('id_cpf');
    if (cpf) cpf.required = isSelf;
    fresh.hidden = existing || isSelf;
    fresh.querySelectorAll('input').forEach(input => {
      input.disabled = existing || isSelf;
      input.required = !existing && !isSelf && ['id_responsavel_nome', 'id_responsavel_whatsapp'].includes(input.id);
    });
  }
  self?.addEventListener('change', guardianMode);
  guardian.addEventListener('change', guardianMode);
  guardianMode();

  const mModalidade = document.getElementById('id_matricula-modalidade');
  const mUnidade = document.getElementById('id_matricula-unidade');
  const mTurma = document.getElementById('id_matricula-turma');
  const mValor = document.getElementById('id_matricula-valor_mensalidade');
  const mVenc = document.getElementById('id_matricula-dia_vencimento');
  if (mTurma) {
    const options = [...mTurma.options];
    function filterTurmas() {
      const mod = mModalidade && mModalidade.value;
      const uni = mUnidade && mUnidade.value;
      options.forEach(opt => {
        if (!opt.value) return;
        const okMod = !mod || opt.dataset.modalidade === mod;
        const okUni = !uni || !opt.dataset.unidade || opt.dataset.unidade === uni;
        opt.hidden = opt.disabled = !(okMod && okUni);
        if (opt.hidden && opt.selected) mTurma.value = '';
      });
    }
    function fillFromTurma() {
      const opt = mTurma.selectedOptions[0];
      if (!opt || !opt.value) return;
      if (mValor && !mValor.value && opt.dataset.valor) mValor.value = opt.dataset.valor;
      if (mVenc && !mVenc.value && opt.dataset.vencimento) mVenc.value = opt.dataset.vencimento;
    }
    mModalidade?.addEventListener('change', filterTurmas);
    mUnidade?.addEventListener('change', filterTurmas);
    mTurma.addEventListener('change', fillFromTurma);
    filterTurmas();
  }

  form.addEventListener('submit', e => {
    for (let i = 0; i < panels.length; i++) { if (!valid(i)) { e.preventDefault(); return; } }
  });
  const errorIndex = panels.findIndex(panel => panel.querySelector('.errorlist'));
  show(errorIndex < 0 ? 0 : errorIndex, false);
})();

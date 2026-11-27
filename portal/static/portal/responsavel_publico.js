(() => {
  const escolha = document.getElementById('id_proprio_responsavel');
  const proprio = document.querySelector('[data-cpf-atleta]');
  const outro = document.querySelector('[data-outro-responsavel]');
  if (!escolha || !proprio || !outro) return;
  function atualizar() {
    proprio.hidden = !escolha.checked;
    outro.hidden = escolha.checked;
    for (const grupo of [proprio, outro]) {
      grupo.querySelectorAll('input').forEach(campo => {
        campo.disabled = grupo.hidden;
        campo.required = !grupo.hidden;
      });
    }
  }
  escolha.addEventListener('change', atualizar);
  atualizar();
})();

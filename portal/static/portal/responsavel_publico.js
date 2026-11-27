(() => {
  const nascimento = document.getElementById('id_data_nascimento');
  const busca = document.querySelector('[data-autorizados-buscar]');
  const atualizarBusca = () => {
    if (!nascimento || !busca) return;
    const partes = nascimento.value.split('-').map(Number);
    const hoje = new Date();
    let idade = hoje.getFullYear() - partes[0];
    if (hoje.getMonth() + 1 < partes[1] || (hoje.getMonth() + 1 === partes[1] && hoje.getDate() < partes[2])) idade--;
    const maior = partes.length === 3 && Number.isFinite(idade) && idade >= 18;
    busca.hidden = maior;
    busca.querySelectorAll('input,textarea').forEach(campo => {campo.disabled = maior; campo.required = !maior;});
  };
  nascimento?.addEventListener('change', atualizarBusca);
  atualizarBusca();
  const escolha = document.getElementById('id_proprio_responsavel');
  const proprio = document.querySelector('[data-cpf-atleta]');
  const outro = document.querySelector('[data-outro-responsavel]');
  if (!escolha || !proprio || !outro) return;
  function atualizar() {
    proprio.hidden = false;
    outro.hidden = escolha.checked;
    for (const grupo of [proprio, outro]) {
      grupo.querySelectorAll('input').forEach(campo => {
        campo.disabled = grupo.hidden;
        campo.required = grupo === proprio ? escolha.checked : !grupo.hidden;
      });
    }
  }
  escolha.addEventListener('change', atualizar);
  atualizar();
})();

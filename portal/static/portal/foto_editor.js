(() => {
  const dialog = document.querySelector('[data-foto-editor]');
  if (!dialog || !window.HTMLDialogElement) return;
  const canvas = dialog.querySelector('[data-foto-canvas]');
  const context = canvas.getContext('2d');
  const slider = dialog.querySelector('[data-foto-zoom]');
  const error = dialog.querySelector('[data-foto-erro]');
  const apply = dialog.querySelector('[data-foto-aplicar]');
  let input, image, angle = 0, x = 0, y = 0, pointer = null, objectUrl = null;
  const draw = (target = canvas) => {
    if (!image) return;
    const size = target.width;
    const rotated = Math.abs(angle % 180) === 90;
    const width = rotated ? image.naturalHeight : image.naturalWidth;
    const height = rotated ? image.naturalWidth : image.naturalHeight;
    const scale = Math.max(400 / width, 400 / height) * Number(slider.value);
    x = Math.max(-(width * scale - 400) / 2, Math.min((width * scale - 400) / 2, x));
    y = Math.max(-(height * scale - 400) / 2, Math.min((height * scale - 400) / 2, y));
    const ctx = target === canvas ? context : target.getContext('2d');
    ctx.clearRect(0, 0, size, size);
    ctx.save(); ctx.scale(size / 400, size / 400);
    ctx.translate(200 + x, 200 + y); ctx.rotate(angle * Math.PI / 180);
    ctx.drawImage(image, -image.naturalWidth * scale / 2, -image.naturalHeight * scale / 2,
      image.naturalWidth * scale, image.naturalHeight * scale);
    ctx.restore();
  };
  const reset = () => {angle = 0; x = y = 0; slider.value = '1'; draw();};
  const open = async (field, source) => {
    input = field; image = null; error.hidden = true; apply.disabled = true;
    if (objectUrl) {URL.revokeObjectURL(objectUrl); objectUrl = null;}
    if (!dialog.open) dialog.showModal();
    try {
      if (source instanceof File) {
        if (source.size > 10 * 1024 * 1024) throw new Error('A foto deve ter até 10 MB.');
        objectUrl = URL.createObjectURL(source); source = objectUrl;
      }
      const photo = new Image(); photo.src = source; await photo.decode();
      if (photo.naturalWidth * photo.naturalHeight > 40000000) throw new Error('A foto é muito grande. Escolha uma versão menor.');
      image = photo; reset(); apply.disabled = false;
    } catch (e) {error.textContent = e.message.startsWith('A foto') ? e.message : 'Não foi possível abrir a foto. Use JPG, PNG ou WEBP.'; error.hidden = false;}
  };
  document.querySelectorAll('input[type=file][name=foto]').forEach(field => {
    field.addEventListener('change', () => {if (field.files[0]) open(field, field.files[0]);});
  });
  document.querySelectorAll('[data-ajustar-foto-atual]').forEach(button => button.addEventListener('click', () => {
    const field = button.closest('form').querySelector('input[type=file][name=foto]');
    if (field) open(field, button.dataset.ajustarFotoAtual);
  }));
  slider.addEventListener('input', () => draw());
  dialog.querySelectorAll('[data-foto-girar]').forEach(button => button.addEventListener('click', () => {angle += Number(button.dataset.fotoGirar); x = y = 0; draw();}));
  dialog.querySelector('[data-foto-reset]').addEventListener('click', reset);
  dialog.querySelectorAll('[data-foto-fechar]').forEach(button => button.addEventListener('click', () => dialog.close()));
  canvas.addEventListener('pointerdown', event => {pointer = {id:event.pointerId,x:event.clientX,y:event.clientY}; canvas.setPointerCapture(event.pointerId);});
  canvas.addEventListener('pointermove', event => {
    if (!pointer || pointer.id !== event.pointerId) return;
    const ratio = 400 / canvas.getBoundingClientRect().width;
    x += (event.clientX - pointer.x) * ratio; y += (event.clientY - pointer.y) * ratio;
    pointer.x = event.clientX; pointer.y = event.clientY; draw();
  });
  canvas.addEventListener('pointerup', () => {pointer = null;});
  canvas.addEventListener('pointercancel', () => {pointer = null;});
  canvas.addEventListener('keydown', event => {
    const passos = {ArrowLeft:[-10,0],ArrowRight:[10,0],ArrowUp:[0,-10],ArrowDown:[0,10]};
    if (!passos[event.key]) return;
    event.preventDefault(); x += passos[event.key][0]; y += passos[event.key][1]; draw();
  });
  apply.addEventListener('click', () => {
    if (!image || !input) return;
    apply.disabled = true;
    const result = document.createElement('canvas'); result.width = result.height = 800; draw(result);
    result.toBlob(blob => {
      if (!blob) {apply.disabled = false; return;}
      const files = new DataTransfer(); files.items.add(new File([blob], 'foto-ajustada.jpg', {type:'image/jpeg'}));
      input.files = files.files;
      let preview = input.parentElement.querySelector('.foto-ajuste-preview');
      if (!preview) {preview = document.createElement('img'); preview.className = 'foto-ajuste-preview'; preview.alt = 'Prévia da foto ajustada'; input.after(preview);}
      const anterior = preview.dataset.previewUrl;
      if (anterior) URL.revokeObjectURL(anterior);
      preview.src = preview.dataset.previewUrl = URL.createObjectURL(blob);
      dialog.close(); apply.disabled = false;
    }, 'image/jpeg', .9);
  });
})();

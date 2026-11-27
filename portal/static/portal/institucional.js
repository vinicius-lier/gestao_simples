(() => {
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const menu = document.getElementById('menu');
  const toggle = document.querySelector('[data-menu-toggle]');
  const setMenu = open => {
    if (!menu || !toggle) return;
    menu.hidden = !open;
    toggle.setAttribute('aria-expanded', String(open));
    toggle.textContent = open ? 'Fechar' : 'Menu';
  };
  toggle?.addEventListener('click', () => setMenu(menu.hidden));
  document.querySelectorAll('[data-menu-link]').forEach(link => link.addEventListener('click', () => setMenu(false)));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && menu && !menu.hidden) { setMenu(false); toggle.focus(); }
  });

  const carousel = document.querySelector('[data-carrossel]');
  if (carousel) {
    const track = carousel.querySelector('[data-carrossel-trilho]');
    const slides = [...carousel.querySelectorAll('[data-slide]')];
    const thumbs = [...carousel.querySelectorAll('[data-carrossel-ir]')];
    let current = 0;
    const show = index => {
      if (!slides.length) return;
      current = (index + slides.length) % slides.length;
      track.style.transform = `translateX(-${current * 100}%)`;
      slides.forEach((slide, i) => {
        slide.inert = i !== current;
        slide.setAttribute('aria-hidden', String(i !== current));
      });
      thumbs.forEach((thumb, i) => thumb.setAttribute('aria-pressed', String(i === current)));
    };
    carousel.querySelector('[data-carrossel-anterior]')?.addEventListener('click', () => show(current - 1));
    carousel.querySelector('[data-carrossel-proximo]')?.addEventListener('click', () => show(current + 1));
    thumbs.forEach((thumb, i) => thumb.addEventListener('click', () => show(i)));
    carousel.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
        event.preventDefault(); show(current + (event.key === 'ArrowLeft' ? -1 : 1));
      }
    });
    let touchX = null;
    carousel.addEventListener('touchstart', event => { touchX = event.touches[0].clientX; }, {passive:true});
    carousel.addEventListener('touchend', event => {
      if (touchX === null) return;
      const delta = event.changedTouches[0].clientX - touchX;
      if (Math.abs(delta) > 40) show(current + (delta < 0 ? 1 : -1));
      touchX = null;
    }, {passive:true});
    show(0);
  }

  const hero = document.getElementById('heroImg');
  const progress = document.getElementById('prog');
  const header = document.getElementById('hdr');
  let ticking = false;
  const update = () => {
    const y = window.scrollY;
    const height = document.documentElement.scrollHeight - window.innerHeight;
    if (progress) progress.style.width = `${height > 0 ? y / height * 100 : 0}%`;
    if (header) header.style.boxShadow = y > 10 ? '0 6px 20px -12px rgba(0,0,0,.35)' : 'none';
    if (hero && !reduced && y < 900) hero.style.transform = `scale(1.08) translateY(${y * .06}px)`;
    ticking = false;
  };
  window.addEventListener('scroll', () => {
    if (!ticking) { ticking = true; requestAnimationFrame(update); }
  }, {passive:true});
  update();
  if (!reduced && 'IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      entry.target.animate([{opacity:.65,transform:'translateY(16px)'},{opacity:1,transform:'none'}],
        {duration:650,easing:'cubic-bezier(.2,.7,.2,1)'});
      observer.unobserve(entry.target);
    }), {threshold:.1});
    document.querySelectorAll('[data-reveal]').forEach(element => observer.observe(element));
  }
})();

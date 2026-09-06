/* ============================================================
   Escola de Judô Keiko Fukuda — main.js
   JavaScript enxuto, sem dependências. Enhancement progressivo:
   a página funciona sem JS; aqui apenas melhoramos a experiência.
   ============================================================ */

"use strict";

/* ------------------------------------------------------------
   1. CONFIGURAÇÃO EDITÁVEL
   Ajuste apenas os valores abaixo.
   ------------------------------------------------------------ */
const CONFIG = {
  // [CONTEÚDO A DEFINIR] Número no formato internacional, só dígitos:
  // DDI (55) + DDD + número. Ex.: 5521999999999
  whatsappNumber: "5521000000000",

  whatsappMessage:
    "Olá! Conheci a Escola de Judô Keiko Fukuda pelo site e gostaria de saber mais sobre a aula experimental.",

  // Perfil oficial informado pela escola:
  instagramUrl: "https://www.instagram.com/escoladejudokeikofukuda/",
};

/* ------------------------------------------------------------
   2. WHATSAPP
   Aplica o link em todo elemento com [data-whatsapp].
   Um data-whatsapp-message="..." no elemento personaliza a mensagem.
   ------------------------------------------------------------ */
function buildWhatsappUrl(message) {
  const text = encodeURIComponent(message || CONFIG.whatsappMessage);
  return `https://wa.me/${CONFIG.whatsappNumber}?text=${text}`;
}

function initWhatsappLinks() {
  document.querySelectorAll("[data-whatsapp]").forEach((el) => {
    el.setAttribute(
      "href",
      buildWhatsappUrl(el.getAttribute("data-whatsapp-message"))
    );
    el.setAttribute("target", "_blank");
    el.setAttribute("rel", "noopener");
  });

  document.querySelectorAll("[data-instagram]").forEach((el) => {
    el.setAttribute("href", CONFIG.instagramUrl);
    el.setAttribute("target", "_blank");
    el.setAttribute("rel", "noopener");
  });
}

/* ------------------------------------------------------------
   3. MENU MOBILE
   ------------------------------------------------------------ */
function initMobileMenu() {
  const toggle = document.querySelector("[data-nav-toggle]");
  const nav = document.querySelector("[data-nav]");
  const backdrop = document.querySelector("[data-nav-backdrop]");
  if (!toggle || !nav) return;

  const setState = (open) => {
    toggle.setAttribute("aria-expanded", String(open));
    nav.classList.toggle("is-open", open);
    if (backdrop) backdrop.classList.toggle("is-open", open);
    document.body.style.overflow = open ? "hidden" : "";
    if (open) {
      const firstLink = nav.querySelector("a");
      if (firstLink) firstLink.focus({ preventScroll: true });
    }
  };

  toggle.addEventListener("click", () => {
    setState(toggle.getAttribute("aria-expanded") !== "true");
  });

  if (backdrop) backdrop.addEventListener("click", () => setState(false));

  nav.querySelectorAll("a").forEach((link) =>
    link.addEventListener("click", () => setState(false))
  );

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && toggle.getAttribute("aria-expanded") === "true") {
      setState(false);
      toggle.focus();
    }
  });

  // Se a tela crescer para desktop, garante o menu fechado.
  const desktop = window.matchMedia("(min-width: 768px)");
  desktop.addEventListener("change", (e) => {
    if (e.matches) setState(false);
  });
}

/* ------------------------------------------------------------
   4. HEADER STICKY (sombra ao rolar)
   ------------------------------------------------------------ */
function initStickyHeader() {
  const header = document.querySelector("[data-header]");
  if (!header) return;

  const update = () => {
    header.classList.toggle("is-stuck", window.scrollY > 8);
  };
  update();
  window.addEventListener("scroll", update, { passive: true });
}

/* ------------------------------------------------------------
   5. ANIMAÇÕES DE SCROLL (IntersectionObserver)
   ------------------------------------------------------------ */
function initScrollReveal() {
  const items = document.querySelectorAll(".reveal");
  if (!items.length) return;

  const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce || !("IntersectionObserver" in window)) {
    items.forEach((el) => el.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries, obs) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-visible");
        obs.unobserve(entry.target);
      });
    },
    { threshold: 0.14, rootMargin: "0px 0px -8% 0px" }
  );

  items.forEach((el, i) => {
    // pequeno atraso escalonado entre irmãos próximos
    const siblingIndex = [...(el.parentElement?.children || [])].indexOf(el);
    el.style.setProperty("--reveal-delay", `${Math.min(siblingIndex, 5) * 70}ms`);
    observer.observe(el);
  });
}

/* ------------------------------------------------------------
   6. SCROLLSPY — marca o link ativo do menu
   ------------------------------------------------------------ */
function initScrollSpy() {
  const links = [...document.querySelectorAll(".primary-nav__link[href^='#']")];
  if (!links.length || !("IntersectionObserver" in window)) return;

  const map = new Map();
  links.forEach((link) => {
    const section = document.querySelector(link.getAttribute("href"));
    if (section) map.set(section, link);
  });

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        links.forEach((l) => l.removeAttribute("aria-current"));
        const link = map.get(entry.target);
        if (link) link.setAttribute("aria-current", "true");
      });
    },
    { rootMargin: "-45% 0px -50% 0px" }
  );

  map.forEach((_, section) => observer.observe(section));
}

/* ------------------------------------------------------------
   7. LIGHTBOX DA GALERIA
   ------------------------------------------------------------ */
function initLightbox() {
  const gallery = document.querySelector("[data-gallery]");
  const lightbox = document.querySelector("[data-lightbox]");
  if (!gallery || !lightbox) return;

  const imgEl = lightbox.querySelector(".lightbox__img");
  const closeBtn = lightbox.querySelector("[data-lightbox-close]");
  let lastFocused = null;

  const open = (src, alt) => {
    lastFocused = document.activeElement;
    imgEl.src = src;
    imgEl.alt = alt || "";
    lightbox.classList.add("is-open");
    lightbox.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    closeBtn.focus();
  };

  const close = () => {
    lightbox.classList.remove("is-open");
    lightbox.setAttribute("aria-hidden", "true");
    imgEl.removeAttribute("src");
    document.body.style.overflow = "";
    if (lastFocused) lastFocused.focus();
  };

  gallery.querySelectorAll("[data-full]").forEach((btn) => {
    btn.addEventListener("click", () => {
      open(btn.getAttribute("data-full"), btn.getAttribute("data-alt"));
    });
  });

  closeBtn.addEventListener("click", close);
  lightbox.addEventListener("click", (e) => {
    if (e.target === lightbox) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && lightbox.classList.contains("is-open")) close();
  });
}

/* ------------------------------------------------------------
   8. ANO DO RODAPÉ
   ------------------------------------------------------------ */
function initFooterYear() {
  const el = document.querySelector("[data-year]");
  if (el) el.textContent = new Date().getFullYear();
}

/* ------------------------------------------------------------
   INICIALIZAÇÃO
   ------------------------------------------------------------ */
document.addEventListener("DOMContentLoaded", () => {
  initWhatsappLinks();
  initMobileMenu();
  initStickyHeader();
  initScrollReveal();
  initScrollSpy();
  initLightbox();
  initFooterYear();
});

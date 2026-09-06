# Escola de Judô Keiko Fukuda — Landing Page

Landing page institucional, estática e responsiva (Mobile First), sem
frameworks. Feita em HTML5, CSS3 e JavaScript puro.

Funil pretendido: **Instagram → Landing Page → WhatsApp → Aula experimental → Matrícula.**

---

## Estrutura

```
landing/
├── index.html
├── css/
│   ├── reset.css        # normalização mínima
│   ├── variables.css    # design tokens (cores, tipografia, espaços, sombras…)
│   ├── style.css        # componentes e layout base (mobile first)
│   └── responsive.css   # media queries min-width
├── js/
│   └── main.js          # menu, sticky header, reveal on scroll, lightbox, WhatsApp
├── assets/
│   ├── images/          # fotos reais da escola (adicionar)
│   ├── icons/           # ícones SVG estão inline no HTML
│   └── logo/            # logo-fukuda.png
└── README.md
```

Ordem de carregamento do CSS (importa): `reset → variables → style → responsive`.

---

## Como visualizar

Abra `index.html` direto no navegador, ou sirva a pasta:

```sh
# a partir de landing/
python -m http.server 8000
# depois acesse http://localhost:8000
```

Sem build. Sem dependências. Só a fonte do Google Fonts é externa.

---

## Configuração rápida (o que editar primeiro)

### 1. WhatsApp e Instagram — `js/main.js`

No topo do arquivo, em `CONFIG`:

```js
const CONFIG = {
  whatsappNumber: "5521000000000",   // [DEFINIR] DDI+DDD+número, só dígitos
  whatsappMessage: "Olá! Conheci a Escola de Judô Keiko Fukuda pelo site...",
  instagramUrl: "https://www.instagram.com/escoladejudokeikofukuda/",
};
```

- Todo elemento com `data-whatsapp` recebe o link `https://wa.me/NUMERO?text=MENSAGEM`.
- Um atributo `data-whatsapp-message="..."` no próprio elemento personaliza a mensagem daquele botão.
- Todo elemento com `data-instagram` recebe a URL do Instagram.
- Enquanto o JS não roda, esses links apontam para `#` ou para a seção `#experimental`.

> Observação: existe um número (`5521997616485`) usado em outra parte do
> repositório (`portal/`). Confirme com a escola antes de reutilizá-lo aqui.

### 2. Textos e placeholders

Procure no `index.html` por marcadores explícitos:

- `[CONTEÚDO A DEFINIR]`, `[ENDEREÇO A DEFINIR]`, `[TELEFONE A DEFINIR]`,
  `[HORÁRIOS A DEFINIR]`, `[FAIXA ETÁRIA A DEFINIR]`
- `[NOME A DEFINIR]`, `[GRADUAÇÃO A DEFINIR]`, `[FUNÇÃO A DEFINIR]` (professores)
- `[Depoimento a definir]` (prova social — trocar por relatos reais)
- Seção **"Por que Keiko Fukuda"**: **todo o texto é provisório**. Não há
  fatos históricos confirmados — validar com a escola antes de publicar.

Nada de dados inventados (endereço, telefone, professores, graduações,
horários, número de alunos, anos, títulos, campeonatos).

### 3. Imagens

Os blocos cinza (`.media-placeholder`, `.gallery__ph`, `.teacher-card__photo`)
são placeholders. Para trocar:

- **Hero:** substituir o `<div class="media-placeholder">` dentro de
  `.hero__frame` por `<img src="assets/images/hero.jpg" alt="…" width="900" height="1125" loading="eager">`.
- **Galeria:** trocar cada `.gallery__ph` por `<img … loading="lazy">`. O
  botão de cada `figure` já tem `data-full="assets/images/galeria-N.jpg"`
  para o lightbox — aponte para a versão ampliada.
- **Professores / origem do nome:** trocar os placeholders por `<img>`.
- **Open Graph:** criar `assets/images/og-image.jpg` (1200×630) e conferir
  a `<meta property="og:image">`.
- **Favicon:** hoje usa `assets/logo/logo-fukuda.png` como placeholder.

### 4. SEO / domínio

- `<link rel="canonical">` e `og:url` estão com `https://www.exemplo.com.br/` —
  trocar pelo domínio real.

### 5. Mapa

Na seção Contato, `.location__map` é um placeholder. Trocar pelo `<iframe>`
de embed do Google Maps quando o endereço estiver confirmado.

### 6. Ideograma

O caractere **柔** ("jū", suavidade — primeiro caractere de 柔道/jūdō) é
usado só como marca-d'água decorativa no hero. Validar leitura/estilo com a
escola antes de publicar.

---

## Identidade visual

Definida em `css/variables.css`:

| Token | Valor | Uso |
|---|---|---|
| `--color-primary` | `#b32025` | vermelho japonês, CTAs, acentos |
| `--color-primary-dark` | `#8c171b` | hover / active |
| `--color-ink` | `#171717` | títulos, seções escuras |
| `--color-text` | `#303030` | corpo de texto |
| `--color-muted` | `#6b6b6b` | textos secundários |
| `--color-bg` | `#f8f6f1` | fundo off-white (washi) |
| `--color-surface` | `#ffffff` | cartões |
| `--color-border` | `#e4e1da` | linhas |

**Tipografia:** *Noto Serif* (títulos) + *Inter* (texto/UI), via Google Fonts.
Também há escala de espaçamento, raios, sombras, `--container` e durações de
transição como tokens.

Direção de arte: editorial, muito espaço em branco, keyline vermelha fina,
círculo vermelho (hinomaru) como âncora gráfica discreta. Sem estética
agressiva de academia de luta.

---

## Seções

1. Header (sticky, menu hamburger no mobile, CTA "Aula Experimental")
2. Hero — "Muito mais que aprender Judô."
3. A Escola — pilares: Respeito, Disciplina, Confiança, Desenvolvimento, Comunidade
4. Por que Keiko Fukuda — faixa escura, **texto provisório**
5. Nossas aulas — Infantil / Juvenil / Adulto
6. Metodologia — Aprender · Praticar · Evoluir · Conviver (timeline no desktop)
7. Professores — cartões com placeholders
8. Galeria — mosaico assimétrico + lightbox
9. Prova social — depoimentos (placeholders)
10. CTA Aula Experimental — faixa vermelha → WhatsApp
11. Contato / Localização — endereço, horários, telefone, WhatsApp, Instagram, mapa
12. Footer + botão flutuante de WhatsApp

---

## Acessibilidade

- HTML semântico, headings em ordem, `lang="pt-BR"`.
- Skip link para o conteúdo.
- Menu mobile com `aria-expanded`/`aria-controls`, fecha com `Esc`, clique
  fora e ao selecionar um item; foco movido para o menu ao abrir.
- Lightbox com `role="dialog"`/`aria-modal`, fecha com `Esc`/backdrop, foco
  devolvido ao elemento de origem.
- `:focus-visible` com anel vermelho em todos os interativos.
- `prefers-reduced-motion`: desativa animações, reveal e o pulso do botão de
  WhatsApp. *(única exceção de `!important` no projeto está em `reset.css`,
  no bloco padrão de redução de movimento.)*
- Alvos de toque ≥ 44px; contraste AA nos textos.

## Responsividade

Pensada para: **320 · 375 · 430 · 768 · 1024 · 1440 · 1920**.
Mobile first; `responsive.css` só adiciona com `min-width`. Breakpoints:
480, 640, 768 (navegação desktop), 1024 (metodologia horizontal), 1280, 1536.

## Performance

- Sem bibliotecas JS. `main.js` (~5 KB) com `defer`.
- `IntersectionObserver` para reveal e scrollspy.
- `preconnect` para o Google Fonts, `display=swap`, poucos pesos.
- Ícones SVG inline (sem requisições extras). Usar `loading="lazy"` nas
  imagens da galeria ao inseri-las.

---

## Checklist antes de publicar

- [ ] `CONFIG.whatsappNumber` real em `js/main.js`
- [ ] Textos da seção "Por que Keiko Fukuda" validados
- [ ] Professores: fotos, nomes, graduações e funções reais
- [ ] Depoimentos reais (com autorização)
- [ ] Fotos reais no hero e na galeria
- [ ] Endereço, horários e telefone reais + `<iframe>` do mapa
- [ ] `canonical`, `og:url` e `og:image` com o domínio/arquivo finais
- [ ] Revisar o uso do ideograma 柔
- [ ] Favicon definitivo

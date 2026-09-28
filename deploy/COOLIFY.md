# Publicar no Coolify

Passo a passo para colocar o sistema no ar pelo Coolify: aplicação Django
(Dockerfile na raiz), PostgreSQL, rotinas agendadas, Pix pela Woovi com
repasse automático para a chave Pix da academia e WhatsApp pela Evolution API.

Ordem recomendada: **Woovi (habilitar subcontas) → banco → aplicação → primeiro
acesso → tarefas agendadas → Woovi no sandbox → WhatsApp → Woovi em
produção**. Suba **uma** academia primeiro.

---

## 0. Antes de começar

- **Woovi: peça ao suporte a habilitação de _Subcontas_** (crédito e saque de
  subconta) na sua
  conta (sandbox e produção). Sem isso, a criação da subconta e o Pix com
  split são recusados — o sistema não consegue receber.
- Um domínio (ou subdomínio) com registro DNS **A** apontando para o IP do
  servidor do Coolify. Ex.: `gestao.seudominio.com.br`.
- O repositório conectado ao Coolify pelo **GitHub App** (Sources → GitHub App),
  para o deploy automático a cada push.
- No servidor: **Servers → (seu servidor) → General → Timezone =
  `America/Sao_Paulo`**. As tarefas agendadas usam esse fuso.

## 1. Banco PostgreSQL

1. No projeto (ex.: `gestao-simples`, ambiente `production`): **+ New →
   Database → PostgreSQL** (versão 17).
2. Deixe **Make it publicly available desligado**. A aplicação acessa pela
   rede interna do Coolify.
3. Anote, na aba **General**, o **Username**, a **Password**, o **Initial
   Database** e o host da **Postgres URL (internal)**, que é o nome do
   contêiner, algo como `abc123xyz`. Eles viram as variáveis `POSTGRES_*`.
4. Aba **Backups**: ative um backup agendado, de preferência para um S3
   (Settings → S3). Sem backup, um problema no servidor apaga as mensalidades
   e o histórico de recebimentos.

## 2. Aplicação

1. **+ New → Application → (repositório pelo GitHub App)**. Branch: `main`.
2. **Build Pack: `Dockerfile`** (o arquivo está na raiz do repositório). Se a
   aplicação foi criada com **Nixpacks** (o padrão do Coolify), troque em
   **Configuration → General → Build Pack**. O Nixpacks ignora o Dockerfile,
   sobe com `migrate && gunicorn` e **não roda o `collectstatic`**: o site abre,
   mas **sem CSS nem JS**. O Dockerfile roda o `collectstatic` no build e o
   WhiteNoise serve `/static/`.
3. **Ports Exposes: `8000`**.
4. **Domains**: `https://gestao.seudominio.com.br`. O Coolify emite o HTTPS
   (Let's Encrypt) e redireciona HTTP → HTTPS sozinho.
5. **Health check**: deixe o do Coolify **desligado**. O Dockerfile já tem um
   `HEALTHCHECK` que chama `/health/` (200 `{"status": "ok", "database": "ok"}`
   com o banco acessível; 503 sem ele), e o Coolify usa o dele. Se preferir
   o do Coolify: caminho `/health/`, porta `8000`.
6. Aba **Environment Variables**: cole as variáveis abaixo. Nenhuma precisa
   ser marcada como *Build Variable*.

```dotenv
# Django
DJANGO_DEBUG=false
# gere com: python -c "import secrets; print(secrets.token_urlsafe(64))"
DJANGO_SECRET_KEY=
# 127.0.0.1 e localhost são obrigatórios: o health check chama por eles.
ALLOWED_HOSTS=gestao.seudominio.com.br,127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=https://gestao.seudominio.com.br
SITE_URL=https://gestao.seudominio.com.br
SESSION_COOKIE_SECURE=true
CSRF_COOKIE_SECURE=true
# O proxy do Coolify já força HTTPS; ligar isto quebra o health check interno.
SECURE_SSL_REDIRECT=false
# Suba para 31536000 depois que o HTTPS estiver estável.
SECURE_HSTS_SECONDS=0
WEB_CONCURRENCY=2

# Banco (valores do passo 1)
DB_ENGINE=postgresql
POSTGRES_DB=
POSTGRES_USER=
POSTGRES_PASSWORD=
# Só o host (nome do contêiner), nunca a URL inteira — senão o deploy cai
# com "UnicodeError: label too long".
POSTGRES_HOST=
POSTGRES_PORT=5432
POSTGRES_SSLMODE=prefer
DB_CONN_MAX_AGE=60

# Woovi (Pix). O padrão do código é produção (https://api.woovi.com).
# Para a fase de testes (passo 5), use o sandbox:
WOOVI_BASE_URL=https://api.woovi-sandbox.com
WOOVI_APP_ID=

# WhatsApp via Evolution (passo 6): URL do serviço Evolution e a
# AUTHENTICATION_API_KEY dele. A academia só informa o número e lê o QR Code.
EVOLUTION_BASE_URL=
EVOLUTION_API_KEY=
EVOLUTION_TIMEOUT=15
# Opcional: webhook de status da conexão (/webhooks/evolution/<instancia>/).
EVOLUTION_WEBHOOK_TOKEN=
```

7. **Deploy**. A cada deploy o contêiner roda `migrate` antes de subir o
   Gunicorn. Acompanhe em **Deployments → logs**. Se aparecer
   `ImproperlyConfigured ... DJANGO_SECRET_KEY`, a chave está vazia ou curta.

O `WOOVI_APP_ID` nunca aparece em tela, log ou banco: ele só vai no header
das chamadas à Woovi.

## 3. Primeiro acesso

Na aplicação: aba **Terminal** → contêiner da app:

```bash
python manage.py createsuperuser
```

Depois, em `https://gestao.seudominio.com.br/admin/`: cadastre a **Academia**
e, em **Acessos às academias**, vincule a proprietária a ela, marcando
*administrador da academia*. O superusuário é da plataforma (você); a
proprietária usa só o portal.

Migrar dados do SQLite local não é automático. Se precisar deles, faça
`dumpdata` local e `loaddata` no contêiner. Para o piloto, cadastrar do zero
costuma ser mais seguro.

## 4. Tarefas agendadas

Aplicação → **Scheduled Tasks → + Add**:

| Name | Command | Frequency | Timeout |
|---|---|---|---|
| `rotina-diaria-cobranca` | `python manage.py enviar_lembretes_cobranca` | `0 9 * * *` | `600` |

> O horário segue o **fuso do servidor**. Com o servidor em UTC (o padrão, e a API do
> Coolify não altera o fuso), use `0 12 * * *` para rodar às 9h de Brasília.

- **Rotina diária**: gera as mensalidades do mês (e antecipa as do mês
  seguinte que vencem em até 7 dias), marca as vencidas, confere se algum
  Pix já foi pago antes de cobrar e envia os lembretes. Sem ela, **nenhuma
  mensalidade nova é criada a partir do 2º mês**.
- **Repasses do Pix: não têm tarefa agendada.** Quando um Pix é pago, o
  próprio site transfere o saldo para a chave Pix da academia, em segundo
  plano, e volta a cada minuto **só enquanto houver repasse em aberto**
  (novas tentativas após 1, 5, 15, 60 e 180 minutos; depois disso o repasse
  fica em **Requer atenção**, com aviso no portal para superusuários e em
  `/admin/financeiro/repasse/`). Sem Pix, nada roda. Depois de um deploy o
  site retoma o que ficou aberto, e a rotina diária dá uma passada. Para
  rodar na hora, à mão: `python manage.py processar_repasses` no Terminal.

> Cuidado com o **Execute Now** da rotina diária: se o WhatsApp já estiver
> conectado e houver mensalidade na janela de lembrete, saem mensagens reais.

## 4.1 Monitoramento (Discord)

Os alertas vão para um canal do Discord, nunca para o WhatsApp da academia.

- **Sistema** (erros 500 e alertas da plataforma, como repasse travado ou Pix
  pago em dobro): variável `ALERTAS_DISCORD_WEBHOOK_URL` da aplicação. O mesmo
  erro é avisado no máximo uma vez por hora; as repetições são contadas.
- **RAM e disco da VPS**: tarefa agendada `python manage.py verificar_servidor`
  a cada 5 minutos (`*/5 * * * *`, timeout `60`). Limites em
  `MONITORAMENTO_LIMITE_RAM` (90) e `MONITORAMENTO_LIMITE_DISCO` (85).
  Gráficos: Servers → (servidor) → Metrics (Sentinel com métricas ligadas).
- **Coolify** (deploy, backup ou tarefa com falha, servidor inacessível,
  disco): Notifications → Discord, com o mesmo webhook.
- **Uptime Kuma** (site, DNS, Evolution, WhatsApp da escola conectado):
  serviço do modelo "Uptime Kuma", com notificação Discord.
  Configuração dos monitores, critérios de sucesso e validação em
  [UPTIME_KUMA.md](UPTIME_KUMA.md).

## 4.2 Assinatura do sistema (Minha assinatura)

A academia paga a mensalidade do sistema por Pix, direto na chave da
plataforma (sem Woovi): variáveis `PLATAFORMA_PIX_CHAVE`, `PLATAFORMA_PIX_NOME`
e `PLATAFORMA_PIX_CIDADE`. No `/admin/` → **Assinaturas** → cadastre o plano da
academia (valor, dia de vencimento, início) e use a ação **Gerar agora a fatura
do mês**. Depois, a rotina diária gera as faturas sozinha e avisa no Discord as
vencidas. A academia vê em Configurações → Minha assinatura, paga pelo QR Code
e clica **Já paguei**; você recebe o aviso no Discord e confirma em
Faturas da assinatura → ação **Confirmar pagamento**.

## 5. Woovi no sandbox (teste de ponta a ponta)

1. Crie a conta em `https://app.woovi-sandbox.com`, peça a habilitação de
   subcontas e gere o **AppID** em **API/Plugins → Nova API**. Coloque-o
   em `WOOVI_APP_ID` (com `WOOVI_BASE_URL` do sandbox) e faça **Redeploy**.
2. **Webhooks** — em API/Plugins → Webhooks, cadastre **três**, todos com a
   URL `https://gestao.seudominio.com.br/webhooks/woovi/`:
   - `OPENPIX:CHARGE_COMPLETED` (Pix pago → baixa + repasse);
   - `OPENPIX:MOVEMENT_CONFIRMED` (transferência para a chave concluída);
   - `OPENPIX:MOVEMENT_FAILED` (transferência falhou → nova tentativa).

   Não há segredo a configurar: cada aviso é validado pela assinatura
   `x-webhook-signature` da Woovi. Aviso sem assinatura válida recebe 401.
3. Com o usuário da proprietária: **Configurações → Recebimento** → cadastre
   a chave Pix. O sistema cria (ou recupera) a subconta dessa chave.
4. Cadastre um aluno com mensalidade, clique em **Gerar Pix** em Financeiro →
   Cobranças e abra **Ver Pix**. No sandbox, crie uma *conta bancária de
   teste* e leia o QR Code com a câmera do celular: ele simula o pagamento.
   Guia: https://developers.woovi.com/docs/test-environment/test-account/flow-company-bank-test
5. Confira: a mensalidade vira **Paga**; em `/admin/financeiro/repasse/`
   aparece um repasse que, em até um minuto, vai para **Processando** e,
   com o aviso da Woovi, para **Concluída**.

## 6. WhatsApp (Evolution API)

1. No mesmo projeto: **+ New → Service → Evolution API**. O Coolify gera a
   `AUTHENTICATION_API_KEY` e um domínio próprio para ela (ex.:
   `https://evo.seudominio.com.br`).
2. Antes do primeiro deploy do serviço, nas variáveis dele, **desligue o
   armazenamento de conversas**. O sistema só envia lembretes, e guardar
   mensagens de famílias é dado pessoal sem necessidade (LGPD):

   ```dotenv
   DATABASE_SAVE_DATA_NEW_MESSAGE=false
   DATABASE_SAVE_MESSAGE_UPDATE=false
   DATABASE_SAVE_DATA_CONTACTS=false
   DATABASE_SAVE_DATA_CHATS=false
   DATABASE_SAVE_DATA_LABELS=false
   DATABASE_SAVE_DATA_HISTORIC=false
   ```

3. Nas variáveis da **aplicação**: `EVOLUTION_BASE_URL` = o domínio HTTPS do
   serviço (`https://evo.seudominio.com.br`) e `EVOLUTION_API_KEY` = o valor
   de `AUTHENTICATION_API_KEY` do serviço. Redeploy da aplicação. É uma vez só,
   para todas as academias.
4. No sistema, cada academia: **Configurações → WhatsApp** → número com DDD →
   **Gerar QR Code** (na primeira vez a conexão é criada sozinha) → no celular
   do número: WhatsApp → Aparelhos conectados → Conectar um aparelho → ler o
   QR → **Já li o QR Code**: a situação vai para *Conectado*. No mesmo lugar,
   cadastre o **WhatsApp para avisos da escola** (aviso de matrícula nova).
5. Teste com o seu próprio número antes de ligar a rotina para todos (roteiro
   em `deploy/EVOLUTION.md`, seção 5, rodando o `shell` pelo Terminal do Coolify).

> Número dedicado e volume baixo no começo: a Evolution usa o WhatsApp Web,
> que não é a API oficial, e há risco de bloqueio do número.

## 7. Virar a Woovi para produção

1. Na conta de **produção** (`https://app.woovi.com`), com subcontas
   habilitados: gere um novo AppID e cadastre os **mesmos três webhooks**.
2. Variáveis da aplicação: `WOOVI_BASE_URL=https://api.woovi.com` e o novo
   `WOOVI_APP_ID`. Redeploy.
3. A proprietária cadastra de novo a chave Pix em **Configurações →
   Recebimento** (a subconta de produção é outra). Os Pix gerados no sandbox
   não valem em produção: gere de novo ou cancele as mensalidades de teste.
4. Faça **um pagamento real pequeno** e acompanhe o repasse até **Concluída**
   antes de liberar para todas as famílias.

### A validar no primeiro pagamento real

A documentação da Woovi não fecha estes pontos; o código foi escrito para
tolerar qualquer resposta, mas o custo e o resultado precisam ser conferidos:

- **Tarifa por saque** (confirmada em produção): R$ 1,00 por saque abaixo de
  R$ 1.000, cobrada do saldo da subconta **além** do valor pedido — pedir o
  saldo inteiro volta "Saldo insuficiente". O repasse pede saldo − tarifa
  (`WOOVI_TARIFA_SAQUE_CENTAVOS`, padrão 100); a partir de R$ 1.000
  (`WOOVI_SAQUE_SEM_TARIFA_CENTAVOS`) saca tudo, sem tarifa. Com repasse a cada
  pagamento, a academia paga R$ 1,00 por mensalidade, além da taxa do Pix.
- **Sem split**: a Woovi recusa split de 100% ("O valor total do split de
  pagamento não pode ser igual ao valor da cobrança"). O Pix vai sem split e,
  depois do pagamento, o repasse credita na subconta o **líquido** (valor pago
  menos a taxa da Woovi — a taxa é paga pela academia).
- **Saque automático da conta principal**: desligue no painel da Woovi. O
  dinheiro das academias fica na conta principal até ser creditado na subconta
  (em até ~1 minuto); um saque automático nesse intervalo faria o crédito falhar
  por falta de saldo.
- **Nome do recebedor** que a família vê no app do banco ao pagar.
- Se os webhooks `MOVEMENT_*` chegam para saques de subconta. Se não
  chegarem, o sistema conclui o repasse pelo extrato após 30 minutos.

## Conferência rápida

- `https://gestao.seudominio.com.br/login/` abre com o CSS carregado, e
  `https://gestao.seudominio.com.br/static/portal/site.css` responde 200.
- `https://gestao.seudominio.com.br/health/` responde `{"status": "ok", "database": "ok"}`.
- `https://gestao.seudominio.com.br/webhooks/woovi/` responde `{"status": "ok"}`.
- Deployments: o contêiner fica **healthy**.
- Scheduled Tasks → histórico: a rotina diária e os repasses aparecem com sucesso.
- `/admin/financeiro/repasse/`: nenhum repasse em **Requer atenção**.

## O que muda em relação ao deploy na EC2

A EC2 não é mais usada. O Coolify publica a branch `main` e substitui o
`deploy/deploy.sh`, os serviços systemd (`academia-gunicorn`,
`academia-lembretes.timer`, `academia-repasses.timer`) e o Nginx. O antigo
workflow `.github/workflows/deploy.yml`, que publicava por SSH na EC2 a cada
push na `main`, foi removido. Os arquivos de `deploy/` da EC2 ficam só como
referência.

# MVP de gestão de alunos

Interface Django Templates + Bootstrap, em português. O portal usa os models existentes e a autenticação nativa do Django.

## Executar

Use Python 3.12 ou superior compatível com o Django fixado em `requirements.txt`.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # preencha ASAAS_BASE_URL/ASAAS_API_KEY para usar o Pix (ver abaixo)
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Sem o `.env` preenchido, o sistema funciona normalmente — só o botão **Gerar Pix** do financeiro falha com uma mensagem amigável ("ASAAS_BASE_URL não configurada"), em vez de gerar a cobrança. Isso é esperado, não é bug.

No `/admin/`, o superusuário deve cadastrar a academia, as modalidades e as turmas desejadas. Cadastre um usuário comum (sem acesso de equipe) e, em **Acessos às academias**, vincule-o à academia. Cada usuário tem uma academia. Apenas superusuários podem gerenciar esses vínculos.

Abra `/login/` com o usuário vinculado. Mesmo superusuários precisam de vínculo a uma academia ativa para acessar o portal.

## Fluxo

- `/`: dashboard com total de alunos, alunos ativos e matrículas ativas.
- `/alunos/`: busca por nome, paginação, detalhe e edição.
- `/alunos/novo/`: aluno, responsável existente ou novo e matrícula no mesmo formulário.
- No detalhe: edição dos dados do aluno e vínculo do responsável; cada matrícula tem seu próprio link de edição para evitar escolher arbitrariamente entre múltiplas inscrições.
- Logout usa POST com CSRF.

Responsáveis são reutilizados dentro da academia pelo CPF sem pontuação. Sem CPF, a comparação usa nome e WhatsApp normalizados. Havendo vários candidatos, o formulário solicita seleção explícita. Reutilização não sobrescreve dados de contato nem o identificador Asaas existente.

Academia é derivada exclusivamente do usuário autenticado. IDs de aluno, responsável, modalidade, turma e matrícula são filtrados no servidor. A relação turma/modalidade, datas e dia do vencimento são validados pelo model de matrícula. Valores negativos e nascimento futuro são rejeitados. A gravação de responsável, aluno e matrícula acontece em uma única transação; falha na matrícula desfaz o cadastro inteiro.

## Financeiro e limites

A integração Asaas e a geração de mensalidades existente foram preservadas. Ao ativar a matrícula, o sistema cria a 1ª mensalidade (a do primeiro vencimento a partir da data de início). As seguintes são criadas pela **rotina diária** (`enviar_lembretes_cobranca`, ver abaixo): ela garante as mensalidades do mês corrente e antecipa as do mês seguinte que vencem em até 7 dias, a tempo do lembrete de 5 dias antes. Só entram matrículas ativas de alunos com status **ativo** — aluno trancado ou inativo não gera cobrança nova. O comando `gerar_mensalidades --ano --mes` continua disponível para gerar um mês avulso.

### Painel financeiro (`/financeiro/`)

- **Painel**: previsto, recebido, a receber e em atraso do mês corrente, vencimentos dos próximos 7 dias e lista de inadimplentes. A cada acesso, mensalidades pendentes vencidas são promovidas para `vencida` automaticamente (`Mensalidade.objects.marcar_vencidas()`), sem depender de job externo.
- **Cobranças** (`/financeiro/cobrancas/`): lista com busca por aluno, filtro por situação e por mês, paginada. Ações por linha: **Marcar como pago** (define `status`, `forma_pagamento` e `pago_em`), **Gerar Pix** (chama a integração Asaas já existente) e **Cobrar** (abre o WhatsApp do responsável com a mensagem preenchida — `portal/templatetags/portal_extras.py`).
- Qualquer usuário vinculado à academia pode registrar pagamentos e gerar cobranças (não é uma ação restrita a administrador da academia).
- **Cancelar / Isentar** (só administrador da academia): tira uma mensalidade em aberto da cobrança e dos lembretes. Se ela já tem cobrança no Asaas, a cobrança é excluída lá antes; se o Asaas recusar (por exemplo, porque já foi paga), nada muda localmente.
- No detalhe do aluno, cada matrícula mostra as últimas mensalidades e o status.

### Webhook do Asaas (`/webhooks/asaas/`)

Endpoint público (`POST`, isento de CSRF) que recebe a confirmação de pagamento do Asaas e marca a mensalidade correspondente como paga automaticamente — sem conferência manual. Aceita `PAYMENT_RECEIVED` e `PAYMENT_CONFIRMED`, casando pelo `asaas_payment_id`. Pagamento que chega para uma mensalidade **cancelada** não altera nada, gera um aviso no log (`integracoes.asaas.views`, para conferir/estornar à mão) e responde 200 — um erro faria o Asaas reenviar e, com falhas seguidas, pausar a fila de webhooks. Configure `ASAAS_WEBHOOK_TOKEN` (mesmo token cadastrado no painel do Asaas) para exigir o cabeçalho `asaas-access-token`; sem essa variável definida, o endpoint aceita qualquer chamada — use isso só em desenvolvimento.

### Portal do responsável (`/responsavel/`)

Área pública, sem o login de staff, onde o responsável financeiro acompanha as mensalidades dos próprios alunos e paga por Pix, boleto ou cartão.

- **Acesso**: sem senha. O staff clica **"Gerar acesso ao portal de pagamentos"** no detalhe do aluno (`AcessoAcademia`/administrador não é exigido para isso — qualquer usuário da academia pode gerar). Isso cria um `TokenAcessoResponsavel` (link de uso único, válido por 24h) e tenta enviar pelo WhatsApp automaticamente; se a API do WhatsApp não estiver configurada, mostra o link para o operador mandar manualmente (mesmo padrão wa.me usado no financeiro).
- Abrir o link mostra uma tela com o botão **Entrar**; o token só é gasto nesse clique (POST). Assim, prévia de link do WhatsApp e antivírus — que abrem o link por GET — não queimam o acesso antes da família. Links de pagamento (lembretes e "Enviar cobrança") valem 7 dias; o acesso avulso ao portal, 24h. Reabrir um link já usado no mesmo navegador segue direto, sem "link expirado".
- Ao clicar em Entrar, o navegador ganha uma sessão comum (`request.session['responsavel_id']`) que dura o padrão de sessão do Django — não precisa do link de novo até expirar os cookies.
- **Painel**: lista os alunos vinculados àquele responsável e as mensalidades de cada um, com botão **Pagar** nas pendentes/atrasadas.
- **Pagar**: gera (ou reaproveita) uma cobrança Asaas com `billing_type=UNDEFINED` (`criar_cobranca_multipla_asaas`), mostra o QR/copia-e-cola do Pix na hora, um link do boleto (PDF) e um botão "Pagar com cartão" que abre o checkout hospedado do próprio Asaas (`invoiceUrl`) — cartão nunca passa pelo nosso servidor.
- Isolamento: toda consulta filtra por `matricula__atleta__responsavel_financeiro=request.responsavel` — um responsável nunca alcança mensalidade de outra família, mesmo advinhando o ID na URL.

### WhatsApp Cloud API (`integracoes/whatsapp/`)

Cliente da API oficial da Meta, mesmo formato do cliente do Asaas. Usado para enviar automaticamente o acesso ao portal (`enviar_acesso_portal_responsavel`), o botão manual "Enviar cobrança" do painel financeiro e os lembretes automáticos abaixo (todos via `enviar_cobranca_responsavel`). Exige `WHATSAPP_PHONE_NUMBER_ID` e `WHATSAPP_ACCESS_TOKEN` (`.env.example`) e templates aprovados no Meta Business Manager (nomes configuráveis via `WHATSAPP_TEMPLATE_ACESSO`/`WHATSAPP_TEMPLATE_COBRANCA`). Sem configurar, todo fluxo cai automaticamente no link manual (wa.me) — nada quebra.

No painel financeiro, o botão único **"Enviar cobrança"** substitui o antigo par "Cobrar" (wa.me manual) + "Enviar cobrança": ele tenta mandar pela API automaticamente e só cai para o link manual se o WhatsApp não estiver configurado ou a chamada falhar.

### Lembretes automáticos de cobrança (`financeiro/lembretes.py`)

Comando `enviar_lembretes_cobranca` — a rotina diária de cobrança (na EC2 roda pelo timer `deploy/academia-lembretes.timer`; no Windows, agende pelo Agendador de Tarefas). Primeiro gera as mensalidades pendentes de criação (ver "Financeiro e limites"), depois avisa o responsável pelo WhatsApp em 4 estágios, cada um disparado **no máximo uma vez por mensalidade**:

1. **5 dias antes** do vencimento;
2. **1 dia antes**, se ainda não paga;
3. **no dia** do vencimento, se ainda não paga;
4. **atrasada** — dispara uma vez ao ficar em atraso; não repete todo dia depois disso (evita virar máquina de spam).

Controle de idempotência: tabela `LembreteCobranca` (`mensalidade` + `estagio`, único), criada só depois do envio dar certo — se falhar (WhatsApp não configurado, API fora do ar, responsável sem WhatsApp cadastrado), tenta de novo na próxima execução em vez de desistir para sempre. O link enviado é o mesmo link de pagamento de uso único do portal do responsável (`TokenAcessoResponsavel` + `?next=`), montado com a variável `SITE_URL` (`.env.example`) já que o comando roda fora de uma request HTTP.

**Ainda não implementado** (fora do escopo deste MVP financeiro): desconto/bolsa/isenção com valor e motivo próprios, gráfico de receita por mês, e uma tabela de pagamentos separada da mensalidade (hoje a mensalidade acumula os dois papéis, o que é suficiente enquanto não há pagamento parcial).

Bootstrap é carregado por CDN e precisa de internet para aplicar a aparência. O portal não altera as permissões dos demais Django Admins: contas operacionais devem permanecer sem `is_staff`; o Admin é reservado à administração confiável.

A trava por academia para reutilização de responsáveis funciona em bancos com bloqueio de linhas. SQLite não fornece essa garantia concorrente; não há nova restrição de unicidade de CPF, para preservar dados legados.

## Verificar

```sh
python manage.py check
python manage.py test
python manage.py makemigrations --check --dry-run
```

Os testes do portal cobrem autenticação, academia ausente/inativa, isolamento, formulários, reutilização de responsável, edição, rollback, CSRF, busca e paginação. Os testes financeiros e Asaas originais permanecem intactos.


## Polos, professores e turmas

No menu do sistema, administradores podem cadastrar e editar unidades/polos, professores, modalidades (lutas, esportes) e turmas. Turmas vinculam polo, modalidade e professor. Cadastros antigos foram vinculados à Matriz; nomes de professores existentes foram preservados e associados ao novo cadastro.

O antigo cadastro de “serviços” passou a se chamar **modalidade** em toda a interface e no código (app `modalidades`, model `Modalidade`, `Turma.modalidade`, `Matricula.modalidade`). O rótulo de app interno permanece `servicos` apenas para preservar o histórico de migrations; as tabelas continuam `servicos_*`.

A **modalidade** guarda só o nome e a descrição — nada de valores ou horários. As **graduações/faixas** são geridas dentro da própria modalidade (seção inline no cadastro dela; a linha em branco no fim adiciona uma faixa, “Excluir” remove). **Valor de referência da mensalidade, dia de vencimento, horário e dias da semana ficam na turma.** Na matrícula do aluno, valor e vencimento são preenchidos a partir da turma escolhida e continuam editáveis para exceções (bolsa, desconto).

Superusuários ou usuários com a opção “administrador da academia” no acesso podem gerenciar esses cadastros. Um administrador pode abrir o detalhe do aluno e criar outra matrícula; transferir uma matrícula de polo também exige administrador. Os filtros de acesso atuais continuam sendo por Academia; permissões de visualização individuais por polo ainda não estão implementadas.

No cadastro do aluno, marque “O próprio aluno é o responsável financeiro” e informe CPF e WhatsApp. O sistema cria/reutiliza um Responsavel compatível com a integração Asaas e preserva os identificadores existentes. Não dispara cobranças ao cadastrar.

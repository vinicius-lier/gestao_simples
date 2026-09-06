# Academia Gestão

Sistema de gestão para academias (ex: academias de artes marciais), construído em Django. Permite cadastrar academias, atletas, responsáveis financeiros, serviços/turmas, matrículas e controlar mensalidades.

## Stack

- Python + [Django 6.1](https://docs.djangoproject.com/en/6.1/)
- SQLite (banco padrão de desenvolvimento)
- Interface via Django Admin (não há frontend/API própria ainda)

## Estrutura dos apps

| App | Responsabilidade |
|---|---|
| `academias` | Cadastro das academias (tenant principal do sistema) |
| `atletas` | Atletas e seus responsáveis financeiros |
| `modalidades` | Modalidades oferecidas (lutas, esportes), suas graduações/faixas, professores e turmas |
| `matriculas` | Vínculo entre um atleta, uma modalidade/turma e o valor da mensalidade |
| `financeiro` | Mensalidades geradas a partir das matrículas; painel, cobranças e pagamentos no portal |
| `integracoes` | Cliente e serviços do Asaas (clientes, cobrança Pix, webhook de pagamento) — não é um app Django, é uma lib interna |
| `config` | Configurações do projeto Django (settings, urls, wsgi/asgi) |

### Modelo de dados

Todas as entidades principais pertencem a uma `Academia`, o que permite operar múltiplas academias na mesma base:

- **Academia**: nome, nome fantasia, CNPJ, contato.
- **Responsavel**: responsável financeiro, vinculado a uma academia.
- **Atleta**: vinculado a uma academia e, opcionalmente, a um responsável financeiro. Possui status (`ativo`, `inativo`, `trancado`) e faixa.
- **Modalidade**: modalidade (luta ou esporte) oferecida pela academia — apenas nome e descrição. Valores e horários ficam nas turmas.
- **Graduacao**: graduação/faixa de uma modalidade, com ordem de evolução. Cadastrada dentro da própria modalidade.
- **Turma**: turma de uma modalidade, com unidade/polo, professor responsável, dias da semana, horário, local, valor de referência da mensalidade e dia de vencimento.
- **Matricula**: liga um atleta a uma modalidade (e opcionalmente turma). Valor de mensalidade e dia de vencimento vêm da turma e podem ser ajustados por matrícula.
- **Mensalidade**: cobrança mensal gerada a partir de uma matrícula, com competência, vencimento, status (`pendente`, `paga`, `vencida`, `cancelada`, `isenta`), `forma_pagamento` e `asaas_payment_id`. Integrada ao [Asaas](https://www.asaas.com/) (cliente, cobrança Pix e webhook de confirmação de pagamento — ver `MVP.md`).

## Como rodar localmente

### PostgreSQL

A conexão é configurada pelas variáveis de `.env.example`. Transfira o bloco
do banco para seu `.env`, preservando as credenciais das outras integrações,
e defina uma senha forte em `POSTGRES_PASSWORD`.

Com Docker Desktop instalado e iniciado, execute no PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
docker compose up -d --wait db
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

O Compose cria o banco e o usuário na primeira inicialização e conserva os
dados no volume `postgres_data`. Alterar a senha no `.env` depois disso não
altera a senha do usuário já criado. Não execute `docker compose down -v`
se precisar preservar os dados. O serviço fica acessível apenas nesta máquina.

Para um servidor PostgreSQL existente, dispense o Compose e preencha host,
porta, banco, usuário e senha fornecidos pelo administrador/provedor.
Em produção, configure `POSTGRES_SSLMODE` conforme o provedor; `verify-full`
valida o certificado e o hostname e exige uma CA confiável configurada no cliente.
O Compose é destinado ao desenvolvimento local. O usuário criado pela imagem
é administrador; em produção utilize uma credencial restrita ao banco da aplicação.

### Dados antigos do SQLite

Use `DB_ENGINE=sqlite` no desenvolvimento e `DB_ENGINE=postgresql` na EC2.
Preserve o SQLite e seu backup antes de transferir dados para PostgreSQL. N?o execute migrations
esperando que elas transfiram automaticamente os dados do SQLite.

Configure DJANGO_SECRET_KEY, DJANGO_DEBUG e as vari?veis POSTGRES_* do
`.env.example` antes de iniciar o projeto. Para publica??o na EC2, consulte
[deploy/README.md](deploy/README.md).

## Status do projeto

Projeto em estágio inicial. Até o momento existem apenas os modelos de dados e o cadastro via Django Admin — não há views/API pública nem frontend customizado.

## Ambientes

Use `.env.example.local` como referencia no Windows e `.env.example.production`
na EC2. Se o `.env` ja existir, edite apenas as variaveis necessarias e preserve
as credenciais. Nao sobrescreva o arquivo. A chave de desenvolvimento so e
aceita com DEBUG=true. Consulte `deploy/README.md` para publicar.

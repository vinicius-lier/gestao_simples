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
| `servicos` | Serviços oferecidos (ex: modalidades) e turmas |
| `matriculas` | Vínculo entre um atleta, um serviço/turma e o valor da mensalidade |
| `financeiro` | Mensalidades geradas a partir das matrículas |
| `config` | Configurações do projeto Django (settings, urls, wsgi/asgi) |

### Modelo de dados

Todas as entidades principais pertencem a uma `Academia`, o que permite operar múltiplas academias na mesma base:

- **Academia**: nome, nome fantasia, CNPJ, contato.
- **Responsavel**: responsável financeiro, vinculado a uma academia.
- **Atleta**: vinculado a uma academia e, opcionalmente, a um responsável financeiro. Possui status (`ativo`, `inativo`, `trancado`) e faixa.
- **Servico**: modalidade/serviço oferecido pela academia, com valor padrão e dia de vencimento.
- **Turma**: turma de um serviço, com professor, dias da semana, horário e local.
- **Matricula**: liga um atleta a um serviço (e opcionalmente turma), define valor de mensalidade e dia de vencimento próprios.
- **Mensalidade**: cobrança mensal gerada a partir de uma matrícula, com competência, vencimento, status (`pendente`, `paga`, `vencida`, `cancelada`, `isenta`) e campo `asaas_payment_id` (preparado para integração com o [Asaas](https://www.asaas.com/)).

## Como rodar localmente

```bash
# criar e ativar ambiente virtual
python -m venv .venv
source .venv/bin/activate

# instalar dependências
pip install -r requirements.txt

# aplicar migrações
python manage.py migrate

# criar um superusuário para acessar o admin
python manage.py createsuperuser

# subir o servidor de desenvolvimento
python manage.py runserver
```

Acesse `http://127.0.0.1:8000/admin/` e faça login com o superusuário criado.

## Status do projeto

Projeto em estágio inicial. Até o momento existem apenas os modelos de dados e o cadastro via Django Admin — não há views/API pública nem frontend customizado.

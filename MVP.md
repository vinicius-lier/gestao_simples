# MVP de gestão de alunos

Interface Django Templates + Bootstrap, em português. O portal usa os models existentes e a autenticação nativa do Django.

## Executar

Use Python 3.12 ou superior compatível com o Django fixado em `requirements.txt`.

```sh
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

No `/admin/`, o superusuário deve cadastrar a academia, os serviços e as turmas desejadas. Cadastre um usuário comum (sem acesso de equipe) e, em **Acessos às academias**, vincule-o à academia. Cada usuário tem uma academia. Apenas superusuários podem gerenciar esses vínculos.

Abra `/login/` com o usuário vinculado. Mesmo superusuários precisam de vínculo a uma academia ativa para acessar o portal.

## Fluxo

- `/`: dashboard com total de alunos, alunos ativos e matrículas ativas.
- `/alunos/`: busca por nome, paginação, detalhe e edição.
- `/alunos/novo/`: aluno, responsável existente ou novo e matrícula no mesmo formulário.
- No detalhe: edição dos dados do aluno e vínculo do responsável; cada matrícula tem seu próprio link de edição para evitar escolher arbitrariamente entre múltiplas inscrições.
- Logout usa POST com CSRF.

Responsáveis são reutilizados dentro da academia pelo CPF sem pontuação. Sem CPF, a comparação usa nome e WhatsApp normalizados. Havendo vários candidatos, o formulário solicita seleção explícita. Reutilização não sobrescreve dados de contato nem o identificador Asaas existente.

Academia é derivada exclusivamente do usuário autenticado. IDs de aluno, responsável, serviço, turma e matrícula são filtrados no servidor. A relação turma/serviço, datas e dia do vencimento são validados pelo model de matrícula. Valores negativos e nascimento futuro são rejeitados. A gravação de responsável, aluno e matrícula acontece em uma única transação; falha na matrícula desfaz o cadastro inteiro.

## Financeiro e limites

A integração Asaas e a geração de mensalidades existente foram preservadas. O formulário salva a matrícula; não dispara cobranças nem gera mensalidades automaticamente. Continue usando o comando `gerar_mensalidades` do projeto.

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

No menu do sistema, administradores podem cadastrar e editar unidades/polos, professores e turmas. Turmas vinculam polo, serviço e professor. Cadastros antigos foram vinculados à Matriz; nomes de professores existentes foram preservados e associados ao novo cadastro.

Superusuários ou usuários com a opção “administrador da academia” no acesso podem gerenciar esses cadastros. Um administrador pode abrir o detalhe do aluno e criar outra matrícula; transferir uma matrícula de polo também exige administrador. Os filtros de acesso atuais continuam sendo por Academia; permissões de visualização individuais por polo ainda não estão implementadas.

No cadastro do aluno, marque “O próprio aluno é o responsável financeiro” e informe CPF e WhatsApp. O sistema cria/reutiliza um Responsavel compatível com a integração Asaas e preserva os identificadores existentes. Não dispara cobranças ao cadastrar.

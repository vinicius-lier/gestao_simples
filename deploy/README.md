# Deploy EC2 Ubuntu 24.04

Preparado para revis?o. Nenhum deploy, conex?o SSH, commit ou push foi realizado.
WSGI verificado: `config/wsgi.py`, callable `config.wsgi:application`.
Python 3.12 do Ubuntu 24.04 ? compat?vel com Django 6.1.

## GitHub

Em Settings > Secrets and variables > Actions (ou no environment production):

- Secret `EC2_HOST`: hostname ou IPv4 p?blico da EC2, sem protocolo.
- Secret `EC2_USER`: `ubuntu`.
- Secret `EC2_SSH_KEY`: chave privada dedicada ao Actions; a p?blica correspondente
  deve estar em `/home/ubuntu/.ssh/authorized_keys` (uma linha por chave).
- Variable `EC2_KNOWN_HOSTS`: linha `HOST ssh-ed25519 CHAVE_PUBLICA_DO_SERVIDOR`.

Obtenha a chave do servidor por sess?o confi?vel, como AWS Session Manager:
`sudo cat /etc/ssh/ssh_host_ed25519_key.pub`. Combine o hostname/IP usado em
EC2_HOST com os dois primeiros campos dessa chave p?blica. N?o use a chave do
cliente/deploy aqui. N?o confie em ssh-keyscan sem conferir a impress?o digital
por outro canal. Se a EC2 for substitu?da, verifique e atualize essa vari?vel.

Crie o environment `production`. Para revisar o primeiro deploy, configure
required reviewers se o plano permitir. Ap?s validar, remova essa exig?ncia
se quiser execu??o sem aprova??o a cada push main. Proteja main com revis?o.
O workflow_dispatch tamb?m aceita somente main.

SSH deve estar acess?vel ao runner. N?o abra PostgreSQL (5432) ou Gunicorn
(8000) para a internet. Runners hospedados t?m IPs vari?veis: restrinja o acesso
com um runner dedicado de sa?da fixa ou rede privada quando poss?vel. N?o h?
altera??es autom?ticas no Security Group neste workflow.

## Prepara??o ?nica da EC2 (ap?s revisar)

Use uma sess?o administrativa existente. Nginx e PostgreSQL j? instalados:

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv rsync acl
sudo install -d -o ubuntu -g ubuntu -m 755 /home/ubuntu/apps/gestao_simples
cd /home/ubuntu/apps/gestao_simples
python3.12 -m venv .venv
mkdir -p media local staticfiles
```

Se a venv j? existe, confira sua vers?o e n?o a recrie automaticamente.
O banco e usu?rio PostgreSQL devem existir e ser acess?veis pela aplica??o.
N?o se cria banco, usu?rio, superusu?rio ou migration nova neste deploy.

Mantenha/edite o `.env` diretamente no servidor com seu editor. N?o copie o
.env local por rsync e n?o substitua o arquivo existente. Configure:

```dotenv
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<chave exclusiva, aleatoria, de pelo menos 50 caracteres>
ALLOWED_HOSTS=<dominio-real>,localhost,127.0.0.1
DB_ENGINE=postgresql
POSTGRES_DB=<banco-existente>
POSTGRES_USER=<usuario-do-banco>
POSTGRES_PASSWORD=<senha-do-banco>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_SSLMODE=prefer
DB_CONN_MAX_AGE=60
SITE_URL=https://<dominio-real>
```

Preserve as vari?veis Asaas/Meta existentes. Configure o token de webhook Asaas
antes de expor o endpoint. Gere a chave Django por um gerenciador de senhas e
n?o publique o resultado em logs do Actions. N?o use `source .env`: python-dotenv
carrega o arquivo tanto no Django/Gunicorn quanto nos comandos de manuten??o.

```bash
chmod 600 /home/ubuntu/apps/gestao_simples/.env
chmod 700 /home/ubuntu/.ssh
chmod 600 /home/ubuntu/.ssh/authorized_keys
```

Fa?a upload apenas dos arquivos revisados de `deploy/` para a mesma pasta na EC2
(por scp/SFTP na sua sess?o administrativa). Instale o servi?o e sudoers:

```bash
cd /home/ubuntu/apps/gestao_simples
sudo install -o root -g root -m 644 deploy/academia-gunicorn.service /etc/systemd/system/academia-gunicorn.service
sudo visudo -cf deploy/academia-sudoers
sudo install -o root -g root -m 440 deploy/academia-sudoers /etc/sudoers.d/academia-deploy
sudo visudo -c
sudo systemctl daemon-reload
sudo systemctl enable academia-gunicorn
```

N?o use `--now` nessa etapa: o primeiro deploy instala o c?digo e depend?ncias
antes de iniciar o servi?o. Atualiza??es futuras da unit exigem instala??o
administrativa e daemon-reload; o Actions n?o pode alterar arquivos de root.

### Lembretes de cobranca (timer)

O envio diario de lembretes por WhatsApp roda por um systemd timer, instalado
uma vez como admin (o Actions nao mexe em arquivos de root):

```bash
sudo install -o root -g root -m 644 deploy/academia-lembretes.service /etc/systemd/system/academia-lembretes.service
sudo install -o root -g root -m 644 deploy/academia-lembretes.timer   /etc/systemd/system/academia-lembretes.timer
sudo systemctl daemon-reload
sudo systemctl enable --now academia-lembretes.timer
```

Passo a passo completo do WhatsApp/Evolution (servidor no compose, conexao por
QR, teste e rollback): `deploy/EVOLUTION.md`.

Sudoers m?nimo (os nomes sem `.service` coincidem com o script):

```sudoers
ubuntu ALL=(root) NOPASSWD: /usr/bin/systemctl restart academia-gunicorn, /usr/bin/systemctl reload nginx
```

Ubuntu EC2 pode j? ter uma regra ampla em `/etc/sudoers.d/90-cloud-init-users`.
Adicionar a regra acima n?o revoga permiss?es preexistentes. Audite com
`sudo -l -U ubuntu`; remova regras amplas apenas ap?s garantir outro acesso
administrativo/SSM de recupera??o. N?o ? poss?vel isolar o Actions dos demais
processos que usam a mesma conta ubuntu.

## Nginx

Edite a configura??o existente como administrador, evitando duplicar server
blocks para o mesmo dom?nio. No server do site, use:

```nginx
location /static/ {
    alias /home/ubuntu/apps/gestao_simples/staticfiles/;
}
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

N?o publique media/ indiscriminadamente: pode conter dados privados. A pasta ?
preservada, mas n?o recebe um location p?blico neste exemplo.
D? ao Nginx travessia dos diret?rios pais e leitura dos est?ticos, sem acesso ao .env:

```bash
sudo setfacl -m u:www-data:--x /home/ubuntu /home/ubuntu/apps /home/ubuntu/apps/gestao_simples
sudo setfacl -R -m u:www-data:rX /home/ubuntu/apps/gestao_simples/staticfiles
sudo setfacl -m d:u:www-data:rX /home/ubuntu/apps/gestao_simples/staticfiles
sudo nginx -t
sudo systemctl reload nginx
```

Configure o dom?nio e certificado HTTPS na configura??o existente antes de
expor dados reais. O workflow n?o instala nem altera Nginx/certificados. Para
formul?rios atr?s de HTTPS, avalie CSRF_TRUSTED_ORIGINS e SECURE_PROXY_SSL_HEADER
conforme seu proxy; essas op??es n?o s?o ativadas implicitamente neste patch.

## Primeiro deploy e valida??o

1. Revise os arquivos, configure EC2, secrets e variable, e fa?a backup do banco.
2. Commit/push em main inicia automaticamente. Alternativamente use Actions >
   Deploy EC2 > Run workflow > main ap?s o workflow existir no GitHub.
3. Acompanhe cada etapa: testes locais do runner, SSH, rsync, script e limpeza.
4. O script faz check, migrations existentes, collectstatic, restart, teste HTTP
   em /login/ e reload Nginx. Qualquer erro retorna falha e interrompe a sequ?ncia.
5. Na EC2, valide:

```bash
systemctl status academia-gunicorn --no-pager
sudo journalctl -u academia-gunicorn -n 100 --no-pager
sudo nginx -t
```

Abra o dom?nio HTTPS, confirme login e carregamento de CSS. N?o fa?a cobran?as
reais s? para testar o deploy. `bash deploy/deploy.sh` executa o mesmo procedimento
manualmente; tamb?m instala depend?ncias e aplica migrations.

## Preserva??o e limites

- .env j? estava no .gitignore; n?o foi editado. Tamb?m s?o exclu?dos .env.*,
  .venv/, media/, staticfiles/, local/, db.sqlite3*, logs e dumps de migra??o.
- N?o h? --delete: arquivos exclusivos da EC2 sobrevivem. Arquivos locais que
  tiverem o MESMO caminho de arquivos versionados ser?o atualizados. Guarde
  configura??es locais em local/ ou fora da pasta; acrescente exclus?es expl?citas
  antes de usar outro caminho. C?digo removido do Git permanece na EC2 e requer
  remo??o administrativa cuidadosa, sem apagar dados locais.
- O deploy ? in-place, n?o at?mico e sem rollback autom?tico. Falha ap?s rsync,
  pip ou migrate pode deixar estado parcial; restart causa breve indisponibilidade.
  N?o cancele uma execu??o durante migrations. Backup e migrations compat?veis
  com o c?digo anterior s?o necess?rios. Rollback de c?digo n?o reverte o banco.
- Concurrency serializa workflows; flock protege o script contra execu??o manual
  simult?nea. N?o execute rsync manual enquanto houver deploy em andamento.
- O HTTP check valida Gunicorn, n?o o caminho p?blico completo nem integra??es.
- Nginx, PostgreSQL, certificados, permiss?es, acesso SSH e sudoers s? podem ser
  confirmados na EC2 depois da revis?o. Nenhum teste remoto foi executado.
- Testes existentes preservados. A altera??o anterior de CSS continua separada.

Refer?ncias: https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency
https://gunicorn.org/deploy/
https://docs.djangoproject.com/en/dev/releases/6.1/

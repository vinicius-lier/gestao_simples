# WhatsApp via Evolution API — colocar no ar

Runbook para ligar o envio de mensagens (lembretes de cobranca e link de
acesso) pela Evolution API. O codigo ja esta pronto: cliente, servicos de
instancia/QR, `EvolutionWhatsAppProvider`, painel *Configuracoes -> WhatsApp*,
webhook opcional e a regua de lembretes (que resolve o provedor por academia).
Falta so infra + configuracao + agendamento.

> **Risco operacional.** Nao ha flag que segure o envio de WhatsApp em si.
> Assim que uma academia tiver `provider=evolution` **e** instancia conectada
> **e** o timer rodar, saem mensagens reais. As flags
> `LEMBRETES_GERAM_COBRANCA_ASAAS` / `LEMBRETES_ENVIAM_N8N` controlam so a
> cobranca Asaas e o n8n, nao o disparo. Suba **uma** academia primeiro.

---

## 1. Subir a Evolution (servidor)

Os servicos ficam no profile `evolution` do `compose.yaml` (nao sobem num
`docker compose up` comum): app Evolution + Postgres dedicado + Redis.

No `.env` do servidor (ver `.env.example.production`):

```dotenv
EVOLUTION_IMAGE=atendai/evolution-api:v2.1.1
EVOLUTION_PORT=8080
EVOLUTION_SERVER_URL=http://127.0.0.1:8080
EVOLUTION_AUTHENTICATION_API_KEY=<chave aleatoria forte>
EVOLUTION_DB_PASSWORD=<senha aleatoria forte>
EVOLUTION_TIMEOUT=15
EVOLUTION_WEBHOOK_TOKEN=<chave aleatoria forte>   # opcional, ver secao 6
```

```bash
cd /home/ubuntu/apps/gestao_simples
docker compose --profile evolution up -d
docker compose --profile evolution ps
curl -s http://127.0.0.1:8080 | head -c 200      # responde JSON de boas-vindas
```

A porta e publicada so em `127.0.0.1` — nao exponha 8080 na internet. Nginx
nao precisa de location para a Evolution: o Django fala com ela pelo loopback.

## 2. Env vars da aplicacao

Uma variavel por academia. O **nome** dela vai no banco (`credencial_ref`); o
**valor** e a apikey que a Evolution aceita (igual a
`EVOLUTION_AUTHENTICATION_API_KEY`, ou a `apikey` que a criacao da instancia
devolve).

```dotenv
EVOLUTION_API_KEY_KEIKO=<mesma chave da Evolution>
```

Reinicie o gunicorn para ele enxergar a nova variavel:

```bash
sudo systemctl restart academia-gunicorn
```

## 3. Registro IntegracaoWhatsApp da academia

Em `/admin/academias/integracaowhatsapp/` (ou pelo painel), crie/edite o
registro **da academia**:

| campo                     | valor                                  |
|---------------------------|----------------------------------------|
| `academia`                | a academia                             |
| `provider`                | `Evolution API`                        |
| `evolution_base_url`      | `http://127.0.0.1:8080`                |
| `evolution_instance_name` | `keiko` (slug unico, sem espacos)      |
| `credencial_ref`          | `EVOLUTION_API_KEY_KEIKO`              |
| `numero_whatsapp`         | opcional; ajuda no pareamento          |

## 4. Conectar o numero (QR)

Painel -> *Configuracoes -> WhatsApp*:

1. **Criar instancia** -> status vai para *aguardando QR code*.
2. **Gerar QR code**. No celular do numero: WhatsApp -> Aparelhos conectados
   -> Conectar aparelho -> escanear. O QR expira rapido; regenere se preciso.
3. **Verificar status** -> deve ficar *conectado* (a Evolution guarda a sessao;
   nao precisa reparear a cada deploy).

## 5. Teste de envio controlado

Antes de ligar a regua, mande uma mensagem para um numero seu:

```bash
/home/ubuntu/apps/gestao_simples/.venv/bin/python manage.py shell
```

```python
from academias.models import Academia
from integracoes.whatsapp import get_provider

class R:  # responsavel de mentira so para o teste
    nome = "Teste"
    whatsapp = "+55DDDNUMERO"          # seu numero

ac = Academia.objects.get(nome__icontains="keiko")
get_provider(ac).enviar_acesso(R(), "https://exemplo.com/teste")
# -> {'provider': 'evolution', 'message_id': '...', 'raw': {...}} e a msg chega
```

Erros vem sanitizados (`WhatsAppProviderError`) — apikey e base_url nunca
aparecem.

## 6. Webhook de status (opcional)

Nao e obrigatorio — o painel tem "Verificar status". Se quiser status em tempo
real, configure na instancia da Evolution o webhook para:

```
https://<dominio>/webhooks/evolution/<instancia>/
```

com o header `x-evolution-token: <EVOLUTION_WEBHOOK_TOKEN>`. O endpoint so
atualiza o status da integracao — nunca dispara cobranca. Em producao, sem o
token definido ele responde 503.

## 7. Ligar a regua de lembretes

Instale o timer (uma vez, como admin — o Actions nao mexe em arquivos de root):

```bash
cd /home/ubuntu/apps/gestao_simples
sudo install -o root -g root -m 644 deploy/academia-lembretes.service /etc/systemd/system/academia-lembretes.service
sudo install -o root -g root -m 644 deploy/academia-lembretes.timer   /etc/systemd/system/academia-lembretes.timer
sudo systemctl daemon-reload
sudo systemctl enable --now academia-lembretes.timer
```

Confira e teste um disparo manual:

```bash
systemctl list-timers academia-lembretes.timer --no-pager
sudo systemctl start academia-lembretes.service          # roda agora
journalctl -u academia-lembretes.service -n 50 --no-pager
```

O comando roda `marcar_vencidas()` + `enviar_lembretes()`: 5 dias antes, 1 dia
antes, no dia e quando atrasa; cada estagio no maximo uma vez por mensalidade.
Acompanhe em `/admin/financeiro/lembretecobranca/` (status, tentativas,
`ultimo_erro`, `provider`, `provider_message_id`). Status `erro` e retentado no
proximo disparo; `enviado` nao.

O horario padrao e 09:00 do fuso da maquina — ajuste `OnCalendar` no
`.timer` se a EC2 estiver em UTC.

## 8. Rollback

- Voltar uma academia para Meta: `provider=meta` no registro (efeito imediato).
- Parar todos os disparos: `sudo systemctl disable --now academia-lembretes.timer`.
- Derrubar a Evolution: `docker compose --profile evolution down`
  (dados ficam nos volumes `evolution_*`; use `-v` so para apagar de vez).

# Contas Woovi próprias — implantação e compatibilidade

## Fluxos

| Origem | Credencial | Destino | Depois da baixa |
| --- | --- | --- | --- |
| Pix de aluno já emitido | Referência da conta legada (`WOOVI_APP_ID`) | Conta original | Crédito/repasse legado |
| Novo Pix de mensalidade ou matrícula | `WOOVI_ACADEMIA_<id>_APP_ID` | Conta própria da academia | Baixa, sem repasse |
| Assinatura do sistema | Referência gravada na fatura (`WOOVI_PLATAFORMA_APP_ID` ou original) | Conta da plataforma | Baixa da assinatura |

Uma academia sem conta própria conectada não emite novos Pix. Códigos legados ainda vigentes continuam disponíveis, mesmo perto de expirar ou após mudança do valor da mensalidade. A migração e a ativação não cancelam nem reemitem cobranças. Após o vencimento, uma nova emissão usa a conta própria, se conectada. Cancelamentos explicitamente solicitados continuam usando a origem do Pix.

`ContaRecebimento` mantém a origem, ambiente, identidade externa, referência do segredo, status e onboarding. `CobrancaPix` mantém essa conta e uma identificação explícita do modelo. Os identificadores, QR codes, links, vínculos e histórico existentes permanecem. A identidade de contas com histórico e a origem de cobranças não podem ser editadas pelo fluxo normal; o admin não permite cadastrar ou trocar essas origens.

## Antes de publicar

1. Faça backup do banco e mantenha **o mesmo** `WOOVI_APP_ID` e `WOOVI_BASE_URL` usados nas cobranças antigas. Não coloque o AppID da academia nessas variáveis.
2. Aplique `python manage.py migrate`. As migrations `financeiro/0010_contas_proprias` e `assinaturas/0003_contas_proprias` são aditivas e fixam a referência e o ambiente originais nos registros existentes. Não chamam a Woovi nem movimentam valores.
3. Provisione a credencial **da própria empresa da academia**, em segredo do servidor `WOOVI_ACADEMIA_<id>_APP_ID`. O número é o ID da academia no banco. A URL `WOOVI_ACADEMIA_<id>_BASE_URL` pode ser definida por academia; alternativamente use `WOOVI_ACADEMIAS_BASE_URL`. Produção e sandbox não se misturam.
4. Se necessário, configure `WOOVI_PLATAFORMA_APP_ID` e `WOOVI_PLATAFORMA_BASE_URL` para novas faturas do sistema. Faturas antigas continuam na referência original. Sem a variável nova, a conta original continua atendendo a assinatura.
5. Configure `SITE_URL` com o endereço HTTPS público correto. Mantenha o endpoint antigo `/webhooks/woovi/` para cobranças legadas, movimentos e assinatura.
6. O administrador da academia acessa **Configurações → Recebimento**, confere a taxa e usa **Verificar e ativar**. A ativação confere uma única conta padrão, identidade externa e CNPJ, registra/verifica o webhook específico e somente então libera novas cobranças.

O endpoint novo é `/webhooks/woovi/contas/<id_da_conta>/`. Não confundir o ID da conta local com o ID da academia. A criação do webhook usa a credencial da academia e precisa das permissões correspondentes na Woovi. O webhook assinado aceita somente cobranças da conta indicada; o endpoint original recusa cobranças próprias. Para toda cobrança conhecida, incluindo legado e faturas da plataforma, o pagamento e o identificador da transação são confirmados por GET com a credencial de origem antes da baixa: a assinatura RSA da Woovi, sozinha, não identifica a empresa. Indisponibilidade da consulta retorna 503 para retentativa, sem registrar baixa. Eventos duplicados não repetem baixa ou repasse.

Não aplique rollback de schema após emitir cobranças próprias: perder a informação de origem permitiria classificá-las como legadas. Para uma reversão operacional, interrompa novas emissões e preserve o código capaz de consultar as duas origens e receber seus webhooks.

## Abertura da conta

O botão **Iniciar abertura da conta** usa `POST /api/v1/kyc/onboarding`, com `partner: true`, CNPJ, nome, correlação e retorno ao portal. Isso requer uma credencial dedicada `WOOVI_ONBOARDING_APP_ID`, com a habilitação de parceiro e `KYC_ONBOARDING_LINK`. A análise é consultada por `GET /api/v1/account-register/{correlationID}`.

Documentos, KYC e aceite legal acontecem exclusivamente no domínio seguro da Woovi. O portal guarda o link e o estado, aceita apenas links oficiais HTTPS e não envia esse link como referrer. Se a API de parceiro não estiver habilitada, a tela oferece o cadastro oficial e informa a etapa de conexão pelo suporte.

A página usa `Referrer-Policy: same-origin`: preserva a origem dos formulários internos para a validação CSRF em HTTPS e não envia referência para sites externos. Não usar `no-referrer` na resposta inteira, pois pode provocar 403 nos formulários. Os links externos mantêm `rel="noopener noreferrer"`.

## Conexão automática pela Partner API

Com o cadastro aprovado, o sistema conclui a conexão sozinho, sem ninguém entrar em API/Plugins nem copiar AppID:

1. `POST /api/v1/partner/application` com a credencial de parceiro (`WOOVI_ONBOARDING_APP_ID`, escopo `PARTNER_APPLICATION_POST`), o CNPJ da academia e escopos mínimos: `CHARGE_POST`, `CHARGE_GET`, `CHARGE_GET_LIST`, `CHARGE_DELETE`, `WEBHOOK_POST`, `WEBHOOK_GET_LIST`, `ACCOUNT_GET_LIST`, `ACCOUNT_GET`. Nunca saque, transferência, débito ou crédito (`partner.validar_escopos` recusa).
2. A Woovi devolve `clientId` e `clientSecret`; o AppID é `Base64(clientId:clientSecret)`, montado só em `integracoes/woovi/partner.py` (`montar_app_id`).
3. O AppID é guardado cifrado (Fernet, `CREDENTIALS_ENCRYPTION_KEY`) em `ContaRecebimento.credencial_cifrada`. Uma credencial já vinculada a outra conta é recusada pela impressão SHA-256.
4. Segue a mesma ativação do fallback: conta padrão única, CNPJ da conta igual ao da academia, webhook próprio em `/webhooks/woovi/contas/<id>/` (`webhook_configurado_em`) e as travas de origem.

Gatilhos: **Atualizar situação do cadastro** (na tela de Recebimento) e o comando `python manage.py conectar_contas_woovi`, idempotente, que pode ser agendado (não está agendado no Coolify). A etapa (`ContaRecebimento.etapa`: não iniciado → aguardando KYC → em análise → aprovado → credencial criada → notificações configuradas → ativa, ou erro) é derivada dos campos existentes.

**Fallback manual (operador):** se a Partner API não estiver habilitada ou falhar, o cadastro continua aprovado e a tela orienta que o suporte conclua pelo servidor: o operador provisiona `WOOVI_ACADEMIA_<id>_APP_ID` e a academia usa **Verificar e ativar**. A credencial cifrada, quando existe, tem precedência sobre a variável. O portal continua sem pedir tokens em formulários.

Credenciais nunca aparecem em log, tela, admin ou mensagem de erro. Depois da conexão, a credencial cifrada não pode ser trocada pelo fluxo normal (mesma trava da origem). Trocar a `CREDENTIALS_ENCRYPTION_KEY` depois de conectar torna a credencial ilegível: a conta precisa ser reconectada por procedimento revisado.

Pendências com a Woovi: confirmar no sandbox o caminho `/api/v1/partner/...` (a especificação OpenAPI usa esse; os guias citam `/api/openpix/v1/...` e `/api/woovi/v1/...`); a habilitação das features `PARTNER` e `KYC_ONBOARDING_LINK` e do escopo `PARTNER_APPLICATION_POST`; e se a repetição (resposta 200) de `partner/application` devolve o mesmo `clientSecret`. Taxa personalizada do afiliado existe só no painel da Woovi (Ajustes) e não foi ativada.

## Taxas e segurança

A tela informa permanentemente: “Taxa de processamento Pix: R$ 0,85 por Pix recebido.” e “Essa taxa é cobrada pela Woovi diretamente sobre os pagamentos processados e não é receita do sistema Keiko Fukuda.” A confirmação é exigida antes da ativação, com data de ciência registrada.

Essa mensagem é comercial. A contabilidade usa exclusivamente `fee` retornado pela API. Ausência de `fee` deixa taxa/líquido desconhecidos; zero é uma taxa válida. Uma informação posterior pode completar a taxa sem repetir a baixa. O valor efetivamente recebido e sua data ficam registrados.

Segredos não são gravados no banco, HTML, mensagens ou logs. Cada origem resolve apenas referências permitidas, sem fallback da academia para a plataforma. Uma impressão SHA-256 detecta substituição inesperada da credencial de uma conta conectada. A troca de AppID/conta após conexão exige procedimento operacional revisado; não altere a variável silenciosamente nem use `QuerySet.update` para contornar a proteção de origem.

## Validação operacional ainda necessária

- Confirmar com a Woovi a habilitação de parceiro/KYC e as permissões de leitura de conta, cobrança e webhook dos AppIDs reais.
- Confirmar a tarifa comercial de R$ 0,85 no contrato da academia; o software não negocia nem impõe essa tarifa à Woovi.
- Validar em sandbox o cadastro, ativação, criação, pagamento, consulta e cancelamento, com credenciais da academia e da plataforma distintas. Os testes automatizados bloqueiam rede real.
- A ativação recusa empresas com múltiplas contas retornadas pela API, pois o payload de cobrança não seleciona uma conta bancária: a vinculação é da credencial. Uma configuração com múltiplas contas exige confirmação do vínculo com a Woovi antes de ampliar essa regra.
- Manter o processamento dos repasses pendentes até concluir o legado. O comando manual `smoke_woovi` continua voltado ao modelo legado e não valida esta nova conexão.
- Risco preexistente: uma falha após a criação remota do Pix e antes da persistência local pode deixar uma cobrança sem registro local. Concilie operações de resultado desconhecido antes de repetir emissões; esta migração não implementa uma fila durável de emissão.

Referências oficiais consultadas: [API Woovi](https://developers.woovi.com/api-redoc) e [onboarding KYC](https://developers.woovi.com/docs/baas/kyc/kyc-api-onboarding-create).

## Arquivos desta mudança

- Integração: `integracoes/woovi/client.py`, `credenciais.py` (novo), `contas.py` (novo), `services.py`, `repasses.py`, `views.py` e `assinatura.py`.
- Dados e administração: `financeiro/models.py`, `financeiro/admin.py`, `assinaturas/models.py` e `assinaturas/services.py`.
- Migrations: `financeiro/migrations/0010_contas_proprias.py` e `assinaturas/migrations/0003_contas_proprias.py`.
- Configuração: `config/settings.py`, `config/urls.py`, `config/test_runner.py` e `.env.example`. O `.env` real não foi alterado.
- Portal: `portal/views_recebimento.py`, `portal/forms.py`, `portal/views.py` e `portal/templates/portal/recebimento.html`.
- Testes novos: `tests/test_woovi_conta_propria.py` e `tests/test_migracao_contas_woovi.py`.
- Testes ajustados: `tests/woovi_base.py`, `tests/test_woovi_services.py`, `tests/test_woovi_webhook.py`, `tests/test_assinaturas.py`, `financeiro/test_taxa_e_valor_apos_vencimento.py`, `portal/test_financeiro.py`, `portal/test_recebimento.py` e `portal/test_responsavel.py`.
- Documentação: este arquivo.

Na rodada de implementação, não foram realizados commit, aplicação de migrations no banco operacional, abertura real de conta nem chamadas autenticadas à Woovi. O ambiente visual de revisão usou dados fictícios, sem consultar o banco operacional.

Em 29/09/2026, após autorização para aplicar migrations, fazer commit e push, as duas migrations foram aplicadas ao SQLite local `db.sqlite3`. Foi criado um backup anterior em `~/.codex/backups/gestao_simples/db-pre-contas-proprias-20260929-144709.sqlite3`. A comparação dos campos preexistentes confirmou a preservação dos registros de contas, cobranças Pix, mensalidades, repasses e faturas. Essa aplicação local não executa migrations no servidor de produção.

## Resultado da validação desta rodada

- `python manage.py check`: sem problemas.
- `python manage.py makemigrations --check --dry-run`: nenhuma mudança de schema sem migration.
- `python manage.py test`: 649 testes aprovados, em 448,522 segundos.
- Último reforço de confirmação de origem no webhook legado: 39 testes direcionados aprovados (`tests.test_woovi_webhook` e `tests.test_woovi_conta_propria`), incluindo o novo caso adicional de isolamento do legado.
- Migration de ida com dados financeiros antigos: teste aprovado, preservando códigos Pix, vínculos, repasse e credencial das faturas antigas mesmo com a nova credencial da plataforma configurada.
- Interface revisada no navegador em desktop e 390 px; sem rolagem horizontal no celular. Detector da skill Impeccable sem achados na tela alterada.

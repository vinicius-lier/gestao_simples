# Uptime Kuma — Gestão Simples

## Situação da revisão

Verificação externa em 28/09/2026: `/`, `/health/`, `/login/` e
`/static/portal/logo-fukuda.png` responderam HTTP 200 em
`https://judokeikofukuda.com.br`, sem redirecionamento. O health retornou
`{"status": "ok", "database": "ok"}`; o login continha o campo de senha.
O DNS resolveu para `207.246.78.92` naquele momento (não fixar esse IP como
critério de sucesso, pois pode mudar).

Painel existente revisado e alterado em 28/09/2026. Resultado observado:
**10 monitores ligados e 1 desligado (WhatsApp da escola)**.

| ID | Monitor | Resultado da revisão |
| --- | --- | --- |
| 1 | Sistema (site e banco) | Trocada palavra-chave dependente de espaços por JSONata `status & ":" & database` = `ok:ok`; timeout 15 s e sem redirecionamentos; ligado |
| 2 | Evolution API | Raiz da API acessível; preservado |
| 3 | WhatsApp da escola | JSONata `instance.state` = `open`, timeout 15 s, sem redirecionamentos; preservados instância `academia-1`, credencial e intervalo de 300 s; continua desligado |
| 4 | Webhook da Woovi | URL incompleta substituída por `https://judokeikofukuda.com.br/webhooks/woovi/`; timeout 15 s, sem redirecionamentos; recuperou com HTTP 200 e palavra-chave `"ok"` |
| 5 | Painel do Coolify | `/api/health` acessível; preservado |
| 6 | DNS do site | Substituído domínio temporário sslip.io por `judokeikofukuda.com.br`; consulta A via 1.1.1.1; ligado |
| 8 | SisArb | Alvo e disponibilidade revisados; outro sistema, preservado |
| 10 | Shiai Sistem | Alvo e disponibilidade revisados; outro sistema, preservado |
| 11 | Keiko — site público e certificado | Criado, 60 s, aviso de certificado e domínio habilitados; ligado |
| 12 | Keiko — página de login | Criado, 300 s, palavra-chave `type="password"`; ligado |
| 13 | Keiko — arquivos estáticos | Criado, 300 s; ligado |

Os três novos monitores têm 2 retentativas, timeout de 15 s, sem
redirecionamentos e a notificação Discord existente associada. Não foi
disparado teste manual de notificação; mudanças de estado podem gerar
os alertas automáticos configurados. A faixa HTTP existente `200-299` foi
preservada; a tabela abaixo recomenda restringir a 200. Os monitores novos
responderam 200 nas verificações observadas. Login e estáticos tiveram uma
verificação inicial; site teve três. A janela completa de observação dos
monitores de 5 minutos fica para operação contínua.

A API Evolution responde, mas o monitor de conexão não encontra `open`.
Conferir a instância no sistema e parear novamente se necessário; esta
revisão não desconectou nem reconectou aparelhos. Kuma e Evolution usam
endereços HTTP na configuração existente; migrar o painel para HTTPS e a
Evolution para HTTPS ou rede privada é uma pendência de infraestrutura.

## Referência de configuração dos monitores públicos

Base: `https://judokeikofukuda.com.br`. Usar GET, timeout de 15 segundos,
2 retentativas com intervalo de 30 segundos e somente HTTP 200 como sucesso.
Definir redirecionamentos máximos como 0 nestas URLs já canônicas para que
uma página de erro/login redirecionada não pareça saudável. Manter a
validação TLS habilitada. Não habilitar modo invertido.

| Nome | Tipo no Kuma | Alvo | Intervalo | Critério adicional |
| --- | --- | --- | --- | --- |
| Keiko — aplicação e banco | HTTP(s) - JSON Query | `/health/` | 60 s | Expressão `status & ":" & database`, valor esperado `ok:ok` |
| Keiko — site público | HTTP(s) | `/` | 60 s | Ativar aviso de expiração do certificado |
| Keiko — login | HTTP(s) - Keyword | `/login/` | 300 s | Palavra-chave literal `type="password"` |
| Keiko — arquivos estáticos | HTTP(s) | `/static/portal/logo-fukuda.png` | 300 s | Confirma entrega do arquivo, não apenas resposta do Django |
| Keiko — DNS público | DNS | `judokeikofukuda.com.br`, tipo A | 300 s | Resolver público `1.1.1.1`, porta 53; resposta A válida |

A expressão do health usa JSONata. Falha de conteúdo JSON pode alertar
imediatamente, independentemente das retentativas, conforme a versão do
Kuma. Confirmar a expressão no painel e verificar um heartbeat saudável.
O endpoint executa `SELECT 1`; isso verifica conectividade ao banco, mas
não comprova integridade dos dados nem execução das rotinas financeiras.
Ele envia cabeçalhos contra cache; se houver CDN, configurar também bypass
de cache para `/health/`.

Configurar alertas de certificado para 30, 14 e 7 dias, conforme os campos
disponíveis na versão instalada. Centralizar o aviso no monitor do site
para evitar alertas duplicados do mesmo certificado.

## Referência de configuração da Evolution e do WhatsApp

Usar o endereço que o contêiner do Kuma consegue acessar. `127.0.0.1`
dentro dele aponta para o próprio Kuma, não para o servidor Evolution.
No Coolify, confirmar a rede compartilhada e o hostname real do serviço.
Não publicar Redis ou PostgreSQL na internet para monitorá-los.

| Nome | Tipo | Alvo | Intervalo | Sucesso |
| --- | --- | --- | --- | --- |
| Evolution — API | HTTP(s) | `<EVOLUTION_BASE_URL>/` | 60 s | HTTP 200, timeout 15 s, 2 retentativas |
| Keiko — WhatsApp conectado | HTTP(s) - JSON Query | `<EVOLUTION_BASE_URL>/instance/connectionState/<INSTANCIA>` | 120 s | HTTP 200, expressão `instance.state`, valor esperado `open` |

No monitor de conexão, adicionar o cabeçalho JSON
`{"apikey": "<CREDENCIAL_DA_INSTANCIA>"}`. Resolver a chave conforme a
integração da academia: `credencial_ref`, quando preenchida, ou
`EVOLUTION_API_KEY`. Confirmar o nome em `evolution_instance_name`; não
presumir que seja `keiko`. Usar credencial da instância quando disponível.
Guardar a chave somente no painel privado do Kuma; não incluí-la em URL,
captura, página pública, commit ou exportação compartilhada.

`close` ou `connecting` deve falhar mesmo se a API responder 200. Esse
monitor apenas consulta o estado: não envia mensagens nem gera QR Code.
A raiz da Evolution respondendo não prova que o WhatsApp esteja conectado.

## Rotinas e infraestrutura

- **Rotina diária de cobrança:** viável por monitor Push, com janela de
  26 horas para uma execução diária. Integrar o heartbeat apenas após
  término bem-sucedido da rotina já agendada. Não criar um segundo
  agendamento de cobrança. Ainda não instrumentado neste projeto.
- **Backup:** alertas de falha do Coolify e, se houver integração de
  conclusão, Push após backup confirmado, com janela maior que a frequência
  do backup. Nunca enviar sucesso só porque o backup foi iniciado. Um
  heartbeat não substitui teste de restauração.
- **RAM e disco:** já existe `python manage.py verificar_servidor`; manter
  execução a cada 5 minutos e métricas do Sentinel. Não criar um monitor
  HTTP que sempre responde 200 para representar capacidade do servidor.
- **Banco:** o health já cobre a conexão utilizada pelo Django. Um monitor
  PostgreSQL separado só agrega diagnóstico se existir acesso privado e
  usuário de monitoramento restrito. TCP sozinho não valida consultas.
- **Redis da Evolution:** monitor Redis opcional na rede privada; a conexão
  WhatsApp é o sinal principal de disponibilidade para a escola.
- **Woovi:** não usar criação de cobrança, pagamento ou repasse como teste.
  Os alertas da aplicação e do provedor cobrem falhas funcionais. Não tratar
  uma resposta 401 de API como prova de operação financeira saudável.

## Notificações e operação

Associar os monitores ao canal Discord operacional já usado pelo sistema.
O webhook é um segredo. O teste de notificação envia uma mensagem real;
executá-lo quando autorizado pelo responsável do canal. Evitar repetição
contínua de alertas; avisar queda e recuperação. Usar janela de manutenção
nos deploys planejados.

Se o Kuma estiver na mesma VPS, uma queda completa também impede que ele
alerte. Para cobertura dessa falha, manter ao menos o monitor público do
health em uma instância externa. Não é necessário expor monitores internos
ou nomes de academias em página de status pública.

Após cadastrar, observar pelo menos três verificações de cada monitor e
confirmar destino das notificações. Validar falhas em ambiente de teste
(JSON diferente, estado `close`, HTTP 503), sem derrubar produção. Registrar
versão do Kuma, IDs criados e data de ativação nesta revisão. A versão do
Kuma não foi levantada. Os IDs e resultados desta execução estão na seção
inicial. A proteção contra cache em `config/views.py` passou nos cinco testes
de `tests.test_health`, mas ainda depende de deploy para valer em produção.

## Referências oficiais

- [Uptime Kuma: tipos de monitor e recursos](https://github.com/louislam/uptime-kuma)
- [Uptime Kuma: campos e comportamento de JSON Query](https://github.com/louislam/uptime-kuma/blob/master/src/lang/en.json)
- [Evolution: consulta de conexão](https://docs.evoapicloud.com/api-reference/instance-controller/connection-state)

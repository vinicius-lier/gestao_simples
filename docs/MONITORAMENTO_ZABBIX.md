# Monitoramento operacional — Keiko Fukuda

## Coleta pública ativa

Host `Keiko_Web` (nome visível `Keiko Fukuda`), cenário `Keiko - Site e banco`:
- `https://www.judokeikofukuda.com.br/`: HTTP 200.
- `/health/`: HTTP 200 e expressão `"status":\s*"ok".*"database":\s*"ok"`.
- Intervalo 1 minuto, timeout 10 segundos, validação TLS habilitada.
- Alertas: passo falhou; ausência de coleta por 3 minutos; banco acima de 2 segundos
  durante 3 minutos, com pelo menos 3 amostras.
- Ação `Keiko - Discord - Falha e recuperação`, restrita ao host, envia ao administrador
  via Discord. Sem repetição periódica. Recuperação usa o mesmo destino.

## Coleta interna preparada, ainda não publicada

GET `/monitoramento/metricas/`, HTTPS, cabeçalho
`Authorization: Bearer {$KEIKO.MONITORAMENTO.TOKEN}`. Chave aleatória exclusiva,
no mínimo 32 caracteres, somente no ambiente runtime `MONITORAMENTO_TOKEN` do
Coolify e em macro **Texto secreto** do host Zabbix. Nunca em URL, Git ou logs.
Vazio desabilita a rota (404); falha na consulta retorna 503, nunca contagens zero.
Resposta sem cache, sem nomes, IDs, valores financeiros, telefones ou payloads.

Item mestre HTTP (texto), intervalo 1 minuto, timeout 10 segundos, HTTP 200,
TLS validado. Itens dependentes numéricos com JSONPath `$.<campo>`:

| Campo | Significado / condição de alerta |
|---|---|
| `repasses_com_falha` | Repasse não concluído, com falha, requer atenção ou reconciliação; > 0 |
| `repasses_pendentes_30m` | Pendente/processando, sem atualização há 30 minutos; > 0 |
| `webhooks_com_erro` | Evento financeiro em ERRO; > 0 |
| `webhooks_pendentes_5m` | Evento RECEBIDO há mais de 5 minutos; > 0 |
| `contas_com_erro` | Conta com ERRO de negócio ativo; > 0 |
| `lembretes_com_erro` | Lembrete com ERRO de mensalidade pendente/vencida; > 0 |
| `whatsapp_desconectados` | Evolution configurado com erro ou conexão anterior perdida; > 0 |
| `tipos_erro_registrados_24h` | Tipos de erro registrados no mecanismo existente nas últimas 24h; > 0 |
| `captura_erros_configurada` | Configuração existente de alertas Discord presente; alerta se = 0 |

Acrescentar ausência de coleta por 5 minutos. Não substituir erro de JSONPath por
zero. Ativar itens/alertas internos apenas após publicação e coleta normal validada.
Não há migrations, dependências novas, retries ou ações financeiras neste código.

## Limites e resposta operacional

- Alerta identifica pendência; não é autorização para repetir saque ou envio.
  Conferir no provedor e no sistema antes de qualquer correção financeira.
- ENVIADO no lembrete significa envio aceito, **não entrega ao destinatário**.
  O webhook Evolution atual registra conexão; não persiste recibos de entrega.
- Estado de conexão depende do último evento recebido; não é uma sonda ativa.
- `AlertaEnviado` registra tentativa deduplicada antes do POST ao Discord;
  a contagem não prova entrega da notificação nem conta todas as ocorrências.
  A presença da configuração também não prova validade do webhook Discord.
- Erros históricos não resolvidos continuam alertando. Erros registrados saem da
  janela de 24h por tempo; recuperação desse indicador não comprova correção.
- Atualizações no repasse reiniciam sua idade. Pagamento pendente do cliente,
  isoladamente, não significa indisponibilidade bancária.
- Disponibilidade pública da Woovi é sondada no host Ciclo_API; não comprova
  autenticação de contas nem sucesso de Pix. Nenhuma operação financeira de teste.
- Não alterar rotinas ou dados para simular incidentes. Testes de código usam banco
  isolado e HTTP externo bloqueado; teste de canal deve ser explicitamente sintético.

## Implantação pendente

Autorizar a nova credencial de leitura e a publicação no serviço **Keiko_fukuda**,
repositório `gestao_simples`, branch `main`. A base examinada foi `4507a4f`.
Revalidar o topo remoto antes de publicar; não incluir alterações locais de outros
trabalhos. Confirmar hash implantado, `/health/`, resposta autenticada e coleta
normal pelo Zabbix. Shiai permanece pausado durante sua transição.

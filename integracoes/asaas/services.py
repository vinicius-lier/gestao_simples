from django.db import transaction

from financeiro.models import Mensalidade
from integracoes.asaas.client import AsaasAPIError, AsaasClient

# Prefixo do externalReference enviado ao Asaas. Formato estável e sem dados
# pessoais: "mensalidade:<pk>". Serve para reconciliar cobranças quando a
# resposta de criação se perde (timeout) — evitando cobrança duplicada.
EXTERNAL_REFERENCE_PREFIXO = "mensalidade"


def external_reference_da_mensalidade(mensalidade):
    return f"{EXTERNAL_REFERENCE_PREFIXO}:{mensalidade.pk}"


def _copiar_dados_cobranca(origem, destino):
    """Reflete no objeto em memória (destino) os campos de cobrança já
    persistidos (origem), para o chamador não precisar dar refresh_from_db."""
    destino.asaas_payment_id = origem.asaas_payment_id
    destino.asaas_invoice_url = origem.asaas_invoice_url
    destino.asaas_bank_slip_url = origem.asaas_bank_slip_url


def sincronizar_responsavel_asaas(responsavel):
    if not responsavel.cpf:
        raise ValueError(
            "O responsável precisa possuir CPF para integração com o Asaas."
        )

    asaas = AsaasClient()

    if responsavel.asaas_customer_id:
        cliente = asaas.buscar_cliente_por_id(
            responsavel.asaas_customer_id
        )

        if cliente is not None:
            return responsavel.asaas_customer_id

        responsavel.asaas_customer_id = ""
        responsavel.save(update_fields=["asaas_customer_id"])

    cliente = asaas.buscar_cliente_por_cpf_cnpj(
        responsavel.cpf
    )

    if cliente is None:
        cliente = asaas.criar_cliente(
            nome=responsavel.nome,
            cpf_cnpj=responsavel.cpf,
            telefone=responsavel.whatsapp,
            email=responsavel.email,
        )

    customer_id = cliente.get("id") if isinstance(cliente, dict) else None
    if not customer_id:
        raise AsaasAPIError(
            "A resposta do Asaas não contém o identificador do cliente."
        )

    responsavel.asaas_customer_id = customer_id
    responsavel.save(
        update_fields=["asaas_customer_id"]
    )

    return customer_id


def criar_cobranca_asaas(mensalidade, billing_type="PIX"):
    """Cria (ou reaproveita) a cobrança da mensalidade no Asaas.

    Blindagem contra cobrança duplicada quando duas requisições disparam ao
    mesmo tempo (ex.: operador clica "Gerar Pix" enquanto o responsável abre a
    página pública):

    1. checagem barata no objeto recebido (status / responsável / id);
    2. `select_for_update()` sobre a linha da Mensalidade dentro de uma
       transação;
    3. nova checagem de `asaas_payment_id` já sob o lock — se outra requisição
       criou a cobrança, devolve a existente sem chamar o Asaas;
    4. `externalReference` estável no payload + reconciliação: se a chamada de
       criação falhar por timeout/conexão mas o Asaas já tiver criado a
       cobrança, ela é localizada e adotada em vez de criar uma segunda.
    """
    if mensalidade.status == "paga":
        raise ValueError("Uma mensalidade paga não pode ser cobrada.")

    if mensalidade.status == "cancelada":
        raise ValueError("Uma mensalidade cancelada não pode ser cobrada.")

    if mensalidade.status != "pendente":
        raise ValueError(
            "Somente mensalidades pendentes podem gerar cobrança no Asaas."
        )

    responsavel = mensalidade.matricula.atleta.responsavel_financeiro
    if responsavel is None:
        raise ValueError(
            "O atleta não possui responsável financeiro para a cobrança."
        )

    if mensalidade.asaas_payment_id:
        return mensalidade.asaas_payment_id

    with transaction.atomic():
        travada = (
            Mensalidade.objects.select_for_update()
            .select_related("matricula__atleta__responsavel_financeiro")
            .get(pk=mensalidade.pk)
        )

        # Outra requisição concorrente pode ter criado a cobrança entre a
        # checagem acima e a aquisição do lock.
        if travada.asaas_payment_id:
            _copiar_dados_cobranca(travada, mensalidade)
            return travada.asaas_payment_id

        if travada.status in ("paga", "cancelada"):
            raise ValueError(
                f"Uma mensalidade {travada.get_status_display().lower()} "
                "não pode ser cobrada."
            )

        customer_id = sincronizar_responsavel_asaas(responsavel)
        asaas = AsaasClient()
        external_reference = external_reference_da_mensalidade(travada)

        try:
            cobranca = asaas.criar_cobranca(
                customer=customer_id,
                billing_type=billing_type,
                valor=travada.valor,
                vencimento=travada.vencimento,
                descricao=f"Mensalidade {travada.competencia:%m/%Y}",
                external_reference=external_reference,
            )
        except AsaasAPIError:
            # A criação remota pode ter concluído mesmo sem resposta útil.
            cobranca = asaas.buscar_cobranca_por_external_reference(
                external_reference
            )
            if not isinstance(cobranca, dict):
                raise

        payment_id = cobranca.get("id") if isinstance(cobranca, dict) else None
        if not payment_id:
            raise AsaasAPIError(
                "A resposta do Asaas não contém o identificador da cobrança."
            )

        travada.asaas_payment_id = payment_id
        travada.asaas_invoice_url = cobranca.get("invoiceUrl") or ""
        travada.asaas_bank_slip_url = cobranca.get("bankSlipUrl") or ""
        travada.save(
            update_fields=[
                "asaas_payment_id",
                "asaas_invoice_url",
                "asaas_bank_slip_url",
            ]
        )
        _copiar_dados_cobranca(travada, mensalidade)

        return payment_id


def criar_cobranca_multipla_asaas(mensalidade):
    """Como criar_cobranca_asaas, mas com billing_type=UNDEFINED: o Asaas
    deixa o próprio pagador escolher Pix, boleto ou cartão na hora de
    pagar. Usada na página pública do responsável."""
    return criar_cobranca_asaas(mensalidade, billing_type="UNDEFINED")


def garantir_cobranca_asaas(mensalidade):
    """Garante que a mensalidade tem uma cobrança Asaas no formato padrão de
    apresentação (UNDEFINED — Pix, boleto ou cartão à escolha do pagador) e
    devolve o payment_id.

    Idempotente: se a cobrança já existe, não chama o Asaas. É o ponto de
    entrada pensado para o fluxo de lembrete (ver financeiro.lembretes)."""
    if mensalidade.asaas_payment_id:
        return mensalidade.asaas_payment_id
    return criar_cobranca_multipla_asaas(mensalidade)


def obter_pix_mensalidade(mensalidade):
    if not mensalidade.asaas_payment_id:
        raise ValueError(
            "A mensalidade ainda não possui cobrança criada no Asaas."
        )

    asaas = AsaasClient()
    dados = asaas.obter_pix_qrcode(mensalidade.asaas_payment_id)

    if not isinstance(dados, dict):
        raise AsaasAPIError(
            "A resposta do Asaas não contém os dados PIX da cobrança."
        )

    payload = dados.get("payload") or None
    qr_code_base64 = dados.get("encodedImage") or None

    return {
        "payment_id": mensalidade.asaas_payment_id,
        "payload": payload,
        "qr_code_base64": qr_code_base64,
        "expiracao": dados.get("expirationDate") or None,
    }

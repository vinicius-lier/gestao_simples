from integracoes.asaas.client import AsaasAPIError, AsaasClient


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


def criar_cobranca_asaas(mensalidade):
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

    customer_id = sincronizar_responsavel_asaas(responsavel)
    asaas = AsaasClient()
    cobranca = asaas.criar_cobranca(
        customer=customer_id,
        billing_type="PIX",
        valor=mensalidade.valor,
        vencimento=mensalidade.vencimento,
        descricao=(
            f"Mensalidade {mensalidade.competencia:%m/%Y}"
        ),
    )

    payment_id = cobranca.get("id") if isinstance(cobranca, dict) else None
    if not payment_id:
        raise AsaasAPIError(
            "A resposta do Asaas não contém o identificador da cobrança."
        )

    mensalidade.asaas_payment_id = payment_id
    mensalidade.save(update_fields=["asaas_payment_id"])

    return payment_id

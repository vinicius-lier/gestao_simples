from integracoes.asaas.client import AsaasClient


def sincronizar_responsavel_asaas(responsavel):
    if responsavel.asaas_customer_id:
        return responsavel.asaas_customer_id

    if not responsavel.cpf:
        raise ValueError(
            "O responsável precisa possuir CPF para integração com o Asaas."
        )

    asaas = AsaasClient()

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

    responsavel.asaas_customer_id = cliente["id"]
    responsavel.save(
        update_fields=["asaas_customer_id"]
    )

    return cliente["id"]
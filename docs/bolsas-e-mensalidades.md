# Bolsas e mensalidades

No cadastro do aluno, informe a bolsa percentual (0 a 100), início e fim do
benefício. Bolsa integral gera mensalidade isenta, sem Pix nem lembrete.
O período é considerado por competência: um benefício válido em algum dia
do mês vale para a mensalidade daquele mês. Sem datas, vale indefinidamente.

O desconto de irmãos deve ser marcado após confirmar o parentesco. A bolsa
tem prioridade e não acumula com o desconto familiar. O desconto de irmãos
reduz apenas o valor em dia; bolsas parciais reduzem os dois valores.
As taxas de matrícula não recebem esses benefícios.

As tabelas da academia são configuradas no admin, em Tabela mensalidades.
O ano indica o início da vigência; a tabela continua até ser substituída.
Sem tabela aplicável, preserva-se o valor individual da matrícula.
Matrículas com valor zero continuam isentas. Nenhuma alteração recalcula
parcelas já emitidas, pagas ou com Pix existente.

A carga da Keiko configura dez turmas, tabela de janeiro de 2027 (130/150,
vencimento 15, irmãos 10%) e cinco bolsas integrais com início em janeiro.
Não distribui alunos entre turmas e não altera a turma teste.

No terminal da aplicação, simule com os IDs previamente conferidos:

```text
python manage.py configurar_keiko_2027 --academia ID --modalidade ID --unidade ID --bolsistas ID1 ID2 ID3 ID4 ID5
```

Somente acrescente `--aplicar` após conferir a simulação. A carga é
transacional e pode ser repetida sem duplicar os registros.

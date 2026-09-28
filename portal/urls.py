from django.contrib.auth import views as auth_views
from django.urls import path
from . import views, views_assinatura, views_responsavel, views_whatsapp, views_experimentais, views_matricula, views_recebimento

app_name = 'portal'
urlpatterns = [
    path('experimental/', views_experimentais.agendar, name='experimentais_publico'),
    path('experimental/resultado/', views_experimentais.resultado, name='experimental_resultado'),
    path('experimental/<int:pk>/', views_experimentais.agendar, name='experimental_agendar'),
    path('agenda/', views_experimentais.agenda, name='agenda'),
    path('agenda/nova/', views_experimentais.agenda, {'novo': True}, name='experimental_nova'),
    path('agenda/configuracoes/', views_experimentais.configuracao, name='experimental_config'),
    path('agenda/<int:pk>/', views_experimentais.agenda, name='experimental_detalhe'),
    path('agenda/inscricoes/<int:pk>/status/', views_experimentais.status, name='experimental_status'),
    # Convite de matrícula — link que o professor envia para a família preencher.
    path('matricula/recebido/', views_matricula.matricula_convite_recebido, name='matricula_convite_recebido'),
    path('matricula/<str:token>/', views_matricula.matricula_convite, name='matricula_convite'),
    path('login/', auth_views.LoginView.as_view(template_name='portal/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.pagina_publica, name='publica'),
    path('painel/', views.dashboard, name='dashboard'),
    path('alunos/', views.alunos, name='alunos'),
    path('matriculas/convites/', views_matricula.convites, name='matricula_convites'),
    path('matriculas/importar/', views_matricula.importar_fichas, name='matricula_importar'),
    path('matriculas/convites/<int:pk>/', views_matricula.convite_detalhe, name='matricula_convite_detalhe'),
    path('matriculas/convites/<int:pk>/acao/', views_matricula.convite_acao, name='matricula_convite_acao'),
    path('matriculas/convites/<int:pk>/enviar/', views_matricula.convite_enviar, name='matricula_convite_enviar'),
    path('alunos/<int:pk>/matriculas/nova/', views.aluno_form, {'nova_matricula': True}, name='nova_matricula'),
    path('alunos/novo/', views.aluno_form, name='novo'),
    path('alunos/<int:pk>/', views.detalhe, name='detalhe'),
    path('alunos/<int:pk>/editar/', views.aluno_form, name='editar'),
    path('alunos/<int:pk>/excluir/', views.aluno_excluir, name='excluir_aluno'),
    path('alunos/<int:pk>/matriculas/<int:matricula_pk>/editar/', views.aluno_form, name='editar_matricula'),
    path('alunos/<int:pk>/matriculas/<int:matricula_pk>/ativar/', views.matricula_ativar, name='ativar_matricula'),
    path('alunos/<int:pk>/acesso-responsavel/', views.gerar_acesso_responsavel, name='gerar_acesso_responsavel'),
    path('financeiro/', views.financeiro_dashboard, name='financeiro'),
    path('financeiro/cobrancas/', views.financeiro_cobrancas, name='financeiro_cobrancas'),
    path('financeiro/cobrancas/<int:pk>/recibo/', views.financeiro_recibo, name='financeiro_recibo'),
    path('financeiro/cobrancas/<int:pk>/pagar/', views.financeiro_marcar_pago, name='financeiro_marcar_pago'),
    path('financeiro/cobrancas/<int:pk>/encerrar/', views.financeiro_encerrar_cobranca, name='financeiro_encerrar_cobranca'),
    path('financeiro/cobrancas/<int:pk>/pix/', views.financeiro_gerar_pix, name='financeiro_gerar_pix'),
    path('financeiro/cobrancas/<int:pk>/pix/qrcode/', views.financeiro_pix_qrcode, name='financeiro_pix_qrcode'),
    path('financeiro/cobrancas/<int:pk>/enviar/', views.financeiro_enviar_cobranca, name='financeiro_enviar_cobranca'),
    path('configuracoes/recebimento/', views_recebimento.recebimento, name='recebimento'),
    path('configuracoes/assinatura/', views_assinatura.minha_assinatura, name='minha_assinatura'),
    path('configuracoes/assinatura/faturas/<int:pk>/paguei/', views_assinatura.assinatura_informar_pagamento, name='assinatura_informar_pagamento'),
    # Configurações → WhatsApp (Evolution API). Tenant = request.academia.
    path('configuracoes/whatsapp/', views_whatsapp.whatsapp_config, name='whatsapp_config'),
    path('configuracoes/whatsapp/qrcode/', views_whatsapp.whatsapp_qrcode, name='whatsapp_qrcode'),
    path('configuracoes/whatsapp/status/', views_whatsapp.whatsapp_status, name='whatsapp_status'),
    path('configuracoes/whatsapp/desconectar/', views_whatsapp.whatsapp_desconectar, name='whatsapp_desconectar'),
    # Portal do responsável — área pública, sem o login de staff.
    path('responsavel/entrar/', views_responsavel.responsavel_login, name='responsavel_login'),
    path('responsavel/esqueci-a-senha/', views_responsavel.responsavel_esqueci, name='responsavel_esqueci'),
    path('responsavel/senha/', views_responsavel.responsavel_senha, name='responsavel_senha'),
    path('responsavel/mensalidade/<int:pk>/recibo/', views_responsavel.responsavel_recibo, name='responsavel_recibo'),
    path('responsavel/entrar/<str:token>/', views_responsavel.responsavel_entrar, name='responsavel_entrar'),
    path('responsavel/link-expirado/', views_responsavel.responsavel_link_expirado, name='responsavel_link_expirado'),
    path('responsavel/sair/', views_responsavel.responsavel_sair, name='responsavel_sair'),
    path('responsavel/', views_responsavel.responsavel_painel, name='responsavel_painel'),
    path('responsavel/mensalidade/<int:pk>/pagar/', views_responsavel.responsavel_pagar, name='responsavel_pagar'),
]

for tipo in ('unidades', 'professores', 'turmas', 'modalidades'):
    urlpatterns += [
        path(f'{tipo}/', views.cadastros, {'tipo': tipo}, name=tipo),
        path(f'{tipo}/novo/', views.cadastros, {'tipo': tipo, 'novo': True}, name=f'{tipo}_novo'),
        path(f'{tipo}/<int:pk>/editar/', views.cadastros, {'tipo': tipo}, name=f'{tipo}_editar'),
        path(f'{tipo}/<int:pk>/excluir/', views.cadastros, {'tipo': tipo, 'excluir': True}, name=f'{tipo}_excluir'),
        path(f'{tipo}/<int:pk>/', views.cadastros, {'tipo': tipo, 'detalhe': True}, name=f'{tipo}_detalhe'),
    ]

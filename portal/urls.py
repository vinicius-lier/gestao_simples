from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = 'portal'
urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='portal/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.pagina_publica, name='publica'),
    path('painel/', views.dashboard, name='dashboard'),
    path('alunos/', views.alunos, name='alunos'),
    path('alunos/<int:pk>/matriculas/nova/', views.aluno_form, {'nova_matricula': True}, name='nova_matricula'),
    path('alunos/novo/', views.aluno_form, name='novo'),
    path('alunos/<int:pk>/', views.detalhe, name='detalhe'),
    path('alunos/<int:pk>/editar/', views.aluno_form, name='editar'),
    path('alunos/<int:pk>/matriculas/<int:matricula_pk>/editar/', views.aluno_form, name='editar_matricula'),
]

for tipo in ('unidades', 'professores', 'turmas', 'modalidades', 'graduacoes'):
    urlpatterns += [
        path(f'{tipo}/', views.cadastros, {'tipo': tipo}, name=tipo),
        path(f'{tipo}/novo/', views.cadastros, {'tipo': tipo, 'novo': True}, name=f'{tipo}_novo'),
        path(f'{tipo}/<int:pk>/editar/', views.cadastros, {'tipo': tipo}, name=f'{tipo}_editar'),
    ]

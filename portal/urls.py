from django.contrib.auth import views as auth_views
from django.urls import path
from . import views

app_name = 'portal'
urlpatterns = [
    path('login/', auth_views.LoginView.as_view(template_name='portal/login.html'), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('alunos/', views.alunos, name='alunos'),
    path('alunos/novo/', views.aluno_form, name='novo'),
    path('alunos/<int:pk>/', views.detalhe, name='detalhe'),
    path('alunos/<int:pk>/editar/', views.aluno_form, name='editar'),
    path('alunos/<int:pk>/matriculas/<int:matricula_pk>/editar/', views.aluno_form, name='editar_matricula'),
]

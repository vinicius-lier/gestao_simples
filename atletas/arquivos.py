"""Foto e documentos dos alunos: onde ficam, o que é aceito e como a foto é
preparada.

Os arquivos ficam em ``MEDIA_ROOT`` (em produção, o volume persistente do
Coolify montado em /app/media) e **nunca** são servidos por uma URL pública:
quem entrega é uma view que confere a academia de quem pede. Por isso o
nome no disco é aleatório e não carrega o nome do aluno.
"""
import io
import os
import uuid

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile

TAMANHO_MAXIMO_DOCUMENTO = 10 * 1024 * 1024
TAMANHO_MAXIMO_FOTO = 10 * 1024 * 1024
LADO_MAXIMO_FOTO = 800

EXTENSOES_DOCUMENTO = {
    '.pdf': 'application/pdf',
    '.jpg': 'image/jpeg',
    '.jpeg': 'image/jpeg',
    '.png': 'image/png',
    '.webp': 'image/webp',
    '.heic': 'image/heic',
    '.heif': 'image/heif',
}
# Assinaturas dos formatos aceitos: a extensão sozinha não garante o tipo.
_ASSINATURAS = {
    '.pdf': (b'%PDF',),
    '.jpg': (b'\xff\xd8\xff',),
    '.jpeg': (b'\xff\xd8\xff',),
    '.png': (b'\x89PNG\r\n\x1a\n',),
    '.webp': (b'RIFF',),
}


def _extensao(nome):
    return os.path.splitext(nome or '')[1].lower()


def caminho_foto(atleta, _nome):
    return f'academias/{atleta.academia_id}/alunos/{atleta.pk or "novo"}/foto-{uuid.uuid4().hex}.jpg'


def caminho_documento(documento, nome):
    ext = _extensao(nome) if _extensao(nome) in EXTENSOES_DOCUMENTO else ''
    return (
        f'academias/{documento.academia_id}/alunos/{documento.atleta_id}/'
        f'documentos/{uuid.uuid4().hex}{ext}'
    )


def validar_documento(arquivo):
    ext = _extensao(arquivo.name)
    if ext not in EXTENSOES_DOCUMENTO:
        raise ValidationError('Envie o documento em PDF ou como foto (JPG, PNG, WEBP ou HEIC).')
    if arquivo.size > TAMANHO_MAXIMO_DOCUMENTO:
        raise ValidationError('O arquivo passa de 10 MB.')
    assinaturas = _ASSINATURAS.get(ext)
    if assinaturas:
        inicio = arquivo.read(16)
        arquivo.seek(0)
        if not any(inicio.startswith(a) for a in assinaturas):
            raise ValidationError('O arquivo não parece ser do tipo informado. Confira e envie de novo.')
    return arquivo


def preparar_foto(arquivo):
    """Abre a foto enviada, gira conforme o celular gravou, reduz para no
    máximo 800 px e salva como JPEG **sem os metadados** (o EXIF de fotos de
    celular pode trazer a localização). Devolve um ContentFile pronto para o
    ImageField."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    if arquivo.size > TAMANHO_MAXIMO_FOTO:
        raise ValidationError('A foto passa de 10 MB.')
    try:
        imagem = Image.open(arquivo)
        # Confere o tamanho antes de decodificar (o open só lê o cabeçalho).
        if imagem.width * imagem.height > 50_000_000:
            raise ValidationError('A foto é grande demais.')
        imagem.load()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise ValidationError('Não conseguimos abrir esta foto. Envie em JPG, PNG ou WEBP.') from None
    finally:
        arquivo.seek(0)
    imagem = ImageOps.exif_transpose(imagem)
    if imagem.mode not in ('RGB', 'L'):
        fundo = Image.new('RGB', imagem.size, 'white')
        fundo.paste(imagem, mask=imagem.getchannel('A') if 'A' in imagem.getbands() else None)
        imagem = fundo
    imagem.thumbnail((LADO_MAXIMO_FOTO, LADO_MAXIMO_FOTO))
    saida = io.BytesIO()
    imagem.convert('RGB').save(saida, 'JPEG', quality=85, optimize=True)
    return ContentFile(saida.getvalue(), name='foto.jpg')


def apagar_arquivo(campo):
    """Apaga o arquivo do disco sem quebrar se ele já não existir."""
    if campo and campo.name:
        try:
            campo.storage.delete(campo.name)
        except OSError:
            pass

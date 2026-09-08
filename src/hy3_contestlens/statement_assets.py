"""Import Luogu statement images as verified local assets, never at run time."""
from __future__ import annotations

import hashlib
import io
import json
import re
from pathlib import Path
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .image_understanding import markdown_images
from .utils import atomic_write, atomic_write_json

MAX_BYTES = 8 * 1024 * 1024
TRUSTED_IMAGE = re.compile(r'https://cdn\.luogu\.com\.cn/upload/(?:image_hosting|pic)/[A-Za-z0-9_-]+\.png\Z')


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def download_image(url: str) -> bytes:
    if not TRUSTED_IMAGE.fullmatch(url):
        raise ValueError('Unsupported statement image URL')
    request = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with build_opener(_NoRedirect()).open(request, timeout=30) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('Statement image exceeds 8 MiB')
    return data


def validate_image(data: bytes) -> None:
    from PIL import Image
    if len(data) > MAX_BYTES:
        raise ValueError('Statement image exceeds 8 MiB')
    with Image.open(io.BytesIO(data)) as image:
        if image.format != 'PNG' or image.width * image.height > 25_000_000:
            raise ValueError('Unsupported or oversized image')
        image.verify()


def localize_images(text: str, directory: Path, *, fetch=download_image) -> str:
    """Leave the statement unchanged on error; cache assets for offline reimports."""
    index_path = directory / 'images' / 'sources.json'
    records = json.loads(index_path.read_text(encoding='utf-8')) if index_path.exists() else {}
    replacements = {}
    for url in markdown_images(text):
        if not url.startswith(('https:', 'http:', '//')):
            continue
        if not TRUSTED_IMAGE.fullmatch(url):
            raise ValueError('Unsupported statement image URL: ' + url)
        name = hashlib.sha256(url.encode()).hexdigest() + '.png'
        path = directory / 'images' / name
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError('Image cache must not contain symlinks')
        data = path.read_bytes() if path.exists() else fetch(url)
        validate_image(data)
        digest = hashlib.sha256(data).hexdigest()
        if url in records and records[url]['sha256'] != digest:
            raise ValueError('Cached statement image changed: ' + url)
        atomic_write(path, data)
        records[url] = {'path': 'images/' + name, 'sha256': digest, 'bytes': len(data)}
        replacements[url] = 'images/' + name
    if replacements:
        atomic_write_json(index_path, records)
        for url, relative in replacements.items():
            text = text.replace(url, relative)
    return text

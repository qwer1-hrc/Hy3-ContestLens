import io
import json

import pytest
from PIL import Image

from hy3_contestlens.statement_assets import localize_images
from hy3_contestlens.image_understanding import markdown_images


def png():
    stream = io.BytesIO()
    Image.new('RGB', (12, 12), 'white').save(stream, format='PNG')
    return stream.getvalue()


def test_localization_and_offline_reimport(tmp_path):
    url = 'https://cdn.luogu.com.cn/upload/image_hosting/sample.png'
    text = f'![graph]({url})\n<img src="{url}">'
    calls = []
    def fetch(source):
        calls.append(source)
        return png()
    result = localize_images(text, tmp_path, fetch=fetch)
    assert calls == [url]
    relative = markdown_images(result)[0]
    assert relative.startswith('images/')
    assert (tmp_path/relative).is_file()
    assert url not in result
    assert localize_images(text, tmp_path, fetch=lambda _: pytest.fail('Network used for cached image')) == result
    assert localize_images(result, tmp_path) == result
    records = json.loads((tmp_path/'images/sources.json').read_text())
    assert records[url]['path'] == relative


@pytest.mark.parametrize('url', ['http://127.0.0.1/a.png', 'https://example.com/a.png',
    'https://cdn.luogu.com.cn.evil.test/upload/pic/a.png', '//cdn.luogu.com.cn/upload/pic/a.png'])
def test_untrusted_image_hosts_never_fetched(tmp_path, url):
    with pytest.raises(ValueError):
        localize_images(f'![x]({url})', tmp_path, fetch=lambda _: pytest.fail('Untrusted URL fetched'))


def test_invalid_download_does_not_create_asset_index(tmp_path):
    with pytest.raises(Exception):
        localize_images('![x](https://cdn.luogu.com.cn/upload/pic/a.png)', tmp_path, fetch=lambda _: b'<html>Error</html>')
    assert not (tmp_path/'images/sources.json').exists()

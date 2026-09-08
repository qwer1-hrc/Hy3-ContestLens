"""Repair imported statement images and refresh only the changed bindings."""
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT/'src'))

from hy3_contestlens.statement_assets import localize_images
from hy3_contestlens.service import ServiceHub
from hy3_contestlens.image_understanding import StatementImages
from hy3_contestlens.utils import atomic_write


def main():
    hub = ServiceHub()
    root = PROJECT.parent/'contest_data'
    scope = next(s for s in hub.store.list_scopes() if s['display_name'] == root.name)
    image_service = StatementImages(hub.resources, hub.settings.image_understanding)
    count = 0
    for manifest in hub.catalog.list():
        if not manifest.statement_relative_path:
            continue
        path = root/manifest.statement_relative_path
        original = path.read_text(encoding='utf-8')
        localized = localize_images(original, path.parent)
        if localized != original:
            atomic_write(path, localized.encode('utf-8'))
        # Also recover a migration interrupted between writing and rebinding.
        found = hub.resources.find_problem_assets(scope['scope_id'], manifest.problem_id)
        selected = found['auto_selected_candidate_id']
        if not selected:
            raise RuntimeError('Cannot bind ' + manifest.problem_id)
        candidate = hub.resources.get_candidate(selected)
        old = hub.store.get_binding(manifest.problem_id)
        if old['document']['sha256'] != candidate['document']['sha256']:
            hub.resources.bind_candidate(scope['scope_id'], manifest.problem_id, candidate, confirmed_by='configured_root_asset_import')
        inspection = image_service.inspect(scope['scope_id'], candidate['document'])
        if inspection['warnings']:
            raise RuntimeError(str(inspection['warnings']))
        for item in inspection['images']:
            image_service.render(scope['scope_id'], candidate['document'], item)
            count += 1
        if inspection['images']:
            print(manifest.problem_id, len(inspection['images']), 'images verified', flush=True)
    print('TOTAL', count, 'images; no external-reference errors')


if __name__ == '__main__':
    main()

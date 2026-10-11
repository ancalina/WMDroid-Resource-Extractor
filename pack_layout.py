# SPDX-License-Identifier: GPL-3.0-or-later
"""Readable resource paths and canonical IDs; extraction provenance stays intact."""
import json
import re
from collections import Counter
from pathlib import PurePosixPath


NETWORK_ROLES = {
    f'IDI_CFI_TRAY_{link.upper()}_{state}': f'library/status/network/{link}/{role}.png'
    for link in ('wired', 'wireless')
    for state, role in [('WARNING', 'warning'),
                        *[(f'ACQUIRING_{i}', f'acquiring-{i:02d}') for i in range(1, 4)]]
}


MEDIA_ROLES = {
    'CHECKBOXES.PNG': 'checkboxes',
    'LISTVIEWHEADER.PNG': 'list-view-header',
    'PROGRESS.PNG': 'progress',
    'WMP_PUSHBUTTON.PNG': 'push-button',
    'WMP_TOOLBARBUTTON.PNG': 'toolbar-button',
}


def _library_role(row):
    if row.get('provider') == 'netshell.dll' and row.get('kind') == 'icon':
        return NETWORK_ROLES.get(row.get('resource_id'))
    if row.get('provider') == 'wmploc.DLL' and row.get('kind') == 'png':
        role = MEDIA_ROLES.get(row.get('resource_id'))
        if role:
            return 'library/media-player/' + role + '.png'
    return None


def _named_assets(profile):
    result = {}
    for row in profile.get('vista', {}).get('catalog', {}).get('assets', []):
        role = _library_role(row)
        if role:
            asset = row['asset']
            if asset in result and result[asset] != role:
                raise ValueError('Conflicting library resource role: ' + asset)
            result[asset] = role
    return result


def _slug(label):
    value = re.sub(r'[^a-z0-9]+', '-', label.lower()).strip('-')
    if not value:
        raise ValueError('Folder label has no readable English role')
    return value


def physical_paths(files, profile):
    known = {}

    def add(asset, path):
        key = 'assets/' + asset
        if key in known and known[key] != path:
            raise ValueError('Conflicting layout role: ' + key)
        known[key] = path

    fixed = {
        'fonts/tahoma.ttf': 'fonts/tahoma/regular.ttf',
        'fonts/tahomabd.ttf': 'fonts/tahoma/bold.ttf',
        'fonts/malgun.ttf': 'fonts/malgun/regular.ttf',
        'fonts/malgunbd.ttf': 'fonts/malgun/bold.ttf',
        'wallpaper_classic.gif': 'backgrounds/classic.gif',
        'rom-gradients.json': 'themes/gradients.json',
        'calendar/lunar/korean.json': 'calendar/lunar/korean.json',
        'vista/catalog.json': 'metadata/library.json',
        'vista-folders/catalog.json': 'metadata/folders.json',
        'icons/start.png': 'launcher/start-logo.png',
        'drawables/wm_start.png': 'titlebar/start-logo.png',
        # Keep native low-resolution references separate from launcher overrides.
        'drawables/wm_wireless.png': 'library/today/wireless.png',
        'drawables/wm_lock.png': 'library/today/lock.png',
        'drawables/wm_ok.png': 'titlebar/ok.png',
        'drawables/wm_volume.png': 'status/volume.png',
        'drawables/wm_signal.png': 'status/cellular-signal.png',
        'drawables/wm_bluetooth_status.png': 'status/bluetooth.png',
        'drawables/wm_battery_full.png': 'status/battery/full.png',
        'drawables/wm_battery_medium.png': 'status/battery/medium.png',
        'drawables/wm_battery_low.png': 'status/battery/low.png',
        'drawables/wm_battery_charge.png': 'status/battery/charging.png',
        'rom-ui/message_warning.png': 'dialogs/warning.png',
        'rom-ui/message_question.png': 'dialogs/question.png',
        'rom-ui/sip_keyboard_505.png': 'input/keyboard.png',
        'rom-ui/notes.png': 'apps/notes.png',
        'rom-ui/contacts.png': 'apps/contacts.png',
        'rom-calendar/event_symbols_11008.png': 'calendar/event-symbols.png',
        'rom-calendar/appointment_symbols_11029.png': 'calendar/appointment-symbols.png',
        'rom-calendar/year_grid_12000.png': 'calendar/year-grid-compact.png',
        'rom-calendar/year_grid_12001.png': 'calendar/year-grid-tall.png',
    }
    for asset, path in fixed.items():
        add(asset, path)
    for role in ('default', 'loudest', 'quietest'):
        add('sounds/wm_volume_' + role + '.wav', 'sounds/volume/' + role + '.wav')
    for role in ('soft', 'loud'):
        add('sounds/wm_touch_' + role + '.wav', 'sounds/screen-tap/' + role + '.wav')
    # Unique native animation frames in playback order, including the pause frame.
    frames = [4685, *range(4724, 4734), 4688, *range(4734, 4744)]
    for index, frame in enumerate(frames, 1):
        add(f'rom-status/connection_progress_{frame}.png',
            f'status/connection/connecting-{index:02d}.png')

    def core_assets(value):
        if isinstance(value, dict):
            asset = value.get('asset')
            if asset:
                stem = PurePosixPath(asset).stem
                if asset in fixed:
                    pass
                elif asset.startswith('drawables/wm_cpl_'):
                    add(asset, 'settings/' + stem[len('wm_cpl_'):].replace('_', '-') + '.png')
                elif asset.startswith('drawables/wm_settings_'):
                    add(asset, 'settings/' + stem[len('wm_settings_'):].replace('_', '-') + '.png')
                elif asset.startswith('drawables/wm_'):
                    add(asset, 'launcher/icons/' + stem[len('wm_'):].replace('_', '-') + '.png')
                elif asset.startswith('rom-ui/contact_'):
                    add(asset, 'contacts/' + stem[len('contact_'):].replace('_', '-') + '.png')
                elif asset.startswith('rom-ui/doclist_'):
                    add(asset, 'files/' + stem[len('doclist_'):].replace('_', '-') + '.png')
                elif asset.startswith('rom-calendar/') and asset not in fixed:
                    add(asset, 'calendar/' + stem.replace('_', '-') + '.png')
                elif asset.startswith(('media/', 'pwi/')):
                    add(asset, asset)
            for child in value.values():
                core_assets(child)
        elif isinstance(value, list):
            for child in value:
                core_assets(child)

    for section in ('rom', 'sdk_msi'):
        core_assets(profile.get(section, {}))
    vista = profile.get('vista', {})
    named_assets = _named_assets(profile)
    core_assets(vista.get('raster_aliases', []))
    folders = vista.get('folders', {}).get('icons', [])
    roles = [_slug(row['label']) for row in folders]
    counts, variants = Counter(roles), Counter()
    for row, role in zip(folders, roles):
        variants[role] += 1
        suffix = f'-variant-{variants[role]:02d}' if counts[role] > 1 else ''
        add(row['asset'], 'folders/' + role + suffix + '.png')

    def library_assets(value):
        if isinstance(value, dict):
            for field in ('asset', 'original_asset'):
                asset = value.get(field)
                if asset:
                    if not asset.startswith('vista/'):
                        raise ValueError('Unexpected raw library asset: ' + asset)
                    # Raw resources lack proven roles; keep provider/native name here.
                    add(asset, named_assets.get(asset, 'library/' + asset[len('vista/'):]))
            for child in value.values():
                library_assets(child)
        elif isinstance(value, list):
            for child in value:
                library_assets(child)

    library_assets(vista.get('catalog', {}))
    result, payloads = {}, {}
    for key, data in sorted(files.items()):
        if key not in known:
            raise ValueError('Resource has no supported layout role: ' + key)
        path = known[key]
        if (len(path) > 240 or not re.fullmatch(r'[A-Za-z0-9_.\-/]+', path)
                or any(part in ('', '.', '..') for part in path.split('/'))):
            raise ValueError('Unsafe physical resource path: ' + path)
        if path in payloads and payloads[path] != data:
            raise ValueError('Different resource bytes share physical path: ' + path)
        payloads[path] = data
        result[key] = path
    return result


def canonical_resources(files, profile):
    paths = physical_paths(files, profile)
    result = {}
    catalogs = {'assets/vista/catalog.json', 'assets/vista-folders/catalog.json'}

    def rewrite(value):
        if isinstance(value, dict):
            for field in ('asset', 'original_asset'):
                asset = value.get(field)
                if asset:
                    key = 'assets/' + asset
                    if key not in paths:
                        raise ValueError('Catalog references missing resource: ' + asset)
                    value[field] = paths[key]
            for child in value.values():
                rewrite(child)
        elif isinstance(value, list):
            for child in value:
                rewrite(child)

    for key, data in sorted(files.items()):
        if key in catalogs:
            catalog = json.loads(data)
            rewrite(catalog)
            if key == 'assets/vista-folders/catalog.json':
                for row in catalog['icons']:
                    row['id'] = PurePosixPath(row['asset']).stem
            else:
                ids = {row['id'] for row in catalog['assets']}
                for row in catalog['assets']:
                    role = _library_role(row)
                    if role:
                        canonical_id = str(PurePosixPath(role).with_suffix(''))[len('library/'):].replace('/', '-')
                        ids.remove(row['id'])
                        if canonical_id in ids:
                            raise ValueError('Conflicting library catalog ID: ' + canonical_id)
                        row['id'] = canonical_id
                        ids.add(canonical_id)
            # Other raw library IDs remain native identifiers until roles are known.
            data = json.dumps(catalog, ensure_ascii=False, separators=(',', ':')).encode()
        canonical = 'assets/' + paths[key]
        if canonical in result and result[canonical] != data:
            raise ValueError('Different resource bytes share canonical ID: ' + canonical)
        result[canonical] = data
    return result

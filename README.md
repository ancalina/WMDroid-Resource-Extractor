# WMDroid resource extractor

[한국어](README.ko.md)

Extract a WMDroid resource-pack ZIP from supported user-provided media. This
repository includes no Microsoft/OEM images, fonts, sounds, ROMs, DLLs or
installers. Keep generated packs outside public source and binary releases.

## Requirements

- Python 3 and Pillow.
- Vista ISO: installed `7z` and `wimlib-imagex`, roughly 4 GB of temporary space
  alongside the output.
- SDK MSI: installed `7z`.

The extractor does not download files, install software, use Wine or execute
extracted binaries. It verifies input SHA256 against [profile.json](profile.json)
before parsing; unsupported inputs fail.

## Supported inputs

| Option | Input |
| --- | --- |
| `--vista-iso` | Vista SP1 x64 en-US 6.0.6001.18000; Ultimate WIM index 4 |
| `--rom` | Profile-pinned Korean `PPC_KOR_GSM_VGA.bin` or USA `PPC_USA_GSM_VGA_VR.BIN` from the supported emulator MSI |
| `--sdk-msi` | Windows Mobile 6.1.4 Professional Emulator Images - USA, 6.1.4.20757 |

The supported Vista input is **Windows Vista SP1 Ultimate, x64, English
(en-US), build 6.0.6001.18000**, selected from `sources/install.wim` index 4.
The ISO SHA256 must be
`7bd230e4e5436ce9a3c0b4fe99beba10c5964e2a909de72e50d456e2e35985f5`.
The filename can differ; another edition, architecture, language, build or
repacked ISO is not supported by this profile.

Provide at least one input. `--rom` and `--sdk-msi` are mutually exclusive.
The Vista ISO supplies Malgun 5.00 regular and bold automatically. WM input
supplies Tahoma regular and bold. No separate font input is required.
Launcher custom-font import is independent of this extractor.

`Windows Mobile 6 Professional SDK Refresh.msi` is not the supported MSI.
The MSI adapter verifies the MSI, embedded `sdkfiles.cab`, and
`CGen_PPC_USA_GSM_VGA_VR.BIN` hashes. It extracts those files without installation.
The supported MSI SHA256 is
`5608801ed5bf3cbcd924040b9e21b3b710d510da40b06c71d7ee71a92e6d2fd5`.
Direct Korean/USA ROM and Vista extraction were verified for this source update.
The original MSI was unavailable for a fresh end-to-end rerun; earlier real MSI
extraction checks are separate from these current ROM/Vista checks.

## Create a pack

```sh
python3 check_extractor.py
python3 extract_pack.py --vista-iso /path/vista_x64.iso \
  --rom /path/PPC_KOR_GSM_VGA.bin --output /path/resources.zip
```

Replace `--rom` with `--sdk-msi` to use the supported emulator installer.
An already extracted USA ROM can be passed directly with `--rom`; the input
SHA256 selects the Korean or USA recipe, independently of its filename.
Either Vista or WM input can be omitted. Existing output files are never overwritten.

The CLI prints the output path, current resource count, SHA256 and coverage.
Counts depend on the selected inputs and current profile.
Import the resulting ZIP in WMDroid's resource-pack settings.

The Korean ROM also supplies `calendar/lunar/korean.json`: 150 lunar year
words for 1900–2049 and Korean day, month and leap-month captions. The file
contains data only. Applications load this optional data for lunar calendar display.
USA and Vista packs omit it.

## Language and installation

The launcher UI language and resource-pack language are separate. A pack does
not change UI translations. By default, WMDroid follows the device language:
Korean uses Korean UI; English uses English UI. On Android 7.0 or later, the
first supported language in the device language list is used. If no language
is supported, the UI uses English. An explicit language selection overrides
this behavior.

For Korean resources, use the Korean ROM command above. For English resources,
use the supported USA emulator MSI:

```sh
python3 extract_pack.py --vista-iso /path/vista_x64.iso \
  --sdk-msi /path/emulator-images-usa.msi --output /path/resources-en.zip
```

The Vista ISO must be the supported English ISO for either pack. Do not substitute
a Korean Vista ISO. An extracted USA ROM can replace `--sdk-msi` with
`--rom /path/PPC_USA_GSM_VGA_VR.BIN`. The Korean ROM adds Korean lunar-calendar
data; USA packs do not include it. Font selection remains a separate launcher
setting.

1. Copy the generated ZIP to the Android device. Do not unzip it.
2. Open **Start → Settings → Personal → Resource pack → Import** and select the ZIP.
3. To follow the device language, open **Start → Settings → System → Language**
   and select **System language**. Select **한국어** or **English** to override it.

In Korean UI, the same paths are **시작 → 설정 → 개인 → 리소스팩 → 가져오기**
and **시작 → 설정 → 시스템 → 언어 → 시스템 언어**. Existing explicit language
choices remain unchanged after an update; select **System language** to clear one.
SDK apps use the launcher's selected pack; do not import a second pack into them.

## Edit a pack

Format **2** contains `pack.json` and mapped files at the ZIP root. Each
`assets/<role-path>` ID maps to `<role-path>`. Unzip, replace a mapped file, and
ZIP the manifest and files again. Preserve a supported file format. Launcher
and SDK validate paths, file types and sizes; replacement files need not match
original pixel hashes. Format 1 packs must be regenerated; no aliases are emitted.

Original 32px wireless/lock references use `library/today/{wireless,lock}.png`; the SDK uses its bundled authored drawings by default.
Custom packs can override them with `launcher/icons/{wireless,lock}.png` (32×32 PNG), or `vectors/{wireless,lock}` vector IDs.

| Path | Role |
| --- | --- |
| `launcher/start-logo.png` | Start logo |
| `status/battery/charging.png` | Battery charging |
| `status/connection/connecting-01.png` … `connecting-22.png` | Connection animation |
| `dialogs/question.png` | Question dialog |
| `settings/backlight.png` | Backlight settings icon |
| `folders/yellow-upright-folder-variant-01.png` | Folder selection |
| `fonts/tahoma/regular.ttf`, `bold.ttf` | Tahoma regular/bold |
| `fonts/malgun/regular.ttf`, `bold.ttf` | Malgun regular/bold from Vista |
| `backgrounds/classic.gif` | Today background |
| `sounds/screen-tap/soft.wav`, `loud.wav` | Screen tap sounds |
| `sounds/volume/default.wav` | Volume preview |
| `themes/gradients.json` | Theme gradient table |
| `media/` | Windows Media portrait GIFs and `.skn` layout |
| `pwi/{memo,meeting,phone,todo}.pwi` | Notes templates |
| `library/status/network/{wired,wireless}/acquiring-01.png` … `acquiring-03.png`, `warning.png` | Desktop network icons |
| `library/media-player/{checkboxes,list-view-header,progress,push-button,toolbar-button}.png` | Media Player control strips |

`metadata/` catalogs contain labels, dimensions, native provenance and extraction
checksums. They contain no extracting computer name, user name, local path or
extraction timestamp. ZIP timestamps are fixed to `1980-01-01 00:00:00`.
Catalog IDs for classified library entries are
`status-network-<wired|wireless>-<acquiring-01..03|warning>` and
`media-player-<control>`. Unclassified desktop entries retain native resource
names under `library/`; their UI roles have not been verified.

## Coverage and checks

The profile selects resources from 15 Vista providers, 80 folder icons, the
native 256×256/32-bit Start logo (`sources/arunimg.dll`, group 215, language 1033),
Malgun fonts, and WM launcher/applet images, connection frames, Tahoma fonts,
wallpaper, touch/volume sounds, a 73-record AYGSHELL color table, Media skins and
four PWI templates. It does not extract every Vista or WM resource. Missing
roles use launcher fallbacks; remaining detailed vector replacements are unverified.

PNG pixels are checked against recorded RGBA hashes; source providers and
unchanged files against SHA256. PNG metadata is removed without changing pixels;
emitted catalog hashes describe the encoded output. Cursor containers, masks
and animated frames are preserved. Calendar/contact/notes images retain the
profile's crop/color-key rules and WinCE RGB565 AND-mask handling.

Color-table checks cover the section, raw table and decoded records; UTF16
pointers are bounded to the original section. GDI endpoint rasterization and
color override application remain unverified.

Korean and USA selected images, byte resources and color records match the
Korean reference, except Tahoma and PWI template bytes. Each input has its own
hash checks. SDK demos read original PWI templates from the selected launcher
pack; the templates are not bundled with SDK or launcher releases.

## Parser scope and license

`extract_imgfs.py`, `restore_pe.py`, and `extract_resources.py` are reused unchanged
from the local project's static parsers. Their earliest authorship or external
reference has not been established; this does not establish external copying.
Their extra analysis CLI functions are not called by the pack entry point.
`restore_pe.py` builds analysis containers, not runnable CE executables.

The XPRESS decoder stops at the decoded size specified by an IMGFS chunk; it
does not consume an arbitrary stream to its end. `check_extractor.py` checks the
two official [MS-XCA section 3.1 vectors](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-xca/72da4f8d-2ba3-437d-b772-2e4173713a0b)
and authored flag, overlap, length-extension and invalid-buffer cases against
[section 2.4.4](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-xca/34cb9ab9-5ce6-42d7-a518-107c1c7c65e7).

Authored extractor code: [GPL-3.0-or-later](LICENSE). Input and generated resource
payloads are separate from that license. Source-provenance review remains
incomplete; no redistribution clearance for input media or generated packs is
asserted.

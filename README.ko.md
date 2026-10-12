# WMDroid 리소스 추출기

[English](README.md)

사용자가 제공한 지원 대상 미디어에서 WMDroid 리소스팩 ZIP을 추출합니다. 이 저장소에는 Microsoft/OEM 이미지, 폰트, 사운드, ROM, DLL이나 설치 프로그램을 포함하지 않습니다. 생성한 팩은 공개 소스나 바이너리 릴리스에 포함해서는 안 됩니다.

## 준비물

- Python 3과 Pillow가 필요합니다.
- Vista ISO를 사용하려면 `7z`와 `wimlib-imagex`가 설치되어 있어야 합니다. 출력 위치와 같은 곳에 약 4 GB의 임시 공간도 필요합니다.
- SDK MSI를 사용하려면 `7z`가 설치되어 있어야 합니다.

추출기는 파일을 다운로드하거나 소프트웨어를 설치하지 않습니다. Wine을 사용하거나 추출한 바이너리를 실행하지도 않습니다. 파싱 전에 입력의 SHA256을 [profile.json](profile.json)과 대조하며, 지원하지 않는 입력은 오류로 처리합니다.

## 지원 입력

| 옵션 | 입력 |
| --- | --- |
| `--vista-iso` | Vista SP1 x64 en-US 6.0.6001.18000; Ultimate WIM 인덱스 4 |
| `--rom` | 프로필에 해시가 고정된 한국어 `PPC_KOR_GSM_VGA.bin` 또는 지원되는 에뮬레이터 MSI의 USA `PPC_USA_GSM_VGA_VR.BIN` |
| `--sdk-msi` | Windows Mobile 6.1.4 Professional Emulator Images - USA, 6.1.4.20757 |

지원하는 Vista 입력은 **Windows Vista SP1 Ultimate, x64, 영어 (en-US), 빌드 6.0.6001.18000**입니다. `sources/install.wim`의 인덱스 4를 사용합니다. ISO의 SHA256은 다음 값이어야 합니다.
`7bd230e4e5436ce9a3c0b4fe99beba10c5964e2a909de72e50d456e2e35985f5`.
파일명은 달라도 됩니다. 다른 에디션, 아키텍처, 언어, 빌드나 다시 패키징한 ISO는 이 프로필에서 지원하지 않습니다.

최소 하나의 입력을 제공해야 합니다. `--rom`과 `--sdk-msi`는 동시에 사용할 수 없습니다. Vista ISO에서는 맑은 고딕 5.00의 일반체와 굵은체를 자동으로 추출합니다. WM 입력에서는 Tahoma의 일반체와 굵은체를 추출합니다. 별도의 폰트 입력은 필요하지 않습니다. 런처의 사용자 폰트 가져오기 기능은 이 추출기와 별개입니다.

`Windows Mobile 6 Professional SDK Refresh.msi`는 지원하지 않습니다. MSI 어댑터는 MSI와 내부의 `sdkfiles.cab`, `CGen_PPC_USA_GSM_VGA_VR.BIN` 해시를 검증한 뒤 설치 없이 파일을 추출합니다. 지원하는 MSI의 SHA256은 다음 값입니다.
`5608801ed5bf3cbcd924040b9e21b3b710d510da40b06c71d7ee71a92e6d2fd5`.
이번 소스 변경에서는 한국어 및 USA ROM의 직접 추출과 Vista 추출을 검증했습니다. 원본 MSI를 확보하지 못해 MSI 추출의 전체 과정을 새로 실행하지는 못했습니다. 이전에 수행한 실제 MSI 추출 검사는 이번 ROM/Vista 검사와 별개입니다.

## 팩 생성

```sh
python3 check_extractor.py
python3 extract_pack.py --vista-iso /path/vista_x64.iso \
  --rom /path/PPC_KOR_GSM_VGA.bin --output /path/resources.zip
```

지원하는 에뮬레이터 설치 프로그램을 사용하려면 `--rom`을 `--sdk-msi`로 바꾸세요. 이미 추출한 USA ROM은 `--rom`으로 직접 전달할 수 있습니다. 추출기는 파일명과 관계없이 입력 SHA256에 따라 한국어 또는 USA 추출 방식을 선택합니다. Vista 입력과 WM 입력 중 하나는 생략할 수 있습니다. 기존 출력 파일은 덮어쓰지 않습니다.

CLI는 출력 경로, 현재 리소스 수, SHA256과 추출 범위를 표시합니다. 리소스 수는 선택한 입력과 현재 프로필에 따라 달라집니다. 생성한 ZIP은 WMDroid의 리소스팩 설정에서 가져오세요.

한국어 ROM은 `calendar/lunar/korean.json`도 제공합니다. 이 파일에는 1900–2049년의 음력 연도 데이터 워드 150개와 한국어 일, 월, 윤월 표기가 들어 있습니다. 파일에는 데이터만 포함됩니다. 앱은 음력 달력을 표시할 때 이 선택적 데이터를 불러옵니다. USA 및 Vista 팩에는 이 파일이 없습니다.

## 언어와 적용 방법

런처 UI 언어와 리소스팩의 언어는 별개입니다. 팩을 바꿔도 UI 번역은 바뀌지 않습니다.
WMDroid는 기본적으로 기기 언어를 따릅니다. 한국어 기기에서는 한국어, 영어 기기에서는 영어로 표시합니다.
Android 7.0 이상에서는 기기 언어 목록에서 처음으로 지원되는 언어를 사용합니다.
지원하는 언어가 없으면 영어로 표시합니다. 언어를 직접 선택했다면 그 선택을 유지합니다.

한국어 리소스는 위 명령의 한국어 ROM을 사용하세요. 영어 리소스는 지원하는 USA 에뮬레이터 MSI로 추출하세요.

```sh
python3 extract_pack.py --vista-iso /path/vista_x64.iso \
  --sdk-msi /path/emulator-images-usa.msi --output /path/resources-en.zip
```

두 경우 모두 지정된 영어 Vista ISO가 필요합니다. 한국어 Vista ISO로 대체할 수 없습니다.
이미 추출한 USA ROM이 있으면 `--sdk-msi` 대신 `--rom /path/PPC_USA_GSM_VGA_VR.BIN`을 사용하세요.
한국어 ROM에는 한국어 음력 데이터가 추가됩니다. USA 팩에는 없습니다.
글꼴은 런처 설정에서 별도로 선택합니다.

1. 생성한 ZIP을 Android 기기에 복사하세요. 압축은 풀지 마세요.
2. **시작 → 설정 → 개인 → 리소스팩 → 가져오기**에서 ZIP을 선택하세요.
3. 기기 언어를 따르려면 **시작 → 설정 → 시스템 → 언어 → 시스템 언어**를 선택하세요.
   **한국어** 또는 **English**를 선택하면 기기 언어와 관계없이 해당 언어를 사용합니다.

영어 UI에서는 **Start → Settings → Personal → Resource pack → Import**와
**Start → Settings → System → Language → System language**로 표시됩니다.
업데이트해도 기존에 직접 선택한 언어는 유지됩니다. 기기 언어를 따르려면 **시스템 언어**를 선택하세요.
SDK 앱도 런처에 적용한 팩을 사용합니다. SDK 앱에 별도의 팩을 넣을 필요는 없습니다.

## 팩 편집

포맷 **2**의 팩은 ZIP 최상위에 있는 `pack.json`과 매핑된 파일로 구성됩니다. 각 `assets/<role-path>` ID는 `<role-path>`에 매핑됩니다. 압축을 풀고 매핑된 파일을 교체한 다음, 매니페스트와 파일을 다시 ZIP으로 묶으세요. 지원하는 파일 형식은 유지해야 합니다. 런처와 SDK는 경로, 파일 유형과 크기를 검증합니다. 교체한 파일의 픽셀 해시는 원본과 같을 필요가 없습니다. 포맷 1 팩은 다시 생성해야 하며, 추출기는 별칭을 출력하지 않습니다.

원본 32px 무선 및 잠금(wireless/lock) 참조 이미지는 `library/today/{wireless,lock}.png`에 있습니다. SDK는 기본적으로 직접 작성해 내장한 도안을 사용합니다. 사용자 팩의 `launcher/icons/{wireless,lock}.png`(32×32 PNG) 또는 `vectors/{wireless,lock}` 벡터 ID로 이를 대체할 수 있습니다.

| 경로 | 역할 |
| --- | --- |
| `launcher/start-logo.png` | 시작 로고 |
| `status/battery/charging.png` | 배터리 충전 |
| `status/connection/connecting-01.png` … `connecting-22.png` | 연결 애니메이션 |
| `dialogs/question.png` | 질문 대화상자 |
| `settings/backlight.png` | 화면 조명 설정 아이콘 |
| `folders/yellow-upright-folder-variant-01.png` | 폴더 선택 |
| `fonts/tahoma/regular.ttf`, `bold.ttf` | Tahoma 일반체와 굵은체 |
| `fonts/malgun/regular.ttf`, `bold.ttf` | Vista에서 추출한 맑은 고딕 일반체와 굵은체 |
| `backgrounds/classic.gif` | 오늘 화면 배경 |
| `sounds/screen-tap/soft.wav`, `loud.wav` | 화면 터치음 |
| `sounds/volume/default.wav` | 볼륨 미리 듣기 |
| `themes/gradients.json` | 테마 그라데이션 테이블 |
| `media/` | Windows Media 세로 화면 GIF 및 `.skn` 레이아웃 |
| `pwi/{memo,meeting,phone,todo}.pwi` | 메모 템플릿 |
| `library/status/network/{wired,wireless}/acquiring-01.png` … `acquiring-03.png`, `warning.png` | 데스크톱 네트워크 아이콘 |
| `library/media-player/{checkboxes,list-view-header,progress,push-button,toolbar-button}.png` | Media Player 컨트롤 스트립 |

`metadata/` 카탈로그에는 레이블, 크기, 원본 출처 정보와 추출 체크섬이 들어 있습니다. 추출에 사용한 컴퓨터 이름, 사용자 이름, 로컬 경로나 추출 시각은 포함하지 않습니다. ZIP 타임스탬프는 `1980-01-01 00:00:00`으로 고정합니다.

분류된 라이브러리 항목의 카탈로그 ID는 `status-network-<wired|wireless>-<acquiring-01..03|warning>` 및 `media-player-<control>`입니다. 분류되지 않은 데스크톱 항목은 `library/` 아래에서 원본 리소스 이름을 유지합니다. 해당 항목의 UI 역할은 검증하지 않았습니다.

## 추출 범위 및 검사

프로필은 Vista 제공 파일 15개, 폴더 아이콘 80개, 원본 256×256/32비트 시작 로고(`sources/arunimg.dll`, 그룹 215, 언어 1033), 맑은 고딕 폰트와 WM 런처/애플릿 이미지, 연결 프레임, Tahoma 폰트, 배경 화면, 터치/볼륨 사운드, 레코드 73개의 AYGSHELL 색상 테이블, Media 스킨과 PWI 템플릿 4개에서 리소스를 선택합니다.

Vista나 WM의 모든 리소스를 추출하지는 않습니다. 필요한 역할의 리소스가 없으면 런처의 대체 표시를 사용합니다. 나머지 세부 벡터 대체 도안은 검증하지 않았습니다.

PNG 픽셀은 기록된 RGBA 해시와 대조합니다. 원본 제공 파일과 변경되지 않은 파일은 SHA256으로 검사합니다. PNG 메타데이터는 픽셀을 바꾸지 않고 제거합니다. 출력 카탈로그의 해시는 인코딩된 출력 파일을 나타냅니다.

커서 컨테이너, 마스크와 애니메이션 프레임은 보존합니다. 달력, 연락처와 메모 이미지는 프로필의 자르기 및 컬러키 규칙과 WinCE RGB565 AND-mask 처리를 유지합니다.

색상 테이블 검사는 섹션, 원시 테이블과 디코딩된 레코드를 대상으로 합니다. UTF16 포인터는 원본 섹션 범위로 제한합니다. GDI 끝점의 래스터화와 색상 재정의 적용은 검증하지 않았습니다.

한국어 및 USA에서 선택한 이미지, 바이트 리소스와 색상 레코드는 Tahoma 및 PWI 템플릿 바이트를 제외하면 한국어 참조와 일치합니다. 각 입력은 자체 해시 검사를 거칩니다. SDK 데모는 선택한 런처 팩에서 원본 PWI 템플릿을 읽습니다. 템플릿은 SDK나 런처 릴리스에 포함하지 않습니다.

## 파서 범위 및 라이선스

`extract_imgfs.py`, `restore_pe.py`, `extract_resources.py`는 로컬 프로젝트의 정적 파서를 변경 없이 재사용합니다. 최초 작성자나 외부 참조는 확인되지 않았습니다. 이 사실만으로 외부 코드를 복사했다고 판단할 수는 없습니다. 팩 생성 진입점은 이 파서의 추가 분석용 CLI 기능을 호출하지 않습니다. `restore_pe.py`는 분석용 컨테이너를 만들며, 실행 가능한 CE 실행 파일을 만들지 않습니다.

XPRESS 디코더는 IMGFS 청크에 지정된 디코딩 크기에서 멈추며, 임의의 스트림을 끝까지 읽지 않습니다. `check_extractor.py`는 공식
[MS-XCA 섹션 3.1 테스트 벡터 2개](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-xca/72da4f8d-2ba3-437d-b772-2e4173713a0b)와
직접 작성한 플래그, 겹침, 길이 확장과 잘못된 버퍼 사례를
[섹션 2.4.4](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-xca/34cb9ab9-5ce6-42d7-a518-107c1c7c65e7)에 따라 검사합니다.

직접 작성한 추출기 코드는 [GPL-3.0-or-later](LICENSE)를 따릅니다. 입력과 생성된 리소스 페이로드는 이 라이선스와 별개입니다. 소스 출처 검토는 아직 완료되지 않았습니다. 입력 미디어나 생성한 팩의 재배포가 허용된다고 주장하지 않습니다.

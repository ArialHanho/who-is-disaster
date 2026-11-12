param([string]$Python, [string]$InnoCompiler)
$ErrorActionPreference='Stop'
$projectRoot=Split-Path $PSScriptRoot -Parent
if (-not $Python) { $Python=Join-Path $projectRoot '.venv/Scripts/python.exe' }
$Python=(Resolve-Path -LiteralPath $Python).Path
if (-not $InnoCompiler) { throw 'Inno Setup ISCC.exe 경로를 -InnoCompiler로 지정해 주세요.' }
Push-Location $projectRoot
$buildPath=$env:PATH
try {
  $version=(& $Python -c "from agent_app import __version__; print(__version__)").Trim()
  if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') { throw '앱 버전을 확인해 주세요.' }
  # Do not collect unrelated DLLs from the development host's injected PATH.
  $env:PATH="$(Split-Path $Python);$env:SystemRoot\System32;$env:SystemRoot"
  $runtimeAssets = foreach ($folder in 'character','fonts','icons') {
    '--add-data'
    "$projectRoot/assets/$folder;assets/$folder"
  }
  & $Python -m PyInstaller --clean --noconfirm --windowed --onedir --noupx --name CodeCompanion --paths $projectRoot --icon (Join-Path $projectRoot 'assets/icons/app.ico') @runtimeAssets --add-data "$projectRoot/licenses;licenses" --distpath dist --workpath build/pyinstaller --specpath build packaging/desktop_entry.py
  if ($LASTEXITCODE -ne 0) { throw '실행 파일 빌드 실패' }
  & $InnoCompiler "/DAppVersion=$version" packaging/CodeCompanion.iss
  if ($LASTEXITCODE -ne 0) { throw '설치 파일 빌드 실패' }
} finally { $env:PATH=$buildPath; Pop-Location }

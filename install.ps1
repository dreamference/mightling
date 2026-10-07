<#
Installs the Puffin client on Windows from a release, with nothing compiled.

    irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex

or, to pick a release:

    & ([scriptblock]::Create((irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1))) -Version 1.5.0

It installs `puffin.exe` and its commands (puffin-search, puffin-fetch, puffin-code, and the
sandbox's helpers when the release carries them) into %LOCALAPPDATA%\Programs\Puffin\bin and puts
that folder on your PATH. Nothing needs administrator rights. The model runs on a Puffin node on
your network (a DGX Spark or another GB10): `puffin` finds it by itself.

It downloads the same assets, by the same names and with the same checks, as `puffin update`
(puffin-rs/src/update.rs), so a machine installed this way is updated by that command.

PREVIEW. Windows builds are not signed yet. Smart App Control, which is on in new Windows 11
installs, blocks unsigned programs outright: it must be off to run Puffin (Windows Security,
App & browser control, Smart App Control settings).

Written for Windows PowerShell 5.1, which every Windows 11 machine has, as well as PowerShell 7,
and kept to ASCII so 5.1 reads it the same whatever the code page
(specs/DREAMFERENCE_PUFFIN_WINDOWS_ARM.md section 16.4).

PUFFIN_RELEASE_REPO names another repository (a fork), PUFFIN_RELEASE_API another API root (a
test's stand-in server), PUFFIN_INSTALL_DIR another install folder. GH_TOKEN or GITHUB_TOKEN, when
set, raises GitHub's rate limit and reads a private fork.
#>
param(
    [string]$Version = "",
    [switch]$NoPath
)

$ErrorActionPreference = "Stop"
# Invoke-WebRequest's progress bar slows 5.1's downloads many times over.
$ProgressPreference = "SilentlyContinue"
# .NET Framework's defaults predate TLS 1.2 on some images; GitHub requires it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say([string]$Text) { Write-Host $Text }
function Fail([string]$Text) { Write-Host "ERROR: $Text" -ForegroundColor Red; throw $Text }

$Repo = if ($env:PUFFIN_RELEASE_REPO) { $env:PUFFIN_RELEASE_REPO } else { "dreamference/mightling" }
$Api = if ($env:PUFFIN_RELEASE_API) { $env:PUFFIN_RELEASE_API } else { "https://api.github.com" }
$InstallDir = if ($env:PUFFIN_INSTALL_DIR) { $env:PUFFIN_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\Puffin\bin" }

# -- which machine ---------------------------------------------------------------------------

# Under x64 emulation on an Arm PC, PROCESSOR_ARCHITECTURE says AMD64 and the real one is in
# PROCESSOR_ARCHITEW6432.
$Machine = if ($env:PROCESSOR_ARCHITEW6432) { $env:PROCESSOR_ARCHITEW6432 } else { $env:PROCESSOR_ARCHITECTURE }
switch ($Machine) {
    "ARM64" { $Arch = "aarch64" }
    "AMD64" { $Arch = "x86_64" }
    default { Fail "unsupported processor: $Machine" }
}
$Target = "$Arch-pc-windows-msvc"
$Build = [Environment]::OSVersion.Version.Build
if ($Build -lt 22000) {
    Fail "Puffin needs Windows 11 (build 22000 or later); this is build $Build."
}

# -- which release ---------------------------------------------------------------------------

$Headers = @{ "Accept" = "application/vnd.github+json"; "User-Agent" = "puffin-install" }
$Token = if ($env:GH_TOKEN) { $env:GH_TOKEN } elseif ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { "" }
if ($Token) { $Headers["Authorization"] = "Bearer $Token" }

$ReleaseUrl = if ($Version) { "$Api/repos/$Repo/releases/tags/v$($Version.TrimStart('v'))" } else { "$Api/repos/$Repo/releases/latest" }
try {
    $Release = Invoke-RestMethod -Uri $ReleaseUrl -Headers $Headers -UseBasicParsing
} catch {
    Fail "could not read $ReleaseUrl ($($_.Exception.Message)). Drafts and pre-releases are not offered; a private fork needs GH_TOKEN."
}
$Tag = $Release.tag_name
$Assets = @{}
foreach ($Asset in $Release.assets) { $Assets[$Asset.name] = $Asset.url }

$Required = @("puffin", "codex-code-mode-host")
$Optional = @("puffin-search", "puffin-fetch", "puffin-code", "codex-windows-sandbox-setup", "codex-command-runner")
$SumsName = "puffin-$Target.sha256sums"
foreach ($Name in $Required) {
    if (-not $Assets.ContainsKey("$Name-$Target.gz")) { Fail "release $Tag carries no $Name for $Target." }
}
if (-not $Assets.ContainsKey($SumsName)) { Fail "release $Tag has no $SumsName." }

Say "Installing Puffin $Tag for $Target (preview, unsigned)"

# -- download and check everything before installing anything ---------------------------------

$Work = Join-Path ([IO.Path]::GetTempPath()) ("puffin-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Work | Out-Null
$DownloadHeaders = @{ "Accept" = "application/octet-stream"; "User-Agent" = "puffin-install" }
if ($Token) { $DownloadHeaders["Authorization"] = "Bearer $Token" }

function Get-Asset([string]$Name) {
    $Path = Join-Path $Work $Name
    Say "  downloading $Name"
    Invoke-WebRequest -Uri $Assets[$Name] -Headers $DownloadHeaders -OutFile $Path -UseBasicParsing
    return $Path
}

function Expand-Gzip([string]$From, [string]$To) {
    $In = [IO.File]::OpenRead($From)
    try {
        $Gzip = New-Object IO.Compression.GZipStream($In, [IO.Compression.CompressionMode]::Decompress)
        $Out = [IO.File]::Create($To)
        try { $Gzip.CopyTo($Out) } finally { $Out.Dispose(); $Gzip.Dispose() }
    } finally { $In.Dispose() }
}

try {
    $Sums = @{}
    foreach ($Line in (Get-Content -Path (Get-Asset $SumsName))) {
        $Parts = $Line.Trim() -split "\s+"
        if ($Parts.Count -ge 2) { $Sums[$Parts[1].TrimStart("*")] = $Parts[0].ToLower() }
    }

    $Wanted = @()
    foreach ($Name in $Required) { $Wanted += $Name }
    foreach ($Name in $Optional) {
        if ($Assets.ContainsKey("$Name-$Target.gz")) { $Wanted += $Name } else { Say "  release $Tag carries no $Name; skipping it" }
    }

    $Ready = @()
    foreach ($Name in $Wanted) {
        $Asset = "$Name-$Target.gz"
        $Archive = Get-Asset $Asset
        if (-not $Sums.ContainsKey($Asset)) { Fail "$SumsName has no checksum for $Asset; nothing was installed." }
        $Actual = (Get-FileHash -Algorithm SHA256 -Path $Archive).Hash.ToLower()
        if ($Actual -ne $Sums[$Asset]) { Fail "$Asset does not match its checksum; nothing was installed." }
        $Binary = Join-Path $Work "$Name.exe"
        Expand-Gzip $Archive $Binary
        $Ready += $Name
    }

    # -- install ---------------------------------------------------------------------------------

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    foreach ($Name in $Ready) {
        $Destination = Join-Path $InstallDir "$Name.exe"
        # A running .exe cannot be overwritten, but it can be renamed: move it aside, as
        # `puffin update` does; puffin deletes the leftovers at its next start.
        if (Test-Path $Destination) {
            $Aside = "$Destination.old"
            $Copy = 1
            while (Test-Path $Aside) {
                try { Remove-Item -Force $Aside } catch { $Copy += 1; $Aside = "$Destination.$Copy.old" }
            }
            Move-Item -Force -Path $Destination -Destination $Aside
        }
        Move-Item -Force -Path (Join-Path $Work "$Name.exe") -Destination $Destination
    }
} finally {
    Remove-Item -Recurse -Force -Path $Work -ErrorAction SilentlyContinue
}

# -- PATH ------------------------------------------------------------------------------------

if (-not $NoPath) {
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @()
    if ($UserPath) { $Entries = $UserPath -split ";" | Where-Object { $_ } }
    if (-not ($Entries -contains $InstallDir)) {
        # Writing the user scope also broadcasts the change, so new terminals see it.
        [Environment]::SetEnvironmentVariable("Path", (($Entries + $InstallDir) -join ";"), "User")
        Say "Added $InstallDir to your PATH (new terminals see it)."
    }
    if (-not (($env:Path -split ";") -contains $InstallDir)) { $env:Path = "$env:Path;$InstallDir" }
}

Say ""
Say "Done. Puffin $Tag is in $InstallDir."
Say ""
Say "PREVIEW: these Windows builds are not signed. If Windows blocks puffin.exe, Smart App Control"
Say "is on; turn it off in Windows Security > App & browser control > Smart App Control settings."
Say ""
Say "Next:"
Say "  puffin node list          # the Puffin nodes (DGX Spark or another GB10) on your network"
Say "  cd <your project>; puffin # start working; it finds the node by itself"
Say "  puffin node use <address> # if your network is marked Public and discovery finds nothing"

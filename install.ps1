<#
Installs the Puffin client on Windows from a release, with nothing compiled.

    irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex

or, to pick a release:

    & ([scriptblock]::Create((irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1))) -Version 1.5.0

It installs `puffin.exe` and its commands (puffin-search, puffin-fetch, puffin-code, and the
sandbox's helpers when the release carries them) into %LOCALAPPDATA%\Programs\Puffin\bin and puts
that folder on your PATH, with no administrator rights. Then Windows asks once for them, to set up
the sandbox the agent's commands run in (which /airgapped on needs), to let puffin find nodes on
Private networks, and to run the egress audit; -NoSandbox skips that, and declining leaves a working
client without a sandbox. The model runs on a Puffin node on your network (a DGX Spark or another
GB10): `puffin` finds it by itself.

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
    [switch]$NoPath,
    [switch]$NoSandbox
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

# -- the sandbox, the firewall and the audit: one Administrator prompt ------------------------
#
# Codex's Windows sandbox runs commands as two local accounts that only an administrator can
# create; the offline one is what takes a command's network away at /airgapped on. One elevation
# sets them up for this user, lets puffin hear node announcements (mDNS, UDP 5353) on Private and
# Domain networks, and runs the egress audit once (section 7.1, 10.2, 14 of the spec). Declining
# leaves a working client whose commands run without a sandbox; puffin says so at every start.

$Puffin = Join-Path $InstallDir "puffin.exe"
if ($NoSandbox) {
    Say ""
    Say "Skipped the Windows sandbox (-NoSandbox): commands will run without one."
} elseif (-not (Test-Path (Join-Path $InstallDir "codex-windows-sandbox-setup.exe"))) {
    Say ""
    Say "Release $Tag carries no sandbox setup for ${Target}: commands will run without a sandbox."
} else {
    # Named now, as the user who ran this: with an administrator's credentials typed over the
    # shoulder of a standard user, the elevated process runs as that administrator instead.
    $User = "$env:USERDOMAIN\$env:USERNAME"
    $CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".puffin" }
    New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
    $Step = Join-Path ([IO.Path]::GetTempPath()) ("puffin-elevated-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Step | Out-Null
    $Script = Join-Path $Step "elevated.ps1"
    $Log = Join-Path $Step "elevated.log"
    Set-Content -Path $Script -Encoding ASCII -Value @'
param([string]$Puffin, [string]$User, [string]$CodexHome, [string]$InstallDir, [string]$Log)
$ErrorActionPreference = "Continue"
function Note([string]$Text) { Add-Content -Path $Log -Value $Text -Encoding UTF8 }
& $Puffin sandbox setup --elevated --user $User --codex-home $CodexHome 2>&1 | ForEach-Object { Note "$_" }
Note "sandbox-setup-exit=$LASTEXITCODE"
try {
    foreach ($Name in @("puffin", "puffin-app")) {
        $Rule = "Puffin-mDNS-$Name"
        Get-NetFirewallRule -Name $Rule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
        New-NetFirewallRule -Name $Rule -DisplayName "Puffin node discovery (mDNS, $Name)" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5353 -Program (Join-Path $InstallDir "$Name.exe") -Profile Private, Domain | Out-Null
    }
    Note "firewall=ok"
} catch {
    Note "firewall=failed: $($_.Exception.Message)"
}
& $Puffin audit egress 2>&1 | ForEach-Object { Note "$_" }
Note "audit-exit=$LASTEXITCODE"
'@
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`" -Puffin `"$Puffin`" -User `"$User`" -CodexHome `"$CodexHome`" -InstallDir `"$InstallDir`" -Log `"$Log`""
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    Say ""
    try {
        if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            Say "Setting up the Windows sandbox and the firewall rule (already running as Administrator)..."
            Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -Wait -NoNewWindow
        } else {
            Say "Windows will ask once for Administrator rights: to set up the sandbox that runs the agent's"
            Say "commands, to let puffin find nodes on Private networks, and to run the egress audit."
            Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -Verb RunAs -Wait -WindowStyle Hidden
        }
    } catch {
        Say "The Administrator prompt was declined or failed ($($_.Exception.Message))."
    }
    $Lines = @()
    if (Test-Path $Log) { $Lines = @(Get-Content -Path $Log -Encoding UTF8) }
    Remove-Item -Recurse -Force -Path $Step -ErrorAction SilentlyContinue
    if ($Lines -contains "sandbox-setup-exit=0") {
        Say "Windows sandbox: set up for $User."
    } else {
        Say "Windows sandbox: NOT set up. Commands will run without one, and /airgapped on is refused."
        foreach ($Line in $Lines) { if ($Line -notmatch "^(firewall|audit-exit)=" -and $Line -notmatch "^sandbox-setup-exit=") { Say "  $Line" } }
    }
    if ($Lines -contains "firewall=ok") {
        Say "Firewall: puffin may hear node announcements (UDP 5353) on Private and Domain networks."
    } elseif ($Lines.Count -gt 0) {
        Say "Firewall: the mDNS rule was not added; discovery may find nothing (puffin node use <address> still works)."
    }
    $AuditExit = $Lines | Where-Object { $_ -match "^audit-exit=" } | Select-Object -First 1
    if ($AuditExit) {
        Say "Egress audit:"
        $InAudit = $false
        foreach ($Line in $Lines) {
            if ($Line -match "^firewall=") { $InAudit = $true; continue }
            if ($Line -match "^audit-exit=") { break }
            if ($InAudit) { Say "  $Line" }
        }
        if ($AuditExit -ne "audit-exit=0") {
            Say "  (Run it again once puffin uses a node: puffin audit egress, in an Administrator terminal.)"
        }
    }
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

<#
Installs the Mightling client on Windows from a release, with nothing compiled.

    irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1 | iex

or, to pick a release:

    & ([scriptblock]::Create((irm https://github.com/dreamference/mightling/releases/latest/download/install.ps1))) -Version 1.5.1

It installs `ling.exe` and its commands (ling-search, ling-fetch, ling-code, and the
sandbox's helpers when the release carries them) into %LOCALAPPDATA%\Programs\Mightling\bin and puts
that folder on your PATH, with no administrator rights. Then Windows asks once for them, to set up
the sandbox the agent's commands run in (which /airgapped on needs), to let ling find nodes on
Private networks, and to run the egress audit; -NoSandbox skips that, and declining leaves a working
client without a sandbox. The model runs on a Mightling node on your network (a DGX Spark or another
GB10): `ling` finds it by itself.

It also installs the desktop app, from the release's Mightling-Setup-<version>-<arm64|x64>.exe
(checked against SHA256SUMS), silently and for you alone, under
%LOCALAPPDATA%\Programs\Mightling\App with a Start menu entry; -NoApp leaves it out. Downloaded
by this script rather than a browser, the installer carries no Mark of the Web, so SmartScreen
does not stop it.

It downloads the same assets, by the same names and with the same checks, as `ling update`
(ling-rs/src/update.rs), so a machine installed this way is updated by that command, the desktop
app included: the script records the app it installed (desktop-app, beside the bin folder), and
`ling update` updates an app it finds recorded, under the same checks.

PREVIEW. Windows builds are not signed yet. Smart App Control, which is on in new Windows 11
installs, blocks unsigned programs outright: it must be off to run Mightling (Windows Security,
App & browser control, Smart App Control settings).

Written for Windows PowerShell 5.1, which every Windows 11 machine has, as well as PowerShell 7,
and kept to ASCII so 5.1 reads it the same whatever the code page
(specs/DREAMFERENCE_MIGHTLING_WINDOWS_ARM.md section 16.4).

MIGHTLING_RELEASE_REPO names another repository (a fork), MIGHTLING_RELEASE_API another API root (a
test's stand-in server), MIGHTLING_INSTALL_DIR another install folder. GH_TOKEN or GITHUB_TOKEN, when
set, raises GitHub's rate limit and reads a private fork.
#>
param(
    [string]$Version = "",
    [switch]$NoPath,
    [switch]$NoSandbox,
    [switch]$NoApp
)

$ErrorActionPreference = "Stop"
# Invoke-WebRequest's progress bar slows 5.1's downloads many times over.
$ProgressPreference = "SilentlyContinue"
# .NET Framework's defaults predate TLS 1.2 on some images; GitHub requires it.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

function Say([string]$Text) { Write-Host $Text }
function Fail([string]$Text) { Write-Host "ERROR: $Text" -ForegroundColor Red; throw $Text }

$Repo = if ($env:MIGHTLING_RELEASE_REPO) { $env:MIGHTLING_RELEASE_REPO } else { "dreamference/mightling" }
$Api = if ($env:MIGHTLING_RELEASE_API) { $env:MIGHTLING_RELEASE_API } else { "https://api.github.com" }
$InstallDir = if ($env:MIGHTLING_INSTALL_DIR) { $env:MIGHTLING_INSTALL_DIR } else { Join-Path $env:LOCALAPPDATA "Programs\Mightling\bin" }

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
    Fail "Mightling needs Windows 11 (build 22000 or later); this is build $Build."
}

# -- which release ---------------------------------------------------------------------------

$Headers = @{ "Accept" = "application/vnd.github+json"; "User-Agent" = "mightling-install" }
$Token = if ($env:GH_TOKEN) { $env:GH_TOKEN } elseif ($env:GITHUB_TOKEN) { $env:GITHUB_TOKEN } else { "" }
if ($Token) { $Headers["Authorization"] = "Bearer $Token" }

$ReleaseUrl = if ($Version) { "$Api/repos/$Repo/releases/tags/v$($Version.TrimStart('v'))" } else { "$Api/repos/$Repo/releases/latest" }
try {
    $Release = Invoke-RestMethod -Uri $ReleaseUrl -Headers $Headers -UseBasicParsing
} catch {
    $Status = if ($_.Exception.Response) { [int]$_.Exception.Response.StatusCode } else { 0 }
    if (($Status -eq 403 -or $Status -eq 429) -and "$($_.ErrorDetails.Message) $($_.Exception.Message)" -match "rate limit") {
        Fail "GitHub's API rate limit is used up for this network ($ReleaseUrl). Wait a while, or set GH_TOKEN to any GitHub token and run this again."
    }
    Fail "could not read $ReleaseUrl ($($_.Exception.Message)). Drafts and pre-releases are not offered; a private fork needs GH_TOKEN."
}
$Tag = $Release.tag_name
$Assets = @{}
foreach ($Asset in $Release.assets) { $Assets[$Asset.name] = $Asset.url }

$Required = @("ling", "codex-code-mode-host")
$Optional = @("ling-search", "ling-fetch", "ling-code", "codex-windows-sandbox-setup", "codex-command-runner")
$SumsName = "ling-$Target.sha256sums"
foreach ($Name in $Required) {
    if (-not $Assets.ContainsKey("$Name-$Target.gz")) { Fail "release $Tag carries no $Name for $Target." }
}
if (-not $Assets.ContainsKey($SumsName)) { Fail "release $Tag has no $SumsName." }

Say "Installing Mightling $Tag for $Target (preview)"

# -- the signature ---------------------------------------------------------------------------

# Mightling's release keys, as `ling update` compiles them in (ling-rs/release-signing.pub) and
# install.sh carries them; every release from $SignedSince on signs SHA256SUMS with one of them.
$ReleaseKeys = @('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKIG5+J3RTa4AaoT0o2qIhVr7bGvaa+T5b84rDv31ptj mightling-release-2026-10')
$SignedSince = "1.5.0"

# Whether a tag is $SignedSince or newer. A tag that is not a version is treated as new: a name
# nobody can read is no reason to skip the check (install.sh and `ling update` decide the same way).
function Test-SigningRequired([string]$Name) {
    if ($Name.TrimStart("v") -notmatch "^(\d+)\.(\d+)\.(\d+)") { return $true }
    $Have = @([long]$Matches[1], [long]$Matches[2], [long]$Matches[3])
    $Since = @($SignedSince.Split(".") | ForEach-Object { [long]$_ })
    for ($I = 0; $I -lt 3; $I++) {
        if ($Have[$I] -gt $Since[$I]) { return $true }
        if ($Have[$I] -lt $Since[$I]) { return $false }
    }
    return $true
}

# Windows 11's own OpenSSH client first (an optional feature, on by default), then one on PATH.
function Find-SshKeygen {
    foreach ($Folder in @("System32", "Sysnative")) {
        $Path = Join-Path $env:SystemRoot "$Folder\OpenSSH\ssh-keygen.exe"
        if (Test-Path -LiteralPath $Path) { return $Path }
    }
    $Found = Get-Command ssh-keygen.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($Found) { return $Found.Path }
    return $null
}

# The allowed-signers lines for public-key lines: `mightling-release <type> <key>`.
function Get-Signers([string[]]$Keys) {
    return @($Keys | ForEach-Object { $_.Trim() } | Where-Object { $_ -and -not $_.StartsWith("#") } |
        ForEach-Object { $Parts = $_ -split "\s+"; "mightling-release $($Parts[0]) $($Parts[1])" })
}

# ssh-keygen -Y verify, the signed file on standard input. Windows's ssh-keygen never sees the end
# of a pipe, and 5.1 re-encodes text it pipes to a program, so the input is the file itself
# (Start-Process hands ssh-keygen the file's handle).
function Test-Signature([string]$Keygen, [string]$Signers, [string]$Namespace, [string]$Signature, [string]$Message) {
    $Folder = Split-Path -Parent $Signers
    $Out = Join-Path $Folder "verify.out"
    $Err = Join-Path $Folder "verify.err"
    $Arguments = "-Y verify -f `"$Signers`" -I mightling-release -n $Namespace -s `"$Signature`""
    $Process = Start-Process -FilePath $Keygen -ArgumentList $Arguments -RedirectStandardInput $Message `
        -RedirectStandardOutput $Out -RedirectStandardError $Err -NoNewWindow -Wait -PassThru
    $script:VerifyOutput = (@(Get-Content -Path $Out, $Err) -join " ").Trim()
    return ($Process.ExitCode -eq 0)
}

function Read-Sums([string]$Path) {
    $Table = @{}
    foreach ($Line in (Get-Content -Path $Path)) {
        $Parts = $Line.Trim() -split "\s+"
        if ($Parts.Count -ge 2) { $Table[$Parts[1].TrimStart("*")] = $Parts[0].ToLower() }
    }
    return $Table
}

# -- download and check everything before installing anything ---------------------------------

$Work = Join-Path ([IO.Path]::GetTempPath()) ("mightling-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $Work | Out-Null
$DownloadHeaders = @{ "Accept" = "application/octet-stream"; "User-Agent" = "mightling-install" }

# The token goes only to the API root, never to a download URL the release names: a release that was
# tampered with, or an old repository name taken over, would otherwise collect it (as in install.sh).
function Get-Asset([string]$Name) {
    $Path = Join-Path $Work $Name
    Say "  downloading $Name"
    $Sent = $DownloadHeaders.Clone()
    if ($Token -and $Assets[$Name].StartsWith("$Api/", [StringComparison]::Ordinal)) {
        $Sent["Authorization"] = "Bearer $Token"
    }
    Invoke-WebRequest -Uri $Assets[$Name] -Headers $Sent -OutFile $Path -UseBasicParsing
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
    $SumsFile = Get-Asset $SumsName
    # SHA256SUMS lists every asset of the release, this PC's checksum file and the desktop app's
    # installer among them, and its signature is what ties everything checked below to the release
    # job (specs/DREAMFERENCE_RELEASE_SIGNING.md).
    $Signed = $null
    if ($Assets.ContainsKey("SHA256SUMS.sig")) {
        $Keygen = Find-SshKeygen
        if (-not $Keygen) {
            Fail "ssh-keygen.exe, which checks the release's signature, was not found. Add Windows's OpenSSH Client (Settings > System > Optional features) and run this again; nothing was installed."
        }
        if (-not $Assets.ContainsKey("SHA256SUMS")) { Fail "release $Tag has SHA256SUMS.sig but no SHA256SUMS; nothing was installed." }
        $Signature = Get-Asset "SHA256SUMS.sig"
        $SignedSums = Get-Asset "SHA256SUMS"
        $Signers = Join-Path $Work "allowed_signers"
        $Lines = Get-Signers $ReleaseKeys
        # A key rotation: the release names the new key, signed by a key trusted here under a
        # namespace of its own (specs/DREAMFERENCE_RELEASE_SIGNING.md section 5).
        if ($Assets.ContainsKey("release-key-transition.pub")) {
            if (-not $Assets.ContainsKey("release-key-transition.pub.sig")) {
                Fail "release $Tag names a new release key but carries no endorsement of it; nothing was installed."
            }
            $NewKey = Get-Asset "release-key-transition.pub"
            $NewKeySig = Get-Asset "release-key-transition.pub.sig"
            [IO.File]::WriteAllText($Signers, (($Lines -join "`n") + "`n"))
            if (-not (Test-Signature $Keygen $Signers "mightling-release-key" $NewKeySig $NewKey)) {
                Fail "release $Tag names a new release key that no trusted key has signed; nothing was installed."
            }
            $Lines += Get-Signers (Get-Content -Path $NewKey)
        }
        # LF and no byte-order mark, which ssh-keygen reads as it reads a file written on Linux.
        [IO.File]::WriteAllText($Signers, (($Lines -join "`n") + "`n"))
        if (-not (Test-Signature $Keygen $Signers "mightling-release" $Signature $SignedSums)) {
            Fail "release $Tag failed its signature check: SHA256SUMS is not signed by Mightling's release key ($VerifyOutput). Nothing was installed."
        }
        $Signed = Read-Sums $SignedSums
        if (-not $Signed.ContainsKey($SumsName)) { Fail "SHA256SUMS has no checksum for $SumsName; nothing was installed." }
        if ((Get-FileHash -Algorithm SHA256 -Path $SumsFile).Hash.ToLower() -ne $Signed[$SumsName]) {
            Fail "$SumsName does not match its checksum in SHA256SUMS; nothing was installed."
        }
        Say "  release $Tag is signed by Mightling's release key"
    } elseif (-not $Version) {
        # Asked for the latest release, an install takes whatever is marked latest: the tag of an
        # unsigned one is only a name, and an old-looking one (v1.4.9) must not bypass the
        # signature. A release from before signing is installed only when named with -Version.
        Fail "the latest release ($Tag) is not signed (it has no SHA256SUMS.sig); nothing was installed. Pass -Version to install a release from before $SignedSince on purpose."
    } elseif (Test-SigningRequired $Tag) {
        Fail "release $Tag is not signed (it has no SHA256SUMS.sig); nothing was installed. Every release from $SignedSince on is signed by Mightling's release key, so an unsigned one did not come from its release job."
    } else {
        Say "  release $Tag predates signed releases ($SignedSince); it is checked against its checksums only"
    }

    $Sums = Read-Sums $SumsFile

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

    # The desktop app: the release's installer for this PC (windows.yml names it
    # Mightling-Setup-<version>-<arm64|x64>.exe), checked against the release-wide SHA256SUMS
    # before anything is installed. A release without one installs the command-line client alone.
    $AppSetup = $null
    if (-not $NoApp) {
        $AppArch = if ($Arch -eq "aarch64") { "arm64" } else { "x64" }
        $SetupName = @($Assets.Keys | Where-Object { $_ -match "^Mightling-Setup-.+-$AppArch\.exe$" }) | Select-Object -First 1
        if (-not $SetupName) {
            Say "  release $Tag carries no desktop app for $Target; installing the command-line client only"
        } elseif (-not $Assets.ContainsKey("SHA256SUMS")) {
            Say "  release $Tag has no SHA256SUMS to check $SetupName against; leaving the desktop app out"
        } else {
            # The signed SHA256SUMS; only a release from before signing began gets here without one.
            $AllSums = $Signed
            if ($null -eq $AllSums) { $AllSums = Read-Sums (Get-Asset "SHA256SUMS") }
            if (-not $AllSums.ContainsKey($SetupName)) { Fail "SHA256SUMS has no checksum for $SetupName; nothing was installed." }
            $AppSetup = Get-Asset $SetupName
            $Actual = (Get-FileHash -Algorithm SHA256 -Path $AppSetup).Hash.ToLower()
            if ($Actual -ne $AllSums[$SetupName]) { Fail "$SetupName does not match its checksum; nothing was installed." }
            # SmartScreen's first-run check is for files marked as coming from the internet, which
            # browsers mark; make sure this one carries no such mark.
            Unblock-File -Path $AppSetup
        }
    }

    # -- install ---------------------------------------------------------------------------------

    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    foreach ($Name in $Ready) {
        $Destination = Join-Path $InstallDir "$Name.exe"
        # A running .exe cannot be overwritten, but it can be renamed: move it aside, as
        # `ling update` does; ling deletes the leftovers at its next start.
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

    # The NSIS installer, silent (/S): no administrator, no window, and the app is not started.
    # It closes a running copy of the app before it replaces it.
    $AppResult = ""
    if ($AppSetup) {
        Say "  installing the desktop app"
        try {
            $Process = Start-Process -FilePath $AppSetup -ArgumentList "/S" -PassThru -Wait
            $AppResult = if ($Process.ExitCode -eq 0) { "ok" } else { "the installer exited with $($Process.ExitCode)" }
            if ($AppResult -eq "ok") {
                # What `ling update` reads to update the app too (update.rs, APP_RECORD).
                $AppKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mightling"
                $AppDir = (Get-ItemProperty -Path $AppKey -ErrorAction SilentlyContinue).InstallLocation
                if (-not $AppDir) { $AppDir = Join-Path $env:LOCALAPPDATA "Programs\Mightling\App" }
                $Record = Join-Path (Split-Path -Parent $InstallDir) "desktop-app"
                Set-Content -Path $Record -Encoding UTF8 -Value @("path=$AppDir", "version=$Tag")
            }
        } catch {
            $AppResult = $_.Exception.Message
        }
    }
} finally {
    Remove-Item -Recurse -Force -Path $Work -ErrorAction SilentlyContinue
}

# -- PATH ------------------------------------------------------------------------------------

if (-not $NoPath) {
    # The registry value itself, unexpanded, and written back as the kind it was: the .NET
    # environment calls expand %USERPROFILE% and the like on read and write REG_SZ, which would
    # freeze every entry that depends on another variable.
    $Key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey("Environment")
    $UserPath = [string]$Key.GetValue("Path", "", [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
    $Kind = if ($Key.GetValueNames() -contains "Path") { $Key.GetValueKind("Path") } else { [Microsoft.Win32.RegistryValueKind]::ExpandString }
    # Always an array: with one entry (a fresh profile has just WindowsApps) PowerShell returns a
    # string, and + then glued the install folder onto it (`...\WindowsAppsC:\...`).
    $Entries = @($UserPath -split ";" | Where-Object { $_ })
    if (-not ($Entries -contains $InstallDir)) {
        $Key.SetValue("Path", (($Entries + $InstallDir) -join ";"), $Kind)
        # Setting and clearing a user variable through .NET broadcasts WM_SETTINGCHANGE, so new
        # terminals see the PATH just written.
        [Environment]::SetEnvironmentVariable("MIGHTLING_PATH_CHANGED", "1", "User")
        [Environment]::SetEnvironmentVariable("MIGHTLING_PATH_CHANGED", $null, "User")
        Say "Added $InstallDir to your PATH (new terminals see it)."
    }
    $Key.Close()
    if (-not (($env:Path -split ";") -contains $InstallDir)) { $env:Path = "$env:Path;$InstallDir" }
}

# -- the sandbox, the firewall and the audit: one Administrator prompt ------------------------
#
# Codex's Windows sandbox runs commands as two local accounts that only an administrator can
# create; the offline one is what takes a command's network away at /airgapped on. One elevation
# sets them up for this user, lets ling hear node announcements (mDNS, UDP 5353) on Private and
# Domain networks, and runs the egress audit once (section 7.1, 10.2, 14 of the spec). Declining
# leaves a working client whose commands run without a sandbox; ling says so at every start.
#
# The firewall rules are machine-wide, so there is one per program path, named after it:
# "Mightling-mDNS-" and the first 8 hex digits of the SHA-256 of the full path, lowercased, in
# UTF-8 (section 16.4; `ling uninstall` names and removes them the same way). An install removes
# only the rules whose program is in its own folders, so another user's install keeps its rules.

# Whether Codex's elevated sandbox is set up for a CODEX_HOME, read as Codex reads it
# (sandbox_setup_is_complete in codex-rs/windows-sandbox-rs): its setup marker and its accounts
# file, both at Codex's setup version (SETUP_VERSION in windows-sandbox-rs/src/setup.rs).
$SandboxSetupVersion = 5
function Test-SandboxSetUp([string]$CodexDir) {
    foreach ($File in @(".sandbox\setup_marker.json", ".sandbox-secrets\sandbox_users.json")) {
        try {
            $Version = (Get-Content -Raw -Path (Join-Path $CodexDir $File) -ErrorAction Stop | ConvertFrom-Json).version
        } catch {
            return $false
        }
        if ($Version -ne $SandboxSetupVersion) { return $false }
    }
    return $true
}

$Mightling = Join-Path $InstallDir "ling.exe"
if ($NoSandbox) {
    Say ""
    Say "Skipped the Windows sandbox (-NoSandbox): commands will run without one."
} elseif (-not (Test-Path (Join-Path $InstallDir "codex-windows-sandbox-setup.exe"))) {
    Say ""
    Say "Release $Tag carries no sandbox setup for ${Target}: commands will run without a sandbox."
} else {
    # Named now, as the user who ran this: with an administrator's credentials typed over the
    # shoulder of a standard user, the elevated process runs as that administrator instead, with
    # the administrator's environment and profile. So everything that is this user's is decided
    # here and handed over: the account, CODEX_HOME, and what picks the node the audit judges (the
    # remembered node in CODEX_HOME, the configuration under the home folder, and the variables).
    # The account's own name: $env:USERDOMAIN is the workgroup over OpenSSH, and the sandbox's
    # setup then fails with 1332 (no mapping between account names and security IDs).
    $User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $CodexHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE ".mightling" }
    $UserHome = if ($env:HOME) { $env:HOME } else { $env:USERPROFILE }
    New-Item -ItemType Directory -Force -Path $CodexHome | Out-Null
    # The desktop app's installer (desktop/electron/windows/installer.nsi) is per user, without
    # administrator rights, so it cannot add firewall rules: the app's two programs get theirs here
    # whenever it is present, the copy this run installed above included.
    $AppDir = ""
    try {
        $AppDir = (Get-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Mightling" -Name InstallLocation -ErrorAction Stop).InstallLocation
    } catch { }
    if (-not $AppDir) { $AppDir = Join-Path $env:LOCALAPPDATA "Programs\Mightling\App" }
    $Programs = @($Mightling)
    if (Test-Path (Join-Path $AppDir "Mightling.exe")) {
        $Programs += Join-Path $AppDir "Mightling.exe"
        $Programs += Join-Path $AppDir "resources\ling.exe"
    }
    $Step = Join-Path ([IO.Path]::GetTempPath()) ("mightling-elevated-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $Step | Out-Null
    $Script = Join-Path $Step "elevated.ps1"
    $Log = Join-Path $Step "elevated.log"
    Set-Content -Path $Script -Encoding ASCII -Value @'
param([string]$Mightling, [string]$User, [string]$CodexHome, [string]$UserHome, [string]$VllmHost, [string]$Node,
      [string]$ConfigPath, [string]$InstallDir, [string]$AppDir, [string]$Programs, [string]$Log)
$ErrorActionPreference = "Continue"
function Note([string]$Text) { Add-Content -Path $Log -Value $Text -Encoding UTF8 }
# The rule of one program (section 16.4): the first 8 hex digits of the SHA-256 of its full path,
# lowercased, in UTF-8.
function RuleName([string]$Program) {
    $Sha = [Security.Cryptography.SHA256]::Create()
    try { $Hash = $Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Program.ToLowerInvariant())) } finally { $Sha.Dispose() }
    return "Mightling-mDNS-" + (($Hash[0..3] | ForEach-Object { $_.ToString("x2") }) -join "")
}
& $Mightling sandbox setup --elevated --user $User --codex-home $CodexHome 2>&1 | ForEach-Object { Note "$_" }
Note "sandbox-setup-exit=$LASTEXITCODE"
try {
    # Only this install's rules go: those whose program is in its folders, whatever their name. That
    # also takes the fixed-name rules of 1.7.1 and earlier (Mightling-mDNS-ling, -ling-app) when
    # they point here, and leaves them, and every rule, when they point at another user's install.
    $Mine = @($InstallDir, $AppDir) | Where-Object { $_ } | ForEach-Object { $_.TrimEnd("\") + "\" }
    Get-NetFirewallRule | Where-Object { $_.Name -like "Mightling-mDNS-*" } | ForEach-Object {
        $Program = [string]($_ | Get-NetFirewallApplicationFilter).Program
        $Ours = $false
        foreach ($Dir in $Mine) { if ($Program.StartsWith($Dir, [StringComparison]::OrdinalIgnoreCase)) { $Ours = $true } }
        if ($Ours) { $_ | Remove-NetFirewallRule -ErrorAction Stop }
    }
    foreach ($Program in ($Programs -split "\|" | Where-Object { $_ })) {
        New-NetFirewallRule -Name (RuleName $Program) -DisplayName "Mightling node discovery (mDNS, $Program)" -Direction Inbound -Action Allow -Protocol UDP -LocalPort 5353 -Program $Program -Profile Private, Domain -ErrorAction Stop | Out-Null
    }
    Note "firewall=ok"
} catch {
    Note "firewall=failed: $($_.Exception.Message)"
}
# The audit as the user would run it: their CODEX_HOME (the remembered node), their home folder
# (the configuration file), and their variables, never the administrator's.
$env:CODEX_HOME = $CodexHome
if ($UserHome) { $env:HOME = $UserHome }
foreach ($Pair in @(@("DREAMFERENCE_VLLM_HOST", $VllmHost), @("MIGHTLING_NODE", $Node), @("DREAMFERENCE_CONFIG_PATH", $ConfigPath))) {
    if ($Pair[1]) { Set-Item -Path "Env:$($Pair[0])" -Value $Pair[1] } else { Remove-Item -Path "Env:$($Pair[0])" -ErrorAction SilentlyContinue }
}
& $Mightling audit egress 2>&1 | ForEach-Object { Note "$_" }
Note "audit-exit=$LASTEXITCODE"
'@
    # Each value quoted; an empty one is left out, since Windows PowerShell 5.1 can drop an empty
    # quoted argument and take the next parameter's name for its value. A trailing backslash is
    # doubled, so it does not escape the closing quote.
    $Pass = [ordered]@{
        Mightling = $Mightling; User = $User; CodexHome = $CodexHome; UserHome = $UserHome
        VllmHost = $env:DREAMFERENCE_VLLM_HOST; Node = $env:MIGHTLING_NODE; ConfigPath = $env:DREAMFERENCE_CONFIG_PATH
        InstallDir = $InstallDir; AppDir = $AppDir; Programs = ($Programs -join "|"); Log = $Log
    }
    $Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""
    foreach ($Key in $Pass.Keys) {
        $Value = [string]$Pass[$Key]
        if ($Value.EndsWith("\")) { $Value += "\" }
        if ($Value) { $Arguments += " -$Key `"$Value`"" }
    }
    $Principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $NoDesktop = $false
    Say ""
    try {
        if ($Principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            Say "Setting up the Windows sandbox and the firewall rule (already running as Administrator)..."
            Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -Wait -NoNewWindow
        } elseif (-not [Environment]::UserInteractive) {
            # Over SSH, or from a service, the session has no desktop (session 0, a window station
            # that is not visible): Windows cannot show the Administrator prompt there, and the
            # attempt fails with "requires an interactive window station", which reads as a decline.
            $NoDesktop = $true
            Say "Skipped the Administrator step: this session (SSH, or a service) has no desktop to show the prompt on."
        } else {
            Say "Windows will ask once for Administrator rights: to set up the sandbox that runs the agent's"
            Say "commands, to let ling find nodes on Private networks, and to run the egress audit."
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
    } elseif (Test-SandboxSetUp $CodexHome) {
        # A reinstall: the setup of an earlier install is still in place, and still what Codex uses.
        Say "Windows sandbox: set up for $User (by an earlier install; still in place)."
    } else {
        Say "Windows sandbox: NOT set up. Commands will run without one, and /airgapped on is refused."
        foreach ($Line in $Lines) { if ($Line -notmatch "^(firewall|audit-exit)=" -and $Line -notmatch "^sandbox-setup-exit=") { Say "  $Line" } }
        if ($NoDesktop) {
            Say "  The sandbox setup needs a desktop session. At this PC's screen, run install.ps1 again and accept"
            Say "  the Administrator prompt; or ask an administrator to run, in an Administrator terminal:"
            Say "    & `"$Mightling`" sandbox setup --elevated --user `"$User`" --codex-home `"$CodexHome`""
        }
    }
    if ($Lines -contains "firewall=ok") {
        Say "Firewall: ling may hear node announcements (UDP 5353) on Private and Domain networks."
    } elseif ($Lines.Count -gt 0) {
        Say "Firewall: the mDNS rule was not added; discovery may find nothing (ling node use <address> still works)."
    } elseif ($NoDesktop) {
        Say "Firewall: the mDNS rule needs the same Administrator step; until then ling finds nodes by asking"
        Say "  (ling node list), but does not hear their announcements."
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
            Say "  (Run it again once ling uses a node: ling audit egress, in an Administrator terminal; as another"
            Say "  administrator, set CODEX_HOME=$CodexHome in that terminal first, so it judges your node.)"
        }
    }
}

Say ""
Say "Done. Mightling $Tag is in $InstallDir."
if ($AppResult -eq "ok") {
    Say "The desktop app is installed: open Mightling from the Start menu."
} elseif ($AppResult) {
    Say "The desktop app was NOT installed ($AppResult). If Windows blocked it, Smart App Control is on."
}
Say ""
Say "PREVIEW: these Windows builds are not signed. If Windows blocks ling.exe, Smart App Control"
Say "is on; turn it off in Windows Security > App & browser control > Smart App Control settings."
Say ""
Say "Next:"
Say "  ling node list          # the Mightling nodes (DGX Spark or another GB10) on your network"
Say "  cd <your project>; ling # start working; it finds the node by itself"
Say "  ling node use <address> # if your network is marked Public and discovery finds nothing"

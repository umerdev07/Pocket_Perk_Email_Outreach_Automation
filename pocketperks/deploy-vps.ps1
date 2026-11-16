param(
    [string]$VpsHost = "188.245.213.90",
    [int]$SshPort = 38055,
    [string]$SshUser = "root"
)

$ErrorActionPreference = "Stop"

$repoRoot = (& git -C $PSScriptRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $repoRoot) {
    throw "Could not locate the PocketPerks Git repository."
}

$branch = (& git -C $repoRoot branch --show-current).Trim()
if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
    throw "Switch to the main branch before deploying. Current branch: $branch"
}

$dirty = & git -C $repoRoot status --porcelain --untracked-files=no
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify the local Git state."
}
if ($dirty) {
    throw "Commit tracked source changes on main before deploying."
}

$commit = (& git -C $repoRoot rev-parse --short=7 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $commit) {
    throw "Could not identify the main commit to deploy."
}

$zipPath = Join-Path $repoRoot "pocketperks-main-$commit.zip"
$archiveTree = "${commit}:pocketperks"
& git -C $repoRoot archive --format=zip "--output=$zipPath" $archiveTree
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the source archive."
}

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
try {
    $entries = @($archive.Entries | ForEach-Object { $_.FullName })
    foreach ($required in @("compose.yaml", "backend/Dockerfile", "frontend/Dockerfile")) {
        if ($entries -notcontains $required) {
            throw "The archive is missing required project file: $required"
        }
    }
    foreach ($secretPath in @(".env", "backend/.env", "frontend/.env.local")) {
        if ($entries -contains $secretPath) {
            throw "Refusing to deploy an archive containing a secret file: $secretPath"
        }
    }
} finally {
    $archive.Dispose()
}

$localHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$remoteZip = "/opt/pocketperks/pocketperks-main-$commit.zip"
$remoteRelease = "/opt/pocketperks/releases/$commit"
$remoteTarget = "$SshUser@${VpsHost}"

Write-Output "Uploading commit $commit to the VPS; this does not use GitHub."
& scp -P $SshPort $zipPath "${remoteTarget}:$remoteZip"
if ($LASTEXITCODE -ne 0) {
    throw "Upload failed. The running VPS deployment was not changed."
}

$remoteHashOutput = & ssh -p $SshPort $remoteTarget "sha256sum $remoteZip"
if ($LASTEXITCODE -ne 0) {
    throw "Could not verify the uploaded archive. No deployment was started."
}
$remoteHash = (($remoteHashOutput -join " ") -split "\s+")[0].ToLowerInvariant()
if ($remoteHash -ne $localHash) {
    throw "The uploaded archive hash does not match. No deployment was started."
}

$remoteCommand = "set -e; mkdir -p $remoteRelease; python3 -m zipfile -e $remoteZip $remoteRelease; cp -n /opt/pocketperks/.env $remoteRelease/.env; mkdir -p $remoteRelease/backend; cp -n /opt/pocketperks/backend/.env $remoteRelease/backend/.env; cd $remoteRelease; docker compose -p pocketperks --env-file .env up -d --build; for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do if curl -fsS http://127.0.0.1:3000/api/health; then exit 0; fi; sleep 5; done; docker compose -p pocketperks --env-file .env logs --tail=80 frontend backend; exit 1"
& ssh -p $SshPort $remoteTarget $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "VPS deployment or local health check failed. Review the Docker Compose logs on the VPS."
}

Write-Output "PocketPerks commit $commit deployed. PostgreSQL data volume and VPS environment files were preserved."

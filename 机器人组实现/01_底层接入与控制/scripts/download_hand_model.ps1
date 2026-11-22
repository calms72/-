param(
    [string]$Destination = "models/hand_landmarker.task"
)

$ErrorActionPreference = "Stop"
$modelUrl = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
$expectedSha256 = "FBC2A30080C3C557093B5DDFC334698132EB341044CCEE322CCF8BCF3607CDE1"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$destinationPath = Join-Path $repositoryRoot $Destination
$destinationDirectory = Split-Path -Parent $destinationPath
New-Item -ItemType Directory -Path $destinationDirectory -Force | Out-Null

$temporaryPath = "$destinationPath.download"
try {
    Invoke-WebRequest -Uri $modelUrl -OutFile $temporaryPath
    $actualSha256 = (Get-FileHash -Algorithm SHA256 -Path $temporaryPath).Hash
    if ($actualSha256 -ne $expectedSha256) {
        throw "Model checksum mismatch: expected $expectedSha256, received $actualSha256"
    }
    Move-Item -LiteralPath $temporaryPath -Destination $destinationPath -Force
    Write-Output "Verified model: $destinationPath"
} finally {
    if (Test-Path -LiteralPath $temporaryPath) {
        Remove-Item -LiteralPath $temporaryPath
    }
}

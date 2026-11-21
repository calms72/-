[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Source
)

$ErrorActionPreference = 'Stop'
$vendorRoot = (Resolve-Path -LiteralPath $Source).ProviderPath
if ($vendorRoot -eq $PSScriptRoot) {
    throw 'Source must point to the extracted enterprise package.'
}

$demo = '软件demo\实验前'
$projects = @(
    'AI_Assistant_Basic_Project_Detection'
    'AI_Assistant_Basic_Project_Pose'
    'AI_Assistant_Basic_Project_Hand_Landmark'
)
$folders = @('指导书', "$demo\plc\库")
foreach ($project in $projects) {
    $folders += "$demo\$project\Lib"
    $folders += "$demo\$project\STM32Cube_FW_N6"
}

# Check every source before creating any destination folders.
foreach ($relative in $folders) {
    if (-not (Test-Path -LiteralPath (Join-Path $vendorRoot $relative) -PathType Container)) {
        throw "Missing '$relative'. Select the package folder containing 软件demo and 指导书."
    }
}
foreach ($relative in $folders) {
    $from = Join-Path $vendorRoot $relative
    $to = Join-Path $PSScriptRoot $relative
    Write-Host "Restoring $relative"
    # Copy missing files only. Keep any files already present in the checkout.
    & robocopy.exe $from $to /E /XC /XN /XO /XJ /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) {
        throw "Copy failed for '$relative' (robocopy exit $LASTEXITCODE)."
    }
}
Write-Host 'Vendor folders are ready.'
exit 0

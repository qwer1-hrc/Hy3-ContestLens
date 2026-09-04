[CmdletBinding()]
param(
    [string]$EnvironmentName = "hy3-contestlens",
    [switch]$Offline,
    [string]$CloneFrom = ""
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command conda -ErrorAction SilentlyContinue)) {
    throw "Conda is not available on PATH. Install Miniconda/Anaconda and reopen PowerShell."
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$environmentFile = Join-Path $projectRoot "environment.yml"
$environmentListJson = conda env list --json
if ($LASTEXITCODE -ne 0) {
    throw "Unable to list Conda environments (exit code $LASTEXITCODE)."
}
$knownEnvironments = ($environmentListJson | ConvertFrom-Json).envs
$environmentExists = $knownEnvironments | Where-Object {
    (Split-Path -Leaf $_) -eq $EnvironmentName
}

Push-Location $projectRoot
try {
    if ($environmentExists) {
        if ($Offline -or $CloneFrom) {
            Write-Host "Refreshing project packages in existing Conda environment '$EnvironmentName' using pip cache/index..."
            conda run --name $EnvironmentName python -m pip install -e ".[dev,vision]"
        }
        else {
            Write-Host "Updating Conda environment '$EnvironmentName'..."
            conda env update --name $EnvironmentName --file $environmentFile --prune
        }
    }
    else {
        if ($CloneFrom) {
            Write-Host "Cloning Conda environment '$CloneFrom' into '$EnvironmentName' without channel access..."
            conda create --offline --yes --name $EnvironmentName --clone $CloneFrom
            if ($LASTEXITCODE -eq 0) {
                conda run --name $EnvironmentName python -m pip install -e ".[dev,vision]"
            }
        }
        elseif ($Offline) {
            Write-Host "Creating minimal Conda environment '$EnvironmentName' from the local package cache..."
            conda create --offline --yes --name $EnvironmentName python=3.12 pip setuptools wheel
            if ($LASTEXITCODE -eq 0) {
                conda run --name $EnvironmentName python -m pip install -e ".[dev,vision]"
            }
        }
        else {
            Write-Host "Creating Conda environment '$EnvironmentName'..."
            conda env create --name $EnvironmentName --file $environmentFile
        }
    }

    if ($LASTEXITCODE -ne 0) {
        throw "Conda environment installation failed (exit code $LASTEXITCODE)."
    }

    conda run --name $EnvironmentName python -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency validation failed (exit code $LASTEXITCODE)."
    }
    conda run --name $EnvironmentName python -c "import hy3_contestlens; print('Hy3-ContestLens', hy3_contestlens.__version__)"
    if ($LASTEXITCODE -ne 0) {
        throw "Project import validation failed (exit code $LASTEXITCODE)."
    }
}
finally {
    Pop-Location
}

Write-Host "Environment is ready. Activate it with: conda activate $EnvironmentName"

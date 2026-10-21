param([int[]]$PaperIds = @(1), [switch]$BundledEngine)
$ErrorActionPreference = 'Stop'
$taskFolder = if ($env:SRDP_TASK_DIR) { (Resolve-Path -LiteralPath $env:SRDP_TASK_DIR).Path } else { $PSScriptRoot }
$validationArgs = @((Join-Path $PSScriptRoot 'verify_continuation.py'), '--draft', '--require-current-source') + $PaperIds
$env:PYTHONIOENCODING = 'utf-8'
$taskPython = if ($env:FIGURE_PYTHON) { $env:FIGURE_PYTHON } else { 'python' }
& $taskPython @validationArgs
if ($LASTEXITCODE -ne 0) { throw 'Draft verification failed; no Excel files were saved.' }
$excelTask = $null
try {
    if ($BundledEngine) {
        $taskNode = if ($env:FIGURE_NODE) { $env:FIGURE_NODE } else { (Get-Command node -ErrorAction Stop).Source }
        $engineArgs = @((Join-Path $PSScriptRoot 'author.mjs'), 'engine_save') + $PaperIds
        & $taskNode @engineArgs
        if ($LASTEXITCODE -ne 0) { throw 'Bundled engine validation failed; no current workbooks were replaced.' }
    } else {
        $excelTask = New-Object -ComObject Excel.Application
        $excelTask.Visible = $false
        $excelTask.DisplayAlerts = $false
        $excelTask.AskToUpdateLinks = $false
    }
    foreach ($paperId in $PaperIds) {
        $planPath = Join-Path $taskFolder "A${paperId}_修改计划.json"
        $taskPlan = Get-Content -LiteralPath $planPath -Raw -Encoding utf8 | ConvertFrom-Json
        $currentHash = (Get-FileHash -LiteralPath $taskPlan.target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentHash -ne $taskPlan.input_sha256) { throw "Concurrent edit detected for A${paperId}" }
        $draftPath = Join-Path $taskFolder "A${paperId}_保存前.xlsx"
        $runId = [Guid]::NewGuid().ToString('N')
        $candidatePath = Join-Path $taskFolder "A${paperId}_Excel暂存_${runId}.xlsx"
        if ($BundledEngine) { $candidatePath = Join-Path $taskFolder "A${paperId}_引擎暂存.xlsx" }
        $bookTask = $null
        if (-not $BundledEngine) { try {
            $bookTask = $excelTask.Workbooks.Open($draftPath, 0, $false)
            $excelTask.CalculateFullRebuild()
            $bookTask.SaveAs($candidatePath, 51)
        } finally {
            if ($null -ne $bookTask) {
                $bookTask.Close($false)
                [System.Runtime.InteropServices.Marshal]::ReleaseComObject($bookTask) | Out-Null
            }
        } }
        $candidateArgs = @((Join-Path $PSScriptRoot 'verify_continuation.py'), '--candidate', $candidatePath, $paperId)
        & $taskPython @candidateArgs
        if ($LASTEXITCODE -ne 0) { throw "Candidate verification failed for A${paperId}; the current workbook was not replaced." }
        $backupFolder = [System.IO.Path]::GetDirectoryName($taskPlan.backup)
        if (-not (Test-Path -LiteralPath $backupFolder -PathType Container)) { throw "Backup directory is missing for A${paperId}" }
        $replacementBackup = Join-Path $backupFolder "A${paperId}_替换前_${runId}.xlsx"
        $currentHash = (Get-FileHash -LiteralPath $taskPlan.target -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($currentHash -ne $taskPlan.input_sha256) { throw "Concurrent edit detected before committing A${paperId}; the current workbook was not replaced." }
        [System.IO.File]::Replace($candidatePath, $taskPlan.target, $replacementBackup)
        Write-Output "A${paperId}: verified engine result committed; previous file retained at $replacementBackup"
    }
} finally {
    if ($null -ne $excelTask) {
        $excelTask.Quit()
        [System.Runtime.InteropServices.Marshal]::ReleaseComObject($excelTask) | Out-Null
    }
}
$validationArgs = @((Join-Path $PSScriptRoot 'verify_continuation.py')) + $PaperIds
& $taskPython @validationArgs
if ($LASTEXITCODE -ne 0) { throw 'Saved-file verification failed; stop before reporting completion.' }

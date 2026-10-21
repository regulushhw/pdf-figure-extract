param([Parameter(Mandatory=$true)][string]$Request,
      [Parameter(Mandatory=$true)][string]$Output)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Globalization.Language,Windows.Foundation,ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
function Await-Result($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $task.GetAwaiter().GetResult()
}
$requestData = Get-Content -LiteralPath $Request -Raw -Encoding UTF8 | ConvertFrom-Json
$language = New-Object Windows.Globalization.Language($requestData.language)
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
if ($null -eq $engine) { throw "OCR language unavailable: $($requestData.language)" }
$outputs = @()
foreach ($item in $requestData.images) {
    $stream = [System.IO.File]::OpenRead($item.image)
    $random = $null
    $bitmap = $null
    try {
        $random = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)
        $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($random)) ([Windows.Graphics.Imaging.BitmapDecoder])
        if ($decoder.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or
            $decoder.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension) { throw 'OCR image exceeds Windows engine limit' }
        $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        $lines = @()
        foreach ($line in $result.Lines) {
            $words = @()
            foreach ($word in $line.Words) {
                $r = $word.BoundingRect
                $words += @{text=$word.Text; bbox=@([double]$r.X,[double]$r.Y,
                    [double]($r.X+$r.Width),[double]($r.Y+$r.Height)); confidence=$null}
            }
            $lines += @{text=$line.Text; words=$words}
        }
        $outputs += @{image=$item.image; rotation=$item.rotation; text_angle=$result.TextAngle; lines=$lines}
    } finally {
        if ($null -ne $bitmap) {$bitmap.Dispose()}
        if ($null -ne $random) {$random.Dispose()}
        $stream.Dispose()
    }
}
@{engine='windows';language=$requestData.language;results=$outputs} |
    ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Output -Encoding UTF8

param([Parameter(Mandatory=$true)][string]$InputPath,
      [Parameter(Mandatory=$true)][string]$OutputPath,
      [string]$CrispPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime]
$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await($Operation, $ResultType) {
    $pending = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    $pending.Wait()
    return $pending.Result
}
$stream = $null
$bitmap = $null
try {
    # Chinese recognizers also recognize Latin text; prefer one when available.
    $languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
    $language = $languages | Where-Object { $_.LanguageTag -like 'zh-*' } | Select-Object -First 1
    if (-not $language) { $language = $languages | Select-Object -First 1 }
    if (-not $language) { throw 'OCR_LANGUAGE_MISSING' }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if (-not $engine) { throw 'OCR_LANGUAGE_MISSING' }
    $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($InputPath)) ([Windows.Storage.StorageFile])
    $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    # Bound decoding before allocating a software bitmap.
    $limit = [Windows.Media.Ocr.OcrEngine]::MaxImageDimension
    if ($decoder.PixelWidth -gt $limit -or $decoder.PixelHeight -gt $limit) { throw "OCR_IMAGE_TOO_LARGE:$limit" }
    if ($decoder.FrameCount -ne 1) { throw 'OCR_MULTIPLE_FRAMES' }
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
        [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
        [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    # The English recognizer preserves Latin punctuation and glyphs more accurately.
    # Route predominantly Latin pages after an initial script detection pass.
    $han = [regex]::Matches(($result.Text -replace '\u4e00',''), '[\u3400-\u9fff]').Count
    $latin = [regex]::Matches($result.Text, '[A-Za-z]').Count
    $english = $languages | Where-Object { $_.LanguageTag -like 'en-*' } | Select-Object -First 1
    if ($english -and $language.LanguageTag -like 'zh-*' -and $han -eq 0 -and $latin -gt 5) {
        $language = $english
        $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    }
    if ($language.LanguageTag -like 'zh-*' -and $CrispPath) {
        $bitmap.Dispose()
        $bitmap = $null
        $stream.Dispose()
        $stream = $null
        $file = Await ([Windows.Storage.StorageFile]::GetFileFromPathAsync($CrispPath)) ([Windows.Storage.StorageFile])
        $stream = Await ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
        $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Await ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    }
    $lines = @($result.Lines | ForEach-Object {
        @{text=$_.Text; words=@($_.Words | ForEach-Object {
            @{text=$_.Text; x=$_.BoundingRect.X; y=$_.BoundingRect.Y;
              width=$_.BoundingRect.Width; height=$_.BoundingRect.Height}
        })}
    })
    $data = @{language=$language.LanguageTag; lines=$lines; angle=$result.TextAngle;
              chinese_available=[bool]($languages | Where-Object { $_.LanguageTag -like 'zh-*' })}
    [IO.File]::WriteAllText($OutputPath, ($data | ConvertTo-Json -Depth 8 -Compress), [Text.UTF8Encoding]::new($false))
} catch {
    [Console]::Error.WriteLine($_.Exception.ToString())
    exit 1
} finally {
    if ($bitmap) { $bitmap.Dispose() }
    if ($stream) { $stream.Dispose() }
}

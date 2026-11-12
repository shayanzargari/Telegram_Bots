param([Parameter(Mandatory=$true)][string]$RequestPath,
      [Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$speechType=[Windows.Media.SpeechSynthesis.SpeechSynthesizer,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$streamType=[Windows.Media.SpeechSynthesis.SpeechSynthesisStream,Windows.Media.SpeechSynthesis,ContentType=WindowsRuntime]
$request=Get-Content -LiteralPath $RequestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$voice=$speechType::AllVoices | Where-Object {$_.Language -eq 'fr-FR' -and $_.DisplayName -eq 'Microsoft Julie'} | Select-Object -First 1
if(-not $voice){$voice=$speechType::AllVoices | Where-Object {$_.Language -eq 'fr-FR'} | Select-Object -First 1}
if(-not $voice){throw 'No French (France) voice is installed.'}
$synth=New-Object Windows.Media.SpeechSynthesis.SpeechSynthesizer
$synth.Voice=$voice
$asTask=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
$operation=$synth.SynthesizeTextToStreamAsync([string]$request.text)
$task=$asTask.MakeGenericMethod($streamType).Invoke($null,@($operation))
$task.Wait()
$speechStream=$task.Result
$inputStream=[System.IO.WindowsRuntimeStreamExtensions]::AsStreamForRead($speechStream)
$outputStream=[System.IO.File]::Open($OutputPath,[System.IO.FileMode]::CreateNew,[System.IO.FileAccess]::Write)
try{$inputStream.CopyTo($outputStream)}finally{$outputStream.Dispose();$inputStream.Dispose();$speechStream.Dispose();$synth.Dispose()}
@{voice=$voice.DisplayName;language=$voice.Language} | ConvertTo-Json -Compress

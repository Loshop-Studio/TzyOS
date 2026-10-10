param(
    [Parameter(Mandatory = $true)][string]$Source,
    [Parameter(Mandatory = $true)][string]$Destination
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
$best = @{}

Get-ChildItem -LiteralPath $Source -Filter "*.tzyp" -File | ForEach-Object {
    $archive = $null
    try {
        $archive = [IO.Compression.ZipFile]::OpenRead($_.FullName)
        $entry = $archive.GetEntry("app.json")
        if (-not $entry) { return }
        $reader = New-Object IO.StreamReader($entry.Open())
        try { $meta = $reader.ReadToEnd() | ConvertFrom-Json } finally { $reader.Dispose() }
        $id = [string]$meta.id
        if (-not $id) { return }
        try { $version = [version][string]$meta.version } catch { $version = [version]"0.0.0.0" }
        if (-not $best.ContainsKey($id) -or $version -gt $best[$id].Version) {
            $best[$id] = @{ File = $_; Version = $version }
        }
    } catch {
        Write-Warning ("skip invalid package {0}: {1}" -f $_.Name, $_.Exception.Message)
    } finally {
        if ($archive) { $archive.Dispose() }
    }
}

foreach ($item in $best.Values) {
    Copy-Item -LiteralPath $item.File.FullName -Destination (Join-Path $Destination $item.File.Name) -Force
}
Write-Host ("[*] selected packages: {0}" -f $best.Count)

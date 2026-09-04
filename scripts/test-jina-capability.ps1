<#
.SYNOPSIS
Run isolated live checks for the Smart Search Jina reader.
.DESCRIPTION
This script uses a temporary SMART_SEARCH_CONFIG_DIR and clears other reader
provider env vars for the current process while it runs, so `smart-search read`
uses Jina instead of being satisfied by another configured reader first.

It never prints the Jina key. Set JINA_API_KEY in the environment or pass
`-JinaApiKey` explicitly. Use `smart-search setup` for discovery provider keys.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\test-jina-capability.ps1

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\test-jina-capability.ps1 -Profile full -Modes default,readerlm-v2

.EXAMPLE
powershell -ExecutionPolicy Bypass -File .\scripts\test-jina-capability.ps1 -Urls "https://example.com","https://www.iana.org/help/example-domains" -Modes default
#>

[CmdletBinding()]
param(
    [string[]]$Urls,

    [ValidateSet("quick", "full")]
    [string]$Profile = "quick",

    [ValidateSet("default", "readerlm-v2")]
    [string[]]$Modes = @("default", "readerlm-v2"),

    [string]$JinaApiKey = $env:JINA_API_KEY,

    [string]$JinaReaderApiUrl = "https://r.jina.ai",

    [int]$TimeoutSeconds = 60,

    [string]$EvidenceDir = (Join-Path $env:TEMP ("smart-search-jina-evidence-" + (Get-Date -Format "yyyyMMdd-HHmmss"))),

    [switch]$KeepOtherReaders
)

$ErrorActionPreference = "Stop"

function Get-SafeSlug {
    param([Parameter(Mandatory = $true)][string]$Text)
    $slug = $Text -replace '^https?://', ''
    $slug = $slug -replace '[^A-Za-z0-9._-]+', '-'
    $slug = $slug.Trim('-')
    if ($slug.Length -gt 70) {
        $slug = $slug.Substring(0, 70)
    }
    if (-not $slug) {
        return "url"
    }
    return $slug
}

function ConvertFrom-SmartSearchJson {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [int]$ExitCode
    )

    $trimmed = $Text.Trim()
    $start = $trimmed.IndexOf("{")
    $end = $trimmed.LastIndexOf("}")
    if ($start -lt 0 -or $end -lt $start) {
        return [pscustomobject]@{
            ok = $false
            error_type = "parse_error"
            error = "smart-search did not return a JSON object"
            exit_code = $ExitCode
            raw = $trimmed
        }
    }

    $jsonText = $trimmed.Substring($start, $end - $start + 1)
    try {
        return $jsonText | ConvertFrom-Json
    }
    catch {
        return [pscustomobject]@{
            ok = $false
            error_type = "parse_error"
            error = $_.Exception.Message
            exit_code = $ExitCode
            raw = $trimmed
        }
    }
}

function Invoke-SmartSearchJson {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)

    $output = & smart-search @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($output | Out-String).Trim()
    $data = ConvertFrom-SmartSearchJson -Text $text -ExitCode $exitCode
    if ($null -eq $data.exit_code) {
        $data | Add-Member -NotePropertyName exit_code -NotePropertyValue $exitCode -Force
    }
    return $data
}

function Save-JsonEvidence {
    param(
        [Parameter(Mandatory = $true)]$Data,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $Data | ConvertTo-Json -Depth 80 | Set-Content -LiteralPath $Path -Encoding utf8
}

if (-not $Urls -or $Urls.Count -eq 0) {
    $Urls = @(
        "https://example.com",
        "https://www.iana.org/help/example-domains"
    )

    if ($Profile -eq "full") {
        $Urls += @(
            "https://www.rfc-editor.org/rfc/rfc2606.txt",
            "https://arxiv.org/pdf/1706.03762"
        )
    }
}

if (-not $JinaApiKey) {
    throw "JINA_API_KEY was not found. Set the environment variable or pass -JinaApiKey."
}

New-Item -ItemType Directory -Path $EvidenceDir -Force | Out-Null
$TempConfigDir = Join-Path $env:TEMP ("smart-search-jina-config-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $TempConfigDir -Force | Out-Null

$envNamesToSave = @(
    "SMART_SEARCH_CONFIG_DIR",
    "JINA_API_KEY",
    "JINA_READER_API_URL",
    "JINA_RESPOND_WITH",
    "JINA_TIMEOUT_SECONDS"
)
if (-not $KeepOtherReaders) {
    $envNamesToSave += @(
        "TAVILY_API_KEY",
        "FIRECRAWL_API_KEY",
        "ZHIPU_MCP_API_KEY"
    )
}

$savedEnv = @{}
foreach ($name in $envNamesToSave) {
    $savedEnv[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}

try {
    [Environment]::SetEnvironmentVariable("SMART_SEARCH_CONFIG_DIR", $TempConfigDir, "Process")
    [Environment]::SetEnvironmentVariable("JINA_API_KEY", $JinaApiKey, "Process")
    [Environment]::SetEnvironmentVariable("JINA_READER_API_URL", $JinaReaderApiUrl, "Process")
    [Environment]::SetEnvironmentVariable("JINA_TIMEOUT_SECONDS", [string]$TimeoutSeconds, "Process")

    if (-not $KeepOtherReaders) {
        foreach ($name in @("TAVILY_API_KEY", "FIRECRAWL_API_KEY", "ZHIPU_MCP_API_KEY")) {
            [Environment]::SetEnvironmentVariable($name, $null, "Process")
        }
    }

    Write-Host "Smart Search Jina reader test"
    Write-Host "command     : smart-search read URL --format json"
    Write-Host "reader api  : $JinaReaderApiUrl"
    Write-Host "timeout     : $TimeoutSeconds seconds"
    Write-Host "temp config : $TempConfigDir"
    Write-Host "evidence dir: $EvidenceDir"
    Write-Host "modes       : $($Modes -join ', ')"
    Write-Host ""

    [Environment]::SetEnvironmentVariable("JINA_RESPOND_WITH", $null, "Process")

    $summaries = New-Object System.Collections.Generic.List[object]
    $caseIndex = 0
    foreach ($mode in $Modes) {
        if ($mode -eq "readerlm-v2") {
            [Environment]::SetEnvironmentVariable("JINA_RESPOND_WITH", "readerlm-v2", "Process")
        }
        else {
            [Environment]::SetEnvironmentVariable("JINA_RESPOND_WITH", $null, "Process")
        }

        foreach ($url in $Urls) {
            $caseIndex += 1
            $slug = Get-SafeSlug -Text $url
            $jsonPath = Join-Path $EvidenceDir ("{0:D2}-{1}-{2}.json" -f $caseIndex, $mode, $slug)
            $contentPath = Join-Path $EvidenceDir ("{0:D2}-{1}-{2}.md" -f $caseIndex, $mode, $slug)

            Write-Host ("Running [{0}] mode={1} url={2}" -f $caseIndex, $mode, $url)
            $result = Invoke-SmartSearchJson -Arguments @("read", $url, "--format", "json")
            Save-JsonEvidence -Data $result -Path $jsonPath
            $content = ""
            if ($result.data -and $result.data.evidence) {
                $content = $result.data.evidence.content
            }
            if ($content) {
                $content | Set-Content -LiteralPath $contentPath -Encoding utf8
            }

            $attempts = ""
            if ($result.attempts) {
                $attempts = (($result.attempts | ForEach-Object {
                    "{0}:{1}:{2}" -f $_.provider, $_.status, $_.error_type
                }) -join ",")
            }
            $providers = ""
            if ($result.attempts) {
                $providers = (($result.attempts | ForEach-Object { $_.provider } | Select-Object -Unique) -join ",")
            }

            $preview = $content
            if ($preview.Length -gt 260) {
                $preview = $preview.Substring(0, 260)
            }
            $preview = ($preview -replace "\s+", " ").Trim()

            $summaries.Add([pscustomobject]@{
                case = $caseIndex
                mode = $mode
                ok = ($result.status -in @("complete", "degraded"))
                provider = $providers
                degraded = ($result.status -eq "degraded")
                content_len = $content.Length
                attempts = $attempts
                json = $jsonPath
                content = $(if ($content) { $contentPath } else { "" })
                preview = $preview
            })
        }
    }

    Write-Host ""
    Write-Host "Read summary"
    $summaries | Format-Table case, mode, ok, provider, degraded, content_len, attempts -AutoSize

    Write-Host ""
    Write-Host "Content preview"
    foreach ($item in $summaries) {
        Write-Host ("[{0}] {1} {2}" -f $item.case, $item.mode, $item.preview)
        Write-Host ""
    }

    $summaryPath = Join-Path $EvidenceDir "summary.json"
    Save-JsonEvidence -Data $summaries -Path $summaryPath
    Write-Host "Saved summary : $summaryPath"
    Write-Host "Saved details : $EvidenceDir"
    Write-Host ""
    Write-Host "Expected: attempts should show provider 'jina' with status=complete. If another reader appears, rerun without -KeepOtherReaders."
}
finally {
    foreach ($name in $savedEnv.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnv[$name], "Process")
    }
}

# Script to create GitHub repository using GitHub API
# Requires: GitHub Personal Access Token with 'repo' scope

param(
    [Parameter(Mandatory=$true)]
    [string]$Token,
    
    [Parameter(Mandatory=$false)]
    [string]$OrgName = "Shunyalabsai",
    
    [Parameter(Mandatory=$false)]
    [string]$RepoName = "shunyalabs-python-sdk",
    
    [Parameter(Mandatory=$false)]
    [string]$Description = "Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs",
    
    [Parameter(Mandatory=$false)]
    [switch]$Private = $false
)

$headers = @{
    "Authorization" = "token $Token"
    "Accept" = "application/vnd.github.v3+json"
    "User-Agent" = "Shunyalabs-SDK-Setup"
}

$body = @{
    name = $RepoName
    description = $Description
    private = $Private.IsPresent
    auto_init = $false
    gitignore_template = $null
    license_template = $null
    allow_squash_merge = $true
    allow_merge_commit = $true
    allow_rebase_merge = $true
} | ConvertTo-Json

Write-Host "Creating repository: $OrgName/$RepoName" -ForegroundColor Yellow

try {
    $response = Invoke-RestMethod -Uri "https://api.github.com/orgs/$OrgName/repos" -Method Post -Headers $headers -Body $body -ContentType "application/json"
    
    Write-Host "✓ Repository created successfully!" -ForegroundColor Green
    Write-Host "  URL: $($response.html_url)" -ForegroundColor Cyan
    Write-Host "  Clone URL: $($response.clone_url)" -ForegroundColor Cyan
    
    return $response
} catch {
    Write-Host "✗ Error creating repository:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    
    if ($_.Exception.Response) {
        $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
        $responseBody = $reader.ReadToEnd()
        Write-Host "Response: $responseBody" -ForegroundColor Red
    }
    
    throw
}


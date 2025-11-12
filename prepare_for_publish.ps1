# PowerShell script to prepare repository for publishing to Shunyalabsai
# Run this script: .\prepare_for_publish.ps1

Write-Host "=== Preparing Shunyalabs Python SDK for Publishing ===" -ForegroundColor Green
Write-Host ""

# Step 1: Check current status
Write-Host "Step 1: Checking git status..." -ForegroundColor Yellow
git status --short
Write-Host ""

# Step 2: Stage all changes
Write-Host "Step 2: Staging all changes..." -ForegroundColor Yellow
git add .
Write-Host "✓ All files staged" -ForegroundColor Green
Write-Host ""

# Step 3: Show what will be committed
Write-Host "Step 3: Files to be committed:" -ForegroundColor Yellow
git status --short
Write-Host ""

# Step 4: Check current remote
Write-Host "Step 4: Current remote configuration:" -ForegroundColor Yellow
git remote -v
Write-Host ""

# Step 5: Prompt for remote update
Write-Host "Step 5: Update remote to Shunyalabsai?" -ForegroundColor Yellow
$updateRemote = Read-Host "Update remote to https://github.com/Shunyalabsai/shunyalabs-python-sdk.git? (y/n)"

if ($updateRemote -eq "y" -or $updateRemote -eq "Y") {
    Write-Host "Removing old remote..." -ForegroundColor Yellow
    git remote remove origin
    Write-Host "Adding new remote..." -ForegroundColor Yellow
    git remote add origin https://github.com/Shunyalabsai/shunyalabs-python-sdk.git
    Write-Host "✓ Remote updated" -ForegroundColor Green
    Write-Host ""
    Write-Host "New remote configuration:" -ForegroundColor Yellow
    git remote -v
    Write-Host ""
}

# Step 6: Create commit message
Write-Host "Step 6: Ready to commit" -ForegroundColor Yellow
Write-Host ""
Write-Host "Suggested commit message:" -ForegroundColor Cyan
Write-Host "feat: Whitelabel SDK with Shunyalabs branding and add API Gateway protocol support" -ForegroundColor White
Write-Host ""
Write-Host "Next steps:" -ForegroundColor Green
Write-Host "1. Create repository on GitHub: https://github.com/organizations/Shunyalabsai/repositories/new" -ForegroundColor White
Write-Host "   - Name: shunyalabs-python-sdk" -ForegroundColor White
Write-Host "   - DO NOT initialize with README, .gitignore, or license" -ForegroundColor White
Write-Host ""
Write-Host "2. Commit your changes:" -ForegroundColor White
Write-Host "   git commit -m 'feat: Whitelabel SDK with Shunyalabs branding and add API Gateway protocol support'" -ForegroundColor Cyan
Write-Host ""
Write-Host "3. Push to GitHub:" -ForegroundColor White
Write-Host "   git push -u origin main" -ForegroundColor Cyan
Write-Host ""


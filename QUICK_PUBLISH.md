# Quick Publish Guide

## Current Situation
- ✅ Repository is initialized
- ✅ You're on `main` branch
- ⚠️ Remote points to old repository: `speechmatics/speechmatics-python-sdk`

## Quick Steps to Publish

### 1. Stage All Changes
```powershell
git add .
```

### 2. Commit Changes
```powershell
git commit -m "feat: Whitelabel SDK with Shunyalabs branding and add API Gateway protocol support

- Renamed all speechmatics references to shunyalabs
- Updated package names (shunyalabs-rt, shunyalabs-batch, etc.)
- Added API Gateway protocol support for real-time transcription
- Updated all documentation and examples
- Added CI/CD workflow and contributing guidelines"
```

### 3. Update Remote to Shunyalabsai
```powershell
# Remove old remote
git remote remove origin

# Add new remote
git remote add origin https://github.com/Shunyalabsai/shunyalabs-python-sdk.git

# Verify
git remote -v
```

### 4. Create Repository on GitHub

**Option A: Web Interface**
1. Go to: https://github.com/organizations/Shunyalabsai/repositories/new
2. Repository name: `shunyalabs-python-sdk`
3. Description: `Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs`
4. Visibility: Public or Private
5. **DO NOT** check "Add a README file", "Add .gitignore", or "Choose a license"
6. Click "Create repository"

**Option B: GitHub CLI** (if installed)
```powershell
gh repo create Shunyalabsai/shunyalabs-python-sdk --public --description "Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs" --source=. --remote=origin --push
```

### 5. Push to GitHub
```powershell
git push -u origin main
```

### 6. Verify
Visit: https://github.com/Shunyalabsai/shunyalabs-python-sdk

## Or Use the PowerShell Script

Run the provided script:
```powershell
.\prepare_for_publish.ps1
```

This will:
- Stage all changes
- Show you what will be committed
- Help you update the remote
- Provide next steps

## After Publishing

1. **Add Repository Topics** (Settings → Topics):
   - `python`, `sdk`, `speech-recognition`, `asr`, `real-time`, `transcription`, `shunyalabs`, `api-gateway`, `websocket`

2. **Enable GitHub Actions** (Settings → Actions → General):
   - Allow all actions and reusable workflows

3. **Create First Release** (optional):
   ```powershell
   git tag -a v0.1.0 -m "Initial release: Shunyalabs Python SDK"
   git push origin v0.1.0
   ```
   Then create a release on GitHub with release notes.


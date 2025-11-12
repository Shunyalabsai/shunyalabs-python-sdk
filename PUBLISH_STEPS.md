# Quick Publishing Steps

## Current Status
Your repository is already initialized with git. Follow these steps to publish to GitHub:

## Step 1: Stage All Changes

```bash
# Add all new and modified files
git add .

# Verify what will be committed
git status
```

## Step 2: Commit Changes

```bash
git commit -m "feat: Whitelabel SDK with Shunyalabs branding and add API Gateway protocol support

- Renamed all speechmatics references to shunyalabs
- Updated package names (shunyalabs-rt, shunyalabs-batch, etc.)
- Added API Gateway protocol support for real-time transcription
- Updated all documentation and examples
- Added CI/CD workflow
- Added contributing guidelines"
```

## Step 3: Update Remote (if needed)

If the remote doesn't point to Shunyalabsai account:

```bash
# Remove existing remote
git remote remove origin

# Add new remote for Shunyalabsai
git remote add origin https://github.com/Shunyalabsai/shunyalabs-python-sdk.git

# Or using SSH:
# git remote add origin git@github.com:Shunyalabsai/shunyalabs-python-sdk.git
```

## Step 4: Create Repository on GitHub

1. Go to: https://github.com/organizations/Shunyalabsai/repositories/new
   - Or: https://github.com/new (if logged in as the organization)
2. Repository name: `shunyalabs-python-sdk`
3. Description: `Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs with API Gateway protocol support`
4. Visibility: Choose Public or Private
5. **DO NOT** initialize with README, .gitignore, or license
6. Click "Create repository"

## Step 5: Push to GitHub

```bash
# Push to main branch
git push -u origin main

# If main branch doesn't exist yet, create it:
git branch -M main
git push -u origin main
```

## Step 6: Verify

Visit: https://github.com/Shunyalabsai/shunyalabs-python-sdk

## Optional: Create First Release

```bash
# Create a tag
git tag -a v0.1.0 -m "Initial release: Shunyalabs Python SDK"
git push origin v0.1.0
```

Then on GitHub:
1. Go to Releases → Draft a new release
2. Choose tag `v0.1.0`
3. Title: `v0.1.0 - Initial Release`
4. Description:
   ```
   ## Initial Release
   
   - Shunyalabs-branded Python SDK
   - Support for Real-Time, Batch, Flow, and TTS APIs
   - API Gateway protocol support for real-time transcription
   - Comprehensive documentation and examples
   ```
5. Publish release

## Repository Topics (Add on GitHub)

After publishing, add these topics in repository settings:
- `python`
- `sdk`
- `speech-recognition`
- `asr`
- `real-time`
- `transcription`
- `shunyalabs`
- `api-gateway`
- `websocket`


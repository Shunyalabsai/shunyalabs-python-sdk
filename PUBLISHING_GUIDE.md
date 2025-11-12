# Publishing Guide for Shunyalabs Python SDK

This guide will help you publish the `shunyalabs-python-sdk` repository to GitHub under the `Shunyalabsai` account.

## Prerequisites

1. GitHub account: `Shunyalabsai` (or ensure you have access)
2. Git installed on your machine
3. GitHub CLI (optional, but helpful) or access to create repositories via web

## Step 1: Initialize Git Repository (if not already done)

```bash
# Check if git is already initialized
git status

# If not initialized, run:
git init
```

## Step 2: Add All Files

```bash
# Add all files to staging
git add .

# Check what will be committed
git status
```

## Step 3: Create Initial Commit

```bash
git commit -m "Initial commit: Shunyalabs Python SDK with API Gateway support"
```

## Step 4: Create Repository on GitHub

### Option A: Using GitHub Web Interface

1. Go to https://github.com/organizations/Shunyalabsai/repositories/new
   - Or go to https://github.com/new if you're logged in as the organization
2. Repository name: `shunyalabs-python-sdk`
3. Description: "Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs"
4. Visibility: Choose Public or Private
5. **DO NOT** initialize with README, .gitignore, or license (we already have these)
6. Click "Create repository"

### Option B: Using GitHub CLI

```bash
# Install GitHub CLI if not installed: https://cli.github.com/
gh repo create Shunyalabsai/shunyalabs-python-sdk \
  --public \
  --description "Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs" \
  --source=. \
  --remote=origin \
  --push
```

## Step 5: Add Remote and Push

```bash
# Add the remote repository
git remote add origin https://github.com/Shunyalabsai/shunyalabs-python-sdk.git

# Or if using SSH:
# git remote add origin git@github.com:Shunyalabsai/shunyalabs-python-sdk.git

# Verify remote
git remote -v

# Push to GitHub
git branch -M main  # Rename branch to main if needed
git push -u origin main
```

## Step 6: Set Up Repository Settings

After pushing, go to your repository settings on GitHub:

1. **Settings → General**
   - Add topics: `python`, `sdk`, `speech-recognition`, `asr`, `real-time`, `transcription`, `shunyalabs`
   - Add description if not already set
   - Enable Issues and Discussions if desired

2. **Settings → Branches**
   - Set default branch to `main`
   - Add branch protection rules if needed

3. **Settings → Actions → General**
   - Enable GitHub Actions if you want CI/CD

## Step 7: Create Release Tags (Optional)

For versioned releases:

```bash
# Create and push a tag
git tag -a v0.1.0 -m "Initial release: Shunyalabs Python SDK"
git push origin v0.1.0
```

Then create a release on GitHub:
1. Go to Releases → Draft a new release
2. Choose the tag
3. Add release notes
4. Publish release

## Step 8: Verify Repository

Visit your repository:
```
https://github.com/Shunyalabsai/shunyalabs-python-sdk
```

Verify:
- ✅ README.md displays correctly
- ✅ All files are present
- ✅ LICENSE is visible
- ✅ .gitignore is working
- ✅ Examples are included

## Next Steps

1. **Add GitHub Actions** (already created in `.github/workflows/ci.yml`)
   - The CI workflow will run on push/PR
   - Make sure Actions are enabled in repository settings

2. **Add Repository Badges** (optional)
   - Add badges to README.md for build status, license, etc.

3. **Create Documentation** (optional)
   - Consider adding more detailed documentation
   - Add API reference documentation

4. **Publish to PyPI** (when ready)
   - Each package can be published separately
   - See individual `pyproject.toml` files for package configuration

## Troubleshooting

### If you get "remote origin already exists"
```bash
git remote remove origin
git remote add origin https://github.com/Shunyalabsai/shunyalabs-python-sdk.git
```

### If you need to force push (use with caution)
```bash
git push -u origin main --force
```

### If you want to check what will be pushed
```bash
git log --oneline
git diff origin/main  # Compare with remote
```

## Repository Structure Summary

Your repository will contain:
- ✅ SDK packages (rt, batch, flow, tts)
- ✅ Examples
- ✅ Tests
- ✅ Documentation (README, guides)
- ✅ CI/CD configuration
- ✅ License (MIT)
- ✅ Contributing guidelines

## Quick Commands Reference

```bash
# Check status
git status

# Add files
git add .

# Commit
git commit -m "Your commit message"

# Push
git push origin main

# Create tag
git tag -a v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0

# View remotes
git remote -v

# View commit history
git log --oneline
```


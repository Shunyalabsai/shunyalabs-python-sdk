# Create Repository on GitHub - Quick Steps

## Step 1: Create Repository

1. **Open this URL in your browser:**
   ```
   https://github.com/organizations/Shunyalabsai/repositories/new
   ```
   
   Or if you're logged in as the organization:
   ```
   https://github.com/new
   ```
   (Make sure to select "Shunyalabsai" as the owner)

2. **Fill in the form:**
   - **Repository name:** `shunyalabs-python-sdk`
   - **Description:** `Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs`
   - **Visibility:** Choose Public or Private
   - **⚠️ IMPORTANT:** Do NOT check any of these:
     - ❌ Add a README file
     - ❌ Add .gitignore
     - ❌ Choose a license
   - Click **"Create repository"**

## Step 2: After Creating Repository

Once the repository is created, come back here and run:

```powershell
git push -u origin main
```

## Alternative: Create via GitHub API (if you have a token)

If you have a GitHub personal access token, you can create it via API:

```powershell
$token = "your-github-token"
$headers = @{
    "Authorization" = "token $token"
    "Accept" = "application/vnd.github.v3+json"
}
$body = @{
    name = "shunyalabs-python-sdk"
    description = "Python SDK for Shunyalabs Real-Time, Batch, Flow, and TTS APIs"
    private = $false
} | ConvertTo-Json

Invoke-RestMethod -Uri "https://api.github.com/orgs/Shunyalabsai/repos" -Method Post -Headers $headers -Body $body
```


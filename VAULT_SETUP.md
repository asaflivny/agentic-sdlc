# Local Key Vault Setup (direnv + pass)

Your secrets are now managed through a local encrypted password vault using `pass` and `direnv`.

## Current Status

✅ **Installed:**
- `pass` — Unix password manager (stores secrets in `~/.password-store/`)
- `direnv` — Environment loader (loads `.envrc` when you cd into the project)
- GPG key — Generated for encryption at rest

✅ **Configured:**
- `.envrc` — Loads secrets from pass into environment
- `.zshrc` — direnv hook added (loads on shell start)
- `WEBHOOK_SECRET` — Generated and stored in vault

## How It Works

1. **Secrets are stored encrypted** in `~/.password-store/asdlc/`
2. **On `cd` into this project**, direnv automatically:
   - Reads `.envrc`
   - Fetches secrets from pass
   - Sets environment variables
3. **On `cd` out**, environment variables are unloaded

## Adding More Secrets

```bash
# Store a new secret
pass generate asdlc/api_key 32        # generates random password
# or
pass insert asdlc/github_token        # prompts you to paste a value

# Retrieve a secret (for manual use)
pass show asdlc/webhook_secret

# Edit a secret
pass edit asdlc/webhook_secret

# View what secrets you have
pass ls asdlc/
```

Then uncomment the corresponding lines in `.envrc`:

```bash
# In .envrc
export API_KEY=$(pass show asdlc/api_key 2>/dev/null || echo "")
export GITHUB_TOKEN=$(pass show asdlc/github_token 2>/dev/null || echo "")
```

Reload with: `direnv reload` or just `cd .` to re-enter the directory.

## Manage Your Vault

```bash
# List all secrets
pass ls

# Search for a specific secret
pass ls asdlc/

# Change the password of a secret
pass edit asdlc/webhook_secret

# Delete a secret
pass rm asdlc/webhook_secret

# Backup your vault (encrypted GPG files)
# ~/.password-store is just a git repo of encrypted GPG files
# Safe to copy to another machine if you have the same GPG key
```

## Current Secrets

| Secret | Location | Status |
|--------|----------|--------|
| `WEBHOOK_SECRET` | `asdlc/webhook_secret` | ✅ Stored & loaded |
| `API_KEY` | `asdlc/api_key` | ⏳ Optional (commented out in .envrc) |
| `GITHUB_TOKEN` | `asdlc/github_token` | ⏳ Optional (commented out in .envrc) |
| `JENKINS_API_TOKEN` | `asdlc/jenkins_token` | ⏳ Optional (commented out in .envrc) |

## Files Changed

- **`.envrc`** — Loads secrets from pass (gitignored)
- **`.env.example`** — Template for non-secret config (committed)
- **`.gitignore`** — Updated to ignore `.envrc`
- **`~/.zshrc`** — Added direnv hook

## Troubleshooting

### "Error: pass command not found"
```bash
# Verify installation
which pass direnv

# If missing:
brew install pass direnv gnupg
```

### "Cannot find password store"
```bash
# Pass stores secrets in ~/.password-store/
# If you reinstalled, reinitialize:
pass init <your-gpg-key-id>

# Find your GPG key:
gpg --list-secret-keys
```

### Environment variables not loading
```bash
# Check direnv status
direnv status

# Manually reload
direnv reload

# Or exit and re-enter the directory
cd .
```

### Forgot which secrets are in the vault?
```bash
pass ls asdlc/
pass show asdlc/webhook_secret    # view a specific secret
```

## Security Notes

- ✅ Secrets are encrypted with GPG (symmetric encryption)
- ✅ `.env` and `.envrc` are gitignored (never committed)
- ✅ Only accessible to you (GPG key is local)
- ✅ Safe to backup `~/.password-store/` (GPG-encrypted)
- ⚠️ Never share your GPG private key

## Moving to Another Machine

If you set up this project on another Mac:

```bash
# On new machine:
brew install pass direnv gnupg

# Copy your password store (if you have GPG key)
# OR re-add secrets manually:
pass insert asdlc/webhook_secret    # then paste the value
pass insert asdlc/api_key          # etc.
```

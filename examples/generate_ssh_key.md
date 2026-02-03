# SSH Key Setup Guide

Choose a name for your key, preferably different to `heron_my_key_name` and including your name (eg: `heron_jacob_key`). Then generate a new key:

## macOS / Linux

### 1. Generate your SSH key

```bash
ssh-keygen -t ed25519 -N "" -C "heron_my_key_name" -f ~/.ssh/heron_my_key_name
```

### 2. Add the key to your SSH agent

```bash
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/heron_my_key_name
```

### 3. Display your public key

```bash
cat ~/.ssh/heron_my_key_name.pub
```

Copy this output and share it with your administrator.

---

## Windows 

*Note: not as tested*

### 1. Generate your SSH key

Open PowerShell or Command Prompt and run:

```powershell
ssh-keygen -t ed25519 -N "" -C "heron_my_key_name" -f C:\Users\%USERNAME%\.ssh\heron_my_key_name
```

### 2. Start the SSH agent and add your key

```powershell
Start-Service ssh-agent
ssh-add C:\Users\%USERNAME%\.ssh\heron_my_key_name
```

> **Note:** If the ssh-agent service isn't running, you may need to enable it first:
> ```powershell
> Get-Service ssh-agent | Set-Service -StartupType Manual
> Start-Service ssh-agent
> ```

### 3. Display your public key

```powershell
type C:\Users\%USERNAME%\.ssh\heron_my_key_name.pub
```

Copy this output and share it with your administrator.

---

Issues?


this should be done by default, but if you have issued with keys not authenticating, a common issue on linux/macos is that you need to fix the permissions of the files.A 

```bash
chmod 600 ~/.ssh/heron_my_key_name
chmod 644 ~/.ssh/heron_my_key_name.pub
```


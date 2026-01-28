## Team SSH Access

### What you’ll get

You’ll receive:

* **Lambda login details** for your team account
* A **shared SSH private key** via a secure link (do not share it)

This key lets you SSH into your team’s GPU instances.

* Do not commit the password or ssh key to a repo.
* If you think it leaked, tell us immediately so we can rotate it.

---

## 1) Download and save the key

You’ll get a secure download link for the key file.

Save it to your SSH folder:

* **Mac / Linux**

  * Path: `~/.ssh/heron_teamname_key.pem`

* **Windows**

  * Path: `C:\Users\<yourname>\.ssh\heron_teamname_key.pem`

If you don’t have a `.ssh` folder, create it.

**Important:** If you copy/paste the key into a file instead of downloading, make sure the file ends with a newline.

---

## 2) Fix permissions (mac/linux only)

### Mac / Linux

Run:

```bash
chmod 600 ~/.ssh/heron_teamname_key.pem
```

---

## 3) Add key to SSH agent (optional but handy)

Adding the key to your SSH agent means you won't need to specify `-i` every time.

### Mac / Linux

```bash
# Start the agent if not running
eval "$(ssh-agent -s)"

# Add the key
ssh-add ~/.ssh/heron_teamname_key.pem
```

To confirm it was added:

```bash
ssh-add -l
```

> **Tip:** On Mac, add `--apple-use-keychain` to persist across reboots:
> ```bash
> ssh-add --apple-use-keychain ~/.ssh/heron_teamname_key.pem
> ```

### Windows (PowerShell)

```powershell
# Start the ssh-agent service
Start-Service ssh-agent

# Add the key
ssh-add $env:USERPROFILE\.ssh\heron_teamname_key.pem
```

---

## 4) Create a machine on Lambda.ai

1. Log in to Lambda
2. Create an instance (GPU)
3. Choose a GPU type. depending on time of day some might be limited.
4. Choose a region. 
5. choose the default stack (or a different one you prefer, it doesn't matter)
6. you will need to create a volume if you haven't used this region before.
7. skip firewall rules and confirm.

---

## 5) Connect to the machine

Check you can SSH into the machine from your terminal:

```bash
ssh -i ~/.ssh/heron_team_key ubuntu@<PUBLIC_IP>
```
or if you added your key using ssk-add, it would just be:
```
ssh ubuntu@<PUBLIC_IP>
```

this will be shown next to your instance name

---

## 6) Cursor / VS Code Remote SSH

1. Install VS Code extension: **Remote - SSH**
2. Open command palette:

   * Mac: `Cmd+Shift+P`
   * Windows/Linux: `Ctrl+Shift+P`
3. Run: `Remote-SSH: Connect to Host...`
4. Enter: `ubuntu@<PUBLIC_IP>` 

If VS Code asks for a password, it usually means the key isn’t being used (permissions/config issue).

---

## 7) Auto-shutdown + whitelisting

To keep budgets sane, idle instances may be stopped automatically, or stopped when your team hits credit limits.

If you need a machine to stay up, include `WHITELISTED` in the instance name, e.g.

* `nicky WHITELISTED`

(Still shut it down when you’re done if you can.)

by defualt instances will be available for at least 4 hours, and if gpu is not used for 2 hours, it will be shut down. this can be configured if needed.

---

## Troubleshooting

**“Permission denied (publickey)”**

* Most common causes:

  * key file permissions wrong (`chmod 600 …`)
  * you didn’t use `-i ~/.ssh/heron_team_key`
  * you’re connecting as the wrong user (try `ubuntu`)
  * you did not have exactly 1 newline at the end of the ssh key
* If stuck, post:

  * team name
  * instance name
  * public IP
  * exact error message

**“Bad permissions” / “Unprotected private key file”**

* Fix with `chmod 600 ~/.ssh/heron_team_key` (Mac/Linux) or `icacls` (Windows)

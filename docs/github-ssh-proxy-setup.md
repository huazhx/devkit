# GitHub SSH Proxy Setup

Date: 2026-05-03

## SSH Public Key

Existing public key:

```text
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILJXIyJavylDQD+3evbdkLRZBBPkwZJauH2Wm2ZnSHOm xuzhenhua@n30099
```

Key file:

```text
~/.ssh/id_ed25519.pub
```

This key was added to GitHub under SSH keys.

## Proxy

The shell has these proxy variables:

```text
http_proxy=http://127.0.0.1:17890
https_proxy=http://127.0.0.1:17890
```

SSH does not automatically use `http_proxy`, so GitHub SSH was configured with an SSH `ProxyCommand`.

## SSH Config

File:

```text
~/.ssh/config
```

Configured content:

```sshconfig
Host github.com
  HostName ssh.github.com
  User git
  Port 443
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ProxyCommand nc -X connect -x 127.0.0.1:17890 %h %p
```

Notes:

- `ssh.github.com:443` is GitHub's SSH-over-HTTPS endpoint.
- `nc -X connect -x 127.0.0.1:17890 %h %p` sends SSH traffic through the local HTTP proxy.
- `IdentitiesOnly yes` ensures SSH uses `~/.ssh/id_ed25519` for GitHub.

## Verification

Command:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T github.com
```

Successful result:

```text
Hi huazhx! You've successfully authenticated, but GitHub does not provide shell access.
```

After this, normal GitHub SSH URLs should work:

```bash
git clone git@github.com:OWNER/REPO.git
```

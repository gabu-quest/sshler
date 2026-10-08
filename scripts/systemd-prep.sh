#!/usr/bin/env bash
# Prepare this WSL distro for systemd-managed sshler.
#
# What this script does (in this order):
#   1. Backs up /etc/wsl.conf to /etc/wsl.conf.pre-systemd.bak
#   2. Flips `systemd = false` → `systemd = true` in /etc/wsl.conf
#   3. Backs up /etc/wsl-boot.sh to /etc/wsl-boot.sh.pre-systemd.bak
#   4. Comments out the `su -c "...sshler serve..."` line in wsl-boot.sh
#      so the boot script doesn't double-launch sshler once systemd does.
#      (lan-pin-watch is left alone — it needs root and the boot script is
#       still the right place for it until we migrate that too.)
#   5. Enables linger for $USER so the user systemd manager keeps running
#      between login sessions (matches the always-on autostart behavior).
#
# Does NOT:
#   - Run `wsl --shutdown` (you'll do that from PowerShell when ready).
#   - Start or enable sshler.service yet — that happens AFTER WSL restart,
#     when systemd is actually running.
#
# Idempotent: re-running is safe; each step checks current state first.

set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "This script needs sudo. Re-running with sudo..."
    exec sudo -- "$0" "$@"
fi

ts="$(date +%Y%m%d-%H%M%S)"

# --- 1 + 2: /etc/wsl.conf ----------------------------------------------------
echo ">>> Step 1/5: backing up /etc/wsl.conf"
cp -n /etc/wsl.conf "/etc/wsl.conf.pre-systemd.${ts}.bak"
echo "    backup: /etc/wsl.conf.pre-systemd.${ts}.bak"

echo ">>> Step 2/5: enabling systemd in /etc/wsl.conf"
if grep -qE '^[[:space:]]*systemd[[:space:]]*=[[:space:]]*true' /etc/wsl.conf; then
    echo "    already enabled — no change."
elif grep -qE '^[[:space:]]*systemd[[:space:]]*=' /etc/wsl.conf; then
    sed -i -E 's/^([[:space:]]*systemd[[:space:]]*=[[:space:]]*).*/\1true/' /etc/wsl.conf
    echo "    flipped to true."
else
    # No systemd line at all — append under [boot]. We know [boot] exists on this box.
    if grep -qE '^\[boot\]' /etc/wsl.conf; then
        sed -i -E '/^\[boot\]/a systemd = true' /etc/wsl.conf
    else
        printf '\n[boot]\nsystemd = true\n' >> /etc/wsl.conf
    fi
    echo "    added systemd = true under [boot]."
fi

# --- 3 + 4: /etc/wsl-boot.sh -------------------------------------------------
if [[ -f /etc/wsl-boot.sh ]]; then
    echo ">>> Step 3/5: backing up /etc/wsl-boot.sh"
    cp -n /etc/wsl-boot.sh "/etc/wsl-boot.sh.pre-systemd.${ts}.bak"
    echo "    backup: /etc/wsl-boot.sh.pre-systemd.${ts}.bak"

    echo ">>> Step 4/5: commenting out the sshler launch line in /etc/wsl-boot.sh"
    if grep -qE '^[[:space:]]*su[[:space:]]+-c[[:space:]]+.*sshler[[:space:]]+serve' /etc/wsl-boot.sh; then
        sed -i -E 's|^([[:space:]]*)(su[[:space:]]+-c[[:space:]]+.*sshler[[:space:]]+serve.*)|\1# DISABLED (managed by systemd user service sshler.service): \2|' /etc/wsl-boot.sh
        echo "    commented out. lan-pin-watch line is untouched."
    elif grep -qE '^[[:space:]]*#.*DISABLED.*sshler[[:space:]]+serve' /etc/wsl-boot.sh; then
        echo "    already commented out — no change."
    else
        echo "    !! could not find a 'su -c ... sshler serve' line. Inspect manually:"
        echo "       sudo grep -n sshler /etc/wsl-boot.sh"
    fi
else
    echo ">>> Step 3-4/5: no /etc/wsl-boot.sh found — skipping. (Surprising — check wsl.conf [boot] command=.)"
fi

# --- 5: linger ---------------------------------------------------------------
echo ">>> Step 5/5: enabling linger for user $USER"
# Linger lets the user manager run without an active login session, so sshler
# starts at boot regardless of whether you've opened a terminal yet.
# (Effective after WSL restart with systemd enabled.)
loginctl enable-linger $USER 2>/dev/null && echo "    enabled." || echo "    deferred (loginctl needs systemd up — will run on first systemd boot)."

cat <<'EOF'

============================================================================
DONE. Nothing has been restarted.

Next steps, in this order — at YOUR convenience:

  1. Close all WSL terminals you don't need (saves state to ssh sessions etc.).

  2. From Windows PowerShell (NOT inside WSL):
         wsl --shutdown
     This is the WSL-wide restart that picks up the wsl.conf change.

  3. Reopen WSL. Verify systemd is running:
         systemctl --user is-system-running

  4. Enable + start the sshler unit (already written to
     ~/.config/systemd/user/sshler.service):
         systemctl --user daemon-reload
         systemctl --user enable --now sshler.service

  5. Tail live logs (the thing you actually want):
         journalctl --user -u sshler -f

If anything goes wrong, the backups are at:
   /etc/wsl.conf.pre-systemd.*.bak
   /etc/wsl-boot.sh.pre-systemd.*.bak

Restoring is `sudo cp /etc/wsl.conf.pre-systemd.*.bak /etc/wsl.conf` and a
`wsl --shutdown`.
============================================================================
EOF

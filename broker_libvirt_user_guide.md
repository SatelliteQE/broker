# Broker Libvirt Provider — User Guide

The Libvirt provider lets Broker provision and manage KVM/QEMU virtual machines on a local
or remote libvirt hypervisor, using the same `checkout` / `checkin` / `execute` / `inventory`
workflow as every other Broker provider.

- **Local** hypervisor via `qemu:///system`
- **Remote** hypervisor via `qemu+ssh://user@host/system`
- VMs are created as fast copy-on-write **overlays** of a base `qcow2` image
- Broker connects to the resulting VMs over SSH (password or key based)

---

## 1. Installation

Install Broker with the `libvirt` extra:

```bash
uv pip install "broker[libvirt] @ ."
```

This pulls in `libvirt-python`, which builds against your system's libvirt development
headers. Install those first if the build fails:

- Fedora/RHEL: `sudo dnf install libvirt-devel gcc python3-devel`
- Debian/Ubuntu: `sudo apt-get install libvirt-dev gcc python3-dev`

You also need the `virsh`/`libvirt-client` tools on the machine running Broker (used to
talk to the hypervisor, including over `qemu+ssh`).

---

## 2. Prepare a hypervisor

Two ready-made scenarios bootstrap a hypervisor (install packages, enable the daemon,
create/activate the storage pool and network, optionally seed a base image, and bake in the
SSH auth Broker will use). They are **idempotent** — safe to re-run.

### Local

```bash
broker scenarios run scenario_libvirt_setup_local.yaml \
  --vars AUTH_METHOD=key PUBKEY_PATH=~/.ssh/id_ed25519.pub \
         BASE_IMAGE_URL=https://.../base.qcow2 BASE_IMAGE_NAME=base.qcow2
```

### Remote

```bash
broker scenarios run scenario_libvirt_setup_remote.yaml \
  --vars HYPERVISOR=kvm01.example.com SSH_USER=broker SSH_KEY=~/.ssh/id_ed25519 \
         AUTH_METHOD=key PUBKEY_PATH=~/.ssh/id_ed25519.pub
```

> ⚠️ The image-customization step edits the base `qcow2` **in place**. Run it **before** any
> VMs are backed by that image — customizing a base after checkouts exist corrupts every
> child overlay. Prep the base first, or point `BASE_IMAGE_NAME` at a fresh copy.

Key scenario variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `HYPERVISOR` | — (remote) | Remote hypervisor host; `SSH_USER`/`SSH_KEY` select its SSH identity |
| `STORAGE_POOL` / `POOL_PATH` | `default` / `/var/lib/libvirt/images` | Where images/overlays live |
| `NETWORK` | `default` | Libvirt network the VMs attach to |
| `BASE_IMAGE_URL` / `BASE_IMAGE_NAME` | `""` / `base.qcow2` | Optional base image to download into the pool |
| `AUTH_METHOD` | `key` | `key` or `basic` — must match your `ssh.auth_method` |
| `SSH_TARGET_USER` | `root` | The user Broker will SSH into the guest as |
| `PUBKEY_PATH` | `""` | Public key to inject (when `AUTH_METHOD=key`) |
| `GUEST_PASSWORD` | `""` | Password to set in the image (when `AUTH_METHOD=basic`) |

---

## 3. Configure the provider

Add a `Libvirt` block to `broker_settings.yaml`:

```yaml
Libvirt:
    uri: qemu:///system
    storage_pool: default
    network: default
    default_memory: 2048        # MB
    default_cpus: 2
    default_disk_size: 10       # GiB (overlay virtual size)
    virt_type: kvm              # "qemu" for software emulation (no KVM)
    firmware: bios              # "efi" for UEFI/OVMF images
    dangling_behavior: checkin  # failed-checkout cleanup: checkin | store | prompt
    # name_prefix: test         # defaults to your local username
    # default_username: root    # falls back to ssh.host_username if unset
    # default_password: "<pw>"
    # default_key_filename: "</path/to/key>"
```

Define **multiple hypervisors** and pick one with `--Libvirt <name>`:

```yaml
Libvirt:
    instances:
        local:
            uri: qemu:///system
            default: True
        remote:
            uri: qemu+ssh://broker@kvm01.example.com/system?keyfile=/home/broker/.ssh/id_ed25519
```

### SSH auth (applies to all Broker hosts)

Broker connects to the VMs using the global `ssh` settings. Pick one method with
`ssh.auth_method`:

```yaml
ssh:
    auth_method: key            # "key" or "basic"
    host_username: root
    host_password: "toor"       # used when auth_method: basic
    host_ssh_key_filename: /home/me/.ssh/id_ed25519   # used when auth_method: key
```

The **public** half of `host_ssh_key_filename` is what the setup scenarios bake into the base
image (it does not have to be a special "broker" key — any key you choose).

---

## 4. Everyday usage

### Check out a VM

```bash
# From a base image overlay
broker checkout --libvirt-image base.qcow2

# Sizing overrides
broker checkout --libvirt-image base.qcow2 --libvirt-memory 4096 --libvirt-cpus 4

# From a custom domain XML definition
broker checkout --libvirt-xml /path/to/domain.xml
```

Broker creates an overlay, boots the domain, waits for an IP and for SSH to come up, and adds
the host to your inventory.

### Inspect and connect

```bash
broker inventory
broker inventory --sync Libvirt      # re-discover VMs directly from the hypervisor
```

### Lifecycle actions (no SSH needed)

```bash
broker execute --libvirt-action reboot <host>
```

| Action | Effect |
| --- | --- |
| `power-off` | Graceful ACPI shutdown |
| `power-on` | Start a stopped domain |
| `hard-stop` | Immediate power cut |
| `reboot` | Reboot request |
| `reset` | Hard reset |
| `pause` / `resume` | Suspend to / resume from memory |

### Check in (delete)

```bash
broker checkin <host>          # or: broker checkin --all
```

Checkin destroys the domain, undefines it (including EFI NVRAM), and deletes its overlay
volume. It is idempotent — an already-removed VM is treated as success.

### List hypervisor resources

```bash
broker providers Libvirt --images     # base volumes in the pool
broker providers Libvirt --domains    # existing domains
broker providers Libvirt --networks
broker providers Libvirt --pools
```

---

## 5. Networking & reachability

- **Local (`qemu:///system`)** with the default NAT network: guest IPs are directly reachable
  from the Broker machine. ✅
- **Remote (`qemu+ssh://`)**: use a **bridged/routed** guest network so the VM gets an
  address reachable from the Broker machine. ✅
- **Remote + NAT** (guest IP private to the hypervisor): not reachable without a jump host,
  which is not supported yet. Prefer a bridge for remote hypervisors.

For a routable IP, Broker attaches each VM to the configured `network`; libvirt assigns the
DHCP lease automatically. Base images should have `qemu-guest-agent` installed so Broker can
read the IP directly (it falls back to DHCP leases otherwise).

---

## 6. Remote hypervisors (`qemu+ssh`)

There are **two independent SSH channels**:

| Channel | Auth |
| --- | --- |
| Broker/libvirt → **hypervisor** (`qemu+ssh`) | Key/agent only (runs non-interactively). Set the identity via the `?keyfile=` URI param or your SSH agent. Password auth is not usable here. |
| Broker → **guest VM** | `ssh.auth_method` (basic *or* key). This is what the setup scenarios prepare. |

So a remote hypervisor needs passwordless (key/agent) SSH from the Broker machine — the same
access `qemu+ssh` itself requires. The remote setup scenario preflights this for you.

---

## 7. UEFI / Secure Boot images

Many modern cloud images are UEFI-only. Set:

```yaml
Libvirt:
    firmware: efi
```

Broker then defines the domain with OVMF/UEFI firmware and a per-domain NVMRAM store (which
checkin cleans up automatically). Make sure the hypervisor has the OVMF/edk2 firmware package
installed (`edk2-ovmf` on Fedora/RHEL, `ovmf` on Debian/Ubuntu).

---

## 8. Failed checkouts (`dangling_behavior`)

If a checkout fails after the VM is partially created (for example, the IP never appears),
Broker cleans up according to `LIBVIRT.dangling_behavior`:

- `checkin` (default) — destroy/undefine the domain and delete its overlay, then report the
  error. No orphans left behind.
- `store` — leave the VM in place and record it in inventory as `deploy_failed` for
  post-mortem debugging.
- `prompt` — ask you what to do.

---

## 9. Troubleshooting

| Symptom | Likely cause / fix |
| --- | --- |
| `libvirt-python` fails to install | Install libvirt dev headers (see §1). |
| Checkout hangs then fails waiting for an IP | Guest agent missing and no DHCP lease — install `qemu-guest-agent` in the base image; verify the network is active (`virsh net-list`). |
| Can't reach the VM over SSH | Auth mismatch — confirm `ssh.auth_method` matches how the image was prepared; re-run the setup scenario. |
| Remote checkout fails immediately | `qemu+ssh` needs passwordless key/agent SSH to the hypervisor — set `SSH_KEY`/`?keyfile=` or load your key into the agent. |
| VM won't boot from a UEFI image | Set `firmware: efi` and install OVMF on the hypervisor (see §7). |
| Guest IP not reachable on a remote host | Use a bridged network instead of NAT (see §5). |
| **VM has no internet access** | See detailed network troubleshooting below. |

### VMs Have No Internet Access

**Symptoms:** VM boots successfully, gets an IP address, but cannot ping 8.8.8.8 or reach the internet.

**Quick Diagnosis:**
```bash
# Check if default network exists and is active
virsh net-list

# Validate network configuration  
broker scenarios execute validate_network

# Comprehensive troubleshooting
broker scenarios execute troubleshoot_network

# Check IP forwarding
sysctl net.ipv4.ip_forward
```

**Quick Fix:**
```bash
# Create and configure network (if missing)
sudo -E env PATH=$PATH broker scenarios execute setup_network

# Or manually start existing network:
sudo virsh net-start default
sudo virsh net-autostart default

# Enable IP forwarding if needed:
echo 'net.ipv4.ip_forward=1' | sudo tee /etc/sysctl.d/99-libvirt.conf
sudo sysctl -p /etc/sysctl.d/99-libvirt.conf
```

**Detailed Troubleshooting:**

1. **Check network exists:**
   ```bash
   virsh net-list --all
   ```
   - If "default" network is missing: Run `sudo -E env PATH=$PATH broker scenarios execute setup_network`
   - If network exists but is inactive: `sudo virsh net-start default`

2. **Verify IP forwarding:**
   ```bash
   sysctl net.ipv4.ip_forward
   ```
   - Should show `net.ipv4.ip_forward = 1`
   - If 0: `sudo sysctl -w net.ipv4.ip_forward=1` (temporary) or run setup_network for persistent

3. **Check NAT configuration:**
   ```bash
   virsh net-dumpxml default | grep forward
   ```
   - Should show `<forward mode='nat'/>`

4. **Test from inside VM:**
   ```bash
   # Get shell inside VM
   broker checkout --libvirt-image <image> --auth basic

   # Inside VM:
   ip addr                      # Check if VM has IP
   ping 192.168.122.1          # Can it reach gateway?
   ping 8.8.8.8                # Can it reach internet?
   ping google.com             # Does DNS work?
   ```

5. **Check firewall configuration:**
   
   **On systems using firewalld (Fedora, RHEL, CentOS):**
   ```bash
   # Check if firewalld is running
   systemctl is-active firewalld
   
   # Verify masquerade is enabled in libvirt zone (CRITICAL!)
   sudo firewall-cmd --zone=libvirt --query-masquerade
   # Should return "yes" - if "no", VMs won't have internet access!
   
   # Check virbr0 is in libvirt zone
   sudo firewall-cmd --get-zone-of-interface=virbr0
   # Should return "libvirt"
   ```
   
   **On systems using iptables directly:**
   ```bash
   sudo iptables -t nat -L -n -v | grep virbr
   ```
   - Should see MASQUERADE rules for virbr0

**Common Issues:**

- **Network doesn't exist:** The setup scenarios now create it automatically, but older installations may need manual setup
- **IP forwarding disabled:** Required for NAT to work - setup scenarios enable it persistently in `/etc/sysctl.d/99-libvirt.conf`
- **Firewalld masquerade disabled (Fedora/RHEL):** The most common issue! Firewalld's libvirt zone exists but masquerade is disabled by default - setup scenarios now enable it automatically
- **Firewall blocking NAT:** Libvirt creates iptables rules automatically, but custom firewall configs may interfere
- **Bridge not created:** Normal if no VMs are running - bridge is created when first VM starts

**Prevention:**

Run the setup scenario which now ensures network is properly configured:
```bash
sudo -E env PATH=$PATH broker scenarios execute scenario_libvirt_setup_local
```

**Important - User Permissions:**

To use libvirt without sudo, your user must be in the `libvirt` group:

```bash
# Check if you're in the group
groups | grep libvirt

# If not, add yourself
sudo usermod -aG libvirt $USER

# Log out and back in, or run:
newgrp libvirt
```

The setup scenarios automatically add you to this group, but you need to log out and back in (or run `newgrp libvirt`) for it to take effect.

**Note:** All libvirt commands (including validation scenarios) now explicitly use `qemu:///system` connection to ensure consistency between sudo and non-sudo usage.

### Firewalld Masquerade Issue (Fedora/RHEL)

**Symptom:** VMs can reach the gateway (192.168.124.1) but cannot ping 8.8.8.8 or reach the internet.

**Cause:** On Fedora and RHEL systems using firewalld, the libvirt zone exists and virbr0 is correctly assigned to it, but **masquerade (NAT) is disabled by default**. This is the #1 cause of network issues!

**Diagnosis:**
```bash
# Check if masquerade is enabled (should return "yes")
sudo firewall-cmd --zone=libvirt --query-masquerade
```

If it returns "no", VMs cannot access the internet.

**Fix (manual):**
```bash
# Enable masquerade in libvirt zone
sudo firewall-cmd --permanent --zone=libvirt --add-masquerade
sudo firewall-cmd --reload

# Restart the network to apply rules
sudo virsh -c qemu:///system net-destroy default
sudo virsh -c qemu:///system net-start default
```

**Fix (automatic):**
The setup scenarios now detect firewalld and enable masquerade automatically:
```bash
sudo -E env PATH=$PATH broker scenarios execute scenario_libvirt_setup_local
```

**Why this happens:**
- Libvirt package creates the `libvirt` firewalld zone
- Libvirt adds `virbr0` to this zone
- But masquerade is **not enabled** by default
- Without masquerade, NAT doesn't work, so VMs can't reach the internet
- The setup scenarios now check for this and fix it automatically

# Libvirt Setup Scenarios - Quick Reference

This document provides quick reference for the modular libvirt setup scenarios.

## Scenario Overview

| Scenario | Purpose | Requires Sudo |
|----------|---------|---------------|
| **Network Setup** | | |
| `setup_network` | Create/configure default NAT network for internet | **Yes** |
| `validate_network` | Check network configuration and diagnose issues | No |
| `troubleshoot_network` | Comprehensive network diagnostics | No |
| **Image Management** | | |
| `detect_vm_capabilities` | Detect what's installed in an image | No |
| `cloud_image_guide` | Show download links for official cloud images | No |
| `validate_cloud_image` | Validate a cloud image is ready | No |
| `prepare_regular_image` | Install cloud-init in a regular image (user-space!) | No* |
| `copy_image_to_pool` | Helper to copy prepared image back to pool | **Yes** |
| `import_image` | Download/copy an image into the storage pool (local) | No** |
| `import_image_remote` | Download/copy an image to remote hypervisor | No*** |
| `setup_image` | Orchestrator that handles the full workflow | No** |
| **System Setup** | | |
| `scenario_libvirt_setup_local` | Full system setup (installs packages) | **Yes** |

\* The main work runs without sudo, but a final sudo step (via helper) is needed to copy back to pool  
\** May need sudo for final copy step depending on pool permissions  
\*** Requires SSH access to remote hypervisor

## Quick Start

### Option 1: Use an Official Cloud Image (Recommended - No Sudo!)

```bash
# Get download links
broker scenarios execute cloud_image_guide

# Download manually, then import
broker scenarios execute import_image \
  IMAGE_PATH=~/Downloads/Fedora-Cloud-Base-*.qcow2 \
  IMAGE_NAME=fedora-cloud.qcow2

# Use immediately with --auth (no sudo needed!)
broker checkout --libvirt-image fedora-cloud.qcow2 --auth basic
```

### Option 2: Prepare an Existing Regular Image (Minimal Sudo)

```bash
# Detect what's needed (works with any storage pool)
broker scenarios execute detect_vm_capabilities \
  IMAGE_NAME=fed44.qcow2

# Prepare it (installs cloud-init without sudo!)
broker scenarios execute prepare_regular_image \
  IMAGE_NAME=fed44.qcow2

# Copy back to pool (this step needs sudo)
sudo broker scenarios execute copy_image_to_pool \
  IMAGE_NAME=fed44.qcow2 \
  SOURCE_PATH=$HOME/libvirt-working/fed44.qcow2

# Use it!
broker checkout --libvirt-image fed44.qcow2 --auth basic
```

### Option 3: Full System Setup (One-Time, Requires Sudo)

```bash
# Complete setup including package installation
sudo broker scenarios execute scenario_libvirt_setup_local
```

## Storage Pool Auto-Detection

All scenarios automatically detect your active storage pool! They prefer `default` if it exists, otherwise use the first active pool found.

**Your pools:**
```bash
virsh pool-list
```

**Override if needed:**
```bash
broker scenarios execute detect_vm_capabilities \
  IMAGE_NAME=myimage.qcow2 \
  STORAGE_POOL=gnome-boxes
```

## Network Setup

VMs need a properly configured network to access the internet. The setup scenarios now automatically create the default NAT network if it doesn't exist.

### setup_network (requires sudo)

Create and configure the default NAT network for internet connectivity:

```bash
sudo -E env PATH=$PATH broker scenarios execute setup_network
```

**What it does:**
- Creates default NAT network if missing
- Enables IP forwarding (required for NAT)
- Starts and autostarts network
- Validates configuration

**When to use:**
- First time libvirt setup
- VMs have no internet access
- After fresh libvirt installation

### validate_network

Check network configuration and diagnose issues:

```bash
broker scenarios execute validate_network
```

**Output:** Comprehensive network status including:
- Network active/autostart status
- NAT forwarding configuration
- IP forwarding status
- DHCP configuration
- Bridge interface status
- Firewall/NAT rules

### troubleshoot_network

Comprehensive network diagnostics with fix suggestions:

```bash
broker scenarios execute troubleshoot_network
```

**Provides step-by-step diagnostics:**
1. Network status check
2. IP forwarding verification
3. Bridge interface status
4. Firewall/NAT rules
5. DNS configuration
6. Running VMs and their interfaces

## Detailed Usage

### Detect Image Capabilities

Probe an image to see what's installed (auto-detects storage pool):

```bash
broker scenarios execute detect_vm_capabilities IMAGE_NAME=<image-name>
```

**Output:** Shows cloud-init status, SSH server, guest agent, and recommendations.

### Cloud Image Download Guide

Get links to official cloud images:

```bash
broker scenarios execute cloud_image_guide
```

**Output:** Download links for Fedora, Ubuntu, CentOS Stream, Rocky Linux cloud images.

### Validate Cloud Image

Confirm that a cloud image is ready to use:

```bash
broker scenarios execute validate_cloud_image IMAGE_NAME=<image-name>
```

**Output:** Confirms cloud-init is present and shows usage examples.

### Prepare Regular Image (User-Space)

Install cloud-init in a regular (non-cloud) image **without sudo** for the main work:

```bash
broker scenarios execute prepare_regular_image IMAGE_NAME=<image-name>
```

**Process:**
1. Auto-detects storage pool
2. Copies image to `$HOME/libvirt-working/`
3. Runs virt-customize with `LIBGUESTFS_BACKEND=direct` (user-space)
4. Installs cloud-init and qemu-guest-agent
5. Provides command to copy back (requires sudo)

**Requirements:**
- User in `kvm` group (for `/dev/kvm` access)
- `virt-customize` installed (`libguestfs-tools` package)

**Final step (requires sudo):**
```bash
sudo broker scenarios execute copy_image_to_pool \
  IMAGE_NAME=myimage.qcow2 \
  SOURCE_PATH=$HOME/libvirt-working/myimage.qcow2
```

### Copy Image to Pool (Requires Sudo)

Helper scenario to copy a prepared image back to the storage pool:

```bash
sudo broker scenarios execute copy_image_to_pool \
  IMAGE_NAME=<image-name> \
  SOURCE_PATH=<path-to-prepared-image>
```

**This scenario requires sudo** because it:
- Copies to `/var/lib/libvirt/images` (or equivalent)
- Sets `qemu:qemu` ownership
- Refreshes the storage pool

### Import Image

Download or copy an image into the libvirt storage pool:

```bash
# From URL
broker scenarios execute import_image \
  IMAGE_URL=https://example.com/image.qcow2 \
  IMAGE_NAME=my-image.qcow2

# From local path
broker scenarios execute import_image \
  IMAGE_PATH=/path/to/image.qcow2 \
  IMAGE_NAME=my-image.qcow2
```

May require sudo for final copy depending on pool permissions.

### Setup Image (Orchestrator)

Full workflow - imports, detects type, and routes to appropriate preparation:

```bash
# Import and setup from URL
broker scenarios execute setup_image \
  IMAGE_SOURCE=https://example.com/cloud-image.qcow2 \
  IMAGE_NAME=my-cloud.qcow2

# Setup existing image in pool
broker scenarios execute setup_image \
  IMAGE_NAME=existing-image.qcow2 \
  SKIP_IMPORT=true

# Force a specific setup method
broker scenarios execute setup_image \
  IMAGE_NAME=my-image.qcow2 \
  SKIP_IMPORT=true \
  FORCE_METHOD=cloud    # or "regular" or "manual"
```

## Variable Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IMAGE_NAME` | Yes | - | Name for the image in the storage pool |
| `IMAGE_URL` | No | - | URL to download image from |
| `IMAGE_PATH` | No | - | Local path to copy image from |
| `IMAGE_SOURCE` | No | - | URL or path (setup orchestrator) |
| `SOURCE_PATH` | No | - | Source path for copy_image_to_pool |
| `SKIP_IMPORT` | No | `false` | Skip import step if image already in pool |
| `FORCE_METHOD` | No | - | Force setup method: `cloud`, `regular`, or `manual` |
| `STORAGE_POOL` | No | *auto-detect* | Libvirt storage pool name (auto-detects if not set) |

## Using Images with --auth

Once an image is prepared, use it with any auth method:

```bash
# Password auth (basic = broker's default credentials)
broker checkout --libvirt-image my-image.qcow2 --auth basic

# SSH key auth
broker checkout --libvirt-image my-image.qcow2 --auth key
broker checkout --libvirt-image my-image.qcow2 --auth ~/.ssh/id_ed25519.pub

# Custom password
broker checkout --libvirt-image my-image.qcow2 --auth root:mypassword
```

Cloud-init will inject the credentials on first boot automatically!

## Troubleshooting

### "No storage pool found: no storage pool with matching name 'default'"

Your system uses a different pool name. The scenarios now **auto-detect** your pool!

To see your pools:
```bash
virsh pool-list
```

The scenarios will automatically use the first active pool (or `default` if it exists).

### "virt-inspector not found"

```bash
# Fedora/RHEL
sudo dnf install libguestfs-tools-c

# Ubuntu/Debian
sudo apt-get install libguestfs-tools
```

### "Cannot write to /dev/kvm"

Add yourself to the `kvm` group:

```bash
sudo usermod -aG kvm $USER
newgrp kvm  # or log out and back in
```

### "LIBGUESTFS_BACKEND=direct failed"

Fall back to sudo approach:

```bash
sudo virt-customize -a $HOME/libvirt-working/image.qcow2 \
  --install cloud-init,qemu-guest-agent \
  --run-command 'systemctl enable cloud-init cloud-config cloud-final cloud-init-local' \
  --selinux-relabel
```

## Why This Approach?

### Minimal Sudo Usage
- Cloud images: **zero sudo needed**
- Regular images: sudo only for final copy back to pool
- Main virt-customize work runs in user-space

### Storage Pool Agnostic
- Works with `default`, `gnome-boxes`, or any pool
- Auto-detection means you don't need to know your pool name

### Modular Design
- Each scenario does one thing well
- Compose them as needed
- Clear separation between sudo and non-sudo work

## See Also

- Official setup scenarios: `scenario_libvirt_setup_local.yaml`, `scenario_libvirt_setup_remote.yaml`
- Smoke test: `testing/smoke_libvirt_test.yaml`
- User guide: `broker_libvirt_user_guide.md`
- Provider docs: `broker_libvirt_provider.md`

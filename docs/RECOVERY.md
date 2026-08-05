# Recovery boundary

## Principle

This repository starts only after the vendor platform is healthy. It never
installs or replaces UEFI/BIOS, embedded-controller firmware, DGX OS, the
kernel, the NVIDIA driver, CUDA, or low-level networking firmware. If those
checks fail, recover/update through ASUS or NVIDIA and rerun `bin/doctor`.

## ASUS Ascent GX10

The DMI identity must indicate ASUSTeK and GX10. Use the [official ASUS Ascent
GX10 support page](https://www.asus.com/us/supportonly/gx10/helpdesk_bios/) to
obtain the **ASUS-specific NVIDIA DGX OS** recovery image and its published
SHA-256. At the time this plan was prepared, ASUS listed 7.4.0-3; always use
the current image offered for the exact product and verify its checksum.

Do not use NVIDIA Founders Edition media on an ASUS system. NVIDIA's [system
recovery guide](https://docs.nvidia.com/dgx/dgx-spark/system-recovery.html)
explicitly limits that image to Founders Edition and directs OEM owners to the
OEM's image.

Recovery erases the internal SSD. Before beginning:

- copy this Git repository, `~/ai-data`, the secrets file (securely), and any
  unrelated user data to external storage;
- record the current `/etc/dgx-release`, DMI data, `nvidia-smi`, Tailscale node
  name, and checksums;
- verify the recovery download against ASUS's published hash;
- have a suitable USB device, display, keyboard, and network access.

After ASUS recovery, complete OOBE, use ASUS/DGX Dashboard's supported OTA path
until the platform is current, join Tailscale, clone this repository, and run
the fresh-install procedure. Do not restore old `/etc`, driver packages, or
kernel modules over the recovered system.

## NVIDIA DGX Spark Founders Edition

Only a machine whose DMI identifies NVIDIA DGX Spark should use NVIDIA's
Founders Edition recovery instructions and media. Follow the current NVIDIA
guide exactly, then apply the supported DGX OS updates before running this
repository.

## Expected application baseline

Preflight expects:

- Ubuntu 24.04 on `aarch64`;
- `/etc/dgx-release` identifying DGX Spark;
- GB10 / compute capability 12.1;
- CUDA toolkit major 13;
- a working NVIDIA driver (`nvidia-smi` succeeds);
- Docker, Compose, and NVIDIA Container Toolkit, either vendor-provided or
  installable without replacing the vendor GPU stack;
- at least 120 GiB memory and 150 GiB free for first installation.

The exact current DGX OS component versions vary between Founders and OEM
systems. NVIDIA notes that partner systems can receive updates on a different
schedule. The playbook therefore validates capabilities and supported major
baselines rather than forcing the Founders package versions onto ASUS.

## When preflight fails

| Failure | Correct owner/action |
|---|---|
| Wrong DMI or architecture | Stop; use an explicit supported inventory |
| Missing/invalid `/etc/dgx-release` | Restore/update vendor DGX OS |
| Driver cannot enumerate GB10 | Vendor OTA, recovery, or support |
| Compute capability is not 12.1 | Unsupported by this repository |
| CUDA major is not 13 | Vendor-supported DGX OS/CUDA update |
| Docker GPU smoke test fails | Check vendor Container Toolkit integration |
| Only an application package is absent | Allow Ansible to install it |
| Disk/data loss | Restore Git repo, `~/ai-data`, and secrets backup |

Do not bypass a failed platform check merely to make Ansible continue. No
override is supplied to convert a generic Ubuntu install into DGX OS.

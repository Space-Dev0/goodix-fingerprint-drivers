# System Integration, Concurrency & SELinux Hardening

## 1. Concurrency & Sequential State Machine (SSM)

`libfprint` uses GLib's asynchronous event loop (`GMainLoop`) driven by a **Sequential State Machine (FpiSsm)**. Because USB transfers are non-blocking, every stage of device operation must transition cleanly without race conditions.

```
+-------------------------------------------------------------------------+
|                    Goodix 581a Asynchronous Scan Loop                  |
|                                                                         |
|      [IDLE / SLEEP]                                                     |
|            |                                                            |
|            v                                                            |
|      SCAN_581A_CALIBRATE   --> (Capture Mode 1 ambient air baseline)    |
|            |                                                            |
|            v                                                            |
|      SCAN_581A_CHECK_TOUCH --> (Arm FDT_DOWN interrupt, await touch)    |
|            |                                                            |
|            v (Hardware Touch Interrupt Fired)                           |
|      SCAN_581A_READ_IMG    --> (Capture Mode 5 live encrypted frame)    |
|            |                                                            |
|            v (Frame Processed -> SIGFM Extract)                         |
|      SCAN_581A_AWAIT_UP    --> (Arm FDT_UP interrupt, await release)    |
|            |                                                            |
|            v (Finger Physically Lifted)                                 |
|      [Action Complete / Deactivate]                                     |
+-------------------------------------------------------------------------+
```

### 1.1 The Touch 8 Crash: Root Cause & Resolution

During enrollment testing, `fprintd` consistently crashed or hung on the 8th touch.

#### The Race Condition:
1. When enrollment completed stage 8 (the final stage), `fpi_image_device_sigfm_process()` called `fpi_image_device_deactivate(self, FALSE)`.
2. However, the user's finger was **still physically pressed** against the sensor!
3. The underlying Mode 5 scan SSM was simultaneously completing its read callback and preparing to call `goodix_send_data()`.
4. Deactivating closed the USB device and freed private pointers while the SSM callback was executing, causing an immediate **SIGSEGV / null-pointer dereference** in `goodix_send_data()`.

#### The Fix: Deferred Deactivation
Deactivation is now cleanly deferred until the finger is physically lifted from the sensor:

```c
/* In goodix581a.c:on_581a_mode5_read */
if (!self->scan_ssm)
  return;
fpi_ssm_next_state (ssm); /* Advance to AWAIT_UP */

/* In on_581a_touch_up */
self->scan_ssm = NULL;
fpi_ssm_mark_completed (ssm);
fpi_image_device_report_finger_status (FP_IMAGE_DEVICE (dev), FALSE);

/* In fpi-image-device.c:fp_image_device_report_finger_status */
if (!present && priv->action_pending_deactivation)
  fpi_image_device_deactivate (self, FALSE);
```

---

## 2. SELinux Hardening & Intel TBB Investigation

On Fedora Linux, running `fprintd` under targeted SELinux triggered persistent desktop notification popups:

```
type=AVC msg=audit(1792592456.350:1146): avc:  denied  { read } for  pid=31331
comm="fprintd" name="nr_hugepages" dev="proc" ino=176446
scontext=system_u:system_r:fprintd_t:s0
tcontext=system_u:object_r:sysctl_vm_t:s0 tclass=file permissive=0
```

### 2.1 Deep Reverse Engineering of the AVC Denial

Binary analysis using `gdb` and `objdump` revealed:
1. OpenCV links against **Intel Threading Building Blocks (`libtbb.so.12`)**.
2. When initialized, `libtbb.so.12` calls `dlopen("libtbbmalloc.so.2")`.
3. In `libtbbmalloc.so.2`, the function `rml::internal::doInitialization()` unconditionally executes:
   ```cpp
   parseFile<100, 1>("/proc/sys/vm/nr_hugepages", ...);
   ```
4. Even if `TBB_MALLOC_USE_HUGE_PAGES=0` is set in the environment, TBB checks the environment variable **after** opening `/proc/sys/vm/nr_hugepages`.
5. Under Fedora's default security policy, the `fprintd_t` SELinux domain is strictly prohibited from reading kernel VM tunables (`sysctl_vm_t`). The kernel denies the `openat()` syscall with `-EACCES` and emits an audit event.

### 2.2 Keep the sysctl out of fprintd's service namespace

The installer sets `TBB_MALLOC_USE_HUGE_PAGES=0` and adds this systemd service
setting:

```ini
InaccessiblePaths=-/proc/sys/vm/nr_hugepages
```

`InaccessiblePaths=` hides that one path inside the `fprintd` service's mount
namespace. It does not change the host sysctl or grant `fprintd` access to
other VM settings. The leading `-` tells systemd to ignore the path if it is
absent. This avoids generating an SELinux allow rule for a probe the service
does not need.

After installing or changing the drop-in, reload systemd and restart fprintd:

```bash
sudo systemctl daemon-reload
sudo systemctl restart fprintd
systemctl show fprintd -p InaccessiblePaths
```

Then exercise enrollment or verification and inspect recent denials:

```bash
fprintd-enroll
sudo ausearch -m AVC -c fprintd -ts recent -i
```

The `InaccessiblePaths` property should include `/proc/sys/vm/nr_hugepages`.
Do not generate a policy module from all `fprintd` audit events: that can
silently authorize denials unrelated to this known TBB probe. If the AVC
continues or names a different path, investigate that denial separately.

---

## 3. PAM & System Integration

To use the Goodix 581a for login, lockscreen, and `sudo` authentication on Linux:

### 3.1 Enabling PAM Fingerprint Auth

On Fedora / RHEL:
```bash
sudo authselect enable-feature with-fingerprint
sudo authselect apply-changes
```

On Ubuntu / Debian:
```bash
sudo pam-auth-update --enable fingerprint
```

### 3.2 Systemd Service Drop-In

Ensure `fprintd.service` limits OpenCV to single-threaded mode to prevent unnecessary thread pool overhead:

Create `/etc/systemd/system/fprintd.service.d/sigfm.conf`:
```ini
[Service]
Environment="OPENCV_NUM_THREADS=1" "TBB_MALLOC_USE_HUGE_PAGES=0"
# TBB probes this sysctl even when huge-page allocation is disabled.
InaccessiblePaths=-/proc/sys/vm/nr_hugepages
```

Reload and restart:
```bash
sudo systemctl daemon-reload
sudo systemctl restart fprintd
```

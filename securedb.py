"""Encrypted memory (fix 81). Everything Jarvis saves (memories.db) is kept ENCRYPTED on the disk:
memories, lists, to-dos, reminders, the period tracker, learned fixes, settings.

How it works:
  - On disk there is only memories.db.enc: AES-256-GCM, so it is scrambled and can't be changed unnoticed.
  - The key is made from two things: a random secret in your home folder (.jarvis_key, never in the project
    folder, so it can't end up on GitHub) AND this device's hardware ID (on the Pi: its serial number).
    So the memory file only opens on THIS device. A copied file or a stolen SD card shows only scrambled data.
  - While Jarvis runs, the readable copy lives in RAM (on the Pi: /dev/shm), never on the SD card.
    Every few seconds, and when Jarvis stops, changes are encrypted and saved.
  - Nothing to type at startup: reminders still ring on time.

Honest limit: someone who has the running device itself can still use Jarvis, like any unlocked device.
That's what the PIN (for erasing and the big actions) and guest mode are for.

  python securedb.py --status     is my memory encrypted? (and where)
  python securedb.py --confirm    after checking that everything works: delete the old unencrypted backup
"""
import atexit
import hashlib
import os
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

HERE = Path(__file__).resolve().parent
PLAIN = HERE / "memories.db"                    # the old, unencrypted file (moved away on the first run)
ENC = HERE / "memories.db.enc"                  # what is really kept on the disk
BACKUP = HERE / "memories.db.unencrypted-backup"
KEY_FILE = Path.home() / ".jarvis_key"
MAGIC = b"JARVISENC1"
AAD = b"jarvis memory v1"
SAVE_EVERY = 5                                   # seconds between checks for changes

_real_connect = sqlite3.connect
_runtime = None                                  # the readable copy, in RAM
_key = None
_last = None                                     # (size, mtime) of the runtime copy when last saved
_lock = threading.Lock()
_readonly = "--fast" in sys.argv                 # test runs never change the real memory
STATUS = {"encrypted": False, "where": "", "note": ""}


def _device_id():
    """This device's hardware ID: the Pi's serial number, or the computer's machine ID."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.lower().startswith("serial"):
                serial = line.split(":")[-1].strip()
                if serial.strip("0"):
                    return "pi:" + serial
    except OSError:
        pass
    for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            v = Path(p).read_text().strip()
            if v:
                return "linux:" + v
        except OSError:
            pass
    if sys.platform.startswith("win"):
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                                0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as k:
                return "win:" + winreg.QueryValueEx(k, "MachineGuid")[0]
        except OSError:
            pass
    import uuid
    return "mac:" + hex(uuid.getnode())


def _secret():
    if KEY_FILE.exists():
        data = KEY_FILE.read_bytes()
        if len(data) == 32:
            return data
    data = os.urandom(32)
    KEY_FILE.write_bytes(data)
    try:
        os.chmod(KEY_FILE, 0o600)                 # only this user can read it
    except OSError:
        pass
    return data


def _make_key():
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=hashlib.sha256(_device_id().encode()).digest(),
                info=AAD).derive(_secret())


def encrypt(data, key):
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, AAD)


def decrypt(blob, key):
    if not blob.startswith(MAGIC):
        raise ValueError("not a Jarvis memory file")
    nonce, body = blob[len(MAGIC):len(MAGIC) + 12], blob[len(MAGIC) + 12:]
    return AESGCM(key).decrypt(nonce, body, AAD)


def _runtime_path():
    shm = Path("/dev/shm")
    base = shm if shm.is_dir() and os.access(shm, os.W_OK) else Path(tempfile.gettempdir())
    folder = base / f"jarvis-{os.getuid() if hasattr(os, 'getuid') else 'user'}"
    folder.mkdir(mode=0o700, exist_ok=True)
    return folder / ("memories-test.db" if _readonly else "memories.db"), base == shm


def _snapshot():
    """A consistent copy of the runtime database, even while it's being written."""
    src = _real_connect(str(_runtime))
    try:
        dst = _real_connect(":memory:")
        src.backup(dst)
        tmp = Path(str(_runtime) + ".snap")
        disk = _real_connect(str(tmp))
        dst.backup(disk)
        disk.close()
        dst.close()
        data = tmp.read_bytes()
        tmp.unlink()
        return data
    finally:
        src.close()


def save(force=False):
    """Encrypt the runtime copy and write it to the disk (only if something changed)."""
    global _last
    if _readonly or _runtime is None or not _runtime.exists():
        return False
    with _lock:
        st = _runtime.stat()
        sig = (st.st_size, st.st_mtime_ns)
        if not force and sig == _last:
            return False
        blob = encrypt(_snapshot(), _key)
        tmp = ENC.with_name(ENC.name + ".tmp")
        tmp.write_bytes(blob)
        os.replace(tmp, ENC)                       # all or nothing: a power cut never leaves half a file
        _last = sig
        return True


def _saver():
    while True:
        time.sleep(SAVE_EVERY)
        try:
            save()
        except Exception as e:                     # never stop Jarvis because of a save problem
            print(f"   (encrypted save failed, will retry: {type(e).__name__})")


def _cleanup():
    try:
        save(force=True)
    finally:
        if _runtime is not None and _runtime.exists():
            try:
                _runtime.unlink()                  # the readable copy is gone when Jarvis stops
            except OSError:
                pass


def _connect(database, *args, **kwargs):
    """Every part of Jarvis that opens memories.db really opens the readable copy in RAM."""
    try:
        if Path(os.fspath(database)).name == "memories.db":
            database = str(_runtime)
    except TypeError:
        pass
    return _real_connect(database, *args, **kwargs)


def install():
    """Called once, first thing: unlock the memory and send every memories.db connection to the RAM copy."""
    global _runtime, _key, _last
    if _runtime is not None:
        return STATUS
    _key = _make_key()
    _runtime, in_ram = _runtime_path()
    if _readonly:                                  # fix 99: a test run starts from an EMPTY memory, so the
        _real_connect(str(_runtime)).close()       # results never depend on what you saved in real use
        if _runtime.stat().st_size:                # (a leftover test copy from last time: start clean)
            _runtime.unlink()
            _real_connect(str(_runtime)).close()
        STATUS["note"] = "test run: an empty memory, your real one is not even opened"
    elif ENC.exists():
        try:
            data = decrypt(ENC.read_bytes(), _key)
        except Exception:
            raise SystemExit("🔒 The memory file can't be opened on this device (wrong device or missing key). "
                             "Nothing was changed. If you moved Jarvis to a new device, start with a fresh memory "
                             "by renaming memories.db.enc.")
        _runtime.write_bytes(data)
        STATUS["note"] = "unlocked"
    elif PLAIN.exists() and not _readonly:
        data = PLAIN.read_bytes()                  # the first run: move the old memory into encryption
        _runtime.write_bytes(data)
        ENC.write_bytes(encrypt(data, _key))
        os.replace(PLAIN, BACKUP)
        STATUS["note"] = (f"moved your memory into encryption. A backup of the old file is kept as {BACKUP.name}; "
                          "delete it with: python securedb.py --confirm")
    elif PLAIN.exists():
        _runtime.write_bytes(PLAIN.read_bytes())   # a test run before the first real start
    else:
        _real_connect(str(_runtime)).close()       # a brand-new, empty memory
        STATUS["note"] = "new encrypted memory"
    try:
        os.chmod(_runtime, 0o600)
    except OSError:
        pass
    st = _runtime.stat()
    _last = None if not ENC.exists() else (st.st_size, st.st_mtime_ns)
    sqlite3.connect = _connect
    STATUS.update(encrypted=True, where="RAM" if in_ram else "a temporary file")
    if _readonly:                                  # a test run: its copy is thrown away at the end
        def _drop_test_copy():                     # fix 96: Windows may still hold it open; it's reused next time anyway
            try:
                if _runtime.exists():
                    _runtime.unlink()
            except OSError:
                pass
        atexit.register(_drop_test_copy)
    if not _readonly:
        threading.Thread(target=_saver, daemon=True).start()
        atexit.register(_cleanup)
        try:                                       # the Pi service stopping (SIGTERM) also saves first
            import signal
            if threading.current_thread() is threading.main_thread():
                signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        except (ImportError, ValueError, AttributeError):
            pass
    print(f"🔒 Memory: encrypted on disk (AES-256), working copy in {STATUS['where']}"
          + (f" ({STATUS['note']})" if STATUS["note"] and STATUS["note"] != "unlocked" else ""))
    return STATUS


def answer():
    """For 'is my memory encrypted?'"""
    if not STATUS["encrypted"]:
        return "My memory isn't encrypted yet."
    return ("Yes. Everything I save is encrypted with AES-256, and the key is tied to this device, so a copied file "
            "or a stolen memory card can't be read anywhere else.")


if __name__ == "__main__":
    if "--confirm" in sys.argv:
        if BACKUP.exists():
            BACKUP.unlink()
            print("Deleted the old unencrypted backup. Only the encrypted memory is left.")
        else:
            print("There's no unencrypted backup left.")
    else:
        print("Encrypted memory file:", "✅ " + ENC.name if ENC.exists() else "not created yet (start Jarvis once)")
        print("Old unencrypted file:", "⚠️ still there" if PLAIN.exists() else "✅ gone")
        print("Unencrypted backup:", ("⚠️ " + BACKUP.name + " (delete with --confirm once everything works)")
              if BACKUP.exists() else "✅ none")
        print("Key file:", "✅ " + str(KEY_FILE) if KEY_FILE.exists() else "not created yet")
        if ENC.exists():
            blob = ENC.read_bytes()
            print("What the file looks like:", blob[:48])
            try:
                decrypt(blob, _make_key())
                print("Opens on this device: ✅ yes")
            except Exception:
                print("Opens on this device: ❌ no (different device or key)")

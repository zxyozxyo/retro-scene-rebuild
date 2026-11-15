"""First-run check for RetroVault Toolkit. Run by "Install RetroVault.bat",
and safe to run again at any time: it only creates missing folders and
notes, and reports. It never deletes or overwrites anything.

    python first_run.py
"""
import importlib, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
APPS = HERE / "apps"

PACKAGES = [  # (import name, pip name, what needs it)
    ("webview", "pywebview", "every tool window"),
    ("py7zr", "py7zr", "7z archives"),
    ("rarfile", "rarfile", "RAR archives"),
    ("zstandard", "zstandard", "zstd data"),
    ("zipfile_zstd", "zipfile-zstd", "zstd zips"),
    ("Crypto", "pycryptodome", "3DS decryption"),
    ("rescene", "pyReScene", ".srr files"),
    ("requests", "requests", "Internet Archive tools"),
    ("internetarchive", "internetarchive", "IA uploader"),
    ("openpyxl", "openpyxl", "Excel export"),
]

# Folder -> what goes in it. A README.txt is written into each one that has none.
FOLDERS = {
    "apps": "Helper programs used by every tool.\n\n"
            "Put here:\n"
            "  7z.exe, 7za.exe   - 7-Zip console (https://www.7-zip.org, 'console version' / 7-Zip Extra)\n"
            "  UnRAR.exe         - free UnRAR for Windows (https://www.rarlab.com/rar_add.htm)\n"
            "  chdman.exe        - optional, CHD conversion (comes with MAME, https://www.mamedev.org)\n",
    "apps/winrar_pack-4.20": "Windows/console RAR builds, named YYYY-MM-DD_rarNNN.exe.\n\n"
            "Don't rename them by hand: put any WinRAR / RAR installers or rar.exe files you have\n"
            "in one folder and use RSR Scanner > Database > 'Import from folder...'.\n"
            "It identifies each build by its contents and files it here under the right name.\n"
            "'Check packers' lists what is still missing (the full list is in apps_manifest.json).\n",
    "apps/dosrar_pack": "DOS RAR builds (YYYY-MM-DD_dosrarNNN.exe). Filled the same way as winrar_pack-4.20:\n"
            "use RSR Scanner > Database > 'Import from folder...' on your RAR for DOS installers.\n",
    "apps/dosrar_pack/_dos32": "DOS32 helpers for the 32-bit DOS RAR builds: emx.exe, emx.dll, rsx.exe.\n",
    "apps/dosbox-x": "DOSBox-X, which runs the DOS RAR builds.\n"
            "Download the Windows portable zip from https://dosbox-x.com and unpack it here,\n"
            "so that apps/dosbox-x/dosbox-x.exe exists.\n",
    "apps/zip_pack/precomp/windows": "precomp, used to re-create ZIP releases.\n"
            "From https://github.com/schnaader/precomp-cpp/releases - put precomp.exe here.\n",
    "apps/romtools": "Optional programs for ROM Manipulation:\n"
            "  DolphinTool.exe - GameCube/Wii (comes with Dolphin, https://dolphin-emu.org)\n"
            "  nkit.exe        - NKit images\n"
            "  keys/           - console keys you own\n",
    "rclone": "Optional: rclone.exe (https://rclone.org) for the RClone IA Uploader.\n",
}


def check_packages():
    missing = []
    for mod, pip_name, why in PACKAGES:
        try:
            importlib.import_module(mod)
        except Exception:
            missing.append((pip_name, why))
    return missing


def make_folders():
    made = []
    for rel, text in FOLDERS.items():
        d = HERE / rel
        if not d.is_dir():
            d.mkdir(parents=True, exist_ok=True)
            made.append(rel)
        note = d / "README.txt"
        if not note.exists():
            note.write_text(text, encoding="utf-8")
    return made


def main():
    print()
    print("RetroVault Toolkit - first-run check")
    print("=" * 40)
    ok = True

    v = sys.version_info
    print(f"Python {v.major}.{v.minor}.{v.micro}", "OK" if v >= (3, 10) else "- too old, need 3.10 or newer")
    ok &= v >= (3, 10)

    miss = check_packages()
    if miss:
        ok = False
        print("Python packages MISSING:")
        for name, why in miss:
            print(f"   {name:16} ({why})")
        print("   Fix: run 'Install RetroVault.bat' again.")
    else:
        print(f"Python packages: all {len(PACKAGES)} installed")

    made = make_folders()
    if made:
        print("Created folders (each has a README.txt saying what goes in it):")
        for m in made:
            print("   " + m)

    print()
    print("Helper programs and packer builds")
    print("-" * 40)
    try:
        import apps_check
        res = apps_check.check(APPS)
        for title, r in res.items():
            miss_n = len(r["missing"]) + len(r["different"])
            if r["required"]:
                tag = "OK     " if not miss_n else "MISSING"
                print(f" {tag} {title}: {r['ok']} of {r['total']}")
            else:
                print(f" {'OK     ' if r['ok'] else 'NEEDED '} {title}: {'found' if r['ok'] else 'not found - see apps/README.txt'}")
        need = sum(len(r["missing"]) + len(r["different"]) for r in res.values() if r["required"])
    except Exception as e:
        need = None
        print(f" (could not check: {e})")

    print()
    print("What next")
    print("-" * 40)
    print(" 1. Start the suite with 'RetroVault.bat' (or the desktop shortcut).")
    if need:
        print(" 2. The RSR Scanner needs the RAR/DOS builds above to capture or rebuild")
        print("    every release. Collect the installers you can find in one folder and use")
        print("    RSR Scanner > Database > 'Import from folder...'. Each apps/ folder has a")
        print("    README.txt explaining what belongs there.")
    print(" The other tools don't need the RAR builds, only 7-Zip and UnRAR in apps/.")
    print()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

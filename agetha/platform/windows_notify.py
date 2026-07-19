"""
Windows toast notifications branded as Agetha (not PowerShell).

Requires a Start Menu .lnk with System.AppUserModel.ID = AGETHA_AUMID, then
ToastNotificationManager.CreateToastNotifier(AGETHA_AUMID). See:
https://learn.microsoft.com/windows/win32/shell/enable-desktop-toast-with-appusermodelid
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from agetha.app_config import BASE_DIR
from agetha.utils import ICON_PATH, IS_WINDOWS, logger

# Must match the AppUserModelID written on the Start Menu shortcut.
AGETHA_AUMID = "Agetha.Desktop"
_START_MENU_NAME = "Agetha.lnk"


def set_process_aumid(aumid: str = AGETHA_AUMID) -> bool:
    """Associate this process with Agetha for taskbar / toast identity."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        hr = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        if hr == 0:
            logger.info(f"Process AppUserModelID set: {aumid}")
            return True
        logger.warning(f"SetCurrentProcessExplicitAppUserModelID failed: HRESULT=0x{hr & 0xFFFFFFFF:08X}")
    except Exception as exc:
        logger.warning(f"Could not set process AUMID: {exc}")
    return False


def _launcher_paths() -> tuple[str, str, str]:
    """Return (target, arguments, working_directory) for the Start Menu shortcut."""
    root = BASE_DIR.resolve()
    bat = root / "Medic_Checker.bat"
    if bat.is_file():
        return str(bat), "", str(root)
    main_py = root / "main.py"
    py = sys.executable or "python"
    # Prefer pythonw when available so launching from Start has no console.
    if py.lower().endswith("python.exe"):
        pythonw = Path(py).with_name("pythonw.exe")
        if pythonw.is_file():
            py = str(pythonw)
    return py, f'"{main_py}"', str(root)


def start_menu_shortcut_path() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / _START_MENU_NAME


def _run_powershell_encoded(encoded: str, *, timeout: int) -> subprocess.CompletedProcess[str]:
    """Run encoded PowerShell without flashing/minimizing the parent console.

    ``powershell -WindowStyle Hidden`` is avoided: on Windows it often minimizes
    the caller's console (e.g. Medic_Checker) instead of only hiding the child.
    """
    kwargs: dict = {
        "args": [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-EncodedCommand",
            encoded,
        ],
        "capture_output": True,
        "text": True,
        "timeout": timeout,
        "shell": False,
    }
    if IS_WINDOWS:
        # CREATE_NO_WINDOW: no console for the child; does not touch the parent.
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return subprocess.run(**kwargs)


def ensure_start_menu_shortcut() -> bool:
    """
    Create/update %APPDATA%\\...\\Start Menu\\Programs\\Agetha.lnk with AGETHA_AUMID.
    Required for desktop WinRT toasts to appear as Agetha.
    """
    if not IS_WINDOWS:
        return False
    target, args, workdir = _launcher_paths()
    lnk = start_menu_shortcut_path()
    icon = str(ICON_PATH.resolve()) if ICON_PATH.is_file() else ""
    lnk.parent.mkdir(parents=True, exist_ok=True)

    # Escape for single-quoted PowerShell strings
    def _ps(s: str) -> str:
        return s.replace("'", "''")

    ps = f"""
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$lnkPath = '{_ps(str(lnk))}'
$target  = '{_ps(target)}'
$args    = '{_ps(args)}'
$workdir = '{_ps(workdir)}'
$icon    = '{_ps(icon)}'
$aumid   = '{_ps(AGETHA_AUMID)}'

if (Test-Path -LiteralPath $lnkPath) {{ Remove-Item -LiteralPath $lnkPath -Force }}

if (-not ('AumidWriterAgetha' -as [type])) {{
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("00021401-0000-0000-C000-000000000046")]
public class ShellLinkCoClassAgetha {{ }}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("000214F9-0000-0000-C000-000000000046")]
public interface IShellLinkWAgetha {{
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cchMaxPath, IntPtr pfd, int fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cchMaxName);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cchMaxPath);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cchMaxPath);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cchIconPath, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
    void Resolve(IntPtr hwnd, int fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("0000010b-0000-0000-C000-000000000046")]
public interface IPersistFileAgetha {{
    void GetClassID(out Guid pClassID);
    [PreserveSig] int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, uint dwMode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string pszFileName, [MarshalAs(UnmanagedType.Bool)] bool fRemember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string pszFileName);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string ppszFileName);
}}

[StructLayout(LayoutKind.Sequential, Pack = 4)]
public struct PROPERTYKEYAgetha {{
    public Guid fmtid;
    public UInt32 pid;
}}

[StructLayout(LayoutKind.Sequential)]
public struct PROPVARIANTAgetha {{
    public UInt16 vt;
    public UInt16 wReserved1;
    public UInt16 wReserved2;
    public UInt16 wReserved3;
    public IntPtr p;
}}

[ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
 Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99")]
public interface IPropertyStoreAgetha {{
    void GetCount(out UInt32 cProps);
    void GetAt(UInt32 iProp, out PROPERTYKEYAgetha pkey);
    void GetValue(ref PROPERTYKEYAgetha key, out PROPVARIANTAgetha pv);
    void SetValue(ref PROPERTYKEYAgetha key, ref PROPVARIANTAgetha pv);
    void Commit();
}}

public static class AumidWriterAgetha {{
    public static void Create(string lnkPath, string target, string arguments, string workdir, string icon, string aumid) {{
        var link = (IShellLinkWAgetha)new ShellLinkCoClassAgetha();
        link.SetPath(target);
        if (!string.IsNullOrEmpty(arguments)) link.SetArguments(arguments);
        if (!string.IsNullOrEmpty(workdir)) link.SetWorkingDirectory(workdir);
        link.SetDescription("Agetha AI Companion");
        if (!string.IsNullOrEmpty(icon)) link.SetIconLocation(icon, 0);
        var store = (IPropertyStoreAgetha)link;
        var key = new PROPERTYKEYAgetha {{
            fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"),
            pid = 5
        }};
        var pv = new PROPVARIANTAgetha();
        pv.vt = 31; // VT_LPWSTR
        pv.p = Marshal.StringToCoTaskMemUni(aumid);
        try {{
            store.SetValue(ref key, ref pv);
            store.Commit();
            var pf = (IPersistFileAgetha)link;
            pf.Save(lnkPath, true);
        }} finally {{
            if (pv.p != IntPtr.Zero) {{
                Marshal.FreeCoTaskMem(pv.p);
                pv.p = IntPtr.Zero;
            }}
        }}
    }}
}}
"@
}}

[AumidWriterAgetha]::Create($lnkPath, $target, $args, $workdir, $icon, $aumid)
Write-Output 'OK'
"""
    try:
        encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        proc = _run_powershell_encoded(encoded, timeout=45)
        out = (proc.stdout or "").strip()
        # Success if Apply printed OK, or shortcut exists after a benign re-run
        if lnk.is_file() and (proc.returncode == 0 or "OK" in out):
            if "OK" in out or proc.returncode == 0:
                logger.info(f"Start Menu shortcut ready: {lnk} (AUMID={AGETHA_AUMID})")
                return True
        err = (proc.stderr or out or f"exit {proc.returncode}").strip()
        # Strip CLIXML progress noise for logs
        if err.startswith("#< CLIXML"):
            err = f"exit {proc.returncode}; stdout={out[:120]!r}"
        logger.warning(f"Start Menu shortcut AUMID setup failed: {err[:300]}")
    except Exception as exc:
        logger.warning(f"Start Menu shortcut setup error: {exc}")
    return lnk.is_file()


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def show_toast(title: str, message: str) -> bool:
    """
    Show a WinRT toast under AGETHA_AUMID (appears as Agetha, not Windows PowerShell).
    Ensures the Start Menu shortcut exists first.
    """
    if not IS_WINDOWS:
        return False
    if not title and not message:
        return False
    ensure_start_menu_shortcut()

    title_x = _xml_escape((title or "Agetha").strip() or "Agetha")
    body_x = _xml_escape((message or "").strip())
    icon_uri = ""
    if ICON_PATH.is_file():
        # Toast appLogoOverride wants a file URI
        icon_uri = ICON_PATH.resolve().as_uri()

    image_xml = ""
    if icon_uri:
        image_xml = f"<image placement='appLogoOverride' src='{_xml_escape(icon_uri)}'/>"

    toast_xml = (
        "<toast><visual><binding template='ToastGeneric'>"
        f"<text>{title_x}</text><text>{body_x}</text>{image_xml}"
        "</binding></visual></toast>"
    )

    def _ps(s: str) -> str:
        return s.replace("'", "''")

    ps = f"""
$ErrorActionPreference = 'Stop'
$aumid = '{_ps(AGETHA_AUMID)}'
$xmlText = @'
{toast_xml}
'@
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($xmlText)
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($aumid).Show($toast)
Write-Output 'OK'
"""
    try:
        encoded = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
        proc = _run_powershell_encoded(encoded, timeout=30)
        if proc.returncode == 0 and "OK" in (proc.stdout or ""):
            logger.info("Toast shown via AUMID Agetha.Desktop")
            return True
        err = ((proc.stderr or proc.stdout) or f"exit {proc.returncode}").strip()
        logger.warning(f"AUMID toast failed: {err[:300]}")
    except Exception as exc:
        logger.warning(f"AUMID toast error: {exc}")
    return False

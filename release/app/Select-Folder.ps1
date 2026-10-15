param(
    [string]$Title = '选择要打开的文件夹',
    [string]$InitialPath = '',
    [long]$OwnerHandle = 0,
    [switch]$ShowFiles,
    [switch]$LoadOnly
)

# Shared by the desktop app, installer and updater. Windows PowerShell 5.1
# still uses the old tree picker, so use the modern Shell dialog directly.
if (-not ('MDReader.NativeFolderPicker' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;

namespace MDReader {
    [ComImport, Guid("DC1C5A9C-E88A-4DDE-A5A1-60F82A20AEF7")]
    internal class FileOpenDialog { }

    [ComImport, Guid("42F85136-DB7E-439C-85F1-E4075D135FC8"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IFileDialog {
        [PreserveSig] int Show(IntPtr owner);
        void SetFileTypes(uint count, IntPtr filters);
        void SetFileTypeIndex(uint index);
        void GetFileTypeIndex(out uint index);
        void Advise(IntPtr events, out uint cookie);
        void Unadvise(uint cookie);
        void SetOptions(uint options);
        void GetOptions(out uint options);
        void SetDefaultFolder(IShellItem item);
        void SetFolder(IShellItem item);
        void GetFolder(out IShellItem item);
        void GetCurrentSelection(out IShellItem item);
        void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
        void GetFileName(out IntPtr name);
        void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
        void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetResult(out IShellItem item);
        void AddPlace(IShellItem item, uint location);
        void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string extension);
        void Close(int result);
        void SetClientGuid(ref Guid guid);
        void ClearClientData();
        void SetFilter(IntPtr filter);
    }

    [ComImport, Guid("43826D1E-E718-42EE-BC55-A1E261C37BFE"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellItem {
        void BindToHandler(IntPtr context, ref Guid handler, ref Guid iid, out IntPtr result);
        void GetParent(out IShellItem item);
        void GetDisplayName(uint kind, out IntPtr name);
        void GetAttributes(uint mask, out uint attributes);
        void Compare(IShellItem item, uint hint, out int order);
    }

    public static class NativeFolderPicker {
        [DllImport("shell32.dll", CharSet = CharSet.Unicode, PreserveSig = false)]
        private static extern void SHCreateItemFromParsingName(
            string path, IntPtr context, ref Guid iid, out IShellItem item);
        [DllImport("user32.dll")]
        private static extern IntPtr SetThreadDpiAwarenessContext(IntPtr context);
        [DllImport("shcore.dll")]
        private static extern int SetProcessDpiAwareness(int awareness);
        [DllImport("user32.dll")]
        private static extern bool SetProcessDPIAware();
        [DllImport("kernel32.dll")]
        private static extern ushort GetThreadUILanguage();
        [DllImport("kernel32.dll")]
        private static extern ushort SetThreadUILanguage(ushort language);

        public static IntPtr EnterDpiContext() {
            try {
                IntPtr previous = SetThreadDpiAwarenessContext(new IntPtr(-4));
                if (previous != IntPtr.Zero) return previous;
            } catch (EntryPointNotFoundException) { }
            try { SetProcessDpiAwareness(2); }
            catch (DllNotFoundException) { SetProcessDPIAware(); }
            catch (EntryPointNotFoundException) { SetProcessDPIAware(); }
            return IntPtr.Zero;
        }

        public static void RestoreDpiContext(IntPtr previous) {
            if (previous != IntPtr.Zero) SetThreadDpiAwarenessContext(previous);
        }

        public static string ExistingDirectory(string path) {
            if (String.IsNullOrWhiteSpace(path) || !Path.IsPathRooted(path)) return null;
            try {
                path = Path.GetFullPath(path);
                while (!Directory.Exists(path)) {
                    DirectoryInfo parent = Directory.GetParent(path);
                    if (parent == null) return null;
                    path = parent.FullName;
                }
                return path;
            } catch (ArgumentException) { return null; }
            catch (NotSupportedException) { return null; }
            catch (PathTooLongException) { return null; }
        }

        public static string Pick(string title, string initialPath, long ownerHandle) {
            return Pick(title, initialPath, ownerHandle, false);
        }

        public static uint DialogOptions(uint options, bool showFiles) {
            // The open-folder browser shows files for context but still returns
            // the current directory. The installer keeps folder-only selection.
            options |= 0x40u | 0x800u | 0x8u; // FORCEFILESYSTEM | PATHMUSTEXIST | NOCHANGEDIR
            if (showFiles) return (options | 0x2000000u) & ~(0x20u | 0x1000u | 0x200u | 0x2000u);
            return options | 0x20u; // PICKFOLDERS
        }

        public static string Pick(string title, string initialPath, long ownerHandle, bool showFiles) {
            IntPtr previous = EnterDpiContext();
            ushort language = GetThreadUILanguage();
            IFileDialog dialog = null;
            IShellItem initial = null, result = null;
            IntPtr name = IntPtr.Zero;
            try {
                // Match the application's Chinese UI even in an English
                // PowerShell host; this affects this dialog's thread only.
                SetThreadUILanguage(0x0804);
                dialog = (IFileDialog)new FileOpenDialog();
                uint options;
                dialog.GetOptions(out options);
                dialog.SetOptions(DialogOptions(options, showFiles));
                dialog.SetTitle(title);
                dialog.SetOkButtonLabel(showFiles ? "打开当前文件夹" : "选择文件夹");
                dialog.SetFileNameLabel(showFiles ? "浏览文件：" : "文件夹：");
                // A default name also lets an empty directory be confirmed in
                // file-browsing mode, without creating or opening any file.
                if (showFiles) dialog.SetFileName("选择当前文件夹");
                string directory = ExistingDirectory(initialPath);
                if (directory != null) {
                    Guid iid = typeof(IShellItem).GUID;
                    SHCreateItemFromParsingName(directory, IntPtr.Zero, ref iid, out initial);
                    dialog.SetFolder(initial);
                }
                int hr = dialog.Show(new IntPtr(ownerHandle));
                if (hr == unchecked((int)0x800704C7)) return null;
                Marshal.ThrowExceptionForHR(hr);
                dialog.GetResult(out result);
                result.GetDisplayName(0x80058000u, out name); // SIGDN_FILESYSPATH
                string path = Marshal.PtrToStringUni(name);
                // Resolve the accepted item rather than GetFolder: a search
                // view is virtual, and an absolute filename can name a file in
                // a different directory from the currently displayed folder.
                if (showFiles) return Directory.Exists(path) ? path : Path.GetDirectoryName(path);
                return path;
            } finally {
                try {
                    if (name != IntPtr.Zero) Marshal.FreeCoTaskMem(name);
                    if (result != null) Marshal.ReleaseComObject(result);
                    if (initial != null) Marshal.ReleaseComObject(initial);
                    if (dialog != null) Marshal.ReleaseComObject(dialog);
                } finally {
                    SetThreadUILanguage(language);
                    RestoreDpiContext(previous);
                }
            }
        }
    }
}
'@
}

function Select-MDReaderFolder {
    param([string]$Title = '选择要打开的文件夹', [string]$InitialPath = '', [long]$OwnerHandle = 0,
          [switch]$ShowFiles)
    [MDReader.NativeFolderPicker]::Pick($Title, $InitialPath, $OwnerHandle, $ShowFiles.IsPresent)
}

if (-not $LoadOnly) {
    Select-MDReaderFolder -Title $Title -InitialPath $InitialPath -OwnerHandle $OwnerHandle -ShowFiles:$ShowFiles
}

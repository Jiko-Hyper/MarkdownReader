/* Native, quiet entry for the existing install/update dialogs. */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>
#define CAP 32768
static wchar_t command[CAP];
static BOOL argument(const wchar_t *value) {
    size_t used = wcslen(command);
    if (used + 3 >= CAP) return FALSE;
    if (used) command[used++] = L' ';
    command[used++] = L'"';
    for (;;) {
        size_t slashes = 0;
        while (*value == L'\\') { ++slashes; ++value; }
        size_t count = (*value == L'"' || !*value) ? slashes * 2 : slashes;
        if (used + count + 3 >= CAP) return FALSE;
        while (count--) command[used++] = L'\\';
        if (!*value) break;
        if (*value == L'"') command[used++] = L'\\';
        command[used++] = *value++;
    }
    command[used++] = L'"'; command[used] = 0;
    return TRUE;
}
static BOOL exists(const wchar_t *path) {
    DWORD flags = GetFileAttributesW(path);
    return flags != INVALID_FILE_ATTRIBUTES && !(flags & FILE_ATTRIBUTE_DIRECTORY);
}
int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR tail, int show) {
    (void)instance; (void)previous; (void)tail; (void)show;
    wchar_t folder[CAP], script[CAP], powershell[CAP];
    BOOL update = FALSE, quiet = FALSE;
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 1;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--update")) update = TRUE;
        if (!wcscmp(argv[i], L"--no-ui")) quiet = TRUE;
    }
    DWORD length = GetModuleFileNameW(NULL, folder, CAP);
    if (!length || length >= CAP || !wcsrchr(folder, L'\\')) goto failure;
    *wcsrchr(folder, L'\\') = 0;
    if (_snwprintf(script, CAP, L"%s\\resources\\setup\\%s", folder,
                  update ? L"更新程序.ps1" : L"安装到桌面.ps1") < 0) goto failure;
    if (!exists(script) && _snwprintf(script, CAP, L"%s\\resources\\%s", folder,
                                    update ? L"更新程序.ps1" : L"安装到桌面.ps1") < 0) goto failure;
    if (!exists(script)) goto failure;
    length = GetSystemDirectoryW(powershell, CAP);
    if (!length || length + 48 >= CAP) goto failure;
    wcscat(powershell, L"\\WindowsPowerShell\\v1.0\\powershell.exe");
    if (!argument(powershell) || !argument(L"-NoLogo") || !argument(L"-NoProfile") || !argument(L"-STA") ||
        !argument(L"-ExecutionPolicy") || !argument(L"Bypass") || !argument(L"-File") ||
        !argument(script) || !argument(L"-NoPause")) goto failure;
    if (!quiet && !argument(L"-Gui")) goto failure;
    for (int i = 1; i < argc; ++i) {
        const wchar_t *flag = NULL;
        if (!wcscmp(argv[i], L"--update")) continue;
        if (!wcscmp(argv[i], L"--no-ui")) { if (!update && !argument(L"-NoUI")) goto failure; continue; }
        if (!update && !wcscmp(argv[i], L"--no-shortcut")) flag = L"-NoShortcut";
        else if (!update && !wcscmp(argv[i], L"--desktop-only")) flag = L"-DesktopOnly";
        else if (!update && !wcscmp(argv[i], L"--associate")) flag = L"-Associate";
        else if (!update && !wcscmp(argv[i], L"--set-default")) flag = L"-SetDefault";
        if (flag) { if (!argument(flag)) goto failure; continue; }
        if (!wcscmp(argv[i], L"--install-dir")) flag = L"-InstallDir";
        else if (update && !wcscmp(argv[i], L"--archive")) flag = L"-Archive";
        else if (!update && !wcscmp(argv[i], L"--workspace-dir")) flag = L"-WorkspaceDir";
        else if (!update && !wcscmp(argv[i], L"--shortcut-dir")) flag = L"-ShortcutDir";
        else if (!update && !wcscmp(argv[i], L"--result-file")) flag = L"-ResultFile";
        if (!flag || ++i >= argc || !argument(flag) || !argument(argv[i])) goto failure;
    }
    STARTUPINFOW startup = {0}; PROCESS_INFORMATION process = {0};
    /* CREATE_NO_WINDOW suppresses the console. SW_HIDE also hides the first
       WinForms dialog, leaving an invisible installer waiting for input. */
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = quiet ? SW_HIDE : SW_SHOWNORMAL;
    if (!CreateProcessW(powershell, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, folder, &startup, &process)) goto failure;
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD status = 1; GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hThread); CloseHandle(process.hProcess); LocalFree(argv);
    return (int)status;
failure:
    if (!quiet) MessageBoxW(NULL, L"无法启动安装。请完整解压程序包，并保留安装.exe 旁的 resources 文件夹。",
                           L"安装 MDReader", MB_OK | MB_ICONERROR);
    LocalFree(argv); return 1;
}

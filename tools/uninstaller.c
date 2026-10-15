/* Small native entry point. Run the verified uninstall script outside the
 * installation so the executable, runtime and selected data can all be removed. */
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <wchar.h>

#define CAP 32768
static wchar_t command[CAP];

static BOOL exists(const wchar_t *path) {
    DWORD flags = GetFileAttributesW(path);
    return flags != INVALID_FILE_ATTRIBUTES && !(flags & FILE_ATTRIBUTE_DIRECTORY);
}

/* Windows command-line quoting, including trailing backslashes. */
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

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, LPWSTR tail, int show) {
    (void)instance; (void)previous; (void)tail; (void)show;
    wchar_t original[CAP], payload[CAP], source[CAP], record[CAP], temporary[CAP], folder[CAP];
    wchar_t script[CAP] = {0}, copied[CAP] = {0}, powershell[CAP], pid[32];
    BOOL yes = FALSE, created = FALSE;
    const wchar_t *result = NULL;
    int argc = 0;
    LPWSTR *argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) return 1;
    for (int i = 1; i < argc; ++i) {
        if (!wcscmp(argv[i], L"--yes")) yes = TRUE;
        else if (!wcscmp(argv[i], L"--result-file") && i + 1 < argc) result = argv[++i];
        else goto failure;
    }
    DWORD length = GetModuleFileNameW(NULL, original, CAP);
    if (!length || length >= CAP || !wcsrchr(original, L'\\')) goto failure;
    *wcsrchr(original, L'\\') = 0;
    if (_snwprintf(payload, CAP, L"%s\\resources", original) < 0) goto failure;
    if (_snwprintf(source, CAP, L"%s\\setup\\卸载.ps1", payload) < 0) goto failure;
    if (!exists(source)) {
        if (_snwprintf(source, CAP, L"%s\\卸载.ps1", payload) < 0) goto failure;
        if (!exists(source)) {
            wcscpy(payload, original);
            if (_snwprintf(source, CAP, L"%s\\卸载.ps1", payload) < 0) goto failure;
        }
    }
    if (!exists(source)) goto failure;
    if (_snwprintf(record, CAP, L"%s\\installation.json", payload) < 0) goto failure;
    if (!exists(record)) {
        if (_snwprintf(record, CAP, L"%s\\installed-target.json", payload) < 0 || !exists(record)) goto failure;
    }
    length = GetTempPathW(CAP, temporary);
    if (!length || length >= CAP) goto failure;
    if (_snwprintf(folder, CAP, L"%sMDReader-uninstall-%08lx-%08lx", temporary,
                  GetCurrentProcessId(), GetTickCount()) < 0 || !CreateDirectoryW(folder, NULL)) goto failure;
    created = TRUE;
    if (_snwprintf(script, CAP, L"%s\\runner.ps1", folder) < 0 ||
        _snwprintf(copied, CAP, L"%s\\installation.json", folder) < 0) goto failure;
    if (!CopyFileW(source, script, TRUE) || !CopyFileW(record, copied, TRUE)) goto failure;
    length = GetSystemDirectoryW(powershell, CAP);
    if (!length || length + 48 >= CAP) goto failure;
    wcscat(powershell, L"\\WindowsPowerShell\\v1.0\\powershell.exe");
    command[0] = 0;
    _snwprintf(pid, 32, L"%lu", GetCurrentProcessId());
    if (!argument(powershell) || !argument(L"-NoLogo") || !argument(L"-NoProfile") || !argument(L"-STA") ||
        !argument(L"-ExecutionPolicy") || !argument(L"Bypass") || !argument(L"-File") ||
        !argument(script) || !argument(L"-NoPause") || !argument(L"-TemporaryRunner") ||
        !argument(L"-LauncherProcessId") || !argument(pid)) goto failure;
    if (yes) {
        if (!argument(L"-Yes") || !argument(L"-DeleteData")) goto failure;
    } else if (!argument(L"-Gui")) goto failure;
    if (result && (!argument(L"-ResultFile") || !argument(result))) goto failure;
    STARTUPINFOW startup = {0}; PROCESS_INFORMATION process = {0};
    /* Hide the console without hiding the confirmation/error dialog. */
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESHOWWINDOW;
    startup.wShowWindow = yes ? SW_HIDE : SW_SHOWNORMAL;
    if (!CreateProcessW(powershell, command, NULL, NULL, FALSE, CREATE_NO_WINDOW,
                        NULL, temporary, &startup, &process)) goto failure;
    CloseHandle(process.hThread); CloseHandle(process.hProcess); LocalFree(argv);
    return 0;
failure:
    if (created) { DeleteFileW(script); DeleteFileW(copied); RemoveDirectoryW(folder); }
    MessageBoxW(NULL, L"无法启动卸载。请从已安装目录或完成安装的解压包运行卸载.exe，\n并保留旁边的 resources 文件夹。",
                L"MDReader 完整卸载", MB_OK | MB_ICONERROR);
    LocalFree(argv);
    return 1;
}

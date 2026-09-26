/*
 * MDReader.exe - 免安装版启动器
 *
 * 只做一件事：把命令行原样交给 runtime\pythonw.exe main.py。
 * 之所以要有这个原生小壳：
 *   - 用户拿到的是一个能双击、能固定到任务栏、能拖放 .md 的 exe；
 *   - 图标与版本信息写在 exe 资源里，资源管理器里看起来是正经程序；
 *   - 源码保持原样，pythonw.exe 直接跑仓库里的 mdreader 包，
 *     插件工作进程、WebView2、原生桥的路径规则全都跟源码运行一致。
 *
 * 构建：tools/build_launcher.ps1（gcc + windres），源码不到 100 行。
 */

#include <windows.h>
#include <shellapi.h>
#include <stdio.h>

static void fail(const wchar_t *message)
{
    MessageBoxW(NULL, message, L"MDReader", MB_ICONERROR | MB_OK);
}

/* 取自身所在目录，末尾带反斜杠 */
static void self_dir(wchar_t *out, DWORD size)
{
    DWORD n = GetModuleFileNameW(NULL, out, size);
    if (n == 0 || n >= size) {
        out[0] = L'\0';
        return;
    }
    wchar_t *slash = wcsrchr(out, L'\\');
    if (slash) {
        slash[1] = L'\0';
    }
}

static BOOL file_exists(const wchar_t *path)
{
    DWORD attr = GetFileAttributesW(path);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

/* 排查用：把这次实际执行的命令行写到 exe 旁边的 MDReader.log。
   只在 --console 时调用，正常双击不会产生任何文件。 */
static void log_command(const wchar_t *dir, const wchar_t *text)
{
    wchar_t path[MAX_PATH * 4];
    _snwprintf(path, ARRAYSIZE(path), L"%sMDReader.log", dir);
    FILE *fp = NULL;
    if (_wfopen_s(&fp, path, L"a, ccs=UTF-8") != 0 || !fp) {
        return;
    }
    fwprintf(fp, L"%s\n", text);
    fclose(fp);
}

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR command_line, int show)
{
    (void)instance;
    (void)previous;
    (void)command_line;
    (void)show;

    wchar_t dir[MAX_PATH * 2];
    self_dir(dir, ARRAYSIZE(dir));

    wchar_t python[MAX_PATH * 4];
    wchar_t script[MAX_PATH * 4];
    _snwprintf(python, ARRAYSIZE(python), L"%sruntime\\pythonw.exe", dir);
    _snwprintf(script, ARRAYSIZE(script), L"%smain.py", dir);

    /* 免安装版的 Tcl/Tk 库在 runtime\tcl 下，先告诉解释器；
       已经设过的环境变量不动，免得盖掉用户自己的配置。 */
    wchar_t tclDir[MAX_PATH * 4];
    wchar_t tkDir[MAX_PATH * 4];
    wchar_t tclInit[MAX_PATH * 8];
    _snwprintf(tclDir, ARRAYSIZE(tclDir), L"%sruntime\\tcl\\tcl8.6", dir);
    _snwprintf(tkDir, ARRAYSIZE(tkDir), L"%sruntime\\tcl\\tk8.6", dir);
    if (GetEnvironmentVariableW(L"TCL_LIBRARY", NULL, 0) == 0) {
        _snwprintf(tclInit, ARRAYSIZE(tclInit), L"%s\\init.tcl", tclDir);
        if (file_exists(tclInit)) {
            SetEnvironmentVariableW(L"TCL_LIBRARY", tclDir);
        }
    }
    if (GetEnvironmentVariableW(L"TK_LIBRARY", NULL, 0) == 0) {
        _snwprintf(tclInit, ARRAYSIZE(tclInit), L"%s\\init.tcl", tkDir);
        if (file_exists(tclInit)) {
            SetEnvironmentVariableW(L"TK_LIBRARY", tkDir);
        }
    }

    if (!file_exists(script)) {
        fail(L"找不到 main.py。\n\n请把 MDReader.exe 放在程序目录里（和 main.py、runtime\\ 同级），"
             L"再双击运行。");
        return 2;
    }
    if (!file_exists(python)) {
        _snwprintf(python, ARRAYSIZE(python), L"%sruntime\\python.exe", dir);
        if (!file_exists(python)) {
            fail(L"找不到 runtime\\pythonw.exe。\n\n这个免安装版需要和 runtime\\ 文件夹一起使用；"
                 L"如果你只复制了 exe，请把整个文件夹解压完整。");
            return 3;
        }
    }

    /* 组装解释器命令行：解释器 + 脚本 + 用户的原始参数 */
    wchar_t raw[MAX_PATH * 8];
    /* WinMain already receives the argument tail, without the executable. */
    wchar_t *extra = command_line ? command_line : L"";
    wcsncpy_s(raw, ARRAYSIZE(raw), extra, _TRUNCATE);

    /* --console：改用 python.exe，输出留在当前控制台里（排查启动问题时用）。
       这个开关只属于启动器，交给程序前要去掉，免得被当成参数解析。 */
    BOOL console_mode = FALSE;
    if (wcsncmp(raw, L"--console", 9) == 0 &&
        (raw[9] == L'\0' || raw[9] == L' ' || raw[9] == L'\t')) {
        wchar_t consolePython[MAX_PATH * 4];
        _snwprintf(consolePython, ARRAYSIZE(consolePython), L"%sruntime\\python.exe", dir);
        if (file_exists(consolePython)) {
            wcscpy_s(python, ARRAYSIZE(python), consolePython);
            console_mode = TRUE;
        }
        memmove(raw, raw + 9, (wcslen(raw + 9) + 1) * sizeof(wchar_t));
    }
    extra = raw;

    size_t needed = wcslen(python) + wcslen(script) + wcslen(extra) + 16;
    wchar_t *cmd = (wchar_t *)HeapAlloc(GetProcessHeap(), 0, needed * sizeof(wchar_t));
    if (!cmd) {
        fail(L"内存不足，无法启动 MDReader。");
        return 4;
    }
    _snwprintf(cmd, needed, L"\"%s\" \"%s\" %s", python, script, extra);

    SetCurrentDirectoryW(dir);

    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    ZeroMemory(&si, sizeof(si));
    ZeroMemory(&pi, sizeof(pi));
    si.cb = sizeof(si);
    /* 不显示控制台；pythonw.exe 本来也不会开窗口 */
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;

    DWORD creation = console_mode ? 0 : CREATE_NO_WINDOW;
    if (console_mode) {
        log_command(dir, cmd);
    }
    if (!CreateProcessW(NULL, cmd, NULL, NULL, TRUE, creation, NULL, dir, &si, &pi)) {
        wchar_t message[1024];
        _snwprintf(message, ARRAYSIZE(message),
                   L"启动 MDReader 失败（错误码 %lu）。\n\n命令行：\n%s", GetLastError(), cmd);
        fail(message);
        HeapFree(GetProcessHeap(), 0, cmd);
        return 5;
    }

    if (console_mode) {
        /* 排查模式：等程序结束，控制台里能看到它打印的东西 */
        WaitForSingleObject(pi.hProcess, INFINITE);
        DWORD code = 0;
        GetExitCodeProcess(pi.hProcess, &code);
        CloseHandle(pi.hThread);
        CloseHandle(pi.hProcess);
        HeapFree(GetProcessHeap(), 0, cmd);
        return (int)code;
    }

    /* 启动器立刻退出，MDReader 自己的窗口接管前台 */
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    HeapFree(GetProcessHeap(), 0, cmd);
    return 0;
}

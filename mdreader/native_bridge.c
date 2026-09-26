/* Window subclassing must never cross a ctypes -> Python callback while Tk
 * is servicing native events. This module owns no Python/Tcl references.
 * All exports and callbacks run on the owning GUI thread. */
#define UNICODE
#define _UNICODE
#include <windows.h>
#include <commctrl.h>
#include <imm.h>
#include <shellapi.h>
#include <stdlib.h>
#define EXPORT __declspec(dllexport)
#define LIMIT 32768
#define KEY L"MDReader.NativeBridge.1"
typedef struct State {
    int active, drops, cursor, length;
    unsigned long revision, messages;
    WCHAR composition[LIMIT];
    HDROP drop;
} State;
static State *state(HWND h) { return (State *)GetPropW(h, KEY); }
static void clear(State *s) {
    s->composition[0] = 0; s->length = s->cursor = 0; s->revision++;
}
static LRESULT CALLBACK bridge(HWND h, UINT m, WPARAM w, LPARAM l, UINT_PTR id, DWORD_PTR ref) {
    State *s = (State *)ref;
    if (m == WM_NCDESTROY) {
        RemoveWindowSubclass(h, bridge, id); RemovePropW(h, KEY);
        if (s->drop) DragFinish(s->drop);
        free(s);
        return DefSubclassProc(h, m, w, l);
    }
    if (m == WM_DROPFILES && s->drops) {
        if (s->drop) DragFinish(s->drop);
        s->drop = (HDROP)w;
        return 0;
    }
    if (m == WM_KILLFOCUS || m == WM_IME_ENDCOMPOSITION) clear(s);
    if (!s->active) return DefSubclassProc(h, m, w, l);
    if (m == WM_IME_SETCONTEXT) l &= ~((LPARAM)ISC_SHOWUICOMPOSITIONWINDOW);
    if (m == WM_IME_STARTCOMPOSITION) { clear(s); s->messages++; return 0; }
    if (m == WM_IME_ENDCOMPOSITION) { s->messages++; return 0; }
    if (m == WM_IME_COMPOSITION) {
        s->messages++;
        if (l & GCS_RESULTSTR) clear(s);
        if (!(l & GCS_RESULTSTR) || (l & GCS_COMPSTR)) {
            HIMC im = ImmGetContext(h);
            if (im) {
                LONG bytes = ImmGetCompositionStringW(im, GCS_COMPSTR, NULL, 0);
                if (bytes >= 0 && bytes < (LIMIT-1)*2) {
                    LONG got = ImmGetCompositionStringW(im, GCS_COMPSTR, s->composition, bytes);
                    if (got >= 0) {
                        s->length = got / 2; s->composition[s->length] = 0;
                        s->cursor = ImmGetCompositionStringW(im, GCS_CURSORPOS, NULL, 0);
                        if (s->cursor < 0) s->cursor = s->length;
                        s->revision++;
                    }
                }
                ImmReleaseContext(h, im);
            }
        }
        if (!l) clear(s);
        /* Tk alone inserts the result string, including its undo/selection. */
        if (l & GCS_RESULTSTR) return DefSubclassProc(h, m, w, l);
        return 0;
    }
    return DefSubclassProc(h, m, w, l);
}
EXPORT int md_attach(HWND h) {
    if (state(h)) return 1;
    State *s = (State *)calloc(1, sizeof(State));
    if (!s) return 0;
    if (!SetPropW(h, KEY, s) || !SetWindowSubclass(h, bridge, 1, (DWORD_PTR)s)) {
        RemovePropW(h, KEY); free(s); return 0;
    }
    return 1;
}
EXPORT void md_detach(HWND h) {
    State *s = state(h); if (!s) return;
    DragAcceptFiles(h, FALSE);
    if (!RemoveWindowSubclass(h, bridge, 1)) return;
    RemovePropW(h, KEY);
    if (s->drop) DragFinish(s->drop);
    free(s);
}
EXPORT void md_active(HWND h, int active) {
    State *s = state(h); if (!s || s->active == active) return;
    s->active = active; clear(s);
    SendMessageW(h, WM_IME_SETCONTEXT, 1, 0xC000000F);
}
EXPORT unsigned long md_read(HWND h, WCHAR *buf, int cap, int *cursor, unsigned long *messages) {
    State *s = state(h); if (!s) return 0;
    lstrcpynW(buf, s->composition, cap);
    *cursor = s->cursor; *messages = s->messages;
    return s->revision;
}
EXPORT void md_drop_enable(HWND h) {
    State *s = state(h); if (!s) return; s->drops = 1; DragAcceptFiles(h, TRUE);
}
EXPORT int md_drop_count(HWND h) {
    State *s = state(h); return (s && s->drop) ? (int)DragQueryFileW(s->drop, 0xFFFFFFFF, NULL, 0) : 0;
}
EXPORT void md_drop_read(HWND h, int index, WCHAR *buf, int cap) {
    State *s = state(h); if (s && s->drop) DragQueryFileW(s->drop, index, buf, cap);
}
EXPORT void md_drop_clear(HWND h) {
    State *s = state(h); if (s && s->drop) { DragFinish(s->drop); s->drop = NULL; }
}

// SPDX-License-Identifier: Apache-2.0
// Experimental Windows adapter: only the fresh worker-owned VST editor and its
// file dialog receive input. No desktop keyboard/mouse, shell or caller Lua.

void kontakt_regular(const fs::path& path) {
    if (!path.is_absolute() || !fs::is_regular_file(path)) fail("KONTAKT_CONTENT_MISSING", "Expected a registered regular file");
    for (auto p = path; !p.empty(); p = p == p.root_path() ? fs::path{} : p.parent_path()) {
        if (fs::is_symlink(fs::symlink_status(p))) fail("KONTAKT_PATH_UNSUPPORTED", "Linked content paths are unsupported");
#ifdef _WIN32
        const DWORD attrs = GetFileAttributesW(p.c_str());
        if (attrs == INVALID_FILE_ATTRIBUTES || (attrs & FILE_ATTRIBUTE_REPARSE_POINT)) fail("KONTAKT_PATH_UNSUPPORTED", "Reparse content paths are unsupported");
#endif
    }
}

json validate_kontakt_request(const Options& options) {
    const auto root = fs::absolute(options.workspace).lexically_normal();
    if (!fs::is_directory(root) || fs::absolute(options.script).lexically_normal() != root / "prepare.json" ||
        fs::absolute(options.output).lexically_normal() != root / "prepared.vstpreset" || fs::exists(options.output))
        fail("INVALID_ARGUMENT", "Preparation requires a fresh managed workspace");
    kontakt_regular(fs::absolute(options.script));
    auto bytes = read_file(options.script, 16384, "INVALID_ARGUMENT");
    auto request = json::parse(bytes);
    if (!request.is_object() || request.size() != 4 || !request.contains("version") || !request["version"].is_number_integer() || request["version"] != 1 ||
        !request.contains("instrument") || !request["instrument"].is_string() || !request.contains("snapshot") ||
        !(request["snapshot"].is_string() || request["snapshot"].is_null()) || !request.contains("nonce") || !request["nonce"].is_string())
        fail("INVALID_ARGUMENT", "Invalid typed preparation request");
    auto nonce = request["nonce"].get<std::string>();
    if (nonce.size() != 32 || !std::all_of(nonce.begin(), nonce.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }))
        fail("INVALID_ARGUMENT", "Invalid preparation identity");
    for (const auto& key : {"instrument", "snapshot"}) if (!request[key].is_null()) {
        auto text = request[key].get<std::string>();
        if (text.empty() || text.size() > 8192 || std::any_of(text.begin(), text.end(), [](unsigned char c) { return c < 32; }))
            fail("INVALID_ARGUMENT", "Invalid content path");
        auto path = fs::path(std::u8string(text.begin(), text.end())); auto extension = utf8(path.extension());
        std::transform(extension.begin(), extension.end(), extension.begin(), [](unsigned char c) { return static_cast<char>(std::tolower(c)); });
        if (extension != (std::string(key) == "instrument" ? ".nki" : ".nksn")) fail("INVALID_ARGUMENT", "Invalid Kontakt content type");
        kontakt_regular(path);
    }
    return request;
}

#ifdef _WIN32
void kontakt_progress(const fs::path& root, const char* stage) {
    std::ofstream file(root / "prepare.progress", std::ios::binary | std::ios::trunc);
    file << stage;
}
class KontaktFrame final : public IPlugFrame {
public:
    HWND window{};
    DECLARE_FUNKNOWN_METHODS
    KontaktFrame() { FUNKNOWN_CTOR }
    ~KontaktFrame() { FUNKNOWN_DTOR }
    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* size) override {
        if (!window || !view || !size || size->getWidth() < 1 || size->getHeight() < 1 || size->getWidth() > 4096 || size->getHeight() > 4096) return kInvalidArgument;
        RECT r{0,0,size->getWidth(),size->getHeight()}; AdjustWindowRect(&r, WS_OVERLAPPEDWINDOW, FALSE);
        SetWindowPos(window, nullptr, 0, 0, r.right-r.left, r.bottom-r.top, SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
        return view->onSize(size);
    }
};
IMPLEMENT_FUNKNOWN_METHODS(KontaktFrame, IPlugFrame, IPlugFrame::iid)

// Some native editors decline IPlugView key callbacks and handle their HWND's
// normal message queue instead. Target only this worker's existing focused
// descendant, or its single visible direct plugin child. Never choose a control
// by title/class, broadcast to descendants, or attach another input queue.
bool kontakt_owned_child(HWND parent, HWND child) {
    DWORD process = 0;
    return child && IsWindow(child) && IsChild(parent, child) &&
        GetWindowThreadProcessId(child, &process) == GetCurrentThreadId() &&
        process == GetCurrentProcessId() && GetAncestor(child, GA_ROOTOWNER) == parent &&
        IsWindowVisible(child) && IsWindowEnabled(child);
}
HWND kontakt_key_target(HWND parent) {
    HWND focused = GetFocus();
    if (kontakt_owned_child(parent, focused)) return focused;
    HWND target = nullptr;
    for (HWND child = GetWindow(parent, GW_CHILD); child; child = GetWindow(child, GW_HWNDNEXT)) {
        if (!kontakt_owned_child(parent, child)) continue;
        if (target) return nullptr; // An ambiguous hierarchy is unsupported.
        target = child;
    }
    return target;
}

HWND kontakt_focus_child(HWND parent) {
    HWND target = kontakt_key_target(parent);
    if (!target) return nullptr;
    // IPlugView::onFocus is a plugin notification, not Win32 keyboard focus.
    // SetFocus stays on this worker/UI queue; no SetForegroundWindow or
    // AttachThreadInput. A native editor may redirect focus to its own child.
    if (GetFocus() != target) SetFocus(target);
    HWND focused = GetFocus();
    return kontakt_owned_child(parent, focused) ? focused : nullptr;
}

LRESULT CALLBACK kontakt_window(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_CLOSE) {
        auto* closed = reinterpret_cast<bool*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (closed) *closed = true;
        return 0;
    }
    if (message == WM_SETFOCUS && kontakt_focus_child(window)) return 0;
    return DefWindowProcW(window, message, wparam, lparam);
}

bool kontakt_post_f11(HWND parent) {
    HWND target = kontakt_focus_child(parent);
    if (!target) return false;
    const LPARAM down = 1 | (static_cast<LPARAM>(MapVirtualKeyW(VK_F11, MAPVK_VK_TO_VSC)) << 16);
    // Post both messages so any modal dialog in key-down runs on the UI thread
    // while the bounded dialog helper can submit the generated script.
    return PostMessageW(target, WM_KEYDOWN, VK_F11, down) &&
        PostMessageW(target, WM_KEYUP, VK_F11, down | (static_cast<LPARAM>(3) << 30));
}

// Called on a helper thread while Kontakt runs its modal Open dialog on the UI
// thread. The dialog must belong to our process/thread AND our editor window.
// Recognize only standard filename controls; never guess the first edit field.
struct KontaktDialog {
    HWND parent{}; DWORD thread{}; fs::path script;
    std::atomic<bool> submitted{false}, seen{false};
    HWND observed{};
    struct Controls {
        HWND filename{}, combo{}, filetype{}, open{}, cancel{};
        bool browser = false;
        static BOOL CALLBACK collect(HWND child, LPARAM data) {
            auto& c = *reinterpret_cast<Controls*>(data);
            wchar_t text[64]{}; GetClassNameW(child, text, 64); const std::wstring name(text);
            int id = GetDlgCtrlID(child);
            if (id == 1152 && name == L"Edit") c.filename = child;
            if (id == 1148 && (name == L"ComboBox" || name == L"ComboBoxEx32")) c.combo = child;
            if (id == 1136 && (name == L"ComboBox" || name == L"ComboBoxEx32")) c.filetype = child;
            if (name == L"Button" && id == IDOK) c.open = child;
            if (name == L"Button" && id == IDCANCEL) c.cancel = child;
            if (name == L"DUIViewWndClassName" || name == L"SHELLDLL_DefView" || name == L"DirectUIHWND") c.browser = true;
            return TRUE;
        }
    };
    static BOOL CALLBACK find(HWND dialog, LPARAM data) {
        auto& self = *reinterpret_cast<KontaktDialog*>(data);
        DWORD process = 0;
        if (GetWindowThreadProcessId(dialog, &process) != self.thread || process != GetCurrentProcessId() || !IsWindowVisible(dialog)) return TRUE;
        wchar_t name[64]{}; GetClassNameW(dialog, name, 64);
        if (std::wstring(name) != L"#32770" || GetAncestor(dialog, GA_ROOTOWNER) != self.parent) return TRUE;
        self.seen = true;
        kontakt_progress(self.script.parent_path(), "dialog_owned");
        // GetDlgItem searches only direct children. Modern Explorer common
        // dialogs nest native filename controls under a host pane. Identify
        // the fixed standard IDs/classes recursively, never by visible text.
        Controls controls; EnumChildWindows(dialog, Controls::collect, reinterpret_cast<LPARAM>(&controls));
        wchar_t folder[32768]{}; DWORD_PTR folder_size = 0;
        bool common_query = SendMessageTimeoutW(dialog, CDM_GETFOLDERPATH, 32768, reinterpret_cast<LPARAM>(folder), SMTO_ABORTIFHUNG | SMTO_BLOCK, 500, &folder_size) &&
            folder_size >= 2 && folder_size <= 32768;
        // A generic license/error dialog is not an Open dialog. If the legacy
        // query is unavailable, require the complete standard filename/type/
        // browser/Open/Cancel structure before writing anything.
        bool common_structure = controls.combo && controls.filetype && controls.browser && controls.open && controls.cancel;
        if (!common_query && !common_structure) {
            kontakt_progress(self.script.parent_path(), !controls.combo && !controls.filename ? "dialog_filename_missing" :
                !controls.filetype ? "dialog_type_missing" : !controls.browser ? "dialog_browser_missing" : "dialog_buttons_missing");
            return TRUE;
        }
        self.observed = dialog;
        kontakt_progress(self.script.parent_path(), "dialog_common");
        HWND filename = controls.filename;
        HWND combo = controls.combo;
        if (combo) {
            HWND edit = FindWindowExW(combo, nullptr, L"Edit", nullptr);
            if (!edit) {
                HWND inner = FindWindowExW(combo, nullptr, L"ComboBox", nullptr);
                if (inner) edit = FindWindowExW(inner, nullptr, L"Edit", nullptr);
            }
            if (edit) filename = edit;
        }
        if (!filename || !controls.open) return TRUE;
        GetClassNameW(filename, name, 64);
        if (std::wstring(name) != L"Edit") return TRUE;
        kontakt_progress(self.script.parent_path(), "filename_control");
        DWORD_PTR result = 0;
        if (!SendMessageTimeoutW(filename, WM_SETTEXT, 0, reinterpret_cast<LPARAM>(self.script.c_str()), SMTO_ABORTIFHUNG | SMTO_BLOCK, 500, &result) || !result) return TRUE;
        self.submitted = true;
        kontakt_progress(self.script.parent_path(), "file_submitted");
        PostMessageW(dialog, WM_COMMAND, MAKEWPARAM(IDOK, BN_CLICKED), reinterpret_cast<LPARAM>(controls.open));
        return FALSE;
    }
};
#endif

json prepare_kontakt(const Options& options, const json& request, PlugProvider& provider) {
#ifndef _WIN32
    fail("KONTAKT_PLATFORM_UNSUPPORTED", "Kontakt editor preparation currently requires interactive Windows");
#else
    if (provider.getClassInfo().vendor() != "Native Instruments" ||
        (provider.getClassInfo().name() != "Kontakt 7" && provider.getClassInfo().name() != "Kontakt 8"))
        fail("KONTAKT_INSTRUMENT_REQUIRED", "This adapter requires an explicitly selected Kontakt 7/8 class");
    HDESK desktop = OpenInputDesktop(0, FALSE, DESKTOP_READOBJECTS);
    if (!desktop) fail("KONTAKT_INTERACTIVE_DESKTOP_REQUIRED", "A user desktop is required for official plugin UI");
    CloseDesktop(desktop);
    script_deadline = Clock::now() + std::chrono::seconds(options.max_seconds);
    const auto root = fs::absolute(options.workspace), script = root / "load.lua", receipt = root / "receipt.txt";
    if (fs::exists(script) || fs::exists(receipt)) fail("INVALID_ARGUMENT", "Preparation workspace is not fresh");
    { std::ofstream file(script, std::ios::binary); file << cantabile::audio::kontakt::kontakt_lua(request, utf8(receipt)); if (!file) fail("WRITE_FAILED", "Cannot write generated loading script"); }
    initialize_for_control(options, provider);
    struct Editor {
        IPtr<IPlugView> view;
        IPtr<KontaktFrame> frame;
        bool attached = false, closed = false;
        ~Editor() { if (view) { if (attached) view->removed(); view->setFrame(nullptr); } if (frame && frame->window) DestroyWindow(frame->window); }
    } editor;
    editor.view = owned(provider.getControllerPtr()->createView(ViewType::kEditor));
    if (!editor.view || editor.view->isPlatformTypeSupported(kPlatformTypeHWND) != kResultTrue)
        fail("KONTAKT_EDITOR_UNSUPPORTED", "Kontakt has no compatible public editor view");
    editor.frame = owned(new KontaktFrame);
    WNDCLASSW klass{}; klass.lpfnWndProc = kontakt_window; klass.hInstance = GetModuleHandleW(nullptr); klass.lpszClassName = L"CantabileKontaktPreparation";
    if (!RegisterClassW(&klass) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) fail("KONTAKT_EDITOR_UNSUPPORTED", "Cannot create isolated editor");
    editor.frame->window = CreateWindowExW(0, klass.lpszClassName, L"Cantabile - Kontakt sound preparation", WS_OVERLAPPEDWINDOW,
        CW_USEDEFAULT, CW_USEDEFAULT, 1000, 750, nullptr, nullptr, klass.hInstance, nullptr);
    if (!editor.frame->window) fail("KONTAKT_EDITOR_UNSUPPORTED", "Cannot create isolated editor");
    SetWindowLongPtrW(editor.frame->window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(&editor.closed));
    editor.view->setFrame(editor.frame);
    if (editor.view->attached(editor.frame->window, kPlatformTypeHWND) != kResultOk) fail("KONTAKT_EDITOR_UNSUPPORTED", "Cannot attach editor");
    editor.attached = true;
    ViewRect size; if (editor.view->getSize(&size) == kResultOk) editor.frame->resizeView(editor.view, &size);
    ShowWindow(editor.frame->window, SW_SHOWNOACTIVATE);
    editor.view->onFocus(true);
    kontakt_progress(root, "editor_open");
    wait_until(Clock::now() + std::chrono::milliseconds(750));
    if (editor.closed) fail("KONTAKT_PREPARE_CANCELLED", "Preparation editor was closed");
    // Establish the native child focus before SDK dispatch as well. SDK-only
    // editors can still handle F11 without an available native child target.
    kontakt_focus_child(editor.frame->window);
    KontaktDialog dialog; dialog.parent = editor.frame->window; dialog.thread = GetCurrentThreadId(); dialog.script = script;
    std::atomic<bool> dialog_done{false};
    std::jthread submit([&](std::stop_token stop) {
        const auto dialog_deadline = std::min(*script_deadline, Clock::now() + std::chrono::seconds(20));
        while (!stop.stop_requested() && Clock::now() < dialog_deadline && !dialog.submitted) {
            EnumThreadWindows(dialog.thread, KontaktDialog::find, reinterpret_cast<LPARAM>(&dialog));
            std::this_thread::sleep_for(std::chrono::milliseconds(40));
        }
        if (!stop.stop_requested() && !dialog.submitted && dialog.observed && IsWindow(dialog.observed))
            PostMessageW(dialog.observed, WM_COMMAND, MAKEWPARAM(IDCANCEL, BN_CLICKED), reinterpret_cast<LPARAM>(GetDlgItem(dialog.observed, IDCANCEL)));
        dialog_done = true;
    });
    // Prefer the SDK callback. A declined callback does not establish that Lua
    // is disabled: native editors may handle their own window message queue.
    kontakt_progress(root, "shortcut_vst_sent");
    const auto key = editor.view->onKeyDown(0, KEY_F11, 0);
    editor.view->onKeyUp(0, KEY_F11, 0);
    if (key != kResultTrue && !dialog.submitted && !fs::exists(receipt)) {
        wait_until(Clock::now() + std::chrono::milliseconds(250));
        if (editor.closed) fail("KONTAKT_PREPARE_CANCELLED", "Preparation editor was closed");
        // Never issue a second shortcut after an SDK-opened dialog or receipt.
        if (!dialog.seen && !fs::exists(receipt)) {
            kontakt_progress(root, "shortcut_native_target");
            if (!kontakt_post_f11(editor.frame->window))
                fail("KONTAKT_EDITOR_UNSUPPORTED", "No unambiguous focused worker-owned native key target");
            kontakt_progress(root, "shortcut_native_posted");
        }
    }
    while (!fs::exists(receipt)) {
        if (editor.closed) fail("KONTAKT_PREPARE_CANCELLED", "Preparation editor was closed");
        if (dialog_done && !dialog.submitted) fail("KONTAKT_LUA_UNAVAILABLE", "Official Open dialog filename control was unavailable");
        if (Clock::now() >= *script_deadline) fail("KONTAKT_LUA_UNAVAILABLE", "No loading receipt; check Developer features, licensing and native file dialog support");
        wait_until(Clock::now() + std::chrono::milliseconds(30));
    }
    submit.request_stop();
    kontakt_progress(root, "lua_receipt");
    kontakt_regular(receipt);
    auto receipt_bytes = read_file(receipt, 64, "KONTAKT_LOAD_FAILED");
    const auto expected = request["nonce"].get<std::string>() + ":ok";
    const std::string received(receipt_bytes.begin(), receipt_bytes.end());
    if (received == request["nonce"].get<std::string>() + ":failed") fail("KONTAKT_LOAD_FAILED", "Official Lua load did not succeed");
    if (received != expected) fail("KONTAKT_RECEIPT_INVALID", "Loading did not produce a valid per-job receipt");
    wait_until(Clock::now() + std::chrono::milliseconds(750));
    auto state = snapshot_state(provider);
    kontakt_progress(root, "state_captured");
    WaveFile output(options.output); output.bytes(state.data(), state.size()); output.publish();
    return {{"ok", true}, {"schema_version", 1}, {"adapter", "kontakt_lua_editor"},
        {"class_id", provider.getClassInfo().ID().toString()}, {"instrument_count", 1}, {"audible_verified", false}};
#endif
}

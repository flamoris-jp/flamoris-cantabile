// SPDX-License-Identifier: Apache-2.0
// ORIGINAL SYNTHETIC SDK/UI CONTRACT FIXTURE. NOT NATIVE INSTRUMENTS CODE.
// The deliberate Kontakt routing metadata exercises the production adapter;
// this plugin is built only for CI and is never installed/packaged. It does NOT
// run Lua or prove that commercial Kontakt exposes the same editor behavior.
#include "public.sdk/source/vst/vstaudioeffect.h"
#include "public.sdk/source/vst/vsteditcontroller.h"
#include "public.sdk/source/main/pluginfactory.h"
#include "public.sdk/source/common/pluginview.h"
#include "base/source/fstreamer.h"
#include "pluginterfaces/base/keycodes.h"
#include <algorithm>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <string>
#include <vector>
#define NOMINMAX
#include <windows.h>
#include <commdlg.h>
using namespace Steinberg;
using namespace Steinberg::Vst;
namespace fs = std::filesystem;
namespace {
const FUID processor_id(0x9A346635,0xFDD54848,0xBD23AAD3,0x4A726617);
const FUID controller_id(0x1334AF48,0xDDC84387,0x87D99A8B,0x88D94750);
class Processor final : public AudioEffect {
    int32 loaded = 0;
public:
    Processor() { setControllerClass(controller_id); }
    static FUnknown* create(void*) { return static_cast<IAudioProcessor*>(new Processor); }
    tresult PLUGIN_API initialize(FUnknown* context) override {
        auto result = AudioEffect::initialize(context);
        if (result == kResultOk) { addAudioOutput(STR16("Silent synthetic fixture"), SpeakerArr::kStereo); addEventInput(STR16("Input")); }
        return result;
    }
    tresult PLUGIN_API getState(IBStream* stream) override { IBStreamer s(stream,kLittleEndian); return s.writeInt32(loaded) ? kResultOk : kResultFalse; }
    tresult PLUGIN_API setState(IBStream* stream) override { IBStreamer s(stream,kLittleEndian); return s.readInt32(loaded) ? kResultOk : kResultFalse; }
    tresult PLUGIN_API notify(IMessage* message) override { if (std::string(message->getMessageID()) == "synthetic-loaded") { loaded = 1; return kResultOk; } return AudioEffect::notify(message); }
    tresult PLUGIN_API process(ProcessData&) override { return kResultOk; }
};
class Controller;
class Editor final : public CPluginView {
    Controller& controller;
    HWND child{}, extra{}, nested{};
    bool opened = false;
    std::string mode;
    void run_lua_dialog();
    static LRESULT CALLBACK native_window(HWND, UINT, WPARAM, LPARAM);
public:
    explicit Editor(Controller& c) : controller(c) {
        rect = {0,0,400,200};
        const char* value = std::getenv("CANTABILE_FIXTURE_KONTAKT_KEYS");
        mode = value ? value : "native-focus";
    }
    tresult PLUGIN_API isPlatformTypeSupported(FIDString type) override { return std::string(type) == kPlatformTypeHWND ? kResultTrue : kResultFalse; }
    tresult PLUGIN_API attached(void* parent, FIDString type) override;
    tresult PLUGIN_API removed() override;
    tresult PLUGIN_API onKeyDown(char16, int16 key, int16) override;
};
class Controller final : public EditController {
public:
    static FUnknown* create(void*) { return static_cast<IEditController*>(new Controller); }
    IPlugView* PLUGIN_API createView(FIDString name) override { return std::string(name) == ViewType::kEditor ? new Editor(*this) : nullptr; }
    void loaded() { auto message = owned(allocateMessage()); message->setMessageID("synthetic-loaded"); sendMessage(message); }
};
std::string read(const fs::path& path) { std::ifstream f(path, std::ios::binary); return {std::istreambuf_iterator<char>(f),{}}; }
// Decode only fixed-width generated decimal strings. Avoid recursive regex
// implementations whose stack limits depend on a Windows temp path's length.
std::vector<std::string> literals(const std::string& source) {
    std::vector<std::string> values;
    for (size_t pos=0; pos<source.size(); ++pos) {
        if (source[pos] != '"') continue;
        std::string value; size_t i=pos+1;
        while (i+3<source.size() && source[i]=='\\' && source[i+1]>='0' && source[i+1]<='9' &&
               source[i+2]>='0' && source[i+2]<='9' && source[i+3]>='0' && source[i+3]<='9') {
            int byte=(source[i+1]-'0')*100+(source[i+2]-'0')*10+(source[i+3]-'0');
            if (byte>255) throw 1;
            value+=static_cast<char>(byte); i+=4;
        }
        if (!value.empty() && i<source.size() && source[i]=='"') { values.push_back(value); pos=i; }
    }
    return values;
}
tresult Editor::attached(void* parent, FIDString type) {
    auto result = CPluginView::attached(parent, type);
    if (result != kResultOk) return result;
    if (mode == "missing") return kResultOk;
    WNDCLASSW klass{}; klass.lpfnWndProc = native_window;
    klass.hInstance = GetModuleHandleW(nullptr); klass.lpszClassName = L"CantabileSyntheticKontaktChild";
    if (!RegisterClassW(&klass) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS) return kResultFalse;
    child = CreateWindowExW(0, klass.lpszClassName, L"", WS_CHILD | WS_VISIBLE,
        0, 0, 400, 200, static_cast<HWND>(parent), nullptr, klass.hInstance, this);
    if (mode == "native-nested-focus") nested = CreateWindowExW(0, klass.lpszClassName, L"", WS_CHILD | WS_VISIBLE,
        0, 0, 400, 200, child, nullptr, klass.hInstance, this);
    if (mode == "ambiguous") extra = CreateWindowExW(0, L"STATIC", L"", WS_CHILD | WS_VISIBLE,
        0, 0, 10, 10, static_cast<HWND>(parent), nullptr, klass.hInstance, nullptr);
    return child ? kResultOk : kResultFalse;
}
tresult Editor::removed() {
    if (extra) DestroyWindow(extra);
    if (child) DestroyWindow(child);
    child = extra = nested = nullptr;
    return CPluginView::removed();
}
LRESULT CALLBACK Editor::native_window(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
    }
    auto* editor = reinterpret_cast<Editor*>(GetWindowLongPtrW(window, GWLP_USERDATA));
    if (message == WM_SETFOCUS && editor && window == editor->child && editor->nested) {
        SetFocus(editor->nested);
        return 0;
    }
    if (message == WM_KEYDOWN && wparam == VK_F11 && editor && editor->mode != "none") {
        if (editor->mode.find("focus") != std::string::npos && GetFocus() != window) {
            std::cerr << "synthetic_f11_unfocused=true\n";
            return 0;
        }
        std::cerr << "synthetic_native_f11=true\n";
        editor->run_lua_dialog();
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
tresult Editor::onKeyDown(char16, int16 key, int16) {
    if (key != KEY_F11) return kResultFalse;
    if (mode == "native-parent-focus") {
        SetFocus(static_cast<HWND>(systemWindow));
        std::cerr << "synthetic_parent_focus_forwarded=" << (GetFocus() == child ? "true" : "false") << '\n';
        return kResultFalse;
    }
    if (mode != "sdk" && mode != "sdk-false" && mode != "sdk-focus") return kResultFalse;
    if (mode == "sdk-focus" && GetFocus() != child) return kResultFalse;
    run_lua_dialog();
    return mode == "sdk-false" ? kResultFalse : kResultTrue;
}
void Editor::run_lua_dialog() {
    if (opened) { std::cerr << "synthetic_duplicate_f11=true\n"; return; }
    opened = true;
    wchar_t filename[32768]{};
    OPENFILENAMEW dialog{}; dialog.lStructSize=sizeof(dialog); dialog.hwndOwner=static_cast<HWND>(systemWindow);
    dialog.lpstrFile=filename; dialog.nMaxFile=32768; dialog.lpstrFilter=L"Lua\0*.lua\0\0";
    dialog.Flags=OFN_FILEMUSTEXIST | OFN_EXPLORER | OFN_NOCHANGEDIR;
    if (!GetOpenFileNameW(&dialog)) {
        std::cerr << "synthetic_open_result=false extended_error=" << CommDlgExtendedError() << '\n';
        std::ofstream out(fs::current_path()/"receipt.txt",std::ios::binary); out << "synthetic-dialog-declined";
        return;
    }
    try {
        auto source=read(filename);
        std::cerr << "synthetic_open_result=true script_bytes=" << source.size() << '\n';
        auto values=literals(source);
        std::cerr << "synthetic_literal_count=" << values.size() << '\n';
        // instrument, optional snapshot, receipt.part, nonce, rename source, destination.
        if (values.size() < 5) throw 1;
        auto instrument=read(fs::u8path(values[0]));
        bool success=instrument == "original synthetic ready";
        std::cerr << "synthetic_instrument_ready=" << success << '\n';
        if (success) controller.loaded();
        auto nonce=values[values.size()-3];
        auto receipt=fs::u8path(values.back());
        std::ofstream out(receipt,std::ios::binary); out << nonce << (success ? ":ok" : ":failed");
    } catch (...) { std::cerr << "synthetic_decode_exception=true\n"; std::ofstream out(fs::current_path()/"receipt.txt",std::ios::binary); out << "synthetic-decode-failed"; }
}
}
BEGIN_FACTORY_DEF("Native Instruments", "https://github.com/flamoris-jp/flamoris-cantabile", "")
DEF_CLASS2(INLINE_UID_FROM_FUID(processor_id), PClassInfo::kManyInstances, kVstAudioEffectClass,
    "Kontakt 8", Vst::kDistributable, PlugType::kInstrumentSynth, "1.0.0", kVstVersionString, Processor::create)
DEF_CLASS2(INLINE_UID_FROM_FUID(controller_id), PClassInfo::kManyInstances, kVstComponentControllerClass,
    "Synthetic routing fixture controller", 0, "", "1.0.0", kVstVersionString, Controller::create)
END_FACTORY

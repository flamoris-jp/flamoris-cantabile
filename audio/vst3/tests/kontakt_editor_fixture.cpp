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
#include <filesystem>
#include <fstream>
#include <regex>
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
public:
    explicit Editor(Controller& c) : controller(c) { rect = {0,0,400,200}; }
    tresult PLUGIN_API isPlatformTypeSupported(FIDString type) override { return std::string(type) == kPlatformTypeHWND ? kResultTrue : kResultFalse; }
    tresult PLUGIN_API onKeyDown(char16, int16 key, int16) override;
};
class Controller final : public EditController {
public:
    static FUnknown* create(void*) { return static_cast<IEditController*>(new Controller); }
    IPlugView* PLUGIN_API createView(FIDString name) override { return std::string(name) == ViewType::kEditor ? new Editor(*this) : nullptr; }
    void loaded() { auto message = owned(allocateMessage()); message->setMessageID("synthetic-loaded"); sendMessage(message); }
};
std::string read(const fs::path& path) { std::ifstream f(path, std::ios::binary); return {std::istreambuf_iterator<char>(f),{}}; }
// Decode only generated decimal-byte Lua string literals, never execute code.
std::string decode(const std::string& literal) {
    std::string result;
    for (size_t i=1; i+4<literal.size(); i+=4) {
        if (literal[i] != '\\') throw 1;
        result += static_cast<char>(std::stoi(literal.substr(i+1,3)));
    }
    return result;
}
tresult Editor::onKeyDown(char16, int16 key, int16) {
    if (key != KEY_F11) return kResultFalse;
    wchar_t filename[32768]{};
    OPENFILENAMEW dialog{}; dialog.lStructSize=sizeof(dialog); dialog.hwndOwner=static_cast<HWND>(systemWindow);
    dialog.lpstrFile=filename; dialog.nMaxFile=32768; dialog.lpstrFilter=L"Lua\0*.lua\0\0";
    dialog.Flags=OFN_FILEMUSTEXIST | OFN_EXPLORER | OFN_NOCHANGEDIR;
    if (!GetOpenFileNameW(&dialog)) return kResultTrue;
    try {
        auto source=read(filename);
        std::regex strings("\"(?:\\\\[0-9]{3})+\"");
        std::vector<std::string> values;
        for (auto i=std::sregex_iterator(source.begin(),source.end(),strings);i!=std::sregex_iterator();++i) values.push_back(decode(i->str()));
        // instrument, optional snapshot, receipt.part, nonce, rename source, destination.
        if (values.size() < 5) return kResultTrue;
        auto instrument=read(fs::u8path(values[0]));
        bool success=instrument == "original synthetic ready";
        if (success) controller.loaded();
        auto nonce=values[values.size()-3];
        auto receipt=fs::u8path(values.back());
        std::ofstream out(receipt,std::ios::binary); out << nonce << (success ? ":ok" : ":failed");
    } catch (...) { }
    return kResultTrue;
}
}
BEGIN_FACTORY_DEF("Native Instruments", "https://github.com/flamoris-jp/flamoris-cantabile", "")
DEF_CLASS2(INLINE_UID_FROM_FUID(processor_id), PClassInfo::kManyInstances, kVstAudioEffectClass,
    "Kontakt 8", Vst::kDistributable, PlugType::kInstrumentSynth, "1.0.0", kVstVersionString, Processor::create)
DEF_CLASS2(INLINE_UID_FROM_FUID(controller_id), PClassInfo::kManyInstances, kVstComponentControllerClass,
    "Synthetic routing fixture controller", 0, "", "1.0.0", kVstVersionString, Controller::create)
END_FACTORY

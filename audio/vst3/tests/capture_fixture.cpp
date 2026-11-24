// SPDX-License-Identifier: Apache-2.0
// Original synthetic test instrument. No vendor phrases, media or presets.
#include "public.sdk/source/vst/vstaudioeffect.h"
#include "public.sdk/source/vst/vsteditcontroller.h"
#include "public.sdk/source/main/pluginfactory.h"
#include "pluginterfaces/vst/ivstevents.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "base/source/fstreamer.h"
#include <algorithm>
#include <cstdlib>
#include <map>
#include <vector>

using namespace Steinberg;
using namespace Steinberg::Vst;
namespace {
const FUID processor_id(0x04E6087C, 0x83254E9F, 0xAFA3D445, 0xEEBD0031);
const FUID controller_id(0x62BC54AC, 0xB7D4412B, 0xABF68266, 0x31449A1F);
class Fixture final : public AudioEffect {
    struct Pending { Event event; int64 frame; };
    std::vector<Pending> pending;
    int32 transpose = 12, next_id = 100;
    std::map<std::pair<int,int>, std::vector<int32>> ids;
public:
    Fixture() { setControllerClass(controller_id); }
    static FUnknown* create(void*) { return static_cast<IAudioProcessor*>(new Fixture); }
    uint32 PLUGIN_API getLatencySamples() override { return 1024; } // Event timestamps must not shift with audio latency.
    tresult PLUGIN_API initialize(FUnknown* context) override {
        auto result = AudioEffect::initialize(context); if (result != kResultOk) return result;
        addAudioOutput(STR16("Silence"), SpeakerArr::kStereo);
        addEventInput(STR16("Triggers"), 16); addEventOutput(STR16("Generated"), 16);
        return kResultOk;
    }
    tresult PLUGIN_API setState(IBStream* stream) override {
        IBStreamer s(stream, kLittleEndian); int32 value;
        if (!s.readInt32(value) || value < 0 || value > 12) return kResultFalse;
        transpose = value; return kResultOk;
    }
    tresult PLUGIN_API getState(IBStream* stream) override {
        IBStreamer s(stream, kLittleEndian); return s.writeInt32(transpose) ? kResultOk : kResultFalse;
    }
    tresult PLUGIN_API process(ProcessData& data) override {
        if (data.inputParameterChanges) for (int32 i = 0; i < data.inputParameterChanges->getParameterCount(); ++i) {
            auto* q = data.inputParameterChanges->getParameterData(i); int32 offset; ParamValue value;
            if (q && q->getParameterId() == 0 && q->getPoint(q->getPointCount()-1, offset, value) == kResultOk) transpose = static_cast<int32>(value * 12 + 0.5);
        }
        if (!data.numSamples) return kResultOk;
        for (int32 b = 0; b < data.numOutputs; ++b) {
            for (int32 c = 0; c < data.outputs[b].numChannels; ++c) std::fill_n(data.outputs[b].channelBuffers32[c], data.numSamples, 0.F);
            data.outputs[b].silenceFlags = 3;
        }
        const int64 origin = data.processContext ? data.processContext->projectTimeSamples : 0;
        if (data.inputEvents) for (int32 i = 0; i < data.inputEvents->getEventCount(); ++i) {
            Event e{}; if (data.inputEvents->getEvent(i,e) != kResultOk) return kResultFalse;
            if (e.type == Event::kPolyPressureEvent) {
                e.polyPressure.pitch = static_cast<int16>(std::min(127, e.polyPressure.pitch + transpose));
                pending.push_back({e, origin + e.sampleOffset + 17}); continue;
            }
            bool on = e.type == Event::kNoteOnEvent, off = e.type == Event::kNoteOffEvent;
            if (!on && !off) continue;
            int pitch = on ? e.noteOn.pitch : e.noteOff.pitch;
            if (pitch == 64) continue; // Intentional empty output.
            if (on && pitch == 65) std::_Exit(42); // Abrupt owned-worker exit, no OS error dialog.
            if (on && pitch == 61 && data.outputEvents) {
                e.type = Event::kDataEvent; e.data = {0, DataEvent::kMidiSysEx, nullptr}; data.outputEvents->addEvent(e); continue;
            }
            if (on && pitch == 62 && data.outputEvents) {
                for (int n=0;n<8200;++n) data.outputEvents->addEvent(e); continue;
            }
            if (on && pitch == 63 && data.outputEvents) {
                e.sampleOffset = data.numSamples; data.outputEvents->addEvent(e); continue;
            }
            auto key = std::pair{on ? e.noteOn.channel : e.noteOff.channel, pitch};
            if (pitch == 67 && on) e.noteOn.tuning = 1.F;
            if (on) {
                e.noteOn.pitch = static_cast<int16>(std::min(127, pitch + transpose));
                e.noteOn.noteId = next_id++; ids[key].push_back(e.noteOn.noteId);
            } else {
                e.noteOff.pitch = static_cast<int16>(std::min(127, pitch + transpose));
                if (!ids[key].empty()) { e.noteOff.noteId = ids[key].front(); ids[key].erase(ids[key].begin()); }
            }
            pending.push_back({e, origin + e.sampleOffset + 17});
        }
        std::stable_sort(pending.begin(), pending.end(), [](const auto& a,const auto& b){ return a.frame < b.frame; });
        for (auto it = pending.begin(); it != pending.end();) {
            if (it->frame >= origin + data.numSamples) { ++it; continue; }
            auto e = it->event; e.sampleOffset = static_cast<int32>(it->frame - origin);
            if (data.outputEvents) data.outputEvents->addEvent(e);
            it = pending.erase(it);
        }
        return kResultOk;
    }
};
class Controller final : public EditController {
public:
    static FUnknown* create(void*) { return static_cast<IEditController*>(new Controller); }
    tresult PLUGIN_API initialize(FUnknown* context) override {
        auto result = EditController::initialize(context);
        if (result == kResultOk) parameters.addParameter(STR16("Transpose"), nullptr, 12, 1, ParameterInfo::kCanAutomate, 0);
        return result;
    }
    tresult PLUGIN_API setComponentState(IBStream* stream) override {
        IBStreamer s(stream, kLittleEndian); int32 value;
        if (!s.readInt32(value) || value < 0 || value > 12) return kResultFalse;
        return setParamNormalized(0, value / 12.0);
    }
};
}
BEGIN_FACTORY_DEF("FLAMORIS", "https://github.com/flamoris-jp/flamoris-cantabile", "")
DEF_CLASS2(INLINE_UID_FROM_FUID(processor_id), PClassInfo::kManyInstances, kVstAudioEffectClass,
    "Cantabile Capture Fixture", Vst::kDistributable, "Instrument", "1.0.0", kVstVersionString, Fixture::create)
DEF_CLASS2(INLINE_UID_FROM_FUID(controller_id), PClassInfo::kManyInstances, kVstComponentControllerClass,
    "Cantabile Capture Controller", 0, "", "1.0.0", kVstVersionString, Controller::create)
END_FACTORY

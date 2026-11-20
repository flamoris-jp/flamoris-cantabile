// SPDX-License-Identifier: Apache-2.0
#include "../wave_file.hpp"
#include <cantabile/midi.hpp>
#include "public.sdk/source/common/memorystream.h"
#include "public.sdk/source/vst/hosting/eventlist.h"
#include "public.sdk/source/vst/hosting/hostclasses.h"
#include "public.sdk/source/vst/hosting/parameterchanges.h"
#include "public.sdk/source/vst/hosting/plugprovider.h"
#include "public.sdk/source/vst/vstpresetfile.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivstmidicontrollers.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <chrono>
#include <cmath>
#include <fstream>
#include <iostream>
#include <optional>
#include <thread>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <objbase.h>
#include <windows.h>
#endif

namespace {
namespace fs = std::filesystem;
namespace Hosting = VST3::Hosting;
using namespace Steinberg;
using namespace Steinberg::Vst;
using cantabile::audio::Error;
using cantabile::audio::WaveFile;
using cantabile::audio::fail;
using nlohmann::json;

constexpr int sample_rate = 48000;
constexpr int block_size = 512;
constexpr int tail_frames = sample_rate * 2;
using Clock = std::chrono::steady_clock;

void wait_until(Clock::time_point deadline) {
#ifdef _WIN32
    for (auto now = Clock::now(); now < deadline; now = Clock::now()) {
        const auto delay = std::chrono::ceil<std::chrono::milliseconds>(deadline - now).count();
        const auto result = MsgWaitForMultipleObjectsEx(0, nullptr, static_cast<DWORD>(delay),
                                                       QS_ALLINPUT, MWMO_INPUTAVAILABLE);
        if (result == WAIT_FAILED) fail("VST_PROCESS_FAILED", "Cannot wait for Windows plugin messages");
        if (result != WAIT_OBJECT_0) continue;
        MSG message{};
        if (!PeekMessageW(&message, nullptr, 0, 0, PM_REMOVE)) continue;
        if (message.message == WM_QUIT) fail("VST_PROCESS_FAILED", "Plugin requested shutdown");
        TranslateMessage(&message);
        DispatchMessageW(&message);
    }
#else
    std::this_thread::sleep_until(deadline);
#endif
}

std::string utf8(const fs::path& path) {
    auto bytes = path.u8string();
    return {bytes.begin(), bytes.end()};
}

struct Options {
    fs::path plugin, preset, midi, output;
    std::string class_id;
    int max_seconds = 600;
    int warmup_ms = 0;
    bool list = false, realtime = false;
};

int bounded_integer(const fs::path& value, const std::string& flag, int minimum, int maximum) {
    const auto text = utf8(value);
    int number;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), number);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size() ||
        number < minimum || number > maximum)
        fail("INVALID_ARGUMENT", flag + " must be an integer from " + std::to_string(minimum) +
             " to " + std::to_string(maximum));
    return number;
}

template<typename Char>
Options parse(int argc, Char** argv) {
    Options options;
    options.list = argc > 1 && fs::path(argv[1]) == "list";
    for (int i = options.list ? 2 : 1; i < argc; ++i) {
        const auto flag = utf8(argv[i]);
        if (flag == "--realtime" && !options.list) {
            options.realtime = true;
            continue;
        }
        if (++i >= argc) fail("INVALID_ARGUMENT", "Missing value for " + flag);
        const fs::path value = argv[i];
        if (flag == "--plugin") options.plugin = value;
        else if (flag == "--class-id" && !options.list) options.class_id = utf8(value);
        else if (flag == "--preset" && !options.list) options.preset = value;
        else if (flag == "--midi" && !options.list) options.midi = value;
        else if (flag == "--output" && !options.list) options.output = value;
        else if (flag == "--max-seconds" && !options.list) options.max_seconds = bounded_integer(value, flag, 1, 600);
        else if (flag == "--warmup-ms" && !options.list) options.warmup_ms = bounded_integer(value, flag, 0, 30000);
        else fail("INVALID_ARGUMENT", "Unknown option: " + flag);
    }
    if (options.plugin.empty() || (!options.list && (options.midi.empty() || options.output.empty())))
        fail("INVALID_ARGUMENT", "--plugin is required, and rendering also requires --midi and --output");
    return options;
}

std::vector<std::uint8_t> read_file(const fs::path& path, std::size_t limit, const char* code) {
    if (!fs::is_regular_file(path)) fail(code, "Expected a regular input file");
    std::ifstream file(path, std::ios::binary | std::ios::ate);
    const auto size = file.tellg();
    if (size <= 0 || static_cast<std::uint64_t>(size) > limit)
        fail(code, "Input file is empty, unreadable or too large");
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));
    file.seekg(0);
    if (!file.read(reinterpret_cast<char*>(bytes.data()), size) ||
        file.peek() != std::char_traits<char>::eof())
        fail(code, "Cannot read complete input file");
    return bytes;
}

struct Message {
    std::int64_t tick;
    std::int64_t frame;
    int status;
    std::array<int, 2> data;
};

struct Timing {
    std::int64_t tick = 0;
    std::int64_t frame = 0;
    int tempo_us = 500000;
    int numerator = 4;
    int denominator = 4;
};

struct Score {
    int ppq;
    std::int64_t frames;
    std::vector<Timing> timings;
    std::vector<Message> messages;
};

Score schedule(const fs::path& midi, int max_seconds) {
    auto bytes = read_file(midi, 16 * 1024 * 1024, "INVALID_MIDI");
    json project;
    try { project = cantabile::import_midi(bytes); }
    catch (const std::exception& e) { fail("INVALID_MIDI", e.what()); }
    Score score{project.at("ppq").get<int>(), 0, {Timing{}}, {}};
    std::int64_t last_tick = 0;
    std::size_t musical_tracks = 0;
    std::vector<const json*> events;
    for (const auto& track : project.at("tracks")) {
        last_tick = std::max(last_tick, track.at("end_tick").get<std::int64_t>());
        bool musical = false;
        for (const auto& event : track.at("events")) {
            const auto& kind = event.at("kind");
            if (kind == "sysex") fail("UNSUPPORTED_MIDI", "SysEx requires a separate MIDI rendering path");
            if (kind == "midi") {
                musical = true;
                events.push_back(&event);
            } else if (event.at("meta_type") == 81 || event.at("meta_type") == 88) {
                events.push_back(&event);
            }
        }
        if (musical) ++musical_tracks;
    }
    if (musical_tracks != 1)
        fail("UNSUPPORTED_MIDI", "Export one instrument part as MIDI; a conductor track may accompany it");
    std::stable_sort(events.begin(), events.end(), [](const json* a, const json* b) {
        return a->at("tick").get<std::int64_t>() < b->at("tick").get<std::int64_t>();
    });
    long double exact_frame = 0;
    std::int64_t previous_tick = 0;
    Timing active;
    auto advance = [&](std::int64_t tick) {
        exact_frame += static_cast<long double>(tick - previous_tick) * active.tempo_us *
                       sample_rate / (1000000.L * score.ppq);
        previous_tick = tick;
        if (exact_frame > static_cast<long double>(max_seconds) * sample_rate - tail_frames)
            fail("DURATION_LIMIT", "MIDI duration plus two-second tail exceeds max-seconds");
    };
    for (const auto* event : events) {
        auto tick = event->at("tick").get<std::int64_t>();
        advance(tick);
        auto frame = std::llround(exact_frame);
        const auto& data = event->at("data");
        if (event->at("kind") == "midi") {
            auto status = event->at("status").get<int>();
            if ((status & 0xf0) == 0xc0)
                fail("UNSUPPORTED_MIDI", "Remove MIDI Program Change; select the sound with a VST preset");
            score.messages.push_back({tick, frame, status, {data.at(0).get<int>(),
                                      data.size() == 2 ? data.at(1).get<int>() : 0}});
            continue;
        }
        if (event->at("meta_type") == 81) {
            if (data.size() != 3) fail("INVALID_MIDI", "Invalid tempo event");
            active.tempo_us = (data[0].get<int>() << 16) | (data[1].get<int>() << 8) | data[2].get<int>();
            if (!active.tempo_us) fail("INVALID_MIDI", "Tempo must be positive");
        } else {
            if (data.size() != 4 || data[0] == 0 || data[1].get<int>() > 7)
                fail("INVALID_MIDI", "Invalid time signature event");
            active.numerator = data[0].get<int>();
            active.denominator = 1 << data[1].get<int>();
        }
        active.tick = tick;
        active.frame = frame;
        if (score.timings.back().frame == frame) score.timings.back() = active;
        else score.timings.push_back(active);
    }
    advance(last_tick);
    score.frames = static_cast<std::int64_t>(std::ceil(exact_frame)) + tail_frames;
    return score;
}

struct Activation {
    IComponent& component;
    IAudioProcessor& processor;
    bool active = false, processing = false;

    ~Activation() {
        if (processing) processor.setProcessing(false);
        if (active) component.setActive(false);
    }
};

void check(tresult result, const char* message) {
    if (result != kResultOk) fail("VST_PROCESS_FAILED", message);
}

void transfer(const Message& message, std::int64_t position, int ppq,
              EventList& events, ParameterChanges& parameters, IMidiMapping* mapping) {
    Event event{};
    event.sampleOffset = static_cast<int32>(message.frame - position);
    event.ppqPosition = static_cast<double>(message.tick) / ppq;
    auto channel = static_cast<int16>(message.status & 15);
    auto [first, second] = message.data;
    switch (message.status & 0xf0) {
    case 0x90:
        if (second != 0) {
            event.type = Event::kNoteOnEvent;
            event.noteOn = {channel, static_cast<int16>(first), 0.F, second / 127.F, 0, -1};
            break;
        }
        [[fallthrough]];
    case 0x80:
        event.type = Event::kNoteOffEvent;
        event.noteOff.channel = channel;
        event.noteOff.pitch = static_cast<int16>(first);
        event.noteOff.velocity = second / 127.F;
        event.noteOff.noteId = -1;
        break;
    case 0xa0:
        event.type = Event::kPolyPressureEvent;
        event.polyPressure = {channel, static_cast<int16>(first), second / 127.F, -1};
        break;
    default: {
        CtrlNumber controller = static_cast<CtrlNumber>(first);
        ParamValue value = second / 127.;
        if ((message.status & 0xf0) == 0xd0) {
            controller = kAfterTouch;
            value = first / 127.;
        } else if ((message.status & 0xf0) == 0xe0) {
            controller = kPitchBend;
            value = (first | (second << 7)) / 16383.;
        }
        ParamID id;
        if (!mapping || mapping->getMidiControllerAssignment(0, channel, controller, id) != kResultOk)
            fail("UNMAPPED_MIDI", "VST instrument did not map MIDI controller " +
                 std::to_string(controller) + " on channel " + std::to_string(channel + 1));
        int32 index;
        auto* queue = parameters.addParameterData(id, index);
        if (!queue) fail("VST_PROCESS_FAILED", "Cannot allocate VST parameter queue");
        check(queue->addPoint(event.sampleOffset, value, index), "Cannot queue MIDI controller");
        return;
    }}
    check(events.addEvent(event), "Cannot queue MIDI event");
}

json render(const Options& options, const Score& score, PlugProvider& provider) {
    if (!provider.initialize()) fail("VST_LOAD_FAILED", "Cannot initialize VST instrument");
    auto component = provider.getComponentPtr();
    auto controller = provider.getControllerPtr();
    FUnknownPtr<IAudioProcessor> processor(component);
    FUnknownPtr<IMidiMapping> mapping(controller);
    if (!processor || component->getBusCount(kEvent, kInput) < 1 ||
        component->getBusCount(kAudio, kOutput) < 1 || processor->canProcessSampleSize(kSample32) != kResultOk)
        fail("VST_UNSUPPORTED", "VST instrument must accept events and provide 32-bit audio output");
    if (!options.preset.empty()) {
        auto bytes = read_file(options.preset, 128 * 1024 * 1024, "INVALID_PRESET");
        MemoryStream stream(bytes.data(), bytes.size());
        const auto id = FUID::fromTUID(provider.getClassInfo().ID().data());
        if (!PresetFile::loadPreset(&stream, id, component, controller))
            fail("INVALID_PRESET", "Plugin rejected this VST preset or its class ID differs");
    }

    const auto input_count = component->getBusCount(kAudio, kInput);
    const auto output_count = component->getBusCount(kAudio, kOutput);
    if (input_count < 0 || input_count > 64 || output_count > 64)
        fail("VST_UNSUPPORTED", "VST audio bus count exceeds the render limit");
    BusInfo output_info{};
    check(component->getBusInfo(kAudio, kOutput, 0, output_info), "Cannot query main output");
    const auto channels = output_info.channelCount;
    if (output_info.busType != kMain || (channels != 1 && channels != 2))
        fail("VST_UNSUPPORTED", "First audio output must be a mono or stereo main bus");
    for (int32 i = 0; i < input_count; ++i) component->activateBus(kAudio, kInput, i, false);
    for (int32 i = 1; i < output_count; ++i) component->activateBus(kAudio, kOutput, i, false);
    check(component->activateBus(kAudio, kOutput, 0, true), "Cannot activate main output");
    check(component->activateBus(kEvent, kInput, 0, true), "Cannot activate MIDI input");
    const auto mode = options.realtime ? kRealtime : kOffline;
    ProcessSetup setup{mode, kSample32, block_size, sample_rate};
    check(processor->setupProcessing(setup), "VST instrument rejected the selected processing mode");
    Activation activation{*component, *processor};
    check(component->setActive(true), "Cannot start VST instrument");
    activation.active = true;
    const auto processing = processor->setProcessing(true);
    if (processing != kNotImplemented) check(processing, "Cannot start VST processing");
    activation.processing = processing == kResultOk;
    if (options.warmup_ms) wait_until(Clock::now() + std::chrono::milliseconds(options.warmup_ms));
    const auto latency = processor->getLatencySamples();
    if (latency > static_cast<uint32>(tail_frames))
        fail("VST_UNSUPPORTED", "Plugin latency exceeds two seconds");

    WaveFile wave(options.output);
    wave.header(static_cast<uint32>(score.frames), sample_rate);
    std::array<float, block_size> left{}, right{};
    std::array<Sample32*, 2> buffers{left.data(), right.data()};
    std::vector<AudioBusBuffers> inputs(input_count), outputs(output_count);
    outputs[0].numChannels = channels;
    outputs[0].channelBuffers32 = buffers.data();
    EventList events(static_cast<int32>(score.messages.size()));
    ParameterChanges parameters;
    ProcessContext context{};
    context.state = ProcessContext::kPlaying | ProcessContext::kTempoValid |
                    ProcessContext::kTimeSigValid | ProcessContext::kProjectTimeMusicValid |
                    ProcessContext::kContTimeValid;
    context.sampleRate = sample_rate;
    ProcessData process;
    process.processMode = mode;
    process.symbolicSampleSize = kSample32;
    process.numInputs = input_count;
    process.numOutputs = output_count;
    process.inputs = inputs.data();
    process.outputs = outputs.data();
    process.inputEvents = &events;
    process.inputParameterChanges = &parameters;
    process.processContext = &context;

    double peak = 0;
    std::uint64_t clipped = 0;
    std::size_t next = 0, timing_index = 0;
    const auto end = score.frames + latency;
    std::array<unsigned char, block_size * 4> pcm{};
    const auto start = Clock::now();
    for (std::int64_t position = 0; position < end;) {
        if (options.realtime) {
            const auto offset = std::chrono::duration<double>(static_cast<double>(position) / sample_rate);
            wait_until(start + std::chrono::duration_cast<Clock::duration>(offset));
        }
        while (timing_index + 1 < score.timings.size() &&
               score.timings[timing_index + 1].frame <= position) ++timing_index;
        const auto& timing = score.timings[timing_index];
        auto block_end = std::min(position + block_size, end);
        if (timing_index + 1 < score.timings.size())
            block_end = std::min(block_end, score.timings[timing_index + 1].frame);
        const auto count = static_cast<int32>(block_end - position);
        events.clear();
        parameters.clearQueue();
        while (next < score.messages.size() && score.messages[next].frame < block_end)
            transfer(score.messages[next++], position, score.ppq, events, parameters, mapping);
        context.projectTimeSamples = context.continousTimeSamples = position;
        context.tempo = 60000000. / timing.tempo_us;
        context.timeSigNumerator = timing.numerator;
        context.timeSigDenominator = timing.denominator;
        context.projectTimeMusic = static_cast<double>(timing.tick) / score.ppq +
                                   static_cast<double>(position - timing.frame) * context.tempo / (60 * sample_rate);
        process.numSamples = count;
        left.fill(0);
        right.fill(0);
        outputs[0].silenceFlags = 0;
        check(processor->process(process), "VST audio processing failed");
        std::size_t pcm_size = 0;
        for (int32 i = 0; i < count; ++i) {
            if (position + i < latency) continue;
            for (auto sample : {left[i], channels == 1 ? left[i] : right[i]}) {
                if (!std::isfinite(sample)) fail("INVALID_AUDIO", "VST produced non-finite audio");
                peak = std::max(peak, static_cast<double>(std::abs(sample)));
                if (std::abs(sample) > 1) ++clipped;
                auto value = static_cast<std::uint16_t>(std::lrint(std::clamp(sample, -1.F, 1.F) * 32767));
                pcm[pcm_size++] = static_cast<unsigned char>(value);
                pcm[pcm_size++] = static_cast<unsigned char>(value >> 8);
            }
        }
        wave.bytes(pcm.data(), pcm_size);
        position = block_end;
    }
    if (peak < 0.5 / 32767) fail("SILENT_AUDIO", "VST produced no audible PCM; check its preset and samples");
    wave.publish();
    return {{"ok", true}, {"backend", "vst3"}, {"sample_rate", sample_rate},
            {"process_mode", options.realtime ? "realtime" : "offline"},
            {"channels", 2}, {"sample_format", "pcm_s16le"}, {"frames", score.frames},
            {"duration_seconds", static_cast<double>(score.frames) / sample_rate},
            {"peak", peak}, {"clipped_samples", clipped}};
}

json run(const Options& options) {
#ifdef _WIN32
    struct Com {
        HRESULT result = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        ~Com() { if (SUCCEEDED(result)) CoUninitialize(); }
    } com;
    if (FAILED(com.result)) fail("VST_LOAD_FAILED", "Cannot initialize Windows COM");
#endif
    auto application = owned(new HostApplication);
    std::string error;
    auto module = Hosting::Module::create(utf8(fs::absolute(options.plugin)), error);
    if (!module) fail("VST_LOAD_FAILED", error);
    const auto& factory = module->getFactory();
    factory.setHostContext(application);
    auto classes = json::array();
    std::optional<Hosting::ClassInfo> selected;
    for (auto& info : factory.classInfos()) {
        const auto& categories = info.subCategories();
        if (info.category() != kVstAudioEffectClass ||
            std::find(categories.begin(), categories.end(), "Instrument") == categories.end()) continue;
        auto id = info.ID().toString();
        classes.push_back({{"id", id}, {"name", info.name()}, {"vendor", info.vendor()}});
        if (options.class_id.empty() || options.class_id == id) selected = info;
    }
    if (options.list) return {{"ok", true}, {"instruments", std::move(classes)}};
    if (!selected) fail("VST_UNSUPPORTED", "No matching instrument class in the VST module");
    if (options.class_id.empty() && classes.size() != 1)
        fail("VST_UNSUPPORTED", "Module contains several instruments; use list and pass --class-id");
    auto score = schedule(options.midi, options.max_seconds);
    PluginContextFactory::instance().setPluginContext(application);
    PlugProvider provider(factory, *selected);
    return render(options, score, provider);
}

template<typename Char>
int main_for(int argc, Char** argv) {
    try {
        if (argc == 1 || (argc == 2 && fs::path(argv[1]) == "--help")) {
            std::cout << "cantabile-client list --plugin PATH.vst3\n"
                         "cantabile-client --plugin PATH.vst3 [--class-id ID] [--preset FILE.vstpreset]\n"
                         "                 --midi PART.mid --output OUT.wav [--max-seconds 600]\n"
                         "                 [--realtime] [--warmup-ms 0]\n";
            return 0;
        }
        std::cout << run(parse(argc, argv)).dump(-1, ' ', false, json::error_handler_t::replace) << '\n';
        return 0;
    } catch (const std::exception& exception) {
        const auto* error = dynamic_cast<const Error*>(&exception);
        const json result{{"ok", false}, {"error", {{"code", error ? error->code : "RENDER_FAILED"},
                                                  {"message", exception.what()}}}};
        std::cout << result.dump(-1, ' ', false, json::error_handler_t::replace) << '\n';
        return 1;
    }
}
} // namespace

#ifdef _WIN32
int wmain(int argc, wchar_t** argv) { return main_for(argc, argv); }
#else
int main(int argc, char** argv) { return main_for(argc, argv); }
#endif

// SPDX-License-Identifier: Apache-2.0
// Included by renderer.cpp in its private namespace; no general-purpose evaluator.

constexpr std::size_t maximum_state_bytes = 16 * 1024 * 1024;
constexpr std::size_t maximum_script_bytes = 256 * 1024;
constexpr std::size_t maximum_response_bytes = 4 * 1024 * 1024;
constexpr std::uint64_t maximum_script_output_bytes = 128 * 1024 * 1024;

std::string text128(const TChar* text) {
    // SDK strings are UTF-16 even on platforms whose wchar_t is 32-bit.
    std::string result;
    for (std::size_t i = 0; i < 128 && text[i]; ++i) {
        std::uint32_t cp = static_cast<std::uint16_t>(text[i]);
        if (cp >= 0xd800 && cp <= 0xdbff && i + 1 < 128) {
            const auto low = static_cast<std::uint16_t>(text[i + 1]);
            if (low >= 0xdc00 && low <= 0xdfff) { cp = 0x10000 + ((cp - 0xd800) << 10) + low - 0xdc00; ++i; }
            else cp = 0xfffd;
        } else if (cp >= 0xd800 && cp <= 0xdfff) cp = 0xfffd;
        if (cp < 0x80) result.push_back(static_cast<char>(cp));
        else if (cp < 0x800) { result.push_back(static_cast<char>(0xc0 | (cp >> 6))); result.push_back(static_cast<char>(0x80 | (cp & 63))); }
        else if (cp < 0x10000) { result.push_back(static_cast<char>(0xe0 | (cp >> 12))); result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63))); result.push_back(static_cast<char>(0x80 | (cp & 63))); }
        else { result.push_back(static_cast<char>(0xf0 | (cp >> 18))); result.push_back(static_cast<char>(0x80 | ((cp >> 12) & 63))); result.push_back(static_cast<char>(0x80 | ((cp >> 6) & 63))); result.push_back(static_cast<char>(0x80 | (cp & 63))); }
    }
    return result;
}

int32 count_limit(int32 count, int32 maximum, const char* subject) {
    if (count < 0 || count > maximum) fail("VST_UNSUPPORTED", std::string(subject) + " exceeds Inspector limits");
    return count;
}

template<class Interface, class Pointer>
bool supports(Pointer pointer) { return static_cast<bool>(FUnknownPtr<Interface>(pointer)); }

void synchronize_controller(PlugProvider& provider);

void initialize_for_control(const Options& options, PlugProvider& provider) {
    check_deadline();
    if (!provider.initialize()) fail("VST_LOAD_FAILED", "Cannot initialize VST instrument; check license and GUI initialization");
    check_deadline();
    if (!options.preset.empty()) {
        auto bytes = read_file(options.preset, maximum_state_bytes, "INVALID_PRESET");
        MemoryStream stream(bytes.data(), bytes.size());
        const auto id = FUID::fromTUID(provider.getClassInfo().ID().data());
        if (!PresetFile::loadPreset(&stream, id, provider.getComponentPtr(), provider.getControllerPtr()))
            fail("INVALID_PRESET", "Plugin rejected the VST preset or its class ID differs");
    } else synchronize_controller(provider);
    check_deadline();
}

json inspect_plugin(const Options& options, PlugProvider& provider) {
    auto component = provider.getComponentPtr();
    auto controller = provider.getControllerPtr();
    if (!component || !controller) fail("VST_UNSUPPORTED", "Inspector requires IComponent and IEditController");
    FUnknownPtr<IAudioProcessor> processor(component);
    FUnknownPtr<IMidiMapping> mapping(controller);
    FUnknownPtr<IUnitInfo> units(controller);
    const auto& info = provider.getClassInfo();
    json report{{"ok", true}, {"schema_version", 1},
        {"plugin", {{"class_id", info.ID().toString()}, {"name", info.name()}, {"vendor", info.vendor()},
                    {"version", info.version()}, {"sdk_version", info.sdkVersion()},
                    {"preset", options.preset.empty() ? json(nullptr) : json(utf8(options.preset.filename()))}}},
        {"parameters", json::array()}, {"buses", json::array()}, {"programs", json::array()}, {"units", json::array()},
        {"interfaces", {{"IComponent", true}, {"IEditController", true}, {"IAudioProcessor", static_cast<bool>(processor)},
                        {"IEditController2", supports<IEditController2>(controller)}, {"IMidiMapping", static_cast<bool>(mapping)},
                        {"IUnitInfo", static_cast<bool>(units)}, {"INoteExpressionController", supports<INoteExpressionController>(controller)},
                        {"IKeyswitchController", supports<IKeyswitchController>(controller)}, {"IConnectionPoint", supports<IConnectionPoint>(component)}}},
        {"midi_mapping", {{"supported", static_cast<bool>(mapping)}, {"assignments", json::array()}}}};
    std::set<ParamID> ids;
    const auto parameter_count = count_limit(controller->getParameterCount(), 16384, "Parameter count");
    for (int32 i = 0; i < parameter_count; ++i) {
        check_deadline();
        ParameterInfo parameter{};
        if (controller->getParameterInfo(i, parameter) != kResultOk || parameter.id == kNoParamId || !ids.insert(parameter.id).second)
            fail("VST_UNSUPPORTED", "Plugin returned invalid or duplicate parameter metadata");
        auto value = controller->getParamNormalized(parameter.id);
        if (!std::isfinite(value) || value < 0 || value > 1 || parameter.stepCount < 0 ||
            !std::isfinite(parameter.defaultNormalizedValue) || parameter.defaultNormalizedValue < 0 || parameter.defaultNormalizedValue > 1)
            fail("VST_UNSUPPORTED", "Plugin returned invalid normalized parameter metadata");
        String128 display{};
        const bool displayed = controller->getParamStringByValue(parameter.id, value, display) == kResultOk;
        report["parameters"].push_back({{"id", parameter.id}, {"title", text128(parameter.title)},
            {"short_title", text128(parameter.shortTitle)}, {"units", text128(parameter.units)},
            {"unit_id", parameter.unitId}, {"step_count", parameter.stepCount}, {"flags", parameter.flags},
            {"default_value", parameter.defaultNormalizedValue}, {"value", value},
            {"display", displayed ? json(text128(display)) : json(nullptr)}});
    }
    int32 event_inputs = 0, event_outputs = 0;
    for (auto type : {kAudio, kEvent}) for (auto direction : {kInput, kOutput}) {
        const auto count = count_limit(component->getBusCount(type, direction), 64, "Bus count");
        if (type == kEvent && direction == kInput) event_inputs = count;
        if (type == kEvent && direction == kOutput) event_outputs = count;
        for (int32 i = 0; i < count; ++i) {
            check_deadline();
            BusInfo bus{};
            if (component->getBusInfo(type, direction, i, bus) != kResultOk || bus.channelCount < 0 || bus.channelCount > 1024)
                fail("VST_UNSUPPORTED", "Plugin returned invalid bus metadata");
            json entry{{"index", i}, {"media_type", type == kAudio ? "audio" : "event"},
                       {"direction", direction == kInput ? "input" : "output"}, {"name", text128(bus.name)},
                       {"channel_count", bus.channelCount}, {"bus_type", bus.busType}, {"flags", bus.flags}};
            if (processor && type == kAudio) {
                SpeakerArrangement arrangement{};
                entry["speaker_arrangement"] = processor->getBusArrangement(direction, i, arrangement) == kResultOk ? json(arrangement) : json(nullptr);
            }
            report["buses"].push_back(std::move(entry));
        }
    }
    // VST 3 defines MIDI controller mappings for 16 channels, CC0..127,
    // channel pressure (128) and pitch bend (129). No private vendor API.
    if (mapping) for (int32 bus = 0; bus < event_inputs; ++bus) for (int16 channel = 0; channel < 16; ++channel) {
        check_deadline();
        for (CtrlNumber cc = 0; cc <= kPitchBend; ++cc) {
            ParamID id = kNoParamId;
            if (mapping->getMidiControllerAssignment(bus, channel, cc, id) == kResultOk) {
                if (!ids.contains(id)) fail("VST_UNSUPPORTED", "MIDI mapping references an unpublished parameter");
                report["midi_mapping"]["assignments"].push_back({{"bus", bus}, {"channel", channel}, {"controller", cc}, {"id", id}});
            }
        }
    }
    if (units) {
        const auto unit_count = count_limit(units->getUnitCount(), 1024, "Unit count");
        for (int32 i = 0; i < unit_count; ++i) {
            check_deadline(); UnitInfo unit{};
            if (units->getUnitInfo(i, unit) != kResultOk) fail("VST_UNSUPPORTED", "Cannot query unit metadata");
            report["units"].push_back({{"id", unit.id}, {"parent_id", unit.parentUnitId}, {"name", text128(unit.name)}, {"program_list_id", unit.programListId}});
        }
        auto lists = count_limit(units->getProgramListCount(), 256, "Program list count");
        int32 total = 0;
        for (int32 i = 0; i < lists; ++i) {
            check_deadline(); ProgramListInfo list{};
            if (units->getProgramListInfo(i, list) != kResultOk) fail("VST_UNSUPPORTED", "Cannot query program list");
            count_limit(list.programCount, 16384 - total, "Program count"); total += list.programCount;
            json programs = json::array();
            for (int32 program = 0; program < list.programCount; ++program) {
                check_deadline(); String128 name{};
                if (units->getProgramName(list.id, program, name) != kResultOk) fail("VST_UNSUPPORTED", "Cannot query program name");
                programs.push_back({{"index", program}, {"name", text128(name)}});
            }
            report["programs"].push_back({{"id", list.id}, {"name", text128(list.name)}, {"program_count", list.programCount}, {"programs", std::move(programs)}});
        }
    }
    report["midi_output"] = {{"bus_count", event_outputs}, {"available", event_outputs > 0}, {"capture_supported", false},
                             {"status", event_outputs ? "event_bus_advertised_capture_not_implemented" : "no_event_output_bus"}};
    report["phase1_passed"] = true;
    if (report.dump().size() > maximum_response_bytes) fail("RESPONSE_LIMIT", "Inspector JSON exceeds response limit");
    check_deadline();
    return report;
}

void exact_fields(const json& value, std::initializer_list<const char*> allowed) {
    if (!value.is_object()) fail("INVALID_SCRIPT", "Expected a typed operation object");
    for (const auto& [key, ignored] : value.items()) {
        (void)ignored;
        if (std::none_of(allowed.begin(), allowed.end(), [&](const char* item) { return key == item; }))
            fail("INVALID_SCRIPT", "Unknown script field");
    }
}

std::int64_t script_int(const json& value, std::int64_t minimum, std::int64_t maximum) {
    if (!value.is_number_integer() || (value.is_number_unsigned() && value.get<std::uint64_t>() > static_cast<std::uint64_t>(maximum)))
        fail("INVALID_SCRIPT", "Expected a bounded integer");
    auto number = value.get<std::int64_t>();
    if (number < minimum || number > maximum) fail("INVALID_SCRIPT", "Integer exceeds script limits");
    return number;
}

bool is_write(const std::string& op) {
    return op == "set_parameter" || op == "midi" || op == "render" || op == "save_state" || op == "load_state";
}

void verify_no_link(const fs::path& path) {
    for (auto current = fs::absolute(path); !current.empty();) {
        std::error_code ec;
        const auto status = fs::symlink_status(current, ec);
        if (!ec && fs::is_symlink(status)) fail("INVALID_PATH", "Links are forbidden in script paths");
#ifdef _WIN32
        const auto attributes = GetFileAttributesW(current.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_REPARSE_POINT))
            fail("INVALID_PATH", "Reparse points are forbidden in script paths");
#endif
        auto parent = current.parent_path(); if (parent == current) break; current = parent;
    }
}

fs::path script_path(const Options& options, const json& name_value, const char* extension) {
    if (!name_value.is_string()) fail("INVALID_PATH", "Expected a portable basename");
    const auto name = name_value.get<std::string>();
    auto alnum = [](unsigned char ch) { return (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9'); };
    if (name.empty() || name.size() > 120 || !alnum(name.front()) || name.find("..") != std::string::npos ||
        !std::all_of(name.begin(), name.end(), [&](unsigned char ch) { return alnum(ch) || ch == '.' || ch == '_' || ch == '-'; }) ||
        fs::path(name).extension() != extension)
        fail("INVALID_PATH", "Only a bounded portable basename with the expected extension is allowed");
    // Windows device aliases are dangerous even with extensions.
    std::string stem = name.substr(0, name.find('.'));
    std::transform(stem.begin(), stem.end(), stem.begin(), [](unsigned char ch) { return static_cast<char>(std::toupper(ch)); });
    if (stem == "CON" || stem == "PRN" || stem == "AUX" || stem == "NUL" ||
        (stem.size() == 4 && (stem.starts_with("COM") || stem.starts_with("LPT")) && stem[3] >= '1' && stem[3] <= '9'))
        fail("INVALID_PATH", "Device basenames are forbidden");
    const auto result = fs::absolute(options.workspace) / name;
    verify_no_link(result);
    return result;
}

json validate_script(const Options& options) {
    verify_no_link(options.workspace); verify_no_link(options.script);
    if (!fs::is_directory(options.workspace)) fail("INVALID_PATH", "Script workspace must be an existing directory");
    auto bytes = read_file(options.script, maximum_script_bytes, "INVALID_SCRIPT");
    json request;
    try {
        std::vector<std::set<std::string>> keys;
        request = json::parse(bytes.begin(), bytes.end(), [&](int depth, json::parse_event_t event, json& parsed) {
            if (depth > 16) fail("INVALID_SCRIPT", "Script nesting exceeds limit");
            if (event == json::parse_event_t::object_start) keys.emplace_back();
            else if (event == json::parse_event_t::key && !keys.back().insert(parsed.get<std::string>()).second)
                fail("INVALID_SCRIPT", "Duplicate script field");
            else if (event == json::parse_event_t::object_end) keys.pop_back();
            return true;
        });
    } catch (...) { fail("INVALID_SCRIPT", "Invalid script JSON"); }
    exact_fields(request, {"version", "operations", "limits"});
    if (!request.contains("version") || script_int(request.at("version"), 1, 1) != 1 || !request.contains("operations") || !request.at("operations").is_array())
        fail("INVALID_SCRIPT", "Expected DSL version 1 and operations array");
    std::int64_t maximum = 64, timeout = 30000;
    if (request.contains("limits")) {
        exact_fields(request.at("limits"), {"max_operations", "timeout_ms"});
        if (request.at("limits").contains("max_operations")) maximum = script_int(request.at("limits").at("max_operations"), 1, 256);
        if (request.at("limits").contains("timeout_ms")) timeout = script_int(request.at("limits").at("timeout_ms"), 1, 120000);
    }
    if (request.at("operations").empty() || request.at("operations").size() > static_cast<std::size_t>(maximum))
        fail("OPERATION_LIMIT", "Script operation count exceeds its limit");
    int output_count = 0;
    std::set<fs::path> outputs;
    for (const auto& operation : request.at("operations")) {
        if (!operation.is_object() || !operation.contains("op") || !operation.at("op").is_string()) fail("INVALID_SCRIPT", "Operation requires an op name");
        const auto op = operation.at("op").get<std::string>();
        if (op == "inspect" || op == "list_parameters") exact_fields(operation, {"op"});
        else if (op == "get_parameter" || op == "set_parameter") {
            if (op == "get_parameter") exact_fields(operation, {"op", "id"});
            else exact_fields(operation, {"op", "id", "value"});
            if (!operation.contains("id")) fail("INVALID_PARAMETER", "Parameter ID is required");
            try { script_int(operation.at("id"), 0, std::numeric_limits<std::uint32_t>::max() - 1); }
            catch (...) { fail("INVALID_PARAMETER", "Parameter ID must be a published unsigned integer"); }
            if (op == "set_parameter" && (!operation.contains("value") || !operation.at("value").is_number() ||
                !std::isfinite(operation.at("value").get<double>()) || operation.at("value").get<double>() < 0 || operation.at("value").get<double>() > 1))
                fail("INVALID_PARAMETER", "Parameter value must be finite and normalized to 0..1");
        } else if (op == "midi") {
            exact_fields(operation, {"op", "path"}); if (!operation.contains("path")) fail("INVALID_SCRIPT", "MIDI path is required");
            script_path(options, operation.at("path"), ".mid");
        } else if (op == "render") {
            exact_fields(operation, {"op", "midi", "output"}); if (!operation.contains("output")) fail("INVALID_SCRIPT", "Render output is required");
            auto output = script_path(options, operation.at("output"), ".wav");
            if (operation.contains("midi")) script_path(options, operation.at("midi"), ".mid");
            if (!outputs.insert(output).second || fs::exists(output)) fail("OUTPUT_EXISTS", "Script outputs must be new and unique");
            ++output_count;
        } else if (op == "save_state" || op == "load_state") {
            exact_fields(operation, {"op", "name"}); if (!operation.contains("name")) fail("INVALID_SCRIPT", "State basename is required");
            auto state = script_path(options, operation.at("name"), ".vstpreset");
            if (op == "save_state") {
                if (!outputs.insert(state).second || fs::exists(state)) fail("OUTPUT_EXISTS", "State outputs must be new and unique");
                ++output_count;
            }
        } else fail("INVALID_SCRIPT", "Unknown script operation; no general-purpose code is accepted");
        if (is_write(op) && !options.allow_write) fail("PERMISSION_DENIED", "This script requires an explicit write capability");
    }
    if (output_count > 16) fail("OPERATION_LIMIT", "At most 16 script output files are allowed");
    script_deadline = Clock::now() + std::chrono::milliseconds(timeout);
    return request;
}

class BoundedStateStream final : public MemoryStream {
public:
    tresult PLUGIN_API write(void* buffer, int32 count, int32* written) override {
        int64 position = 0; MemoryStream::tell(&position);
        if (count < 0 || position < 0 || static_cast<std::uint64_t>(position) + static_cast<std::uint64_t>(count) > maximum_state_bytes) {
            if (written) *written = 0; return kResultFalse;
        }
        return MemoryStream::write(buffer, count, written);
    }
    tresult PLUGIN_API seek(int64 position, int32 mode, int64* result) override {
        int64 current = 0; MemoryStream::tell(&current);
        const auto base = mode == IBStream::kIBSeekSet ? 0 : mode == IBStream::kIBSeekCur ? current : getSize();
        if (position < -base || position > static_cast<int64>(maximum_state_bytes) - base) return kResultFalse;
        return MemoryStream::seek(position, mode, result);
    }
};

void synchronize_controller(PlugProvider& provider) {
    // Initial processor defaults and controller UI defaults need not agree.
    // The standard host lifecycle transfers the component's state to its
    // separate controller before exposing current parameter values.
    BoundedStateStream stream;
    const auto state = provider.getComponentPtr()->getState(&stream);
    if (state == kNotImplemented) return;
    if (state != kResultOk) fail("VST_LOAD_FAILED", "Cannot read initial component state");
    if (stream.seek(0, IBStream::kIBSeekSet, nullptr) != kResultOk) fail("VST_LOAD_FAILED", "Cannot rewind initial state");
    const auto synchronized = provider.getControllerPtr()->setComponentState(&stream);
    if (synchronized != kNotImplemented && synchronized != kResultOk)
        fail("VST_LOAD_FAILED", "Cannot synchronize initial controller state");
}

std::vector<std::uint8_t> snapshot_state(PlugProvider& provider) {
    BoundedStateStream stream;
    if (!PresetFile::savePreset(&stream, FUID::fromTUID(provider.getClassInfo().ID().data()), provider.getComponentPtr(), provider.getControllerPtr()) ||
        stream.getSize() <= 0 || static_cast<std::uint64_t>(stream.getSize()) > maximum_state_bytes)
        fail("STATE_UNSUPPORTED", "Plugin cannot provide a bounded standard VST preset snapshot");
    return {reinterpret_cast<std::uint8_t*>(stream.getData()), reinterpret_cast<std::uint8_t*>(stream.getData()) + stream.getSize()};
}

bool restore_state(PlugProvider& provider, std::vector<std::uint8_t>& bytes) {
    MemoryStream stream(bytes.data(), bytes.size());
    const bool restored = PresetFile::loadPreset(&stream, FUID::fromTUID(provider.getClassInfo().ID().data()), provider.getComponentPtr(), provider.getControllerPtr());
    if (restored) script_parameter_changes.clear();
    return restored;
}

void flush_parameter(const Options& options, PlugProvider& provider, ParamID id, double value) {
    // IEditController changes are not audio-processor changes. Deliver the same
    // value through the standard zero-sample process flush before saving state.
    auto component = provider.getComponentPtr();
    FUnknownPtr<IAudioProcessor> processor(component);
    if (!processor || processor->canProcessSampleSize(kSample32) != kResultOk) fail("VST_UNSUPPORTED", "Parameter writes require a float processor");
    const auto mode = options.realtime ? kRealtime : kOffline;
    ProcessSetup setup{mode, kSample32, block_size, sample_rate};
    check(processor->setupProcessing(setup), "Cannot prepare parameter flush");
    Activation activation{*component, *processor};
    check(component->setActive(true), "Cannot activate parameter flush"); activation.active = true;
    const auto started = processor->setProcessing(true);
    if (started != kNotImplemented) check(started, "Cannot start parameter flush");
    activation.processing = started == kResultOk;
    ParameterChanges changes; int32 index = 0;
    auto* queue = changes.addParameterData(id, index);
    if (!queue || queue->addPoint(0, value, index) != kResultOk) fail("VST_PROCESS_FAILED", "Cannot queue parameter flush");
    ProcessData process{}; process.processMode = mode; process.symbolicSampleSize = kSample32;
    process.inputParameterChanges = &changes;
    check(processor->process(process), "Plugin rejected the standard parameter flush");
}

json execute_script(const Options& options, const json& request, PlugProvider& provider, const json& report) {
    auto controller = provider.getControllerPtr();
    std::map<ParamID, json> parameters;
    for (const auto& parameter : report.at("parameters")) parameters.emplace(parameter.at("id").get<ParamID>(), parameter);
    // Unknown/read-only IDs are rejected before creating state or touching values.
    bool writes = false;
    for (const auto& operation : request.at("operations")) {
        const auto op = operation.at("op").get<std::string>(); writes |= is_write(op);
        if (op == "get_parameter" || op == "set_parameter") {
            auto found = parameters.find(operation.at("id").get<ParamID>());
            if (found == parameters.end()) fail("INVALID_PARAMETER", "Parameter ID is not published by this plugin");
            if (op == "set_parameter" && (found->second.at("flags").get<int32>() & ParameterInfo::kIsReadOnly))
                fail("PERMISSION_DENIED", "Parameter is read-only");
        }
    }
    std::vector<std::uint8_t> baseline;
    if (writes) { baseline = snapshot_state(provider); check_deadline(); }
    json results = json::array(), audit = json::array(), files = json::array();
    std::optional<Score> queued;
    struct Output { fs::path staged, destination; std::string kind; };
    std::vector<Output> outputs;
    std::map<fs::path, std::vector<std::uint8_t>> state_outputs;
    auto cleanup = [&] {
        for (const auto& output : outputs) {
            std::error_code ec; fs::remove(output.staged, ec); auto partial = output.staged; partial += ".part"; fs::remove(partial, ec);
        }
    };
    std::vector<fs::path> published;
    std::uint64_t output_bytes = 0;
    std::size_t response_bytes = 0;
    try {
        for (std::size_t index = 0; index < request.at("operations").size(); ++index) {
            check_deadline(); const auto& operation = request.at("operations")[index];
            const auto op = operation.at("op").get<std::string>(); json result{{"op", op}};
            if (op == "inspect") result["inspection"] = inspect_plugin(options, provider);
            else if (op == "list_parameters") result["parameters"] = inspect_plugin(options, provider).at("parameters");
            else if (op == "get_parameter") {
                const auto id = operation.at("id").get<ParamID>(); const auto value = controller->getParamNormalized(id);
                if (!std::isfinite(value) || value < 0 || value > 1) fail("INVALID_PARAMETER", "Plugin returned an invalid normalized value");
                result["id"] = id; result["value"] = value;
            } else if (op == "set_parameter") {
                const auto id = operation.at("id").get<ParamID>(); const auto value = operation.at("value").get<double>();
                result["id"] = id; result["previous"] = controller->getParamNormalized(id);
                check(controller->setParamNormalized(id, value), "Plugin rejected the parameter value");
                flush_parameter(options, provider, id, value); script_parameter_changes[id] = value;
                result["value"] = controller->getParamNormalized(id);
            } else if (op == "midi") {
                queued = schedule(script_path(options, operation.at("path"), ".mid"), std::min(options.max_seconds, 120));
                result["queued_messages"] = queued->messages.size(); result["delivery"] = "next_render";
            } else if (op == "render") {
                if (operation.contains("midi")) queued = schedule(script_path(options, operation.at("midi"), ".mid"), std::min(options.max_seconds, 120));
                if (!queued) fail("INVALID_SCRIPT", "Render requires a preceding MIDI operation or midi field");
                const auto wave_bytes = 44ULL + static_cast<std::uint64_t>(queued->frames) * 4ULL;
                if (wave_bytes > maximum_script_output_bytes - output_bytes) fail("OUTPUT_LIMIT", "Script output quota exceeded");
                output_bytes += wave_bytes;
                auto destination = script_path(options, operation.at("output"), ".wav");
                auto staged = fs::absolute(options.workspace) / (".script-" + std::to_string(index) + ".wav");
                auto partial = staged; partial += ".part";
                verify_no_link(staged); verify_no_link(partial); if (fs::exists(staged) || fs::exists(partial)) fail("OUTPUT_EXISTS", "Staging output already exists");
                outputs.push_back({staged, destination, "wav"}); Options rendering = options;
                rendering.output = staged; rendering.preset.clear(); result["audio"] = render(rendering, *queued, provider, true);
                result["name"] = utf8(destination.filename()); queued.reset();
            } else if (op == "save_state") {
                auto destination = script_path(options, operation.at("name"), ".vstpreset");
                auto bytes = snapshot_state(provider); result["name"] = utf8(destination.filename()); result["size"] = bytes.size();
                if (bytes.size() > maximum_script_output_bytes - output_bytes) fail("OUTPUT_LIMIT", "Script output quota exceeded");
                output_bytes += bytes.size(); state_outputs.emplace(destination, std::move(bytes));
            } else if (op == "load_state") {
                auto path = script_path(options, operation.at("name"), ".vstpreset");
                auto staged = state_outputs.find(path);
                auto bytes = staged == state_outputs.end() ? read_file(path, maximum_state_bytes, "INVALID_PRESET") : staged->second;
                if (!restore_state(provider, bytes)) fail("INVALID_PRESET", "Plugin rejected the state preset");
                result["name"] = utf8(path.filename()); result["restored"] = true;
            }
            check_deadline(); response_bytes += result.dump().size();
            if (response_bytes > maximum_response_bytes) fail("RESPONSE_LIMIT", "Script JSON exceeds response limit");
            results.push_back(std::move(result)); audit.push_back({{"index", index}, {"op", op}, {"status", "completed"}});
        }
        // Write preset bytes to exclusive staging files. Final names appear only
        // after every operation and deadline check has succeeded.
        std::size_t state_index = 0;
        for (const auto& [destination, bytes] : state_outputs) {
            auto staged = fs::absolute(options.workspace) / (".script-state-" + std::to_string(state_index++) + ".vstpreset");
            verify_no_link(staged); if (fs::exists(staged)) fail("OUTPUT_EXISTS", "State staging file already exists");
            outputs.push_back({staged, destination, "state"});
#ifdef _WIN32
            auto* file = _wfopen(staged.c_str(), L"wbx");
#else
            auto* file = std::fopen(staged.c_str(), "wbx");
#endif
            if (!file) fail("OUTPUT_FAILED", "Cannot create exclusive state file");
#ifndef _WIN32
            if (::fchmod(::fileno(file), S_IRUSR | S_IWUSR)) { std::fclose(file); fail("OUTPUT_FAILED", "Cannot restrict state file permissions"); }
#endif
            bool good = std::fwrite(bytes.data(), 1, bytes.size(), file) == bytes.size();
            good = std::fclose(file) == 0 && good; if (!good) fail("OUTPUT_FAILED", "Cannot write complete state file");
        }
        check_deadline();
        for (const auto& output : outputs) {
            check_deadline();
            verify_no_link(output.destination); std::error_code ec; fs::create_hard_link(output.staged, output.destination, ec);
            if (ec) fail("OUTPUT_FAILED", "Cannot publish script output without overwrite");
            published.push_back(output.destination); files.push_back({{"name", utf8(output.destination.filename())}, {"kind", output.kind}});
        }
        check_deadline();
        cleanup();
        return {{"ok", true}, {"version", 1}, {"phase1_passed", true}, {"results", std::move(results)},
                {"audit", std::move(audit)}, {"files", std::move(files)}, {"state_scope", "isolated_worker"}};
    } catch (...) {
        auto original = std::current_exception();
        for (const auto& path : published) { std::error_code ignored; fs::remove(path, ignored); }
        cleanup();
        bool restored = !writes;
        if (writes) { try { restored = restore_state(provider, baseline); } catch (...) { restored = false; } }
        script_failure["rollback"] = {{"attempted", writes}, {"restored", restored}};
        const auto failed_index = audit.size();
        if (failed_index < request.at("operations").size()) audit.push_back({{"index", failed_index},
            {"op", request.at("operations")[failed_index].at("op")}, {"status", "failed"}});
        script_failure["audit"] = audit;
        if (!restored) fail("STATE_RESTORE_FAILED", "Plugin rejected error recovery; isolated worker will be discarded");
        std::rethrow_exception(original);
    }
}

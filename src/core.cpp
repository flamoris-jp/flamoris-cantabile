#include <cantabile/core.h>
#include <cantabile/encoding.hpp>
#include <cantabile/midi.hpp>
#include <cantabile/performance.hpp>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <cstring>
#include <cmath>
#include <deque>
#include <limits>
#include <memory>
#include <mutex>
#include <set>
#include <stdexcept>
#include <string>
#include <tuple>

namespace {
using json = nlohmann::json;
constexpr std::size_t max_json_bytes = 32 * 1024 * 1024;
constexpr std::size_t max_events = 100000;
constexpr std::int64_t max_tick = 9007199254740991LL;
struct Error : std::runtime_error {
    std::string code;
    Error(std::string c, const std::string& message) : std::runtime_error(message), code(std::move(c)) {}
};
void require(bool ok, const std::string& message, const char* code = "INVALID_PROJECT") {
    if (!ok) throw Error(code, message);
}
std::int64_t number(const json& v, std::int64_t lo, std::int64_t hi, const std::string& name) {
    require(v.is_number_integer(), name + " must be an integer");
    if (v.is_number_unsigned()) {
        auto x = v.get<std::uint64_t>();
        require(x <= static_cast<std::uint64_t>(hi), name + " is out of range");
        require(lo <= 0 || x >= static_cast<std::uint64_t>(lo), name + " is out of range");
        return static_cast<std::int64_t>(x);
    }
    auto x = v.get<std::int64_t>();
    require(x >= lo && x <= hi, name + " is out of range");
    return x;
}
std::int64_t field_number(const json& obj, const char* key, std::int64_t lo, std::int64_t hi) {
    require(obj.contains(key), std::string("Missing field: ") + key);
    return number(obj.at(key), lo, hi, key);
}
std::string string_field(const json& obj, const char* key, std::size_t limit = 4096) {
    require(obj.contains(key) && obj.at(key).is_string(), std::string(key) + " must be a string");
    auto value = obj.at(key).get<std::string>();
    require(value.size() <= limit, std::string(key) + " exceeds size limit", "LIMIT_EXCEEDED");
    return value;
}
bool valid_id(const std::string& id) {
    return !id.empty() && id.size() <= 128 && std::all_of(id.begin(), id.end(), [](unsigned char c) {
        return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') ||
            (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == ':';
    });
}
json time_map(const json& project, std::int64_t end) {
    auto result = json::array();
    for (const auto& track : project.at("tracks")) for (const auto& event : track.at("events"))
        if (event.at("kind") == "meta" && (event.at("meta_type") == 81 || event.at("meta_type") == 88) &&
            event.at("tick").get<std::int64_t>() <= end)
            result.push_back({{"tick", event.at("tick")}, {"order", event.value("order", std::int64_t{1})}, {"meta_type", event.at("meta_type")}, {"data", event.at("data")}});
    std::stable_sort(result.begin(), result.end(), [](const json& a, const json& b) {
        if (a["tick"] != b["tick"]) return a["tick"].get<std::int64_t>() < b["tick"].get<std::int64_t>();
        return a["order"].get<std::int64_t>() < b["order"].get<std::int64_t>();
    });
    for (auto& item : result) item.erase("order");
    return result;
}
void validate_provenance(const json& p) {
    require(p.is_object() && p.size() == 12 && p.value("kind", "") == "vst3_capture" && p.value("version", 0) == 1, "Invalid capture provenance");
    for (const char* name : {"source_track_id", "catalog_id", "class_id", "plugin_version"}) string_field(p, name, 256);
    for (const char* name : {"midi_sha256", "input_sha256", "state_sha256"}) {
        auto value = string_field(p, name, 64);
        require(value.size() == 64 && std::all_of(value.begin(), value.end(), [](char c) { return (c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'); }), "Invalid provenance digest");
    }
    field_number(p, "source_revision", 0, max_tick);
    auto start = field_number(p, "start_tick", 0, 2147483647);
    require(field_number(p, "end_tick", 1, 2147483647) > start, "Invalid capture interval");
}
void validate_instrument(const json& instrument) {
    require(instrument.is_object(), "instrument must be an object");
    // Legacy opaque instrument settings remain round-trippable. Catalog-backed
    // VST instances use a closed, portable contract with no machine paths.
    if (!instrument.contains("catalog_id")) return;
    for (auto it = instrument.begin(); it != instrument.end(); ++it)
        require(it.key() == "backend" || it.key() == "catalog_id" || it.key() == "state_base64" ||
                it.key() == "sound_id" || it.key() == "plugin_version" || it.key() == "warmup_ms" || it.key() == "realtime" || it.key() == "preroll_ms" || it.key() == "performance", "Unknown catalog instrument field");
    if (instrument.contains("performance") && !instrument["performance"].is_null()) cantabile::validate_performance(instrument["performance"]);
    require(instrument.value("backend", "") == "vst3", "Catalog instruments require VST3");
    require(valid_id(string_field(instrument, "catalog_id", 128)), "Invalid catalog ID");
    if (instrument.contains("sound_id")) require(valid_id(string_field(instrument, "sound_id", 128)), "Invalid sound ID");
    if (instrument.contains("plugin_version")) string_field(instrument, "plugin_version", 256);
    if (instrument.contains("warmup_ms")) field_number(instrument, "warmup_ms", 0, 30000);
    if (instrument.contains("preroll_ms")) field_number(instrument, "preroll_ms", 0, 30000);
    if (instrument.contains("realtime")) require(instrument["realtime"].is_boolean(), "Invalid real-time setting");
    if (instrument.contains("state_base64")) {
        auto text = string_field(instrument, "state_base64", 1398104);
        try {
            auto state = cantabile::base64_decode(text);
            require(state.size() >= 48 && state.size() <= 1024 * 1024, "Invalid state size");
            require(std::memcmp(state.data(), "VST3", 4) == 0, "State must be a standard VST preset");
        } catch (const Error&) { throw; }
        catch (...) { throw Error("INVALID_PROJECT", "Invalid state encoding"); }
    }
}
json empty_project() {
    return {{"schema_version", 1}, {"name", "Untitled"}, {"ppq", 480}, {"smf_format", 1}, {"tracks", json::array()}};
}
void validate_event(const json& e) {
    require(e.is_object(), "Event must be an object");
    require(valid_id(string_field(e, "id", 128)), "Invalid event ID");
    auto tick = field_number(e, "tick", 0, max_tick);
    if (e.contains("order")) field_number(e, "order", 0, max_tick);
    auto kind = string_field(e, "kind", 32);
    if (kind == "note") {
        field_number(e, "pitch", 0, 127);
        auto duration = field_number(e, "duration", 1, max_tick);
        require(duration <= max_tick - tick, "Note end tick is out of range");
        field_number(e, "velocity", 1, 127);
        if (e.contains("release_velocity")) field_number(e, "release_velocity", 0, 127);
        if (e.contains("channel")) field_number(e, "channel", 0, 15);
        if (e.contains("off_order")) field_number(e, "off_order", 0, max_tick);
    } else if (kind == "midi" || kind == "meta" || kind == "sysex") {
        require(e.contains("data") && e.at("data").is_array(), "Event data must be a byte array");
        const auto& data = e.at("data");
        require(data.size() <= 1024 * 1024, "Event payload exceeds 1 MiB", "LIMIT_EXCEEDED");
        for (const auto& b : data) number(b, 0, kind == "midi" ? 127 : 255, "Event byte");
        if (kind == "midi") {
            auto status = field_number(e, "status", 0x80, 0xef);
            auto length = ((status & 0xf0) == 0xc0 || (status & 0xf0) == 0xd0) ? 1U : 2U;
            require(data.size() == length, "Incorrect channel-event data length");
        } else if (kind == "meta") {
            auto type = field_number(e, "meta_type", 0, 127);
            require(type != 0x2f, "End-of-track is managed by track.end_tick");
            if (type == 0x51) {
                require(data.size() == 3, "Tempo must contain three bytes");
                require(data.at(0).get<unsigned>() != 0 || data.at(1).get<unsigned>() != 0 ||
                    data.at(2).get<unsigned>() != 0, "Tempo must be positive");
            }
            if (type == 0x58) require(data.size() == 4, "Time signature must contain four bytes");
            if (type == 0x59) require(data.size() == 2, "Key signature must contain two bytes");
        } else {
            auto status = field_number(e, "status", 0xf0, 0xf7);
            require(status == 0xf0 || status == 0xf7, "SysEx status must be F0 or F7");
        }
    } else throw Error("UNSUPPORTED_EVENT", "Unsupported event kind: " + kind);
}
void normalize(json& project) {
    require(project.is_object(), "Project must be an object");
    require(field_number(project, "schema_version", 1, 1) == 1, "Unsupported project schema");
    field_number(project, "ppq", 1, 32767);
    if (!project.contains("name")) project["name"] = "Untitled";
    string_field(project, "name");
    if (!project.contains("smf_format")) project["smf_format"] = 1;
    field_number(project, "smf_format", 0, 1);
    require(project.contains("tracks") && project.at("tracks").is_array(), "tracks must be an array");
    auto& tracks = project["tracks"];
    require(tracks.size() <= 256, "Project exceeds 256 tracks", "LIMIT_EXCEEDED");
    std::set<std::string> ids;
    std::size_t event_count = 0;
    for (auto& track : tracks) {
        require(track.is_object(), "Track must be an object");
        if (!track.contains("events")) track["events"] = json::array();
        require(track["events"].is_array(), "Track events must be an array");
        event_count += track["events"].size();
        require(event_count <= max_events, "Project exceeds 100000 events", "LIMIT_EXCEEDED");
        if (track.contains("id")) {
            auto id = string_field(track, "id", 128);
            require(valid_id(id) && ids.insert(id).second, "Invalid or duplicate ID: " + id);
        }
        for (auto& event : track["events"]) {
            require(event.is_object(), "Event must be an object");
            if (event.contains("id")) {
                auto id = string_field(event, "id", 128);
                require(valid_id(id) && ids.insert(id).second, "Invalid or duplicate ID: " + id);
            }
        }
    }
    std::uint64_t serial = 1;
    auto assign_id = [&ids, &serial](json& obj, const char* prefix) {
        if (obj.contains("id")) return;
        std::string id;
        do { id = std::string(prefix) + std::to_string(serial++); } while (ids.count(id));
        ids.insert(id); obj["id"] = id;
    };
    for (auto& track : tracks) {
        assign_id(track, "track-");
        if (!track.contains("name")) track["name"] = "MIDI Track";
        string_field(track, "name");
        if (!track.contains("channel")) track["channel"] = 0;
        field_number(track, "channel", 0, 15);
        if (track.contains("render_channel")) field_number(track, "render_channel", 0, 15);
        for (const char* field : {"muted", "solo"})
            if (track.contains(field)) require(track[field].is_boolean(), std::string(field) + " must be boolean");
        if (track.contains("instrument")) validate_instrument(track["instrument"]);
        if (track.contains("provenance")) validate_provenance(track["provenance"]);
        for (const auto& [field, lo, hi] : {std::tuple{"volume", 0., 4.}, std::tuple{"pan", -1., 1.}})
            if (track.contains(field)) require(track[field].is_number() && std::isfinite(track[field].get<double>()) &&
                track[field].get<double>() >= lo && track[field].get<double>() <= hi, std::string("Invalid ") + field);
        if (track.contains("end_tick")) field_number(track, "end_tick", 0, max_tick);
        for (auto& event : track["events"]) { assign_id(event, "event-"); validate_event(event); }
    }
    require(project.dump().size() <= max_json_bytes, "Project exceeds 32 MiB", "LIMIT_EXCEEDED");
}
json& find_track(json& project, const json& command) {
    auto id = string_field(command, "track_id", 128);
    for (auto& track : project["tracks"]) if (track["id"] == id) return track;
    throw Error("NOT_FOUND", "Track does not exist: " + id);
}
json& find_event(json& track, const json& command) {
    auto id = string_field(command, "event_id", 128);
    for (auto& event : track["events"]) if (event["id"] == id) return event;
    throw Error("NOT_FOUND", "Event does not exist: " + id);
}
void patch(json& target, const json& value, const std::set<std::string>& allowed) {
    require(value.is_object(), "patch must be an object", "INVALID_REQUEST");
    for (auto it = value.begin(); it != value.end(); ++it) {
        require(allowed.count(it.key()), "Unsupported patch field: " + it.key(), "INVALID_REQUEST");
        target[it.key()] = it.value();
    }
}
void apply_command(json& project, const json& command) {
    require(command.is_object(), "Command must be an object", "INVALID_REQUEST");
    auto op = string_field(command, "op", 64);
    if (op == "track.add") {
        require(command.contains("track") && command["track"].is_object(), "track must be an object", "INVALID_REQUEST");
        auto track = command["track"];
        if (!track.contains("id")) {
            auto copy = project; copy["tracks"].push_back(track); normalize(copy);
            track = copy["tracks"].back();
        }
        project["tracks"].push_back(track);
    } else if (op == "track.import_midi") {
        // Append only: never replace input, timing, assignments or other tracks.
        auto id = string_field(command, "track_id", 128);
        require(valid_id(id), "Invalid destination track ID");
        for (const auto& existing : project["tracks"]) require(existing["id"] != id, "Destination already exists", "TRACK_EXISTS");
        const auto& provenance = command.at("provenance"); validate_provenance(provenance);
        json imported;
        try { imported = cantabile::import_midi(cantabile::base64_decode(string_field(command, "data_base64", 1398104))); }
        catch (const std::exception& e) { throw Error("INVALID_MIDI", e.what()); }
        require(imported["ppq"] == project["ppq"] && time_map(imported, provenance["end_tick"]) == time_map(project, provenance["end_tick"]),
            "Capture timing differs from the current project", "CAPTURE_TIMING_CONFLICT");
        require(imported["tracks"].size() == 2, "Capture must contain conductor and output tracks");
        json events = json::array();
        for (const auto& track : imported["tracks"]) for (auto e : track["events"]) {
            if (e["kind"] == "meta") continue;
            require(e["kind"] == "midi", "Unsupported capture data");
            e.erase("id"); events.push_back(std::move(e));
        }
        require(!events.empty(), "Capture contains no events", "CAPTURE_EMPTY");
        project["smf_format"] = 1;
        project["tracks"].push_back({{"id", id}, {"name", string_field(command, "name", 128)},
            {"channel", 0}, {"muted", true}, {"events", events}, {"end_tick", imported["tracks"][1]["end_tick"]}, {"provenance", provenance}});
    } else if (op == "track.remove") {
        auto& track = find_track(project, command);
        auto id = track["id"];
        auto& tracks = project["tracks"];
        for (auto it = tracks.begin(); it != tracks.end(); ++it) if ((*it)["id"] == id) { tracks.erase(it); break; }
    } else if (op == "track.update") {
        auto& track = find_track(project, command);
        const auto& value = command.at("patch");
        patch(track, value, {"name", "channel", "render_channel", "instrument", "muted", "solo", "end_tick", "volume", "pan"});
        if (track.contains("instrument") && track["instrument"].is_null()) track.erase("instrument");
        if (track.contains("render_channel") && track["render_channel"].is_null()) track.erase("render_channel");
        if (value.contains("name") && value["name"].is_string()) {
            // Preserve untouched imported track-name bytes exactly; an explicit
            // rename updates the first naming event as well as the display name.
            for (auto& event : track["events"]) {
                if (event.value("kind", "") == "meta" && event.value("meta_type", -1) == 3) {
                    auto name = value["name"].get<std::string>();
                    event["data"] = std::vector<std::uint8_t>(name.begin(), name.end());
                    break;
                }
            }
        }
    } else if (op == "project.update") {
        patch(project, command.at("patch"), {"name", "ppq", "smf_format"});
    } else if (op == "event.add") {
        auto& track = find_track(project, command);
        require(command.contains("event") && command["event"].is_object(), "event must be an object", "INVALID_REQUEST");
        // Assign missing event IDs once during final normalization. Rebuilding a
        // global ID set for each note would make a bulk composition quadratic.
        track["events"].push_back(command["event"]);
    } else if (op == "event.remove") {
        auto& track = find_track(project, command);
        auto id = find_event(track, command)["id"];
        auto& events = track["events"];
        for (auto it = events.begin(); it != events.end(); ++it) if ((*it)["id"] == id) { events.erase(it); break; }
    } else if (op == "event.update") {
        auto& event = find_event(find_track(project, command), command);
        patch(event, command.at("patch"), {"tick", "order", "off_order", "kind", "pitch", "duration", "velocity", "release_velocity", "channel", "status", "meta_type", "data"});
    } else if (op == "events.transpose" || op == "events.quantize") {
        auto& track = find_track(project, command);
        auto start = command.contains("start_tick") ? field_number(command, "start_tick", 0, max_tick) : 0;
        auto end = command.contains("end_tick") ? field_number(command, "end_tick", 0, max_tick) : max_tick;
        require(end >= start, "Invalid selection range", "INVALID_REQUEST");
        auto amount = op == "events.transpose" ? field_number(command, "semitones", -127, 127) : field_number(command, "grid", 1, max_tick);
        for (auto& event : track["events"]) {
            auto tick = field_number(event, "tick", 0, max_tick);
            if (tick < start || tick >= end) continue;
            auto kind = event.at("kind").get<std::string>();
            bool note = kind == "note";
            bool raw_note = kind == "midi" && ((event.at("status").get<int>() & 0xe0) == 0x80);
            bool raw_poly = kind == "midi" && ((event.at("status").get<int>() & 0xf0) == 0xa0);
            if (op == "events.transpose") {
                if (note) event["pitch"] = event.at("pitch").get<std::int64_t>() + amount;
                else if (raw_note || raw_poly) event["data"][0] = event["data"][0].get<std::int64_t>() + amount;
            } else if (note || raw_note) {
                auto q = tick / amount + (tick % amount >= (amount + 1) / 2 ? 1 : 0);
                require(q <= max_tick / amount, "Quantized tick overflows");
                event["tick"] = q * amount;
            }
        }
    } else throw Error("UNSUPPORTED_OP", "Unsupported edit command: " + op);
}

struct Snapshot { json project; std::size_t bytes; };
class Engine {
    json project_ = empty_project();
    std::uint64_t revision_ = 0;
    std::deque<Snapshot> undo_, redo_;
    void guard(const json& request) const {
        require(request.contains("expected_revision"), "expected_revision is required", "INVALID_REQUEST");
        auto expected = number(request["expected_revision"], 0, std::numeric_limits<std::int64_t>::max(), "expected_revision");
        require(static_cast<std::uint64_t>(expected) == revision_, "Project revision changed", "REVISION_CONFLICT");
    }
    void push(std::deque<Snapshot>& history, const json& project) {
        history.push_back({project, project.dump().size()});
        std::size_t size = 0;
        for (const auto& item : history) size += item.bytes;
        while (history.size() > 64 || size > 64 * 1024 * 1024) {
            size -= history.front().bytes; history.pop_front();
        }
    }
    void advance() {
        require(revision_ < static_cast<std::uint64_t>(std::numeric_limits<std::int64_t>::max()), "Revision limit reached", "LIMIT_EXCEEDED");
        ++revision_;
    }
    json state() const {
        return {{"ok", true}, {"revision", revision_}, {"project", project_}, {"can_undo", !undo_.empty()}, {"can_redo", !redo_.empty()}};
    }
public:
    std::mutex mutex;
    json request(const json& request) {
        require(request.is_object(), "Request must be an object", "INVALID_REQUEST");
        auto op = string_field(request, "op", 64);
        if (op == "project.get") return state();
        if (op == "performance.validate") { cantabile::validate_performance(request.at("data")); return {{"ok", true}}; }
        if (op == "audio.plan") {
            if (request.contains("expected_revision")) guard(request);
            const auto selected = request.value("track_id", std::string{});
            bool found = selected.empty(), any_solo = false;
            std::int64_t end = 0;
            json conductor = {{"id", "conductor"}, {"channel", 0}, {"events", json::array()}};
            for (const auto& track : project_["tracks"]) {
                any_solo = any_solo || track.value("solo", false);
                end = std::max(end, track.value("end_tick", std::int64_t{0}));
                for (const auto& e : track["events"]) {
                    auto tick = e.at("tick").get<std::int64_t>();
                    end = std::max(end, tick + (e["kind"] == "note" ? e.at("duration").get<std::int64_t>() : 0));
                    if (e["kind"] == "meta" && (e["meta_type"] == 81 || e["meta_type"] == 88)) conductor["events"].push_back(e);
                }
            }
            conductor["end_tick"] = end;
            auto parts = json::array();
            std::size_t midi_bytes = 0;
            for (const auto& track : project_["tracks"]) {
                if (!selected.empty() && track["id"] != selected) continue;
                if (!selected.empty()) found = true;
                if (selected.empty() && (track.value("muted", false) || (any_solo && !track.value("solo", false)))) continue;
                auto part = track; part["events"] = json::array(); part["end_tick"] = end;
                for (const auto& e : track["events"]) if (e["kind"] != "meta") {
                    auto event = e;
                    if (track.contains("render_channel")) {
                        int channel = track["render_channel"].get<int>();
                        if (event["kind"] == "note") event["channel"] = channel;
                        else if (event["kind"] == "midi") event["status"] = (event["status"].get<int>() & 0xf0) | channel;
                    }
                    part["events"].push_back(std::move(event));
                }
                if (selected.empty() && part["events"].empty()) continue;
                require(parts.size() < 32, "Render exceeds 32 active tracks", "LIMIT_EXCEEDED");
                auto score = project_; score["smf_format"] = 1;
                score["tracks"] = json::array({conductor, part});
                const auto bytes = cantabile::export_midi(score);
                midi_bytes += bytes.size();
                require(bytes.size() <= 1024 * 1024 && midi_bytes <= 16 * 1024 * 1024, "Render MIDI snapshot exceeds limit", "LIMIT_EXCEEDED");
                parts.push_back({{"track_id", track["id"]}, {"instrument", track.value("instrument", json::object())},
                    {"volume", track.value("volume", 1.)}, {"pan", track.value("pan", 0.)},
                    {"midi_base64", cantabile::base64_encode(bytes)}});
            }
            require(found, "Track does not exist", "NOT_FOUND");
            return {{"ok", true}, {"revision", revision_}, {"parts", parts}};
        }
        if (op == "midi.export") {
            if (request.contains("expected_revision")) guard(request);
            auto data = project_;
            if (request.contains("format")) data["smf_format"] = number(request["format"], 0, 1, "format");
            if (request.contains("playback")) require(request["playback"].is_boolean(), "playback must be boolean", "INVALID_REQUEST");
            if (request.value("playback", false)) {
                bool any_solo = false;
                for (const auto& track : data["tracks"]) any_solo = any_solo || track.value("solo", false);
                for (auto& track : data["tracks"]) {
                    if (track.value("muted", false) || (any_solo && !track.value("solo", false))) {
                        std::int64_t end = track.value("end_tick", std::int64_t{0});
                        for (const auto& event : track["events"]) {
                            auto at = event.at("tick").get<std::int64_t>();
                            if (event.at("kind") == "note") at += event.at("duration").get<std::int64_t>();
                            end = std::max(end, at);
                        }
                        track["end_tick"] = end;
                        auto& events = track["events"];
                        events.erase(std::remove_if(events.begin(), events.end(), [](const json& e) {
                            return e.at("kind") != "meta";
                        }), events.end());
                    }
                }
            }
            try {
                auto bytes = cantabile::export_midi(data);
                return {{"ok", true}, {"revision", revision_}, {"data_base64", cantabile::base64_encode(bytes)}, {"size", bytes.size()}};
            } catch (const std::exception& e) { throw Error("MIDI_EXPORT_FAILED", e.what()); }
        }
        guard(request);
        auto project_budget = request.contains("max_project_bytes")
            ? static_cast<std::size_t>(field_number(request, "max_project_bytes", 1, max_json_bytes)) : max_json_bytes;
        auto demand_budget = [project_budget](const json& candidate) {
            require(candidate.dump().size() <= project_budget, "Project exceeds caller's byte budget", "LIMIT_EXCEEDED");
        };
        if (op == "project.load" || op == "midi.import" || op == "project.new") {
            json candidate;
            if (op == "project.new") candidate = empty_project();
            else if (op == "project.load") {
                require(request.contains("data"), "project.load requires data", "INVALID_REQUEST"); candidate = request["data"];
            } else {
                auto text = string_field(request, "data_base64", (16 * 1024 * 1024 + 2) / 3 * 4);
                try { candidate = cantabile::import_midi(cantabile::base64_decode(text)); }
                catch (const std::exception& e) { throw Error("INVALID_MIDI", e.what()); }
            }
            normalize(candidate); demand_budget(candidate);
            advance(); project_ = std::move(candidate); undo_.clear(); redo_.clear();
            return state();
        }
        if (op == "edit.batch") {
            require(request.contains("commands") && request["commands"].is_array(), "commands must be an array", "INVALID_REQUEST");
            require(request["commands"].size() <= 10000, "Batch exceeds 10000 commands", "LIMIT_EXCEEDED");
            if (request["commands"].empty()) return state();
            auto candidate = project_;
            for (const auto& command : request["commands"]) apply_command(candidate, command);
            normalize(candidate);
            demand_budget(candidate);
            push(undo_, project_); advance(); project_ = std::move(candidate); redo_.clear();
            return state();
        }
        if (op == "history.undo" || op == "history.redo") {
            auto& from = op == "history.undo" ? undo_ : redo_;
            auto& to = op == "history.undo" ? redo_ : undo_;
            require(!from.empty(), "No history entry available", "NO_HISTORY");
            demand_budget(from.back().project);
            push(to, project_); advance(); project_ = std::move(from.back().project); from.pop_back();
            return state();
        }
        throw Error("UNSUPPORTED_OP", "Unsupported operation: " + op);
    }
};

json failure(const std::string& code, const std::string& message) {
    return {{"ok", false}, {"error", {{"code", code}, {"message", message}}}};
}
char* allocate_response(const json& response) {
    auto data = response.dump();
    auto result = std::make_unique<char[]>(data.size() + 1);
    std::memcpy(result.get(), data.c_str(), data.size() + 1);
    return result.release();
}
}

extern "C" {
void* cantabile_create(void) {
    try { return new Engine(); } catch (...) { return nullptr; }
}
void cantabile_destroy(void* engine) { delete static_cast<Engine*>(engine); }
char* cantabile_request(void* handle, const char* input) {
    try {
        if (!handle || !input) return allocate_response(failure("INVALID_REQUEST", "Null engine or request"));
        std::size_t size = 0;
        while (size <= max_json_bytes && input[size] != '\0') ++size;
        if (size > max_json_bytes) return allocate_response(failure("LIMIT_EXCEEDED", "Request exceeds 32 MiB"));
        auto request = json::parse(input, input + size, [](int depth, json::parse_event_t, json&) {
            require(depth <= 64, "JSON nesting exceeds 64 levels", "LIMIT_EXCEEDED");
            return true;
        }, true, false);
        auto& engine = *static_cast<Engine*>(handle);
        std::lock_guard<std::mutex> lock(engine.mutex);
        return allocate_response(engine.request(request));
    } catch (const Error& e) {
        try { return allocate_response(failure(e.code, e.what())); } catch (...) { return nullptr; }
    } catch (const json::exception& e) {
        try { return allocate_response(failure("INVALID_REQUEST", e.what())); } catch (...) { return nullptr; }
    } catch (const std::exception& e) {
        try { return allocate_response(failure("INTERNAL_ERROR", e.what())); } catch (...) { return nullptr; }
    } catch (...) { return nullptr; }
}
void cantabile_free(char* response) { delete[] response; }
}

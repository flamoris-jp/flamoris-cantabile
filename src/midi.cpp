#include "cantabile/midi.hpp"

#include <algorithm>
#include <array>
#include <limits>
#include <stdexcept>
#include <string>
#include <utility>

namespace cantabile {
namespace {
using json = nlohmann::json;
using Bytes = std::vector<std::uint8_t>;
constexpr std::size_t max_bytes = 16 * 1024 * 1024;
constexpr std::size_t max_events = 100000;
constexpr std::size_t max_tracks = 256;
constexpr std::uint32_t max_delta = 0x0fffffff;

[[noreturn]] void fail(const std::string& message) {
    throw std::runtime_error("MIDI: " + message);
}

class Reader {
public:
    Reader(const Bytes& bytes, std::size_t begin, std::size_t end)
        : bytes_(bytes), pos_(begin), end_(end) {
        if (begin > end || end > bytes.size()) fail("invalid byte range");
    }
    std::size_t position() const { return pos_; }
    std::size_t remaining() const { return end_ - pos_; }
    std::uint8_t byte() {
        if (!remaining()) fail("truncated input");
        return bytes_[pos_++];
    }
    std::uint16_t u16() {
        auto a = byte();
        return static_cast<std::uint16_t>((a << 8) | byte());
    }
    std::uint32_t u32() {
        std::uint32_t result = 0;
        for (int i = 0; i < 4; ++i) result = (result << 8) | byte();
        return result;
    }
    std::uint32_t vlq() {
        std::uint32_t value = 0;
        for (int i = 0; i < 4; ++i) {
            auto b = byte();
            value = (value << 7) | (b & 0x7f);
            if (!(b & 0x80)) return value;
        }
        fail("VLQ exceeds four bytes");
    }
    Bytes take(std::size_t length) {
        if (length > remaining()) fail("truncated event or chunk");
        Bytes result(bytes_.begin() + static_cast<std::ptrdiff_t>(pos_),
                     bytes_.begin() + static_cast<std::ptrdiff_t>(pos_ + length));
        pos_ += length;
        return result;
    }
    void expect(const char* text) {
        for (int i = 0; i < 4; ++i) if (byte() != static_cast<std::uint8_t>(text[i])) fail("unexpected chunk identifier");
    }
private:
    const Bytes& bytes_;
    std::size_t pos_, end_;
};

void push(Bytes& bytes, std::uint8_t b) {
    if (bytes.size() >= max_bytes) fail("encoded MIDI exceeds 16 MiB");
    bytes.push_back(b);
}
void append(Bytes& bytes, const Bytes& data) {
    if (data.size() > max_bytes - bytes.size()) fail("encoded MIDI exceeds 16 MiB");
    bytes.insert(bytes.end(), data.begin(), data.end());
}
void u16(Bytes& bytes, std::uint16_t n) {
    push(bytes, static_cast<std::uint8_t>(n >> 8)); push(bytes, static_cast<std::uint8_t>(n));
}
void u32(Bytes& bytes, std::uint32_t n) {
    for (int i = 3; i >= 0; --i) push(bytes, static_cast<std::uint8_t>(n >> (i * 8)));
}
void vlq(Bytes& bytes, std::uint64_t n) {
    if (n > max_delta) fail("delta-time or event payload exceeds SMF VLQ range");
    std::array<std::uint8_t, 4> temp{};
    int index = 3;
    temp[index] = static_cast<std::uint8_t>(n & 0x7f);
    while ((n >>= 7) != 0) temp[--index] = static_cast<std::uint8_t>((n & 0x7f) | 0x80);
    for (; index < 4; ++index) push(bytes, temp[index]);
}

std::int64_t integer(const json& object, const char* key, std::int64_t min, std::int64_t max) {
    if (!object.contains(key) || !object[key].is_number_integer()) fail(std::string("integer required: ") + key);
    const auto& value = object[key];
    if (value.is_number_unsigned() && value.get<std::uint64_t>() > static_cast<std::uint64_t>(max)) fail(std::string("out of range: ") + key);
    const auto result = value.get<std::int64_t>();
    if (result < min || result > max) fail(std::string("out of range: ") + key);
    return result;
}
std::int64_t optional_integer(const json& object, const char* key, std::int64_t fallback, std::int64_t min, std::int64_t max) {
    return object.contains(key) ? integer(object, key, min, max) : fallback;
}
Bytes data_bytes(const json& event, int max_value = 255) {
    if (!event.contains("data") || !event["data"].is_array()) fail("event data must be an array of bytes");
    const auto& data = event["data"];
    if (data.size() > max_bytes) fail("event payload exceeds 16 MiB");
    Bytes result;
    result.reserve(data.size());
    for (const auto& value : data) {
        if (!value.is_number_integer() || (value.is_number_unsigned() && value.get<std::uint64_t>() > static_cast<unsigned>(max_value))) fail("invalid event data byte");
        auto n = value.get<std::int64_t>();
        if (n < 0 || n > max_value) fail("invalid event data byte");
        result.push_back(static_cast<std::uint8_t>(n));
    }
    return result;
}
int channel_data_size(std::uint8_t status) {
    if (status < 0x80 || status >= 0xf0) fail("unsupported channel status");
    return ((status & 0xf0) == 0xc0 || (status & 0xf0) == 0xd0) ? 1 : 2;
}
bool valid_utf8(const Bytes& data) {
    try { (void)json(std::string(data.begin(), data.end())).dump(); return true; }
    catch (const json::exception&) { return false; }
}

struct EncodedEvent {
    std::int64_t tick;
    std::int64_t order;
    int priority;
    Bytes bytes;
};

Bytes channel_event(std::uint8_t status, const Bytes& data) {
    if (data.size() != static_cast<std::size_t>(channel_data_size(status))) fail("channel event has wrong data length");
    Bytes result{status};
    append(result, data);
    return result;
}

Bytes encode_track(const json& track, std::size_t& total_events) {
    if (!track.is_object() || !track.contains("events") || !track["events"].is_array()) fail("track events must be an array");
    std::vector<EncodedEvent> items;
    auto end_tick = optional_integer(track, "end_tick", 0, 0, std::numeric_limits<std::int64_t>::max());
    auto channel = optional_integer(track, "channel", 0, 0, 15);
    bool has_name = false;
    for (const auto& event : track["events"]) {
        if (++total_events > max_events) fail("more than 100000 encoded MIDI events");
        if (!event.is_object()) fail("event must be an object");
        auto tick = integer(event, "tick", 0, std::numeric_limits<std::int64_t>::max());
        auto order = optional_integer(event, "order", 1, 0, std::numeric_limits<std::int64_t>::max());
        auto kind = event.at("kind").get<std::string>();
        Bytes encoded;
        int priority = 1;
        if (kind == "note") {
            if (++total_events > max_events) fail("more than 100000 encoded MIDI events");
            auto pitch = integer(event, "pitch", 0, 127);
            auto duration = integer(event, "duration", 1, std::numeric_limits<std::int64_t>::max());
            auto velocity = integer(event, "velocity", 1, 127);
            auto release = optional_integer(event, "release_velocity", 0, 0, 127);
            auto note_channel = optional_integer(event, "channel", channel, 0, 15);
            if (duration > std::numeric_limits<std::int64_t>::max() - tick) fail("note end tick overflows");
            encoded = channel_event(static_cast<std::uint8_t>(0x90 | note_channel), {static_cast<std::uint8_t>(pitch), static_cast<std::uint8_t>(velocity)});
            auto off_order = optional_integer(event, "off_order", 0, 0, std::numeric_limits<std::int64_t>::max());
            items.push_back({tick + duration, off_order, 0, channel_event(static_cast<std::uint8_t>(0x80 | note_channel), {static_cast<std::uint8_t>(pitch), static_cast<std::uint8_t>(release)})});
            end_tick = std::max(end_tick, tick + duration);
        } else if (kind == "midi") {
            auto status = integer(event, "status", 0x80, 0xef);
            encoded = channel_event(static_cast<std::uint8_t>(status), data_bytes(event, 127));
        } else if (kind == "meta") {
            auto type = integer(event, "meta_type", 0, 127);
            if (type == 0x2f) fail("end-of-track is generated from track.end_tick; do not add an EOT event");
            auto data = data_bytes(event);
            encoded = {0xff, static_cast<std::uint8_t>(type)};
            vlq(encoded, data.size()); append(encoded, data);
            if (type == 3) has_name = true;
        } else if (kind == "sysex") {
            auto status = integer(event, "status", 0xf0, 0xf7);
            if (status != 0xf0 && status != 0xf7) fail("SysEx status must be F0 or F7");
            auto data = data_bytes(event);
            encoded = {static_cast<std::uint8_t>(status)};
            vlq(encoded, data.size()); append(encoded, data);
        } else {
            fail("unsupported event kind: " + kind);
        }
        items.push_back({tick, order, priority, std::move(encoded)});
        end_tick = std::max(end_tick, tick);
    }
    // A fresh named track gets a standard Track Name event. Imported name
    // metadata is retained byte-for-byte, including non-UTF-8 text.
    if (!has_name && track.contains("name")) {
        auto name = track.at("name").get<std::string>();
        if (!name.empty()) {
            if (++total_events > max_events) fail("more than 100000 encoded MIDI events");
            Bytes encoded{0xff, 3};
            vlq(encoded, name.size()); append(encoded, Bytes(name.begin(), name.end()));
            items.insert(items.begin(), {0, 0, -1, std::move(encoded)});
        }
    }
    std::stable_sort(items.begin(), items.end(), [](const auto& a, const auto& b) {
        if (a.tick != b.tick) return a.tick < b.tick;
        if (a.order != b.order) return a.order < b.order;
        return a.priority < b.priority;
    });
    Bytes result;
    std::int64_t previous = 0;
    for (const auto& item : items) {
        vlq(result, static_cast<std::uint64_t>(item.tick - previous));
        append(result, item.bytes);
        previous = item.tick;
    }
    vlq(result, static_cast<std::uint64_t>(end_tick - previous));
    if (++total_events > max_events) fail("more than 100000 encoded MIDI events");
    append(result, {0xff, 0x2f, 0});
    return result;
}

} // namespace

json import_midi(const std::vector<std::uint8_t>& bytes) {
    if (bytes.size() > max_bytes) fail("input exceeds 16 MiB");
    Reader input(bytes, 0, bytes.size());
    input.expect("MThd");
    auto header_size = input.u32();
    if (header_size != 6) fail("only six-byte SMF headers are supported; header extensions are not discarded");
    auto format = input.u16();
    auto track_count = input.u16();
    auto division = input.u16();
    if (format > 1) fail("only SMF Type 0 and 1 are supported");
    if (!track_count || track_count > max_tracks || (format == 0 && track_count != 1)) fail("invalid or unsupported track count");
    if (!division || (division & 0x8000)) fail("only positive PPQ timing is supported, not SMPTE");
    json project{{"schema_version", 1}, {"name", ""}, {"ppq", division}, {"smf_format", format}, {"tracks", json::array()}};
    std::size_t total_events = 0;
    for (std::size_t index = 0; index < track_count; ++index) {
        input.expect("MTrk");
        auto length = input.u32();
        if (length > input.remaining()) fail("truncated track chunk");
        auto begin = input.position();
        Reader track(bytes, begin, begin + length);
        (void)input.take(length);
        json output{{"id", "track-" + std::to_string(index + 1)}, {"name", ""}, {"channel", 0}, {"events", json::array()}};
        std::int64_t tick = 0;
        std::int64_t order = 0;
        std::uint8_t running = 0;
        bool ended = false;
        while (track.remaining()) {
            if (++total_events > max_events) fail("more than 100000 MIDI events");
            auto delta = track.vlq();
            if (delta > std::numeric_limits<std::int64_t>::max() - tick) fail("absolute tick overflows");
            tick += delta;
            auto first = track.byte();
            auto status = first;
            bool running_data = first < 0x80;
            if (running_data) {
                if (!running) fail("data byte without running status");
                status = running;
            }
            json event{{"id", "event-" + std::to_string(total_events)}, {"tick", tick}, {"order", order++}};
            if (status >= 0x80 && status <= 0xef) {
                running = status;
                int count = channel_data_size(status);
                Bytes data;
                if (running_data) data.push_back(first);
                while (data.size() < static_cast<std::size_t>(count)) {
                    auto b = track.byte();
                    if (b >= 0x80) fail("channel event data byte has status bit set");
                    data.push_back(b);
                }
                event["kind"] = "midi"; event["status"] = status; event["data"] = data;
            } else if (status == 0xff) {
                running = 0;
                auto type = track.byte();
                if (type >= 0x80) fail("invalid meta event type");
                auto size = track.vlq();
                auto data = track.take(size);
                if (type == 0x2f) {
                    if (size) fail("end-of-track payload must be empty");
                    if (track.remaining()) fail("data follows end-of-track event");
                    ended = true;
                    break;
                }
                event["kind"] = "meta"; event["meta_type"] = type; event["data"] = data;
                if (type == 3 && output["name"] == "" && valid_utf8(data)) output["name"] = std::string(data.begin(), data.end());
            } else if (status == 0xf0 || status == 0xf7) {
                running = 0;
                auto size = track.vlq();
                event["kind"] = "sysex"; event["status"] = status; event["data"] = track.take(size);
            } else {
                fail("system-common/realtime status is not legal as an SMF event");
            }
            output["events"].push_back(std::move(event));
        }
        if (!ended) fail("track is missing its end-of-track event");
        output["end_tick"] = tick;
        project["tracks"].push_back(std::move(output));
    }
    if (input.remaining()) fail("unrecognized or trailing file chunks are not discarded");
    return project;
}

std::vector<std::uint8_t> export_midi(const json& project) {
    auto division = integer(project, "ppq", 1, 32767);
    if (!project.contains("tracks") || !project["tracks"].is_array() || project["tracks"].size() > max_tracks) fail("export permits at most 256 tracks");
    // An empty editor document still has a valid interoperable MIDI export.
    // This encoding-only fallback does not mutate the authoritative project.
    const json empty_tracks = json::array({{{"events", json::array()}}});
    const auto& tracks = project["tracks"].empty() ? empty_tracks : project["tracks"];
    auto format = optional_integer(project, "smf_format", 1, 0, 1);
    auto track_count = tracks.size();
    if (format == 0 && track_count != 1) fail("SMF Type 0 requires exactly one track");
    Bytes output{'M', 'T', 'h', 'd'};
    u32(output, 6); u16(output, static_cast<std::uint16_t>(format)); u16(output, static_cast<std::uint16_t>(track_count)); u16(output, static_cast<std::uint16_t>(division));
    std::size_t total_events = 0;
    for (const auto& track : tracks) {
        auto body = encode_track(track, total_events);
        append(output, {'M', 'T', 'r', 'k'});
        u32(output, static_cast<std::uint32_t>(body.size()));
        append(output, body);
    }
    return output;
}

} // namespace cantabile

#include "cantabile/midi.hpp"

#include <cassert>
#include <iostream>
#include <limits>
#include <stdexcept>

using nlohmann::json;
using Bytes = std::vector<std::uint8_t>;

static Bytes smf(const Bytes& track, int format = 0) {
    Bytes result{'M','T','h','d',0,0,0,6,0,static_cast<std::uint8_t>(format),0,1,1,0xe0,'M','T','r','k'};
    auto n = track.size();
    result.insert(result.end(), {static_cast<std::uint8_t>(n >> 24), static_cast<std::uint8_t>(n >> 16), static_cast<std::uint8_t>(n >> 8), static_cast<std::uint8_t>(n)});
    result.insert(result.end(), track.begin(), track.end());
    return result;
}

template<class F> static void rejects(F action) {
    bool rejected = false;
    try { action(); } catch (const std::exception&) { rejected = true; }
    assert(rejected);
}

int main() {
    // Running status, zero-velocity note-on, nonzero release velocity, CC and
    // both one-data-byte messages must keep their exact event bytes/order.
    auto bytes = smf({
        0,0xc2,8, 0,0xd2,70,
        0,0x92,60,100, 0,64,99,
        0x83,0x60,60,0, 0,0x82,64,31,
        0,0xb2,64,127, 0,0xe2,0,64,
        0,0xa2,60,40,
        0,0xff,0x2f,0});
    auto p = cantabile::import_midi(bytes);
    assert(p["smf_format"] == 0 && p["ppq"] == 480);
    assert(p["tracks"][0]["events"].size() == 9);
    assert(p["tracks"][0]["events"][4]["status"] == 0x92);
    assert(p["tracks"][0]["events"][5]["data"][1] == 31);
    assert(p["tracks"][0]["end_tick"] == 480);
    assert(cantabile::import_midi(cantabile::export_midi(p)) == p);
    assert(cantabile::export_midi(p) == cantabile::export_midi(p));
    auto empty = cantabile::import_midi(cantabile::export_midi(json{{"ppq",480},{"tracks",json::array()}}));
    assert(empty["tracks"].size() == 1 && empty["tracks"][0]["events"].empty());

    // Every truncation is rejected rather than publishing a partial project.
    for (std::size_t i = 0; i < bytes.size(); ++i) {
        Bytes prefix(bytes.begin(), bytes.begin() + static_cast<std::ptrdiff_t>(i));
        rejects([&] { (void)cantabile::import_midi(prefix); });
    }

    auto opaque = smf({0,0xff,0x7f,3,255,128,0, 0,0xf0,3,1,128,247,
                       0,0xf7,2,255,0, 0,0xff,3,1,255, 0,0xff,0x2f,0});
    auto preserved = cantabile::import_midi(opaque);
    assert(preserved["tracks"][0]["name"] == "");
    assert(cantabile::export_midi(preserved) == opaque);

    rejects([&] { (void)cantabile::import_midi(smf({0,0x90,60,100})); }); // no EOT
    rejects([&] { (void)cantabile::import_midi(smf({0,0xff,0x2f,0,0})); }); // trailing track data
    rejects([&] { (void)cantabile::import_midi(smf({0,0xff,0x2f,1,0})); }); // nonempty EOT
    rejects([&] { (void)cantabile::import_midi(smf({0x81,0x80,0x80,0x80,0})); }); // VLQ
    rejects([&] { (void)cantabile::import_midi(smf({0,0x90,60,128,0,0xff,0x2f,0})); });
    rejects([&] { (void)cantabile::import_midi(smf({0,60,100,0,0xff,0x2f,0})); });
    rejects([&] { (void)cantabile::import_midi(smf({0,0x90,60,100,0,0xff,1,0,0,60,0,0,0xff,0x2f,0})); });
    auto bad = bytes; bad[13] = 0; bad[12] = 0;
    rejects([&] { (void)cantabile::import_midi(bad); }); // zero division
    bad = bytes; bad[12] = 0x80;
    rejects([&] { (void)cantabile::import_midi(bad); }); // SMPTE
    bad = bytes; bad[9] = 2;
    rejects([&] { (void)cantabile::import_midi(bad); }); // Type2
    bad = bytes; bad[7] = 7;
    rejects([&] { (void)cantabile::import_midi(bad); }); // header extension
    bad = bytes; bad.push_back(0);
    rejects([&] { (void)cantabile::import_midi(bad); });

    json note1{{"id","a"},{"kind","note"},{"tick",0},{"pitch",60},{"duration",480},{"velocity",100},{"release_velocity",20}};
    json note2{{"id","b"},{"kind","note"},{"tick",480},{"pitch",60},{"duration",480},{"velocity",90}};
    json project{{"schema_version",1},{"ppq",480},{"tracks",json::array({{
        {"id","t"},{"name","Piano"},{"channel",0},{"events",json::array({note2,note1})}
    }})}};
    auto notes = cantabile::import_midi(cantabile::export_midi(project));
    auto events = notes["tracks"][0]["events"];
    assert(events[0]["kind"] == "meta" && events[0]["meta_type"] == 3);
    assert(events[2]["tick"] == 480 && events[2]["status"] == 0x80 && events[2]["data"][1] == 20);
    assert(events[3]["tick"] == 480 && events[3]["status"] == 0x90);

    auto invalid = project; invalid["tracks"][0]["events"][0]["pitch"] = 128;
    rejects([&] { (void)cantabile::export_midi(invalid); });
    invalid = project; invalid["tracks"][0]["events"][0]["tick"] = std::numeric_limits<std::uint64_t>::max();
    rejects([&] { (void)cantabile::export_midi(invalid); });
    invalid = project; invalid["tracks"][0]["end_tick"] = 0x10000000LL + 960;
    rejects([&] { (void)cantabile::export_midi(invalid); });
    invalid = project; invalid["smf_format"] = 0; invalid["tracks"].push_back(invalid["tracks"][0]);
    rejects([&] { (void)cantabile::export_midi(invalid); });
    std::cout << "MIDI codec tests passed\n";
}

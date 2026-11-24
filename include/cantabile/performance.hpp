// SPDX-License-Identifier: Apache-2.0
#pragma once
#include <nlohmann/json.hpp>
#include <set>
#include <stdexcept>
#include <cmath>
#include <algorithm>

namespace cantabile {
inline void validate_performance(const nlohmann::json& value) {
    using json = nlohmann::json;
    auto demand = [](bool condition) { if (!condition) throw std::invalid_argument("Invalid performance metadata"); };
    auto closed = [&](const json& object, const std::set<std::string>& fields) {
        demand(object.is_object()); for (auto it=object.begin();it!=object.end();++it) demand(fields.contains(it.key()));
    };
    auto text = [&](const json& object, const char* key, std::size_t max) {
        demand(object.contains(key) && object[key].is_string()); const auto s = object[key].get<std::string>();
        demand(!s.empty() && s.size() <= max && std::none_of(s.begin(), s.end(), [](unsigned char c){return c<32 || c==127;}));
    };
    auto integer = [&](const json& object, const char* key, std::int64_t lo, std::int64_t hi) {
        demand(object.contains(key) && object[key].is_number_integer());
        demand(object[key].get<long double>() >= lo && object[key].get<long double>() <= hi);
    };
    closed(value, {"version", "note_numbering", "library", "source", "requires_playing", "tempo_sync", "controls", "capture_evidence"});
    demand(value.dump().size() <= 16384);
    integer(value, "version", 1, 1); demand(value.value("note_numbering", "") == "midi_0_127"); text(value,"source",512);
    if (value.contains("library")) text(value,"library",128);
    for (const char* f : {"requires_playing", "tempo_sync"}) if (value.contains(f)) demand(value[f].is_boolean());
    demand(value.contains("controls") && value["controls"].is_array() && value["controls"].size() <= 16);
    std::set<std::string> ids;
    for (const auto& c : value["controls"]) {
        closed(c, {"id", "name", "kind", "notes", "channel", "cc", "parameter_id", "source", "verification", "evidence"});
        text(c,"id",64); demand(ids.insert(c["id"].get<std::string>()).second); text(c,"name",128); text(c,"source",512);
        const auto kind = c.value("kind", "");
        demand(kind=="trigger" || kind=="keyswitch" || kind=="pattern" || kind=="cc" || kind=="parameter" || kind=="gui");
        const auto verification = c.value("verification", "");
        demand(verification=="unverified" || verification=="gui_only" || verification=="midi_documented" || verification=="parameter_documented" || verification=="operator_verified");
        if (verification=="operator_verified") text(c,"evidence",1024);
        else if (c.contains("evidence")) text(c,"evidence",1024);
        if (c.contains("channel")) integer(c,"channel",0,15);
        if (kind=="trigger" || kind=="keyswitch" || kind=="pattern") {
            demand(c.contains("notes") && c["notes"].is_array() && !c["notes"].empty() && c["notes"].size() <= 16);
            for (const auto& n : c["notes"]) { demand(n.is_number_integer()); demand(n.get<long double>()>=0 && n.get<long double>()<=127); }
        } else demand(!c.contains("notes"));
        if (kind=="cc") integer(c,"cc",0,127); else demand(!c.contains("cc"));
        if (kind=="parameter") integer(c,"parameter_id",0,4294967294LL); else demand(!c.contains("parameter_id"));
    }
    if (value.contains("capture_evidence")) {
        const auto& e=value["capture_evidence"];
        closed(e,{"state_sha256","input_sha256","midi_sha256","class_id","plugin_version","event_count","source_revision"});
        demand(e.size()==7);
        for (const char* f : {"state_sha256","input_sha256","midi_sha256"}) {
            text(e,f,64); const auto s=e[f].get<std::string>(); demand(s.size()==64);
            demand(std::all_of(s.begin(),s.end(),[](char c){return (c>='0'&&c<='9')||(c>='a'&&c<='f');}));
        }
        text(e,"class_id",32); text(e,"plugin_version",256); integer(e,"event_count",1,8192); integer(e,"source_revision",0,9007199254740991LL);
    }
}
}

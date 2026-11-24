// SPDX-License-Identifier: Apache-2.0
// Included after Score: event capture shares the renderer's exact input timeline.
class MidiCapture final : public EventList {
    const Options& options;
    const Score& score;
    struct Record { Event event; std::int64_t frame; };
    std::vector<Record> records;
    std::int64_t position = 0;
    int32 samples = 0;
    bool preroll = false, overflow = false, invalid = false, unsupported = false;
    std::uint64_t total = 0, discarded_preroll = 0, discarded_before = 0, discarded_tail = 0;
    std::map<int, std::uint64_t> unsupported_types;
    std::map<std::pair<int,int>, int> outstanding;
    long double tick_at(std::int64_t frame) const {
        auto it = std::upper_bound(score.exact_timings.begin(), score.exact_timings.end(), static_cast<long double>(frame),
            [](long double f, const Timing& t) { return f < t.exact_frame; });
        const auto* active = it == score.exact_timings.begin() ? &score.exact_timings.front() : &*std::prev(it);
        return active->tick + (frame - active->exact_frame) * 1000000.L * score.ppq /
               (static_cast<long double>(active->tempo_us) * sample_rate);
    }
public:
    MidiCapture(const Options& o, const Score& s) : EventList(o.max_events), options(o), score(s) { records.reserve(o.max_events); }
    void begin(std::int64_t frame, int32 count, bool preparing) {
        clear(); position = frame; samples = count; preroll = preparing;
    }
    tresult PLUGIN_API addEvent(Event& e) override {
        // Bound *all* phases/types, including ignored events, before allocation.
        if (++total > static_cast<std::uint64_t>(options.max_events)) { overflow = true; return kResultFalse; }
        if (e.sampleOffset < 0 || e.sampleOffset >= samples || e.busIndex != 0) { invalid = true; return kResultFalse; }
        if (e.type != Event::kNoteOnEvent && e.type != Event::kNoteOffEvent && e.type != Event::kPolyPressureEvent) {
            unsupported = true; ++unsupported_types[e.type]; return kResultFalse;
        }
        const auto channel = e.type == Event::kNoteOnEvent ? e.noteOn.channel : e.type == Event::kNoteOffEvent ? e.noteOff.channel : e.polyPressure.channel;
        const auto pitch = e.type == Event::kNoteOnEvent ? e.noteOn.pitch : e.type == Event::kNoteOffEvent ? e.noteOff.pitch : e.polyPressure.pitch;
        const auto value = e.type == Event::kNoteOnEvent ? e.noteOn.velocity : e.type == Event::kNoteOffEvent ? e.noteOff.velocity : e.polyPressure.pressure;
        if (channel < 0 || channel > 15 || pitch < 0 || pitch > 127 || !std::isfinite(value) || value < 0 || value > 1 ||
            (e.type == Event::kNoteOnEvent && (!std::isfinite(e.noteOn.tuning) || e.noteOn.tuning != 0)) ||
            (e.type == Event::kNoteOffEvent && (!std::isfinite(e.noteOff.tuning) || e.noteOff.tuning != 0))) {
            invalid = true; return kResultFalse;
        }
        if (preroll) { ++discarded_preroll; return kResultOk; }
        const auto frame = position + e.sampleOffset;
        const auto tick = tick_at(frame);
        if (tick < options.start_tick) { ++discarded_before; return kResultOk; }
        auto key = std::pair{static_cast<int>(channel), static_cast<int>(pitch)};
        if (frame > score.frames - tail_frames && !(e.type == Event::kNoteOffEvent && outstanding[key] > 0)) {
            ++discarded_tail; return kResultOk;
        }
        if (e.type == Event::kNoteOnEvent) ++outstanding[key];
        if (e.type == Event::kNoteOffEvent && outstanding[key] > 0) --outstanding[key];
        records.push_back({e, frame});
        return EventList::addEvent(e);
    }
    void finish_block() {
        if (overflow) fail("MIDI_OUTPUT_LIMIT", "Output event limit exceeded; no MIDI published");
        if (unsupported) {
            auto types = json::array();
            for (const auto& [type, count] : unsupported_types) types.push_back(type);
            script_failure["capture"] = {{"unsupported_event_types", types}, {"total_events", total}};
            fail("UNSUPPORTED_OUTPUT_EVENT", "Plugin emitted unsupported event types; no partial MIDI published");
        }
        if (invalid) fail("INVALID_OUTPUT_EVENT", "Output offset/bus/channel/note/value/tuning is unsupported or invalid");
    }
    json publish(const fs::path& output) {
        std::stable_sort(records.begin(), records.end(), [](const auto& a, const auto& b) { return a.frame < b.frame; });
        json events = json::array(), diagnostics = json::array();
        std::map<std::pair<int,int>, int> active;
        std::uint64_t unmatched_off = 0, overlap_on = 0;
        for (std::size_t i = 0; i < records.size(); ++i) {
            const auto& r = records[i]; const auto& e = r.event;
            bool on = e.type == Event::kNoteOnEvent, off = e.type == Event::kNoteOffEvent;
            int channel = on ? e.noteOn.channel : off ? e.noteOff.channel : e.polyPressure.channel;
            int pitch = on ? e.noteOn.pitch : off ? e.noteOff.pitch : e.polyPressure.pitch;
            float value = on ? e.noteOn.velocity : off ? e.noteOff.velocity : e.polyPressure.pressure;
            int note_id = on ? e.noteOn.noteId : off ? e.noteOff.noteId : e.polyPressure.noteId;
            auto tick = std::llround(tick_at(r.frame));
            int rounded = std::clamp(static_cast<int>(std::lround(value * 127)), on ? 1 : 0, 127);
            events.push_back({{"kind", "midi"}, {"tick", tick}, {"order", i},
                {"status", (on ? 0x90 : off ? 0x80 : 0xa0) | channel}, {"data", {pitch, rounded}}});
            diagnostics.push_back({{"sample_frame", r.frame}, {"tick", tick}, {"order", i}, {"bus_index", e.busIndex},
                {"type", e.type}, {"channel", channel}, {"pitch", pitch}, {"value", value}, {"note_id", note_id},
                {"phase", r.frame > score.frames - tail_frames ? "postroll_note_off" : "capture"},
                {"flags", e.flags}, {"ppq_position", std::isfinite(e.ppqPosition) ? json(e.ppqPosition) : json(nullptr)}});
            auto key = std::pair{channel, pitch};
            if (on) { if (active[key]++ > 0) ++overlap_on; }
            if (off) { if (active[key] > 0) --active[key]; else ++unmatched_off; }
        }
        std::uint64_t unterminated = 0;
        for (const auto& [key, count] : active) unterminated += count;
        json project{{"schema_version", 1}, {"ppq", score.ppq}, {"smf_format", 1},
            {"tracks", json::array({{{"name", "Conductor"}, {"events", score.conductor}, {"end_tick", score.end_tick}},
                {{"name", "Captured output"}, {"channel", 0}, {"events", events}, {"end_tick", score.end_tick}}})}};
        auto bytes = cantabile::export_midi(project);
        if (bytes.size() > 1024 * 1024) fail("MIDI_OUTPUT_LIMIT", "Captured MIDI exceeds 1 MiB");
        // Reuse exclusive same-directory publication; no audio header is written.
        WaveFile midi(output); midi.bytes(bytes.data(), bytes.size()); midi.publish();
        return {{"ok", true}, {"schema_version", 1}, {"backend", "vst3"},
            {"capture_status", records.empty() ? "no_events" : "captured"}, {"capture_supported", true},
            {"bus_count", 1}, {"ppq", score.ppq}, {"start_tick", options.start_tick}, {"end_tick", score.end_tick},
            {"sample_rate", sample_rate}, {"event_count", records.size()}, {"total_events", total},
            {"discarded_preroll", discarded_preroll}, {"discarded_before_range", discarded_before}, {"discarded_tail", discarded_tail},
            {"unmatched_note_offs", unmatched_off}, {"unterminated_notes", unterminated}, {"overlapping_note_ons", overlap_on},
            {"rounding", "nearest_tick_and_7bit_half_up_note_on_minimum_1"},
            {"note_ids", "retained_in_diagnostics_not_representable_in_smf"},
            {"latency_policy", "event_timestamps_are_project_time_no_audio_latency_shift"},
            {"events", diagnostics}};
    }
};

#pragma once

#include <cstdint>
#include <vector>
#include <nlohmann/json.hpp>

namespace cantabile {

// Canonical SMF encoding is deterministic. Imported channel events remain raw
// so overlapping/unmatched notes and exact same-tick message order survive.
nlohmann::json import_midi(const std::vector<std::uint8_t>& bytes);
std::vector<std::uint8_t> export_midi(const nlohmann::json& project);

} // namespace cantabile

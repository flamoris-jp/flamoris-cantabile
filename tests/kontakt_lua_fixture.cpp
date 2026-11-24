// SPDX-License-Identifier: Apache-2.0
#include "../audio/vst3/kontakt_lua.hpp"
#include <iostream>
int main() {
    nlohmann::json value;
    std::cin >> value;
    std::cout << cantabile::audio::kontakt::kontakt_lua(value.at("request"), value.at("receipt").get<std::string>());
}

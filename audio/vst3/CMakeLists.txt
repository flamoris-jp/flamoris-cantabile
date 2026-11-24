cmake_minimum_required(VERSION 3.25)

if(NOT ((WIN32 OR CMAKE_SYSTEM_NAME STREQUAL "Linux") AND CMAKE_SIZEOF_VOID_P EQUAL 8))
    message(FATAL_ERROR "CantabileClient requires a 64-bit Windows or Linux build")
endif()
set(SMTG_ENABLE_VSTGUI_SUPPORT OFF CACHE BOOL "" FORCE)
set(SMTG_ENABLE_VST3_HOSTING_EXAMPLES OFF CACHE BOOL "" FORCE)
option(CANTABILE_VST3_TEST_PLUGIN "Build the SDK's MDA test instruments" OFF)
set(SMTG_ENABLE_VST3_PLUGIN_EXAMPLES ${CANTABILE_VST3_TEST_PLUGIN} CACHE BOOL "" FORCE)
set(SMTG_CREATE_PLUGIN_LINK OFF CACHE BOOL "" FORCE)
set(SMTG_RUN_VST_VALIDATOR OFF CACHE BOOL "" FORCE)
add_subdirectory("${CANTABILE_VST3_SDK}" vst3-sdk EXCLUDE_FROM_ALL)
if(CANTABILE_VST3_TEST_PLUGIN)
    smtg_add_vst3plugin(cantabile-capture-fixture tests/capture_fixture.cpp)
    set(public_sdk_SOURCE_DIR "${CANTABILE_VST3_SDK}/public.sdk")
    smtg_target_add_library_main(cantabile-capture-fixture)
    target_link_libraries(cantabile-capture-fixture PRIVATE sdk)
    target_compile_features(cantabile-capture-fixture PRIVATE cxx_std_20)
    add_custom_target(cantabile-vst3-test-plugin)
    add_dependencies(cantabile-vst3-test-plugin mda-vst3 cantabile-capture-fixture)
endif()

set(host "${CANTABILE_VST3_SDK}/public.sdk/source/vst/hosting")
if(WIN32)
    set(module "${host}/module_win32.cpp")
else()
    set(module "${host}/module_linux.cpp")
endif()
add_executable(cantabile-client
    renderer.cpp
    ../../src/midi.cpp
    "${host}/plugprovider.cpp"
    "${CANTABILE_VST3_SDK}/public.sdk/source/common/memorystream.cpp"
    "${module}"
)
target_include_directories(cantabile-client PRIVATE ../../include)
target_compile_features(cantabile-client PRIVATE cxx_std_20)
target_link_libraries(cantabile-client PRIVATE sdk sdk_hosting nlohmann_json::nlohmann_json ${CMAKE_DL_LIBS})
if(MSVC)
    # The SDK's Windows module checks __cplusplus to handle char8_t paths.
    target_compile_options(cantabile-client PRIVATE /Zc:__cplusplus)
endif()
if(WIN32)
    target_link_libraries(cantabile-client PRIVATE ole32 shell32 user32)
    if(MINGW)
        target_link_options(cantabile-client PRIVATE -municode)
    endif()
endif()
install(TARGETS cantabile-client RUNTIME DESTINATION bin)

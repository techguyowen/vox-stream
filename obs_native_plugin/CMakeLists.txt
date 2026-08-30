cmake_minimum_required(VERSION 3.16)
project(obs_live_captions VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)
set(CMAKE_AUTORCC ON)
set(CMAKE_AUTOUIC ON)

# Find OBS Studio SDK
find_package(libobs REQUIRED)
find_package(obs-frontend-api REQUIRED)

# Find Qt (Prefer Qt6, fallback to Qt5)
find_package(Qt6 COMPONENTS Core Widgets QUIET)
if(NOT Qt6_FOUND)
    find_package(Qt5 COMPONENTS Core Widgets REQUIRED)
endif()

add_library(obs-live-captions MODULE
    src/plugin_main.cpp
    src/captions_filter.cpp
    src/captions_filter.h
    src/censor_engine.cpp
    src/censor_engine.h
    src/audio_resampler.cpp
    src/audio_resampler.h
    src/caption_output.cpp
    src/caption_output.h
    src/stt_client.cpp
    src/stt_client.h
    src/captions_dialog.cpp
    src/captions_dialog.h
)

target_include_directories(obs-live-captions PRIVATE
    src
)

if(TARGET Qt6::Core)
    target_link_libraries(obs-live-captions PRIVATE
        OBS::libobs
        OBS::obs-frontend-api
        Qt6::Core
        Qt6::Widgets
    )
else()
    target_link_libraries(obs-live-captions PRIVATE
        OBS::libobs
        OBS::obs-frontend-api
        Qt5::Core
        Qt5::Widgets
    )
endif()

set_target_properties(obs-live-captions PROPERTIES
    PREFIX ""
    OUTPUT_NAME "obs-live-captions"
)

# Installation into OBS Studio plugins folder
if(WIN32)
    install(TARGETS obs-live-captions
        LIBRARY DESTINATION "obs-plugins/64bit"
        RUNTIME DESTINATION "obs-plugins/64bit"
    )
elseif(APPLE)
    install(TARGETS obs-live-captions
        LIBRARY DESTINATION "Library/Application Support/obs-studio/plugins/obs-live-captions/bin"
    )
else()
    install(TARGETS obs-live-captions
        LIBRARY DESTINATION "${CMAKE_INSTALL_LIBDIR}/obs-plugins"
    )
endif()

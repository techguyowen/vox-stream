cmake_minimum_required(VERSION 3.16)
project(obs_live_captions VERSION 1.0.0 LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 1. Look for installed OBS Studio SDK
find_package(libobs QUIET)
find_package(obs-frontend-api QUIET)

# 2. Look for Qt (Optional - builds without Qt if not found)
find_package(Qt6 COMPONENTS Core Widgets QUIET)
if(NOT Qt6_FOUND)
    find_package(Qt5 COMPONENTS Core Widgets QUIET)
endif()

if(TARGET Qt6::Widgets OR TARGET Qt5::Widgets)
    set(HAVE_QT TRUE)
    set(CMAKE_AUTOMOC ON)
    set(CMAKE_AUTORCC ON)
    set(CMAKE_AUTOUIC ON)
    message(STATUS "Qt found. Building full native Qt settings dialog.")
else()
    set(HAVE_QT FALSE)
    message(STATUS "Qt not detected. Building standalone OBS Audio Filter (configured via Web Dashboard & OBS Properties).")
endif()

set(PLUGIN_SOURCES
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
)

if(HAVE_QT)
    list(APPEND PLUGIN_SOURCES
        src/captions_dialog.cpp
        src/captions_dialog.h
    )
endif()

add_library(obs-live-captions MODULE ${PLUGIN_SOURCES})

# Include bundled SDK headers if libobs is not installed as a system package
target_include_directories(obs-live-captions PRIVATE
    src
    ${CMAKE_CURRENT_SOURCE_DIR}/deps/obs-sdk/include
    ${CMAKE_CURRENT_SOURCE_DIR}/deps/httplib
)

if(HAVE_QT)
    target_compile_definitions(obs-live-captions PRIVATE ENABLE_QT=1)
    if(TARGET Qt6::Core)
        target_link_libraries(obs-live-captions PRIVATE Qt6::Core Qt6::Widgets)
    elseif(TARGET Qt5::Core)
        target_link_libraries(obs-live-captions PRIVATE Qt5::Core Qt5::Widgets)
    endif()
endif()

if(TARGET OBS::libobs)
    target_link_libraries(obs-live-captions PRIVATE OBS::libobs)
endif()
if(TARGET OBS::obs-frontend-api)
    target_link_libraries(obs-live-captions PRIVATE OBS::obs-frontend-api)
endif()

set_target_properties(obs-live-captions PROPERTIES
    PREFIX ""
    OUTPUT_NAME "obs-live-captions"
)

if(APPLE AND NOT TARGET OBS::libobs)
    set_target_properties(obs-live-captions PROPERTIES LINK_FLAGS "-undefined dynamic_lookup")
endif()


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

if(WIN32)
    target_link_libraries(obs-live-captions PRIVATE ws2_32 crypt32)
endif()

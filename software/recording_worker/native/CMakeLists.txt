cmake_minimum_required(VERSION 3.20)

project(study_runner_xdf_core VERSION 1.0.0 LANGUAGES CXX)

include(CTest)

add_library(study_runner_xdf_core SHARED
    src/c_api.cpp
    src/xdfwriter_patched.cpp
)
target_compile_features(study_runner_xdf_core PRIVATE cxx_std_20)
target_include_directories(study_runner_xdf_core
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:include>
    PRIVATE
        ${CMAKE_CURRENT_SOURCE_DIR}/src
        ${CMAKE_CURRENT_SOURCE_DIR}/vendor/App-LabRecorder/xdfwriter
)
set_target_properties(study_runner_xdf_core PROPERTIES
    OUTPUT_NAME xdf_core
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN YES
)

if(MSVC)
    target_compile_options(study_runner_xdf_core PRIVATE /W4 /permissive- /EHsc)
    target_compile_definitions(study_runner_xdf_core PRIVATE _SILENCE_CXX20_U8PATH_DEPRECATION_WARNING)
else()
    target_compile_options(study_runner_xdf_core PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(BUILD_TESTING)
    add_executable(study_runner_xdf_core_test tests/core_test.cpp)
    target_compile_features(study_runner_xdf_core_test PRIVATE cxx_std_20)
    target_link_libraries(study_runner_xdf_core_test PRIVATE study_runner_xdf_core)
    add_test(NAME study_runner_xdf_core_test COMMAND study_runner_xdf_core_test)
endif()

install(TARGETS study_runner_xdf_core
    RUNTIME DESTINATION .
    LIBRARY DESTINATION .
    ARCHIVE DESTINATION lib
)
install(FILES include/study_runner_xdf_core.h DESTINATION include)

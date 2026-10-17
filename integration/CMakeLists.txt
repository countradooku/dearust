# Install this file at src/buildtools/CMakeLists.txt, and this Cargo package
# at src/buildtools/rust. The resulting executable paths match dsym.cmake.
cmake_minimum_required(VERSION 3.13)
project(buildtools NONE)

find_program(DARLING_CARGO_EXECUTABLE cargo)
if(NOT DARLING_CARGO_EXECUTABLE)
    message(FATAL_ERROR "Darling's Rust build tools require Cargo and Rust >= 1.85")
endif()

set(DARLING_RUST_TOOLS_SOURCE "${CMAKE_CURRENT_SOURCE_DIR}/rust")
set(DARLING_RUST_TOOLS_TARGET "${CMAKE_CURRENT_BINARY_DIR}/cargo-target")
get_filename_component(DARLING_RUST_BSDLN_DIR "${CMAKE_CURRENT_BINARY_DIR}/../bsdln" ABSOLUTE)
file(GLOB_RECURSE DARLING_RUST_TOOLS_SOURCES CONFIGURE_DEPENDS
    "${DARLING_RUST_TOOLS_SOURCE}/src/*.rs")
add_custom_command(
    OUTPUT "${CMAKE_CURRENT_BINARY_DIR}/rust-buildtools.stamp"
    BYPRODUCTS "${CMAKE_CURRENT_BINARY_DIR}/elfdep" "${CMAKE_CURRENT_BINARY_DIR}/getuuid"
        "${DARLING_RUST_BSDLN_DIR}/bsdln"
    COMMAND "${DARLING_CARGO_EXECUTABLE}" build --release --locked --offline
        --manifest-path "${DARLING_RUST_TOOLS_SOURCE}/Cargo.toml"
        --target-dir "${DARLING_RUST_TOOLS_TARGET}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${DARLING_RUST_TOOLS_TARGET}/release/elfdep" "${CMAKE_CURRENT_BINARY_DIR}/elfdep"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${DARLING_RUST_TOOLS_TARGET}/release/getuuid" "${CMAKE_CURRENT_BINARY_DIR}/getuuid"
    COMMAND "${CMAKE_COMMAND}" -E make_directory "${DARLING_RUST_BSDLN_DIR}"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${DARLING_RUST_TOOLS_TARGET}/release/bsdln" "${DARLING_RUST_BSDLN_DIR}/bsdln"
    COMMAND "${CMAKE_COMMAND}" -E touch "${CMAKE_CURRENT_BINARY_DIR}/rust-buildtools.stamp"
    DEPENDS ${DARLING_RUST_TOOLS_SOURCES}
        "${DARLING_RUST_TOOLS_SOURCE}/Cargo.toml"
        "${DARLING_RUST_TOOLS_SOURCE}/Cargo.lock"
    VERBATIM
)
add_custom_target(darling_rust_buildtools ALL
    DEPENDS "${CMAKE_CURRENT_BINARY_DIR}/rust-buildtools.stamp")

foreach(tool elfdep getuuid)
    add_executable(${tool} IMPORTED GLOBAL)
    set_target_properties(${tool} PROPERTIES IMPORTED_LOCATION "${CMAKE_CURRENT_BINARY_DIR}/${tool}")
    add_dependencies(${tool} darling_rust_buildtools)
endforeach()

if(TARGET bsdln)
    add_dependencies(bsdln darling_rust_buildtools)
endif()

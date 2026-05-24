cmake_minimum_required(VERSION 3.16)
project(xuanji C)

set(CMAKE_C_STANDARD 11)
set(CMAKE_C_STANDARD_REQUIRED ON)

# 编译选项
if(MSVC)
    add_compile_options(/W4 /WX /utf-8)
    add_compile_definitions(_CRT_SECURE_NO_WARNINGS)
else()
    add_compile_options(-Wall -Wextra -Werror -pedantic)
    if(CMAKE_BUILD_TYPE STREQUAL "Debug")
        add_compile_options(-fsanitize=address -fsanitize=undefined)
        add_link_options(-fsanitize=address -fsanitize=undefined)
    endif()
endif()

# 头文件
include_directories(include)

# 核心源文件（纯算法，不碰OS）
set(CORE_SOURCES
    src/oa_bus.c
    src/oa_sched.c
    src/oa_res.c
    src/oa_proc.c
    src/oa_heart.c
    src/oa_fs.c
    src/oa_init.c
)

# 平台抽象层
if(WIN32)
    list(APPEND CORE_SOURCES pal/oa_pal_win.c)
elseif(APPLE)
    list(APPEND CORE_SOURCES pal/oa_pal_unix.c)
else()
    list(APPEND CORE_SOURCES pal/oa_pal_unix.c)
endif()

# 编译为动态库
add_library(xuanji SHARED ${CORE_SOURCES})

# 导出符号
target_compile_definitions(xuanji PRIVATE OA_BUILD_DLL)

# 链接系统库
if(WIN32)
    target_link_libraries(xuanji PRIVATE kernel32 user32)
elseif(APPLE)
    target_link_libraries(xuanji PRIVATE pthread)
else()
    target_link_libraries(xuanji PRIVATE pthread rt)
endif()

# 安装
install(TARGETS xuanji DESTINATION lib)
install(FILES include/xuanji.h include/oa_pal.h DESTINATION include)

# 测试
enable_testing()

if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/tests")
    file(GLOB TEST_SOURCES tests/test_*.c)
    foreach(TEST_SRC ${TEST_SOURCES})
        get_filename_component(TEST_NAME ${TEST_SRC} NAME_WE)
        add_executable(${TEST_NAME} ${TEST_SRC})
        target_link_libraries(${TEST_NAME} PRIVATE xuanji)
        add_test(NAME ${TEST_NAME} COMMAND ${TEST_NAME})
    endforeach()
endif()


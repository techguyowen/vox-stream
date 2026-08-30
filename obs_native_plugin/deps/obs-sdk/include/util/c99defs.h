#pragma once

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#ifdef _MSC_VER
#define EXPORT_API __declspec(dllexport)
#define IMPORT_API __declspec(dllimport)
#else
#define EXPORT_API __attribute__((visibility("default")))
#define IMPORT_API
#endif

#ifndef UNUSED_PARAMETER
#define UNUSED_PARAMETER(param) (void)(param)
#endif

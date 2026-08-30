#pragma once

#include "c99defs.h"
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

static inline void *bmemdup(const void *ptr, size_t size)
{
    if (!ptr || size == 0) return NULL;
    void *out = malloc(size);
    if (out) memcpy(out, ptr, size);
    return out;
}

static inline void bfree(void *ptr)
{
    free(ptr);
}

#ifdef __cplusplus
}
#endif

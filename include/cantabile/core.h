#ifndef CANTABILE_CORE_H
#define CANTABILE_CORE_H

#if defined(_WIN32)
# if defined(CANTABILE_BUILDING_CORE)
#  define CANTABILE_API __declspec(dllexport)
# else
#  define CANTABILE_API __declspec(dllimport)
# endif
#else
# define CANTABILE_API __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* Each instance owns one authoritative project, revision and editing history.
 * Requests and responses are UTF-8 JSON. No filesystem or audio ownership is
 * granted by this ABI. Always release a response with cantabile_free().
 * Calls on an instance are serialized; destruction requires callers to stop. */
CANTABILE_API void* cantabile_create(void);
CANTABILE_API void cantabile_destroy(void* engine);
CANTABILE_API char* cantabile_request(void* engine, const char* request_json);
CANTABILE_API void cantabile_free(char* response_json);

#ifdef __cplusplus
}
#endif
#endif
